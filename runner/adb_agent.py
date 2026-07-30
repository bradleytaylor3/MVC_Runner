"""Core loop for `test-adb`: for each work task, launch the app and repeatedly
dump the on-screen UI, ask the local model for exactly one action, validate
and execute it over ADB, until the model declares the scenario pass/fail or
a step limit is hit. Unlike `run_batch` (code-editing), a failing scenario
doesn't invalidate later ones, so the batch runs every task regardless of
individual verdicts; it only aborts early on a hard error (ADB or Ollama
unreachable)."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from runner import adb_client, ollama_client
from runner.adb_task import AdbInitTask, AdbWorkTask
from runner.ui_dump import UiDumpError, element_by_index, format_elements_for_prompt, parse_elements

HISTORY_WINDOW = 6
ACTION_TYPES = ("tap", "swipe", "input_text", "key", "wait", "done")
SWIPE_DIRECTIONS = ("up", "down", "left", "right")
KEY_NAMES = ("back", "home", "enter")
RESULTS = ("pass", "fail")

TERMINAL_STATUSES = ("pass", "fail", "inconclusive", "error")

# Passed as `format` to ollama_client.generate() so Ollama constrains decoding
# to this shape at the grammar level — this is what makes the action grammar
# reliable even on small (sub-2B) models that won't reliably follow prose
# formatting instructions on their own. It only constrains *shape*: which
# fields besides "action" are actually required still depends on which action
# was picked, so validate_action() below still does the semantic checking.
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(ACTION_TYPES)},
        "index": {"type": "integer"},
        "direction": {"type": "string", "enum": list(SWIPE_DIRECTIONS)},
        "text": {"type": "string"},
        "name": {"type": "string", "enum": list(KEY_NAMES)},
        "seconds": {"type": "number"},
        "result": {"type": "string", "enum": list(RESULTS)},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}

SYSTEM_PREAMBLE = """You are an Android UI testing agent. You control a real device over ADB, one \
action per turn. You will be given a testing goal, acceptance criteria, the current on-screen elements \
(numbered), and a short history of your recent actions. Reply with ONLY a single JSON object describing \
the next action to take — no commentary before, between, or after it.

Valid actions:
{"action": "tap", "index": <element index from the list>}
{"action": "swipe", "direction": "up"|"down"|"left"|"right"}
{"action": "input_text", "text": "<text to type into the currently focused field>"}
{"action": "key", "name": "back"|"home"|"enter"}
{"action": "wait", "seconds": <number, for content that loads asynchronously>}
{"action": "done", "result": "pass"|"fail", "reason": "<short explanation>"}

