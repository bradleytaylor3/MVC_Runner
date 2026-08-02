"""Core loop: for each work task in a batch, resolve its location against the
real file, then either splice a task's exact_code in deterministically (no
model call — the content is already fully decided, so only its indentation
needs adjusting, which is computed from the file, not guessed) or, for tasks
without exact_code, build a structure_type-specific fragment-only prompt,
call the local model, and parse/validate its fragment before splicing it in.
Writes the result unless dry-run. Stops the batch on the first task that
doesn't succeed."""

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

# Passed as `format` to ollama_client.generate() so Ollama constrains decoding
# to this shape — reliable JSON output (including correct escaping of a
# multi-line code fragment) even on small models that won't reliably follow
# free-form delimiter instructions on their own.
FRAGMENT_SCHEMA = {
    "type": "object",
    "properties": {"fragment": {"type": "string"}},
    "required": ["fragment"],
}

FRAGMENT_SYSTEM_PREAMBLE = """You are a code-editing assistant. You will be given an instruction, a \
change type, and (if applicable) the exact current content at one location in one file. Respond with a \
single JSON object of the form {"fragment": "<the replacement content>"} — the complete replacement \
content for that location, as a JSON string (encode newlines as \\n).

For change_type "delete", set "fragment" to an empty string to confirm the deletion. Do not include the \
surrounding read-only context lines in the fragment — only the replacement for the location itself."""

# One-line, structure_type-specific reminder appended to the preamble — this
# is what replaced the old single generic "goal describes intent" framing:
# each kind of edit gets instructions tailored to the mistake it's actually
# prone to (echoing a signature, reproducing a whole function to insert one
# line, etc.), instead of one paragraph trying to cover every case.
STRUCTURE_TYPE_FRAMING = {
    "function": "Write a complete function definition, including its full signature and body — "
                "never just restate a signature or name as the fragment.",
    "class": "Write a complete class definition, including its signature/bases and full body — "
             "never just restate a name or signature.",
    "method": "Write a complete method definition, including its full signature and body, indented to "
              "match the surrounding class body — never just restate a signature or name.",
    "docstring": "Output only the docstring itself (a single string/comment literal appropriate to the "
                 "language) as the fragment — nothing else: no signature, no surrounding code.",
    "import": "Keep the fragment to just the import statement(s) needed.",
    "constant": "Keep the fragment to just the constant/variable declaration(s) needed.",
    "block": "Write the code needed to satisfy the instruction.",
}

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


def _instruction_sentence(task: WorkTask) -> str:
    d = task.description

    if task.structure_type == "docstring":
        return f"Add a one-line docstring as the first statement of {task.target_name}'s body: {d}"

    if task.structure_type == "method":
        if task.change_type == "add":
            if task.start_anchor:
                return f"Inside class {task.parent!r}, starting after {task.start_anchor!r}, add a method that {d}"
            return f"Add a method to the end of class {task.parent!r}'s body that {d}"
        verb = "Modify" if task.change_type == "modify" else "Delete"
        tail = f" so that {d}" if task.change_type == "modify" else f" ({d})"
        return f"{verb} the method named {task.name!r} inside class {task.parent!r}{tail}"

    if task.structure_type in ("function", "class"):
        if task.change_type == "add":
            return f"Starting after {task.start_anchor!r}, add a {task.structure_type} that {d}"
        verb = "Modify" if task.change_type == "modify" else "Delete"
        tail = f" so that {d}" if task.change_type == "modify" else f" ({d})"
        return f"{verb} the {task.structure_type} named {task.name!r}{tail}"

    # import, constant, block
    where = f"{task.start_anchor!r} through {task.end_anchor!r}" if task.end_anchor else f"{task.start_anchor!r}"
    if task.change_type == "add":
        noun = "an import" if task.structure_type == "import" else f"a {task.structure_type}"
        return f"Starting after {where}, add {noun} that {d}"
    verb = "Modify" if task.change_type == "modify" else "Delete"
    tail = f" so that {d}" if task.change_type == "modify" else f" ({d})"
    return f"{verb} the code at {where}{tail}"


