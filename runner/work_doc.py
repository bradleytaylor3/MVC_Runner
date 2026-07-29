"""Load and validate a batch: one init doc plus a sequence of work docs, the
structured JSON tasks a cloud model writes and this runner executes against
a local model."""

import json
from dataclasses import dataclass, field
from pathlib import Path

CHANGE_TYPES = ("add", "modify", "delete")
ANCHOR_TYPES = ("symbol", "marker", "file_start", "file_end")
SYMBOL_TYPES = ("function", "class")
POSITIONS = ("before", "after")

REQUIRED_INIT_FIELDS = ("batch_id", "repo_root", "language")
REQUIRED_WORK_FIELDS = ("id", "title", "file", "goal", "change_type", "acceptance_criteria")


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
class Anchor:
    type: str
    symbol_type: str | None = None
    name: str | None = None
    parent: str | None = None
    text: str | None = None
    text_end: str | None = None
    occurrence: int | None = None
    position: str | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path, task_id: str) -> "Anchor":
        anchor_type = data.get("type")
        if anchor_type not in ANCHOR_TYPES:
            raise WorkDocError(
                f"{source_path}: task {task_id}: anchor.type must be one of {ANCHOR_TYPES}, got {anchor_type!r}"
            )
        if anchor_type == "symbol":
            if "name" not in data or "symbol_type" not in data:
                raise WorkDocError(
                    f"{source_path}: task {task_id}: symbol anchor requires 'name' and 'symbol_type'"
                )
            if data["symbol_type"] not in SYMBOL_TYPES:
                raise WorkDocError(
                    f"{source_path}: task {task_id}: anchor.symbol_type must be one of {SYMBOL_TYPES}"
                )
        if anchor_type == "marker" and "text" not in data:
            raise WorkDocError(f"{source_path}: task {task_id}: marker anchor requires 'text'")

        position = data.get("position")
        if position is not None and position not in POSITIONS:
            raise WorkDocError(f"{source_path}: task {task_id}: anchor.position must be one of {POSITIONS}")

        return cls(
            type=anchor_type,
            symbol_type=data.get("symbol_type"),
            name=data.get("name"),
            parent=data.get("parent"),
            text=data.get("text"),
            text_end=data.get("text_end"),
            occurrence=data.get("occurrence"),
            position=position,
        )


@dataclass
class WorkTask:
    id: str
    title: str
    file: str
    goal: str
    change_type: str
    acceptance_criteria: list[str]
    anchor: Anchor | None = None
    new_file: bool = False
    context_files: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "WorkTask":
        missing = [f for f in REQUIRED_WORK_FIELDS if f not in data]
        if missing:
            raise WorkDocError(f"{source_path}: missing required field(s): {', '.join(missing)}")

        task_id = data["id"]
        change_type = data["change_type"]
        if change_type not in CHANGE_TYPES:
            raise WorkDocError(
                f"{source_path}: task {task_id}: change_type must be one of {CHANGE_TYPES}, got {change_type!r}"
            )

        new_file = data.get("new_file", False)
        anchor_data = data.get("anchor")

        if change_type == "delete" and new_file:
            raise WorkDocError(f"{source_path}: task {task_id}: change_type 'delete' is incompatible with new_file: true")

        if anchor_data is None:
            if not (change_type == "add" and new_file):
                raise WorkDocError(
                    f"{source_path}: task {task_id}: 'anchor' is required unless change_type is 'add' "
                    "with new_file: true"
                )
            anchor = None
        else:
            anchor = Anchor.from_dict(anchor_data, source_path, task_id)
            if change_type == "add" and anchor.type in ("symbol", "marker") and not anchor.position:
                raise WorkDocError(
                    f"{source_path}: task {task_id}: change_type 'add' with a symbol/marker anchor requires "
                    "anchor.position ('before' or 'after')"
                )
            if anchor.type in ("file_start", "file_end") and change_type != "add":
                raise WorkDocError(
                    f"{source_path}: task {task_id}: anchor.type '{anchor.type}' is only valid with change_type 'add'"
                )

        return cls(
            id=task_id,
            title=data["title"],
            file=data["file"],
            goal=data["goal"],
            change_type=change_type,
            acceptance_criteria=data["acceptance_criteria"],
            anchor=anchor,
            new_file=new_file,
            context_files=data.get("context_files", []),
            source_path=source_path,
        )


def load_batch(work_dir: Path) -> tuple[InitTask, list[WorkTask]]:
    """Load and validate every *.json file in work_dir: exactly one init doc
    (kind == "init") plus any number of work docs (kind == "work"), sorted by
    task id."""
    paths = sorted(work_dir.glob("*.json"))
    if not paths:
        raise WorkDocError(f"No work document (*.json) files found in {work_dir}")

    init_task: InitTask | None = None
    work_tasks: list[WorkTask] = []

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WorkDocError(f"{path}: invalid JSON ({e})") from e

        kind = data.get("kind")
        if kind == "init":
            if init_task is not None:
                raise WorkDocError(
                    f"{path}: found a second init doc ({init_task.source_path}); a batch must have exactly one"
                )
            init_task = InitTask.from_dict(data, path)
        elif kind == "work":
            work_tasks.append(WorkTask.from_dict(data, path))
        else:
            raise WorkDocError(f"{path}: 'kind' must be 'init' or 'work', got {kind!r}")

    if init_task is None:
        raise WorkDocError(f'{work_dir}: no init doc (kind: "init") found; a batch requires exactly one')
    if not work_tasks:
        raise WorkDocError(f'{work_dir}: no work docs (kind: "work") found')

    return init_task, sorted(work_tasks, key=lambda t: t.id)
