"""Interactive wizard for building a work_docs/ batch one task at a time —
prompts for each field, validating immediately against the exact same rules
`runner.cli run` will enforce anyway (WorkTask.from_dict), so a mistake is
caught right there instead of surfacing later as a run failure.

Plain input()/print() throughout — works interactively at a terminal, and
can be driven by piped stdin (e.g. a heredoc) for scripted/AI use, though
that requires knowing the exact prompt order in advance since answers are
matched to prompts positionally, not by field name."""

import json
import re
from pathlib import Path

from runner.work_doc import CHANGE_TYPES, STRUCTURE_TYPES, LOCATION_FIELD_SPEC, InitTask, WorkDocError, WorkTask

_TASK_NUM_RE = re.compile(r"task-(\d+)")

STRUCTURE_TYPE_HELP = """Available structure_types:
  function   a new top-level function, or modify/delete an existing one by name
  class      a new class, or modify/delete an existing one by name
  method     a method inside a class (add/modify/delete)
  docstring  a one-line docstring inserted at the start of a function/class's body (add only)
  import     an import statement (add/modify/delete)
  constant   a module-level constant/variable (add/modify/delete)
  block      anything else — an arbitrary span of code (modify/delete), or arbitrary new code (add)
"""


def _prompt(label: str, required: bool = True) -> str:
    suffix = "" if required else " (optional, blank to skip)"
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value or not required:
            return value
        print("  required — please enter a value.")


def _prompt_choice(label: str, choices: tuple) -> str:
    options = "/".join(choices)
    while True:
        value = input(f"{label} [{options}]: ").strip()
        if value in choices:
            return value
        print(f"  must be one of: {options}")


def _prompt_yes_no(label: str, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def _prompt_list(label: str) -> list[str]:
    print(f"{label} (one per line, blank line to finish):")
    items = []
    while True:
        line = input("  - ").strip()
        if not line:
            break
        items.append(line)
    return items


def _prompt_multiline(label: str) -> str | None:
    print(f"{label} (end with a line containing only ':::'; leave it blank to skip):")
    lines = []
    while True:
        line = input()
        if line.strip() == ":::":
            break
        lines.append(line)
    text = "\n".join(lines)
    return text if text.strip() else None


def _find_init_path(work_dir: Path) -> Path | None:
    for path in sorted(work_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("kind") == "init":
            return path
    return None


def _load_or_create_init(work_dir: Path) -> InitTask:
    existing = _find_init_path(work_dir)
    if existing is not None:
        data = json.loads(existing.read_text(encoding="utf-8"))
        init = InitTask.from_dict(data, existing)
        print(f"Using existing init doc ({existing.name}): batch_id={init.batch_id!r}, "
              f"repo_root={init.repo_root!r}, language={init.language!r}")
        return init

    print("No init doc found in this directory — let's create one.")
    batch_id = _prompt("batch_id")
    repo_root = _prompt("repo_root")
    language = _prompt("language")
    conventions = _prompt_list("conventions")
    data = {
        "kind": "init",
        "batch_id": batch_id,
        "repo_root": repo_root,
        "language": language,
        "conventions": conventions,
    }
    path = work_dir / "000-init.json"
    init = InitTask.from_dict(data, path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return init


def _next_task_number(work_dir: Path) -> int:
    numbers = []
    for p in work_dir.glob("task-*.json"):
        m = _TASK_NUM_RE.match(p.stem)
        if m:
            numbers.append(int(m.group(1)))
    return max(numbers, default=0) + 1


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40].rstrip("-") or "task"


def _build_one_task(work_dir: Path, task_num: int) -> Path:
    task_id = f"task-{task_num:03d}"
    print(f"\n--- {task_id} ---")

    title = _prompt("title")
    file = _prompt("file (path relative to repo_root)")
    structure_type = _prompt_choice("structure_type", STRUCTURE_TYPES)
    change_type = _prompt_choice("change_type", CHANGE_TYPES)

    data = {
        "kind": "work",
        "id": task_id,
        "title": title,
        "file": file,
        "structure_type": structure_type,
        "change_type": change_type,
    }

    new_file = False
    if change_type == "add":
        new_file = _prompt_yes_no("Is this a brand-new file (skips all location fields)?", default=False)

    if new_file:
        data["new_file"] = True
    else:
        spec = LOCATION_FIELD_SPEC.get((structure_type, change_type))
        if spec is None:
            print(f"structure_type {structure_type!r} does not support change_type {change_type!r}. "
                  "Let's redo this task.")
            return _build_one_task(work_dir, task_num)
        required, optional = spec
        for field_name in required:
            data[field_name] = _prompt(field_name)
        for field_name in optional:
            value = _prompt(field_name, required=False)
            if value:
                data[field_name] = value
        if data.get("start_anchor"):
            occurrence = _prompt("occurrence (only if start_anchor matches multiple lines)", required=False)
            if occurrence:
                data["occurrence"] = int(occurrence)

    data["description"] = _prompt("description")

    exact_code = _prompt_multiline("exact_code")
    if exact_code is not None:
        data["exact_code"] = exact_code

    acceptance_criteria = _prompt_list("acceptance_criteria")
    while not acceptance_criteria:
        print("acceptance_criteria requires at least one entry.")
        acceptance_criteria = _prompt_list("acceptance_criteria")
    data["acceptance_criteria"] = acceptance_criteria

    context_files = _prompt_list("context_files")
    if context_files:
        data["context_files"] = context_files

    filename = f"{task_id}-{_slugify(title)}.json"
    path = work_dir / filename
    try:
        WorkTask.from_dict(data, path)
    except WorkDocError as e:
        print(f"Validation error: {e}\nLet's redo this task.")
        return _build_one_task(work_dir, task_num)

    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return path


def run_wizard(work_dir: Path, model: str = "qwen3:4b") -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    _load_or_create_init(work_dir)

    print(STRUCTURE_TYPE_HELP)

    written: list[Path] = []
    task_num = _next_task_number(work_dir)
    while True:
        written.append(_build_one_task(work_dir, task_num))
        task_num += 1
        if not _prompt_yes_no("Add another task?", default=True):
            break

    print(f"\n{len(written)} task(s) written to {work_dir}.")
    print(f"Run the batch with: python -m runner.cli run --work-dir {work_dir} --model {model}")