def _reference_indent(task: WorkTask, lines: list[str], resolved: anchor_mod.ResolvedAnchor) -> int | None:
    """The exact indentation (in spaces) the fragment should use, computed
    from real adjacent content rather than left for the model to eyeball —
    docstring/body_start's sibling is the existing line it pushes down
    (start_line); every other insert's sibling is the line right before it
    (the anchor/marker line itself, or the last existing body statement for
    a body_end append). For replace-mode, the sibling is the span's own
    current first line — whatever replaces it keeps the same nesting level.
    Returns None when there's no non-blank line to measure (e.g. inserting
    into a currently-empty body)."""
    if resolved.mode == "replace":
        ref_idx = resolved.start_line
    else:
        ref_idx = resolved.start_line if task.structure_type == "docstring" else resolved.start_line - 1
    if 0 <= ref_idx < len(lines) and lines[ref_idx].strip():
        line = lines[ref_idx]
        base_indent = len(line) - len(line.lstrip())
        # If the reference line itself opens a block (e.g. a method/add
        # whose parent's body turned out to be empty, so "the line right
        # before" is the parent's own declaration line), the new content is
        # that block's first member, one level deeper -- not a sibling at
        # the opener's own indent. Found live: appending the first method
        # into an empty Kotlin class body landed at 0 indent (the class
        # declaration's own indent) instead of 4.
        if resolved.mode == "insert" and line.rstrip().endswith("{"):
            return base_indent + 4
        return base_indent
    return None


def _describe_resolved_location(task: WorkTask, resolved: anchor_mod.ResolvedAnchor) -> str:
    if task.structure_type == "docstring":
        where = f"start of {task.target_name}'s body"
    elif task.structure_type == "method":
        where = f"class {task.parent!r}" + (f", method {task.name!r}" if task.name else "")
    elif task.structure_type in ("function", "class"):
        where = f"{task.structure_type} {task.name!r}" if task.name else f"after {task.start_anchor!r}"
    else:
        where = f"{task.start_anchor!r}" + (f" .. {task.end_anchor!r}" if task.end_anchor else "")

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
    retry_feedback: str | None = None,
) -> str:
    """Only ever called for tasks without exact_code — those are spliced
    deterministically in _attempt_work_task without invoking the model at
    all (see runner.patch.reindent_to)."""
    preamble = FRAGMENT_SYSTEM_PREAMBLE + "\n\n" + STRUCTURE_TYPE_FRAMING[task.structure_type]

    parts = [preamble, "", f"Target language: {init.language}"]
    parts.extend(f"- {c}" for c in init.conventions)

    parts.append(f"\n# Task {task.id}: {task.title}")
    parts.append(f"\nInstruction: {_instruction_sentence(task)}")
    parts.append(f"change_type: {task.change_type}")

    if retry_feedback:
        parts.append("\n## Your previous attempt was rejected — fix this and try again")
        parts.append(retry_feedback)

    if task.pattern_example:
        parts.append("\n## Follow this real example of the same pattern elsewhere in the codebase")
        parts.append("(reference only — do not include it in your output; it shows the shape/style to match, "
                      "not what to write)")
        parts.append(task.pattern_example)

    if task.acceptance_criteria:
        parts.append("\n## Acceptance criteria")
        parts.extend(f"- {c}" for c in task.acceptance_criteria)

    if task.context_files:
        parts.append("\n## Reference files (read-only, do not include these in your output)")
        for rel_path in task.context_files:
            parts.append(f"\n### {rel_path}\n{_read_context_file(repo_root, rel_path)}")

    if task.new_file:
        parts.append(f"\n## New file: {task.file}")
        parts.append("This file does not exist yet. Author its complete content as the fragment.")
        return "\n".join(parts)

    parts.append("\n## Location")
    parts.append(f"File: {task.file}")
    parts.append(f"Location: {_describe_resolved_location(task, resolved)}")

    ref_indent = _reference_indent(task, lines, resolved)
    if ref_indent is not None:
        parts.append(f"Required indentation: the fragment's first line must start with exactly {ref_indent} "
                      f"leading spaces (matching the surrounding code — count them, don't estimate); indent any "
                      f"further lines relative to that base level as normal for the language.")

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


