"""Core loop: for each work document, build a prompt, call the local model,
parse its output, and (unless dry-run) overwrite the target files."""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from runner import ollama_client
from runner.work_doc import WorkDoc, load_work_docs

FILE_BLOCK_RE = re.compile(r"===FILE:\s*(.+?)\s*===\n(.*?)\n===END FILE===", re.DOTALL)

SYSTEM_PREAMBLE = """You are a code-editing assistant. You will be given a task \
and the current contents of one or more files. Rewrite each target file in full \
to satisfy the task, then output ONLY the complete new content of every target \
file using exactly this format, with no other commentary before, between, or \
after the blocks:

===FILE: <relative/path/to/file>===
<the complete new file content>
===END FILE===

Include one such block for every target file, even files you leave unchanged."""


def _read_file(repo_root: Path, rel_path: str) -> str:
    path = repo_root / rel_path
    if not path.is_file():
        return "(file does not exist yet)"
    return path.read_text(encoding="utf-8")


def build_prompt(doc: WorkDoc, repo_root: Path) -> str:
    parts = [SYSTEM_PREAMBLE, "", f"# Task {doc.id}: {doc.title}", "", doc.instructions]

    if doc.acceptance_criteria:
        parts.append("\n## Acceptance criteria")
        parts.extend(f"- {c}" for c in doc.acceptance_criteria)

    if doc.context_files:
        parts.append("\n## Reference files (do not include these in your output)")
        for rel_path in doc.context_files:
            parts.append(f"\n### {rel_path}\n{_read_file(repo_root, rel_path)}")

    parts.append("\n## Target files (rewrite and output each of these in full)")
    for rel_path in doc.target_files:
        parts.append(f"\n### {rel_path}\n{_read_file(repo_root, rel_path)}")

    return "\n".join(parts)


def parse_response(text: str) -> dict[str, str]:
    return {path: content for path, content in FILE_BLOCK_RE.findall(text)}


def execute_work_doc(doc: WorkDoc, repo_root: Path, model: str, host: str, dry_run: bool, think: bool = False) -> dict:
    entry = {"id": doc.id, "title": doc.title}
    start = time.monotonic()

    try:
        prompt = build_prompt(doc, repo_root)
        result = ollama_client.generate(prompt, model=model, host=host, think=think)
    except ollama_client.OllamaError as e:
        entry.update(status="error", error=str(e), elapsed_seconds=time.monotonic() - start)
        return entry

    entry["prompt_eval_count"] = result.prompt_eval_count
    entry["eval_count"] = result.eval_count

    files = parse_response(result.text)
    missing = [f for f in doc.target_files if f not in files]
    if missing:
        entry.update(
            status="parse_error",
            error=f"model output missing block(s) for: {', '.join(missing)}",
            elapsed_seconds=time.monotonic() - start,
        )
        return entry

    if not dry_run:
        for rel_path in doc.target_files:
            path = repo_root / rel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(files[rel_path], encoding="utf-8")

    entry.update(
        status="dry_run" if dry_run else "success",
        files_written=doc.target_files,
        elapsed_seconds=time.monotonic() - start,
    )
    return entry


def run(work_dir: Path, repo_root: Path, model: str, host: str, dry_run: bool, logs_dir: Path, think: bool = False) -> list[dict]:
    docs = load_work_docs(work_dir)

    log_entries = []
    for doc in docs:
        print(f"[{doc.id}] {doc.title} ...", end=" ", flush=True)
        entry = execute_work_doc(doc, repo_root, model, host, dry_run, think=think)
        print(entry["status"])
        log_entries.append(entry)

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps(log_entries, indent=2), encoding="utf-8")

    succeeded = sum(1 for e in log_entries if e["status"] in ("success", "dry_run"))
    failed = len(log_entries) - succeeded
    print(f"\n{succeeded} succeeded, {failed} failed. Log written to {log_path}")

    return log_entries
