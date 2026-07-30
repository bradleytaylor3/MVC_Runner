"""Load and validate an ADB test batch: one init doc (shared device/app
defaults) plus any number of work docs (one UI test scenario each), the
structured JSON tasks a cloud model (or a human, or `test-adb`'s CLI
counterpart to `run`'s work docs) writes and `adb_agent` executes against a
local model driving a real device over ADB."""

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_STEPS = 25

REQUIRED_INIT_FIELDS = ("batch_id",)
REQUIRED_WORK_FIELDS = ("id", "title", "goal", "acceptance_criteria")


class AdbTaskError(ValueError):
    pass


@dataclass
class AdbInitTask:
    batch_id: str
    default_package: str | None = None
    default_serial: str | None = None
    default_max_steps: int = DEFAULT_MAX_STEPS
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "AdbInitTask":
        missing = [f for f in REQUIRED_INIT_FIELDS if f not in data]
        if missing:
            raise AdbTaskError(f"{source_path}: init doc missing required field(s): {', '.join(missing)}")

        return cls(
            batch_id=data["batch_id"],
            default_package=data.get("default_package"),
            default_serial=data.get("default_serial"),
            default_max_steps=data.get("default_max_steps", DEFAULT_MAX_STEPS),
            source_path=source_path,
        )


@dataclass
class AdbWorkTask:
    id: str
    title: str
    goal: str
    acceptance_criteria: list[str]
    package: str | None = None
    activity: str | None = None
    max_steps: int | None = None
    reset_app: bool = True
    source_path: Path | None = None

    @classmethod
    def from_dict(cls, data: dict, source_path: Path) -> "AdbWorkTask":
        missing = [f for f in REQUIRED_WORK_FIELDS if f not in data]
        if missing:
            raise AdbTaskError(f"{source_path}: missing required field(s): {', '.join(missing)}")
        if not isinstance(data["acceptance_criteria"], list) or not data["acceptance_criteria"]:
            raise AdbTaskError(f"{source_path}: 'acceptance_criteria' must be a non-empty list")

        return cls(
            id=data["id"],
            title=data["title"],
            goal=data["goal"],
            acceptance_criteria=data["acceptance_criteria"],
            package=data.get("package"),
            activity=data.get("activity"),
            max_steps=data.get("max_steps"),
            reset_app=data.get("reset_app", True),
            source_path=source_path,
        )

    def resolved_package(self, init: AdbInitTask) -> str | None:
        return self.package or init.default_package

    def resolved_max_steps(self, init: AdbInitTask) -> int:
        return self.max_steps or init.default_max_steps


def load_adb_batch(work_dir: Path) -> tuple[AdbInitTask, list[AdbWorkTask]]:
    """Load and validate every *.json file in work_dir: exactly one init doc
    (kind == "init") plus any number of work docs (kind == "work"), sorted by
    task id."""
    paths = sorted(work_dir.glob("*.json"))
    if not paths:
        raise AdbTaskError(f"No work document (*.json) files found in {work_dir}")

    init_task: AdbInitTask | None = None
    work_tasks: list[AdbWorkTask] = []

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise AdbTaskError(f"{path}: invalid JSON ({e})") from e

        kind = data.get("kind")
        if kind == "init":
            if init_task is not None:
                raise AdbTaskError(
                    f"{path}: found a second init doc ({init_task.source_path}); a batch must have exactly one"
                )
            init_task = AdbInitTask.from_dict(data, path)
        elif kind == "work":
            work_tasks.append(AdbWorkTask.from_dict(data, path))
        else:
            raise AdbTaskError(f"{path}: 'kind' must be 'init' or 'work', got {kind!r}")

    if init_task is None:
        raise AdbTaskError(f'{work_dir}: no init doc (kind: "init") found; a batch requires exactly one')
    if not work_tasks:
        raise AdbTaskError(f'{work_dir}: no work docs (kind: "work") found')

    for task in work_tasks:
        if task.resolved_package(init_task) is None:
            raise AdbTaskError(
                f"{task.source_path}: task {task.id} has no 'package' and init doc has no 'default_package'"
            )

    return init_task, sorted(work_tasks, key=lambda t: t.id)