def _unescape_literal_newlines(fragment: str) -> str:
    """A small model told to 'encode newlines as \\n' sometimes over-escapes
    it: the JSON value it emits contains the two literal characters
    backslash+n rather than an actual line break, so correct JSON decoding
    leaves that literal text sitting in the fragment instead of a newline.
    Found live: this was inflating _validate_fragment_shape's "bare
    signature" rate — a real multi-statement fragment with zero real
    newlines but a literal '\\n' in it is this failure mode, not a model
    that actually wrote one line (real source code never contains that
    exact two-character text), so unescape it rather than reject it."""
    if "\n" not in fragment and "\\n" in fragment:
        return fragment.replace("\\n", "\n")
    return fragment


def parse_fragment(text: str) -> str | None:
    # With `format=FRAGMENT_SCHEMA` passed to generate(), `text` should already
    # be exactly one conforming JSON object. Fall back to the older
    # ===FRAGMENT===...===END FRAGMENT=== delimiter style (handles a host/model
    # that doesn't honor `format`) rather than assuming that.
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("fragment"), str):
        return _unescape_literal_newlines(data["fragment"])

    matches = FRAGMENT_RE.findall(text)
    if len(matches) != 1:
        return None
    content = matches[0]
    if content.startswith("\n"):
        content = content[1:]
    if content.endswith("\n"):
        content = content[:-1]
    return content


def _validate_fragment_shape(task: WorkTask, fragment: str) -> str | None:
    """Lightweight, high-confidence structural sanity checks on a model
    fragment before it's spliced in — cheap ways to catch exactly the
    failure modes STRUCTURE_TYPE_FRAMING warns against (echoing a signature
    or name instead of writing real content) without needing a real parser.
    Deliberately conservative: only flags patterns that are essentially
    never a legitimate answer, to keep false positives (and needless
    retries) rare."""
    non_blank = [line for line in fragment.split("\n") if line.strip()]
    if not non_blank:
        return None

    if task.structure_type in ("function", "class", "method") and len(non_blank) == 1:
        return (f"fragment is a single line ({non_blank[0].strip()!r}) — looks like a bare signature/name "
                "with no body; write the complete definition, including its body")

    if task.structure_type == "docstring" and len(non_blank) > 1:
        return f"docstring fragment has {len(non_blank)} non-blank lines — should be exactly one docstring line"

    stripped = fragment.strip()
    for label, value in (("name", task.name), ("start_anchor", task.start_anchor), ("target_name", task.target_name)):
        if value and stripped == value.strip():
            return f"fragment is just the {label} restated verbatim ({value!r}), not real content"

    return None


def _save_raw_response(logs_dir: Path, run_ts: str, task_id: str, text: str) -> str:
    raw_dir = logs_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{run_ts}-{task_id}.txt"
    raw_path.write_text(text, encoding="utf-8")
    return str(raw_path)


# Statuses caused by the model's output quality rather than a structural/config
# problem — worth a fresh sample from the model, fed back the reason the
# previous attempt was rejected (see retry_feedback in build_prompt). Only
# reachable for tasks without exact_code: those never call the model at all
# (see _attempt_work_task), so they can only fail on anchor_error/
# splice_error, both deterministic given the task/repo state — retrying
# without changing anything would just reproduce the same failure.
RETRYABLE_STATUSES = ("parse_error", "validation_error")


