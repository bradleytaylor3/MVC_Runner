"""Companion to `adb_agent`: once a scenario's action path has been authored
by the model (see `adb_agent.run_adb_task`), it can be replayed deterministically
by resolving each step's saved element selector against the live screen and
executing it directly, with no per-step model call. Only the final pass/fail
judgment still calls the model (one call per scenario instead of one per
step). If a selector can no longer be resolved, or the final judgment
disagrees with what was recorded, that's a real change worth re-authoring
for — not something to paper over — so replay reports it as drift and lets
the caller (`adb_agent.run_adb_batch`) fall back to a full authoring run."""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from runner import adb_client, ollama_client
from runner.adb_task import AdbInitTask, AdbWorkTask
from runner.ui_dump import UiDumpError, UiElement, element_by_index, format_elements_for_prompt, parse_elements

# "reason" is listed before "result" deliberately: under grammar-constrained
# decoding the model fills object keys in schema-declared order, so with
# "result" first it can commit to a verdict *before* generating any
# reasoning about the screen. Confirmed live on a real device: with "result"
# first, the model's "reason" text correctly worked out that the screen
# showed "6" and should fail, while the "result" field it had already
# committed to still said "pass" -- the two were generated independently
# rather than the second following from the first. Putting "reason" first
# forces the explanation to exist before the verdict does, so the verdict
# can actually follow from it.
JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "result": {"type": "string", "enum": ["pass", "fail"]},
    },
    "required": ["result"],
}

JUDGMENT_PREAMBLE = """You are an Android UI testing agent reviewing the screen after a previously-recorded \
action sequence was replayed. Judge whether the acceptance criteria are met by the current screen. Reply \
with ONLY a single JSON object — no commentary before, between, or after it. Write "reason" first, stating \
plainly what the relevant on-screen element(s) actually show; then "result" must follow from that -- if \
your own "reason" doesn't satisfy the acceptance criteria, "result" must be "fail":
{"reason": "<what the screen actually shows, and whether that satisfies the acceptance criteria>", "result": "pass"|"fail"}"""


class ReplayError(ValueError):
    pass


# --- recording format -------------------------------------------------

def build_selector(el: UiElement) -> dict | None:
    """Resource-id is preferred (usually stable across layout changes); text
    + class_name is the fallback for elements without one. An element with
    neither (e.g. an icon-only button) can't be recorded reliably."""
    if el.resource_id:
        return {"resource_id": el.resource_id}
    if el.text:
        return {"text": el.text, "class_name": el.class_name}
    return None


def resolve_selector(elements: list[UiElement], selector: dict) -> UiElement | None:
    """Returns the matching element, or None if it's missing or ambiguous
    (more than one candidate) — either way, not safe to act on blindly."""
    resource_id = selector.get("resource_id")
    if resource_id:
        matches = [el for el in elements if el.resource_id == resource_id]
        return matches[0] if len(matches) == 1 else None

    matches = [el for el in elements if el.text == selector.get("text") and el.class_name == selector.get("class_name")]
    return matches[0] if len(matches) == 1 else None


def capture_step(action: dict, elements: list[UiElement]) -> dict | None:
    """Turns an executed action into a recordable step. Returns None for
    "done" (the terminal verdict is recorded separately) or for a tap whose
    element has no usable selector — either signals the caller shouldn't
    persist a recording for this run at all, since replay couldn't safely
    reproduce every step."""
    action_type = action["action"]
    if action_type == "tap":
        el = element_by_index(elements, action["index"])
        selector = build_selector(el) if el is not None else None
        if selector is None:
            return None
        return {"selector": selector, "action": {"action": "tap"}}
    if action_type == "swipe":
        return {"selector": None, "action": {"action": "swipe", "direction": action["direction"]}}
    if action_type == "input_text":
        return {"selector": None, "action": {"action": "input_text", "text": action["text"]}}
    if action_type == "key":
        return {"selector": None, "action": {"action": "key", "name": action["name"]}}
    if action_type == "wait":
        return {"selector": None, "action": {"action": "wait", "seconds": action["seconds"]}}
    return None


def compute_goal_hash(task: AdbWorkTask) -> str:
    """A recording is only valid for the goal/acceptance_criteria text it was
    authored against — if a work doc is edited, its recording (if any) should
    be treated as stale rather than silently replayed against a goal it no
    longer matches."""
    payload = task.goal + "\n" + "\n".join(task.acceptance_criteria)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def recording_path(work_dir: Path, task_id: str) -> Path:
    return work_dir / "recordings" / f"{task_id}.json"


