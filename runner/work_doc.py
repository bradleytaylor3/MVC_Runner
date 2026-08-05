"""Load and validate a batch: one init doc plus a sequence of work docs, the
structured JSON tasks a cloud model (or a human, or an AI assistant
following AUTHORING_PROMPT.md) writes and this runner executes against a
local model.

Work docs are `structure_type`-driven: `structure_type` names the kind of
thing being added/changed (function, class, method, docstring, import,
constant, or the generic `block` fallback), and together with `change_type`
determines which location field(s) are required and how anchor.py resolves
them. See schema.md for the full field-requirement table."""

import json
from dataclasses import dataclass, field
from pathlib import Path

CHANGE_TYPES = ("add", "modify", "delete")
STRUCTURE_TYPES = ("function", "class", "method", "docstring", "import", "constant", "block")
SYMBOL_STRUCTURE_TYPES = ("function", "class")  # resolvable by name alone, whole-span (anchor.py's symbol resolution)

REQUIRED_INIT_FIELDS = ("batch_id", "repo_root", "language")
REQUIRED_WORK_FIELDS = ("id", "title", "file", "structure_type", "change_type", "description")

# (structure_type, change_type) -> (required location fields, optional location fields).
# Single source of truth for both _validate_location below and work_builder.py's
# wizard (which prompts exactly these fields) — a combination with no entry here
# isn't supported (e.g. docstring only supports "add").
LOCATION_FIELD_SPEC: dict[tuple[str, str], tuple[list[str], list[str]]] = {
    ("function", "add"): (["start_anchor"], []),
    ("function", "modify"): (["name"], []),
    ("function", "delete"): (["name"], []),
    ("class", "add"): (["start_anchor"], []),
    ("class", "modify"): (["name"], []),
    ("class", "delete"): (["name"], []),
    ("method", "add"): (["parent"], ["start_anchor"]),
    ("method", "modify"): (["name", "parent"], []),
    ("method", "delete"): (["name", "parent"], []),
    ("docstring", "add"): (["target_name"], []),
    ("import", "add"): (["start_anchor"], []),
    ("import", "modify"): (["start_anchor"], []),
    ("import", "delete"): (["start_anchor"], ["end_anchor"]),
    ("constant", "add"): (["start_anchor"], []),
    ("constant", "modify"): (["start_anchor"], ["end_anchor"]),
    ("constant", "delete"): (["start_anchor"], ["end_anchor"]),
    ("block", "add"): (["start_anchor"], []),
    ("block", "modify"): (["start_anchor"], ["end_anchor"]),
    ("block", "delete"): (["start_anchor"], ["end_anchor"]),
}


class WorkDocError(ValueError):
    pass


@dataclass
class InitTask:
    batch_id: str
    repo_root: str
    language: str
    conventions: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "InitTask":
        missing = [f for f in REQUIRED_INIT_FIELDS if f not in data]
        if missing:
            raise WorkDocError(f"{source_path}: init doc missing required field(s): {', '.join(missing)}")

        return cls(
            batch_id=data["batch_id"],
            repo_root=data["repo_root"],
            language=data["language"],
            conventions=data.get("conventions", []),
            source_path=source_path,
        )


