"""Load and validate work documents: the structured JSON tasks a cloud model
writes and this runner executes against a local model."""

import json
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FIELDS = ("id", "title", "target_files", "instructions", "acceptance_criteria")


class WorkDocError(ValueError):
    pass


@dataclass
class WorkDoc:
    id: str
    title: str
    target_files: list[str]
    instructions: str
    acceptance_criteria: list[str]
    context_files: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "WorkDoc":
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise WorkDocError(f"{source_path}: missing required field(s): {', '.join(missing)}")
        if not isinstance(data["target_files"], list) or not data["target_files"]:
            raise WorkDocError(f"{source_path}: 'target_files' must be a non-empty list")

        return cls(
            id=data["id"],
            title=data["title"],
            target_files=data["target_files"],
            instructions=data["instructions"],
            acceptance_criteria=data["acceptance_criteria"],
            context_files=data.get("context_files", []),
            source_path=source_path,
        )


def load_work_docs(work_dir: Path) -> list[WorkDoc]:
    """Load and validate every *.json file in work_dir, sorted by task id."""
    paths = sorted(work_dir.glob("*.json"))
    if not paths:
        raise WorkDocError(f"No work document (*.json) files found in {work_dir}")

    docs = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise WorkDocError(f"{path}: invalid JSON ({e})") from e
        docs.append(WorkDoc.from_dict(data, path))

    return sorted(docs, key=lambda d: d.id)