Only emit "done" once you can tell from the current elements whether the acceptance criteria are met \
(pass) or clearly cannot be met / something is broken (fail). Tap only indices that appear in the current \
element list — it changes every turn, so re-check it rather than reusing an index from history."""


class ActionError(ValueError):
    pass


def _extract_json_object(text: str) -> dict:
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    search_text = fence_match.group(1) if fence_match else text

    start = search_text.find("{")
    if start == -1:
        raise ActionError("model output did not contain a JSON object")

    depth = 0
    for i in range(start, len(search_text)):
        if search_text[i] == "{":
            depth += 1
        elif search_text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = search_text[start:i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as e:
                    raise ActionError(f"model output looked like JSON but failed to parse: {e}") from e

    raise ActionError("model output had an unterminated JSON object")


def parse_action(text: str) -> dict:
    # With `format=ACTION_SCHEMA` passed to generate(), Ollama constrains
    # decoding so `text` should already be exactly one conforming JSON object.
    # Still fall back to tolerant extraction (handles code-fence wrapping, or
    # a host/model that doesn't honor `format`) rather than assuming that.
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return _extract_json_object(text)


def validate_action(action: dict, elements: list) -> None:
    action_type = action.get("action")
    if action_type not in ACTION_TYPES:
        raise ActionError(f"'action' must be one of {ACTION_TYPES}, got {action_type!r}")

    if action_type == "tap":
        index = action.get("index")
        if not isinstance(index, int) or element_by_index(elements, index) is None:
            raise ActionError(f"tap index {index!r} is not a valid element index in the current screen")
    elif action_type == "swipe":
        if action.get("direction") not in SWIPE_DIRECTIONS:
            raise ActionError(f"swipe 'direction' must be one of {SWIPE_DIRECTIONS}, got {action.get('direction')!r}")
    elif action_type == "input_text":
        if not isinstance(action.get("text"), str):
            raise ActionError("input_text requires a string 'text' field")
    elif action_type == "key":
        if action.get("name") not in KEY_NAMES:
            raise ActionError(f"key 'name' must be one of {KEY_NAMES}, got {action.get('name')!r}")
    elif action_type == "wait":
        if not isinstance(action.get("seconds"), (int, float)):
            raise ActionError("wait requires a numeric 'seconds' field")
    elif action_type == "done":
        if action.get("result") not in RESULTS:
            raise ActionError(f"done 'result' must be one of {RESULTS}, got {action.get('result')!r}")


def _describe_action(action: dict, elements: list) -> str:
    action_type = action["action"]
    if action_type == "tap":
        el = element_by_index(elements, action["index"])
        return f'tap [{action["index"]}] {el.class_name} "{el.text}"'
    if action_type == "swipe":
        return f"swipe {action['direction']}"
    if action_type == "input_text":
        return f'input_text "{action["text"]}"'
    if action_type == "key":
        return f"key {action['name']}"
    if action_type == "wait":
        return f"wait {action['seconds']}s"
    return f"done: {action['result']} ({action.get('reason', '')})"


def execute_action(action: dict, elements: list, serial: str | None) -> None:
    action_type = action["action"]
    if action_type == "tap":
        el = element_by_index(elements, action["index"])
        adb_client.tap(*el.center, serial=serial)
    elif action_type == "swipe":
        width, height = adb_client.screen_size(serial=serial)
        margin_x, margin_y = width // 5, height // 5
        cx, cy = width // 2, height // 2
        coords = {
            "up": (cx, height - margin_y, cx, margin_y),
            "down": (cx, margin_y, cx, height - margin_y),
            "left": (width - margin_x, cy, margin_x, cy),
            "right": (margin_x, cy, width - margin_x, cy),
        }[action["direction"]]
        adb_client.swipe(*coords, serial=serial)
    elif action_type == "input_text":
        adb_client.input_text(action["text"], serial=serial)
    elif action_type == "key":
        adb_client.press_key(action["name"], serial=serial)
    elif action_type == "wait":
        time.sleep(action["seconds"])
    # "done" performs no device action.


def build_step_prompt(task: AdbWorkTask, history: list[str], elements_text: str) -> str:
    parts = [SYSTEM_PREAMBLE, "", f"# Test {task.id}: {task.title}", "", f"Goal: {task.goal}"]

    parts.append("\n## Acceptance criteria")
    parts.extend(f"- {c}" for c in task.acceptance_criteria)

    if history:
        parts.append("\n## Recent actions")
        parts.extend(history[-HISTORY_WINDOW:])

    parts.append("\n## Current screen elements")
    parts.append(elements_text)

    return "\n".join(parts)


def run_adb_task(
    task: AdbWorkTask,
    init: AdbInitTask,
    model: str,
    host: str,
    serial: str | None,
    dry_run: bool,
    think: bool,
) -> dict:
    entry = {"id": task.id, "title": task.title}
    start = time.monotonic()
    package = task.resolved_package(init)
    max_steps = task.resolved_max_steps(init)

    try:
        if task.reset_app:
            adb_client.force_stop(package, serial=serial)
        adb_client.start_app(package, activity=task.activity, serial=serial)
        time.sleep(1.5)  # let the app finish launching before the first UI dump
    except adb_client.AdbError as e:
        entry.update(status="error", error=str(e), steps=[], elapsed_seconds=time.monotonic() - start)
        return entry

    history: list[str] = []
    steps: list[dict] = []

    for step_num in range(1, max_steps + 1):
        try:
            xml_text = adb_client.dump_ui(serial=serial)
            elements = parse_elements(xml_text)
        except (adb_client.AdbError, UiDumpError) as e:
            entry.update(status="error", error=str(e), steps=steps, elapsed_seconds=time.monotonic() - start)
            return entry

        prompt = build_step_prompt(task, history, format_elements_for_prompt(elements))

        try:
            result = ollama_client.generate(prompt, model=model, host=host, think=think, format=ACTION_SCHEMA)
        except ollama_client.OllamaError as e:
            entry.update(status="error", error=str(e), steps=steps, elapsed_seconds=time.monotonic() - start)
            return entry

        try:
            action = parse_action(result.text)
            validate_action(action, elements)
        except ActionError as e:
            step_entry = {"step": step_num, "error": str(e), "raw_response": result.text}
            steps.append(step_entry)
            history.append(f"Step {step_num}: invalid model output ({e})")
            continue

        description = _describe_action(action, elements)
        if not dry_run:
            try:
                execute_action(action, elements, serial)
            except adb_client.AdbError as e:
                entry.update(status="error", error=str(e), steps=steps, elapsed_seconds=time.monotonic() - start)
                return entry

        steps.append({"step": step_num, "action": action, "executed": not dry_run, "description": description})
        history.append(f"Step {step_num}: {description}")

        if action["action"] == "done":
            entry.update(
                status=action["result"],
                reason=action.get("reason", ""),
                steps=steps,
                elapsed_seconds=time.monotonic() - start,
            )
            return entry

    entry.update(
        status="inconclusive",
        error=f"reached max_steps ({max_steps}) without the model emitting 'done'",
        steps=steps,
        elapsed_seconds=time.monotonic() - start,
    )
    return entry


def run_adb_batch(
    init: AdbInitTask,
    tasks: list[AdbWorkTask],
    model: str,
    host: str,
    serial: str | None,
    dry_run: bool,
    logs_dir: Path,
    think: bool = False,
) -> dict:
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = datetime.now(timezone.utc).isoformat()

    entries = [{
        "id": "init",
        "batch_id": init.batch_id,
        "serial": serial,
        "task_count": len(tasks),
        "status": "init",
    }]

    for task in tasks:
        print(f"[{task.id}] {task.title} ...", end=" ", flush=True)
        entry = run_adb_task(task, init, model, host, serial, dry_run, think)
        print(entry["status"])
        entries.append(entry)

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_ts}.json"
    log_data = {
        "batch_id": init.batch_id,
        "started_at": started_at,
        "dry_run": dry_run,
        "entries": entries,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")

    passed = sum(1 for e in entries if e["status"] == "pass")
    failed = sum(1 for e in entries if e["status"] in ("fail", "inconclusive", "error"))
    print(f"\n{passed} passed, {failed} failed/inconclusive. Log written to {log_path}")

    return log_data