@dataclass
class WorkTask:
    id: str
    title: str
    file: str
    structure_type: str
    change_type: str
    description: str
    acceptance_criteria: list[str]
    name: str | None = None
    parent: str | None = None
    target_name: str | None = None
    start_anchor: str | None = None
    end_anchor: str | None = None
    occurrence: int | None = None
    exact_code: str | None = None
    pattern_example: str | None = None
    contract: dict | None = None
    new_file: bool = False
    context_files: list[str] = field(default_factory=list)
    indent: int | None = None
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "WorkTask":
        missing = [f for f in REQUIRED_WORK_FIELDS if f not in data]
        if missing:
            raise WorkDocError(f"{source_path}: missing required field(s): {', '.join(missing)}")

        task_id = data["id"]

        def err(message: str) -> None:
            raise WorkDocError(f"{source_path}: task {task_id}: {message}")

        structure_type = data["structure_type"]
        if structure_type not in STRUCTURE_TYPES:
            err(f"structure_type must be one of {STRUCTURE_TYPES}, got {structure_type!r}")

        change_type = data["change_type"]
        if change_type not in CHANGE_TYPES:
            err(f"change_type must be one of {CHANGE_TYPES}, got {change_type!r}")

        new_file = data.get("new_file", False)
        name = data.get("name")
        parent = data.get("parent")
        target_name = data.get("target_name")
        start_anchor = data.get("start_anchor")
        end_anchor = data.get("end_anchor")
        occurrence = data.get("occurrence")
        exact_code = data.get("exact_code")
        pattern_example = data.get("pattern_example")
        contract = data.get("contract")
        indent = data.get("indent")

        acceptance_criteria = data.get("acceptance_criteria", [])
        if not isinstance(acceptance_criteria, list):
            err("'acceptance_criteria' must be a list")
        if exact_code is None and not acceptance_criteria:
            err("'acceptance_criteria' must be a non-empty list unless 'exact_code' is set — exact_code "
                "tasks never call the model, so criteria are optional reviewer notes there, not a machine gate")

        if change_type == "delete" and new_file:
            err("change_type 'delete' is incompatible with new_file: true")
        if exact_code is not None and change_type == "delete":
            err("'exact_code' is not valid with change_type 'delete'")
        if exact_code is not None and pattern_example is not None:
            err("'pattern_example' is pointless with 'exact_code' set — the model is never called, so it "
                "never sees the example")
        if exact_code is not None and contract is not None:
            err("'contract' is pointless with 'exact_code' set — the model is never called, so there's "
                "nothing to check its output against")
        if contract is not None and not isinstance(contract, dict):
            err("'contract' must be an object")
        if indent is not None and (not isinstance(indent, int) or isinstance(indent, bool) or indent < 0):
            err("'indent' must be a non-negative integer")

        if new_file:
            if change_type != "add":
                err("new_file requires change_type 'add'")
        else:
            _validate_location(err, structure_type, change_type, name, parent, target_name, start_anchor)

        return cls(
            id=task_id,
            title=data["title"],
            file=data["file"],
            structure_type=structure_type,
            change_type=change_type,
            description=data["description"],
            acceptance_criteria=acceptance_criteria,
            name=name,
            parent=parent,
            target_name=target_name,
            start_anchor=start_anchor,
            end_anchor=end_anchor,
            occurrence=occurrence,
            exact_code=exact_code,
            pattern_example=pattern_example,
            contract=contract,
            new_file=new_file,
            context_files=data.get("context_files", []),
            indent=indent,
            source_path=source_path,
        )


def _validate_location(err, structure_type, change_type, name, parent, target_name, start_anchor) -> None:
    spec = LOCATION_FIELD_SPEC.get((structure_type, change_type))
    if spec is None:
        err(f"structure_type {structure_type!r} does not support change_type {change_type!r}")
        return
    required, _optional = spec
    values = {"name": name, "parent": parent, "target_name": target_name, "start_anchor": start_anchor}
    missing = [f for f in required if not values.get(f)]
    if missing:
        err(f"structure_type {structure_type!r} with change_type {change_type!r} requires: {', '.join(missing)}")


def load_batch(work_dir: Path) -> tuple[InitTask, list[WorkTask]]:
    """Load and validate every *.json file in work_dir. Two doc shapes can
    appear, freely mixed: the original per-task style (kind == "init" plus
    any number of kind == "work" files), and the consolidated style (a
    single file with kind == "batch", holding an inline "init" object plus
    every task inline in a "tasks" list) -- the latter exists so authoring a
    batch by hand or via a chat model costs one file/tool-call instead of
    N+1, without changing what a WorkTask ends up being. Returns every
    collected task sorted by id."""
    paths = sorted(work_dir.glob("*.json"))
    if not paths:
        raise WorkDocError(f"No work document (*.json) files found in {work_dir}")

    init_task: InitTask | None = None
    work_tasks: list[WorkTask] = []

    def set_init(init_data, path: Path) -> None:
        nonlocal init_task
        if not isinstance(init_data, dict):
            raise WorkDocError(f"{path}: 'init' must be an object")
        if init_task is not None:
            raise WorkDocError(
                f"{path}: found a second init doc ({init_task.source_path}); a batch must have exactly one"
            )
        init_task = InitTask.from_dict(init_data, path)

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WorkDocError(f"{path}: invalid JSON ({e})") from e

        kind = data.get("kind")
        if kind == "init":
            set_init(data, path)
        elif kind == "work":
            work_tasks.append(WorkTask.from_dict(data, path))
        elif kind == "batch":
            set_init(data.get("init"), path)
            tasks_data = data.get("tasks")
            if not isinstance(tasks_data, list) or not tasks_data:
                raise WorkDocError(f"{path}: 'batch' doc's 'tasks' must be a non-empty list")
            for i, task_data in enumerate(tasks_data):
                if not isinstance(task_data, dict):
                    raise WorkDocError(f"{path}: tasks[{i}] must be an object")
                task_data = dict(task_data)
                task_data.setdefault("kind", "work")
                work_tasks.append(WorkTask.from_dict(task_data, path))
        else:
            raise WorkDocError(f"{path}: 'kind' must be 'init', 'work', or 'batch', got {kind!r}")

    if init_task is None:
        raise WorkDocError(f'{work_dir}: no init doc (kind: "init" or "batch") found; a batch requires exactly one')
    if not work_tasks:
        raise WorkDocError(f'{work_dir}: no work docs (kind: "work", or inside a "batch" doc\'s "tasks") found')

    return init_task, sorted(work_tasks, key=lambda t: t.id)