def write_recording(work_dir: Path, task: AdbWorkTask, steps: list[dict], expected_result: str, expected_reason: str) -> None:
    path = recording_path(work_dir, task.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "task_id": task.id,
        "goal_hash": compute_goal_hash(task),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "expected_result": expected_result,
        "expected_reason": expected_reason,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_recording(work_dir: Path, task: AdbWorkTask) -> dict | None:
    """Returns None if there's no recording, or if there is one but it no
    longer matches the task's current goal_hash (stale)."""
    path = recording_path(work_dir, task.id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if data.get("goal_hash") != compute_goal_hash(task):
        return None
    return data


# --- replay -------------------------------------------------------------

def _parse_judgment(text: str) -> dict:
    from runner import adb_agent  # deferred: adb_agent imports this module at load time

    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        data = adb_agent._extract_json_object(text)
    if data.get("result") not in ("pass", "fail"):
        raise ReplayError(f"judgment 'result' must be 'pass' or 'fail', got {data.get('result')!r}")
    return data


def build_judgment_prompt(task: AdbWorkTask, elements_text: str) -> str:
    parts = [JUDGMENT_PREAMBLE, "", f"# Test {task.id}: {task.title}", "", f"Goal: {task.goal}"]
    parts.append("\n## Acceptance criteria")
    parts.extend(f"- {c}" for c in task.acceptance_criteria)
    parts.append("\n## Final screen elements")
    parts.append(elements_text)
    return "\n".join(parts)


def replay_adb_task(
    task: AdbWorkTask,
    init: AdbInitTask,
    recording: dict,
    model: str,
    host: str,
    serial: str | None,
    dry_run: bool,
) -> dict:
    """Executes a saved action recording with no per-step model call, then
    makes exactly one model call to judge the final screen against
    acceptance_criteria. Returns status "drift" (with a "drift" reason of
    "selector_missing" or "verdict_changed") if the recording could not be
    safely replayed to its recorded conclusion — the caller should fall back
    to `adb_agent.run_adb_task` in that case, not trust this entry as final.

    Judging free-form acceptance criteria in prose is a harder task than
    picking a tap target from a numbered list, so this is worth a capable
    model even though it costs more per call -- confirmed live against a
    real device: qwen2.5:1.5b hallucinated a "fail" verdict against an
    unambiguous on-screen "4" two runs in a row, while gemma4:12b (this
    module's default, see cli.py) got it right immediately on the same
    prompt."""
    from runner import adb_agent  # deferred: adb_agent imports this module at load time

    entry = {"id": task.id, "title": task.title}
    start = time.monotonic()
    package = task.resolved_package(init)

    # Replay makes at most one model call total (the final judgment below),
    # regardless of how many steps it replays -- unlike run_adb_task, where
    # model_calls grows by one per step. That contrast is the whole point of
    # this module, so every exit path reports these fields for token_savings
    # to compare directly against a full authoring run of the same task.
    no_model_call = dict(model_calls=0, prompt_eval_count=0, eval_count=0)

    try:
        if task.reset_app:
            adb_client.force_stop(package, serial=serial)
        adb_client.start_app(package, activity=task.activity, serial=serial)
        time.sleep(1.5)  # let the app finish launching before the first UI dump
    except adb_client.AdbError as e:
        entry.update(status="error", error=str(e), steps=[], replayed=False, **no_model_call,
                     elapsed_seconds=time.monotonic() - start)
        return entry

    steps_log: list[dict] = []

    for step_num, recorded_step in enumerate(recording["steps"], start=1):
        try:
            xml_text = adb_client.dump_ui(serial=serial)
            elements = parse_elements(xml_text)
        except (adb_client.AdbError, UiDumpError) as e:
            entry.update(status="error", error=str(e), steps=steps_log, replayed=False, **no_model_call,
                         elapsed_seconds=time.monotonic() - start)
            return entry

        action = dict(recorded_step["action"])
        selector = recorded_step["selector"]
        if selector is not None:
            el = resolve_selector(elements, selector)
            if el is None:
                entry.update(
                    status="drift", drift="selector_missing", step=step_num, selector=selector,
                    steps=steps_log, replayed=False, **no_model_call, elapsed_seconds=time.monotonic() - start,
                )
                return entry
            action["index"] = el.index

        description = adb_agent._describe_action(action, elements)
        if not dry_run:
            try:
                adb_agent.execute_action(action, elements, serial)
            except adb_client.AdbError as e:
                entry.update(status="error", error=str(e), steps=steps_log, replayed=False, **no_model_call,
                             elapsed_seconds=time.monotonic() - start)
                return entry

        steps_log.append({"step": step_num, "action": action, "executed": not dry_run, "description": description})

    try:
        xml_text = adb_client.dump_ui(serial=serial)
        elements = parse_elements(xml_text)
    except (adb_client.AdbError, UiDumpError) as e:
        entry.update(status="error", error=str(e), steps=steps_log, replayed=False, **no_model_call,
                     elapsed_seconds=time.monotonic() - start)
        return entry

    prompt = build_judgment_prompt(task, format_elements_for_prompt(elements))
    try:
        result = ollama_client.generate(prompt, model=model, host=host, think=False, format=JUDGMENT_SCHEMA)
    except ollama_client.OllamaError as e:
        entry.update(status="error", error=str(e), steps=steps_log, replayed=False, **no_model_call,
                     elapsed_seconds=time.monotonic() - start)
        return entry

    judged_model_call = dict(model_calls=1, prompt_eval_count=result.prompt_eval_count or 0,
                              eval_count=result.eval_count or 0)

    try:
        judgment = _parse_judgment(result.text)
    except ReplayError as e:
        entry.update(status="error", error=str(e), steps=steps_log, replayed=False, **judged_model_call,
                     elapsed_seconds=time.monotonic() - start)
        return entry

    elapsed = time.monotonic() - start
    if judgment["result"] != recording["expected_result"]:
        entry.update(
            status="drift", drift="verdict_changed", previous_result=recording["expected_result"],
            new_result=judgment["result"], reason=judgment.get("reason", ""),
            steps=steps_log, replayed=False, **judged_model_call, elapsed_seconds=elapsed,
        )
        return entry

    entry.update(
        status=judgment["result"], reason=judgment.get("reason", recording.get("expected_reason", "")),
        steps=steps_log, replayed=True, **judged_model_call, elapsed_seconds=elapsed,
    )
    return entry