def _attempt_work_task(
    task: WorkTask,
    init: InitTask,
    repo_root: Path,
    file_path: Path,
    lines: list[str] | None,
    crlf: bool,
    resolved: anchor_mod.ResolvedAnchor | None,
    model: str,
    host: str,
    dry_run: bool,
    think: bool,
    context_lines: int,
    logs_dir: Path,
    run_ts: str,
    start: float,
    retry_feedback: str | None = None,
) -> dict:
    entry = {"id": task.id, "title": task.title}

    # exact_code means the final content is already fully decided — the only
    # unknown left is how deep the surrounding file nests at the splice
    # point, which is measured directly from the file, not guessed. There is
    # nothing left for a model to contribute (its job would be reindent-only,
    # and even that risks silently altering "already-decided" content), so
    # skip inference entirely and splice deterministically.
    if task.exact_code is not None:
        if task.new_file:
            new_text = task.exact_code
        else:
            target_indent = _reference_indent(task, lines, resolved) or 0
            fragment = patch.reindent_to(task.exact_code, target_indent)
            try:
                new_lines = patch.splice(lines, resolved, task.change_type, fragment)
            except patch.SpliceError as e:
                entry.update(status="splice_error", error=str(e), elapsed_seconds=time.monotonic() - start)
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
            model_called=False,
            fragment_chars=len(task.exact_code),
            elapsed_seconds=time.monotonic() - start,
        )
        return entry

    prompt = build_prompt(task, init, repo_root, lines, resolved, context_lines, retry_feedback=retry_feedback)

    try:
        result = ollama_client.generate(prompt, model=model, host=host, think=think, format=FRAGMENT_SCHEMA)
    except ollama_client.OllamaError as e:
        entry.update(status="error", error=str(e), elapsed_seconds=time.monotonic() - start)
        return entry

    entry["prompt_eval_count"] = result.prompt_eval_count
    entry["eval_count"] = result.eval_count

    def fail(status: str, message: str) -> dict:
        entry.update(status=status, error=message, elapsed_seconds=time.monotonic() - start)
        entry["raw_response_path"] = _save_raw_response(logs_dir, run_ts, task.id, result.text)
        return entry

    fragment = parse_fragment(result.text)
    if fragment is None:
        return fail("parse_error", "model output did not contain exactly one ===FRAGMENT===...===END FRAGMENT=== block")

    is_empty = fragment.strip() == ""
    if task.change_type == "delete" and not is_empty:
        return fail("parse_error", "change_type 'delete' requires an empty fragment, but model returned content")
    if task.change_type in ("add", "modify") and is_empty:
        return fail("parse_error", f"change_type '{task.change_type}' requires a non-empty fragment")

    if not is_empty:
        shape_issue = _validate_fragment_shape(task, fragment)
        if shape_issue:
            return fail("validation_error", shape_issue)

    if task.new_file:
        new_text = fragment
    else:
        bleed = patch.detect_trailing_bleed(lines, resolved, fragment) or patch.detect_leading_bleed(lines, resolved, fragment)
        if bleed:
            return fail("validation_error", bleed)
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
        model_called=True,
        fragment_chars=len(fragment),
        elapsed_seconds=time.monotonic() - start,
    )
    return entry


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
    max_retries: int = 0,
) -> dict:
    start = time.monotonic()
    file_path = repo_root / task.file

    lines: list[str] | None = None
    crlf = False
    resolved: anchor_mod.ResolvedAnchor | None = None

    if task.new_file:
        if file_path.exists():
            return {
                "id": task.id, "title": task.title,
                "status": "error",
                "error": f"new_file task but {task.file} already exists",
                "elapsed_seconds": time.monotonic() - start,
            }
    else:
        read_result = _read_file_lines(repo_root, task.file)
        if read_result is None:
            return {
                "id": task.id, "title": task.title,
                "status": "error",
                "error": f"target file does not exist: {task.file}",
                "elapsed_seconds": time.monotonic() - start,
            }
        lines, crlf = read_result
        try:
            resolved = anchor_mod.resolve_anchor(task, lines, init.language)
        except anchor_mod.AnchorError as e:
            return {
                "id": task.id, "title": task.title,
                "status": "anchor_error", "error": str(e),
                "elapsed_seconds": time.monotonic() - start,
            }

    entry = None
    retry_feedback: str | None = None
    for attempt in range(max_retries + 1):
        entry = _attempt_work_task(
            task, init, repo_root, file_path, lines, crlf, resolved,
            model, host, dry_run, think, context_lines, logs_dir, run_ts, start,
            retry_feedback=retry_feedback,
        )
        entry["attempts"] = attempt + 1
        if entry["status"] not in RETRYABLE_STATUSES or attempt == max_retries:
            break
        retry_feedback = entry.get("error")
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
    max_retries: int = 0,
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
        entry = execute_work_task(
            task, init, repo_root, model, host, dry_run, think, context_lines, logs_dir, run_ts,
            max_retries=max_retries,
        )
        attempts = entry.get("attempts", 1)
        suffix = f" (attempt {attempts})" if attempts > 1 else ""
        print(f"{entry['status']}{suffix}")
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
