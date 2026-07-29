"""Core loop: for each work task in a batch, resolve its anchor against the
real file, build a fragment-only prompt, call the local model, parse and
validate its fragment, splice it into the real file, and (unless dry-run)
write the result. Stops the batch on the first task that doesn't succeed."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from runner import anchor as anchor_mod
from runner import ollama_client
from runner import patch
from runner.work_doc import InitTask, WorkTask

FRAGMENT_RE = re.compile(r"===FRAGMENT===(.*?)===END FRAGMENT===", re.DOTALL)

FRAGMENT_SYSTEM_PREAMBLE = """You are a code-editing assistant. You will be given a goal, a change type, \
and (if applicable) the exact current content at one location in one file. Output ONLY the replacement \
content for that location, using exactly this format, with no other commentary before, between, or after \
the block:

===FRAGMENT===
<the replacement content>
===END FRAGMENT===

For change_type "delete", output an empty fragment (nothing between the markers) to confirm the deletion. \
Do not include the surrounding read-only context lines in your output — only the replacement for the \
location itself."""

OK_STATUSES = ("success", "dry_run")


def _read_file_lines(repo_root: Path, rel_path: str) -> tuple[list[str], bool] | None:
    path = repo_root / rel_path
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    crlf = "\r\n" in text
    lines = text.replace("\r\n", "\n").split("\n")
    return lines, crlf


def _read_context_file(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if not path.is_file():
        return "(file does not exist)"
    return path.read_text(encoding="utf-8")


def _describe_anchor(task: WorkTask, resolved: anchor_mod.ResolvedAnchor) -> str:
    anchor = task.anchor
    if anchor.type == "symbol":
        where = f"{anchor.symbol_type} '{anchor.name}'"
        if anchor.parent:
            where += f" inside '{anchor.parent}'"
    elif anchor.type == "marker":
        where = f'marker "{anchor.text}"'
        if anchor.text_end:
            where += f' .. "{anchor.text_end}"'
    else:
        where = anchor.type.replace("_", " ")

    if resolved.mode == "replace":
        return f"{where} (lines {resolved.start_line + 1}-{resolved.end_line + 1})"
    return f"{where} (insert at line {resolved.start_line + 1})"


def build_prompt(
    task: WorkTask,
    init: InitTask,
    repo_root: Path,
    lines: list[str] | None,
    resolved: anchor_mod.ResolvedAnchor | None,
    context_lines: int,
) -> str:
    parts = [FRAGMENT_SYSTEM_PREAMBLE, "", f"Target language: {init.language}"]
    parts.extend(f"- {c}" for c in init.conventions)

    parts.append(f"\n# Task {task.id}: {task.title}")
    parts.append(f"\nGoal: {task.goal}")
    parts.append(f"change_type: {task.change_type}")

    if task.acceptance_criteria:
        parts.append("\n## Acceptance criteria")
        parts.extend(f"- {c}" for c in task.acceptance_criteria)

    if task.context_files:
        parts.append("\n## Reference files (read-only, do not include these in your output)")
        for rel_path in task.context_files:
            parts.append(f"\n### {rel_path}\n{_read_context_file(repo_root, rel_path)}")

    if task.anchor is None:
        parts.append(f"\n## New file: {task.file}")
        parts.append("This file does not exist yet. Author its complete content as the fragment.")
        return "\n".join(parts)

    parts.append("\n## Location")
    parts.append(f"File: {task.file}")
    parts.append(f"Anchor: {_describe_anchor(task, resolved)}")

    before, after = patch.context_window(lines, resolved, context_lines)
    if before:
        parts.append("\n### Context before (read-only, do not include in your output)")
        parts.append("\n".join(before))

    if resolved.mode == "replace":
        current = patch.extract_fragment(lines, resolved)
        parts.append("\n### Current content at this location — replace this exactly")
        parts.append(current)

    if after:
        parts.append("\n### Context after (read-only, do not include in your output)")
        parts.append("\n".join(after))

    return "\n".join(parts)


def parse_fragment(text: str) -> str | None:
    matches = FRAGMENT_RE.findall(text)
    if len(matches) != 1:
        return None
    content = matches[0]
    if content.startswith("\n"):
        content = content[1:]
    if content.endswith("\n"):
        content = content[:-1]
    return content


def _save_raw_response(logs_dir: Path, run_ts: str, task_id: str, text: str) -> str:
    raw_dir = logs_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{run_ts}-{task_id}.txt"
    raw_path.write_text(text, encoding="utf-8")
    return str(raw_path)


def execute_work_task(
    task: WorkTask,
    init: InitTask,
    repo_root: Path,
    model: str,
    host: str,
    dry_run: bool,
    think: bool,
    context_lines: int,
    logs_dir: Path,
    run_ts: str,
) -> dict:
    entry = {"id": task.id, "title": task.title}
    start = time.monotonic()
    file_path = repo_root / task.file

    lines: list[str] | None = None
    crlf = False
    resolved: anchor_mod.ResolvedAnchor | None = None

    if task.anchor is None:
        if file_path.exists():
            entry.update(
                status="error",
                error=f"new_file task but {task.file} already exists",
                elapsed_seconds=time.monotonic() - start,
            )
            return entry
    else:
        read_result = _read_file_lines(repo_root, task.file)
        if read_result is None:
            entry.update(
                status="error",
                error=f"target file does not exist: {task.file}",
                elapsed_seconds=time.monotonic() - start,
            )
            return entry
        lines, crlf = read_result
        try:
            resolved = anchor_mod.resolve_anchor(task, lines, init.language)
        except anchor_mod.AnchorError as e:
            entry.update(status="anchor_error", error=str(e), elapsed_seconds=time.monotonic() - start)
            return entry

    prompt = build_prompt(task, init, repo_root, lines, resolved, context_lines)

    try:
        result = ollama_client.generate(prompt, model=model, host=host, think=think)
    except ollama_client.OllamaError as e:
        entry.update(status="error", error=str(e), elapsed_seconds=time.monotonic() - start)
        return entry

    entry["prompt_eval_count"] = result.prompt_eval_count
    entry["eval_count"] = result.eval_count

    def fail_parse(message: str) -> dict:
        entry.update(status="parse_error", error=message, elapsed_seconds=time.monotonic() - start)
        entry["raw_response_path"] = _save_raw_response(logs_dir, run_ts, task.id, result.text)
        return entry

    fragment = parse_fragment(result.text)
    if fragment is None:
        return fail_parse("model output did not contain exactly one ===FRAGMENT===...===END FRAGMENT=== block")

    is_empty = fragment.strip() == ""
    if task.change_type == "delete" and not is_empty:
        return fail_parse("change_type 'delete' requires an empty fragment, but model returned content")
    if task.change_type in ("add", "modify") and is_empty:
        return fail_parse(f"change_type '{task.change_type}' requires a non-empty fragment")

    if task.anchor is None:
        new_text = fragment
    else:
        bleed = patch.detect_trailing_bleed(lines, resolved, fragment)
        if bleed:
            return fail_parse(bleed)
        try:
            new_lines = patch.splice(lines, resolved, task.change_type, fragment)
        except patch.SpliceError as e:
            entry.update(status="splice_error", error=str(e), elapsed_seconds=time.monotonic() - start)
            entry["raw_response_path"] = _save_raw_response(logs_dir, run_ts, task.id, result.text)
            return entry
        new_text = "\n".join(new_lines)
        if crlf:
            new_text = new_text.replace("\n", "\r\n")

    if not dry_run:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(new_text, encoding="utf-8")

    entry.update(
        status="dry_run" if dry_run else "success",
        file_written=task.file,
        elapsed_seconds=time.monotonic() - start,
    )
    return entry


def run_batch(
    init: InitTask,
    tasks: list[WorkTask],
    repo_root: Path,
    model: str,
    host: str,
    dry_run: bool,
    logs_dir: Path,
    think: bool = False,
    context_lines: int = 3,
) -> dict:
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started_at = datetime.now(timezone.utc).isoformat()

    entries = [{
        "id": "init",
        "batch_id": init.batch_id,
        "repo_root": str(repo_root),
        "language": init.language,
        "task_count": len(tasks),
        "status": "init",
    }]

    stopped_early = False
    for i, task in enumerate(tasks):
        print(f"[{task.id}] {task.title} ...", end=" ", flush=True)
        entry = execute_work_task(task, init, repo_root, model, host, dry_run, think, context_lines, logs_dir, run_ts)
        print(entry["status"])
        entries.append(entry)

        if entry["status"] not in OK_STATUSES:
            stopped_early = True
            for skipped in tasks[i + 1:]:
                entries.append({"id": skipped.id, "title": skipped.title, "status": "skipped"})
            break

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{run_ts}.json"
    log_data = {
        "batch_id": init.batch_id,
        "started_at": started_at,
        "stopped_early": stopped_early,
        "entries": entries,
    }
    log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")

    succeeded = sum(1 for e in entries if e["status"] in OK_STATUSES)
    failed = sum(1 for e in entries if e["status"] not in OK_STATUSES and e["status"] != "init")
    print(f"\n{succeeded} succeeded, {failed} failed. Log written to {log_path}")

    return log_data
