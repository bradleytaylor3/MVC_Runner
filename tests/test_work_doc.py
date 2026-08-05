"""Covers WorkTask.from_dict's location-field validation, now driven by
LOCATION_FIELD_SPEC (work_doc.py) rather than an if/elif chain — this is
existing, migration-tested behavior being restructured, not new behavior,
so it's worth locking down directly since it previously had no dedicated
test coverage."""

import json
from pathlib import Path

import pytest

from runner.work_doc import WorkDocError, WorkTask, load_batch

PATH = Path("t.json")


def _data(**overrides):
    base = dict(
        kind="work", id="t", title="t", file="f.py",
        description="d", acceptance_criteria=["x"],
    )
    base.update(overrides)
    return base


def test_function_add_requires_start_anchor():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="function", change_type="add"), PATH)
    task = WorkTask.from_dict(
        _data(structure_type="function", change_type="add", start_anchor="# marker"), PATH
    )
    assert task.start_anchor == "# marker"


def test_function_modify_requires_name():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="function", change_type="modify"), PATH)
    task = WorkTask.from_dict(_data(structure_type="function", change_type="modify", name="foo"), PATH)
    assert task.name == "foo"


def test_class_delete_requires_name():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="class", change_type="delete"), PATH)
    task = WorkTask.from_dict(_data(structure_type="class", change_type="delete", name="Foo"), PATH)
    assert task.name == "Foo"


def test_method_add_requires_parent_but_not_start_anchor():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="method", change_type="add"), PATH)
    task = WorkTask.from_dict(_data(structure_type="method", change_type="add", parent="Foo"), PATH)
    assert task.parent == "Foo" and task.start_anchor is None


def test_method_modify_requires_name_and_parent():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="method", change_type="modify", name="bar"), PATH)
    task = WorkTask.from_dict(
        _data(structure_type="method", change_type="modify", name="bar", parent="Foo"), PATH
    )
    assert task.name == "bar" and task.parent == "Foo"


def test_docstring_add_requires_target_name():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(_data(structure_type="docstring", change_type="add"), PATH)
    task = WorkTask.from_dict(_data(structure_type="docstring", change_type="add", target_name="foo"), PATH)
    assert task.target_name == "foo"


def test_docstring_only_supports_add():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(
            _data(structure_type="docstring", change_type="modify", target_name="foo"), PATH
        )
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(
            _data(structure_type="docstring", change_type="delete", target_name="foo"), PATH
        )


def test_import_and_constant_and_block_require_start_anchor():
    for structure_type in ("import", "constant", "block"):
        for change_type in ("modify", "delete") if structure_type != "import" else ("add", "modify"):
            with pytest.raises(WorkDocError):
                WorkTask.from_dict(_data(structure_type=structure_type, change_type=change_type), PATH)
            task = WorkTask.from_dict(
                _data(structure_type=structure_type, change_type=change_type, start_anchor="x = 1"), PATH
            )
            assert task.start_anchor == "x = 1"


def test_delete_incompatible_with_new_file():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(
            _data(structure_type="function", change_type="delete", name="foo", new_file=True), PATH
        )


def test_new_file_skips_location_validation():
    task = WorkTask.from_dict(
        _data(structure_type="function", change_type="add", new_file=True), PATH
    )
    assert task.new_file is True and task.start_anchor is None


def test_pattern_example_parses():
    task = WorkTask.from_dict(
        _data(structure_type="method", change_type="add", parent="Foo", pattern_example="fun bar() {}"),
        PATH,
    )
    assert task.pattern_example == "fun bar() {}"


def test_pattern_example_rejected_alongside_exact_code():
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(
            _data(
                structure_type="method", change_type="add", parent="Foo",
                exact_code="fun baz() {}", pattern_example="fun bar() {}",
            ),
            PATH,
        )


def test_acceptance_criteria_optional_with_exact_code():
    data = _data(structure_type="function", change_type="modify", name="foo", exact_code="def foo():\n    pass")
    del data["acceptance_criteria"]
    task = WorkTask.from_dict(data, PATH)
    assert task.acceptance_criteria == []


def test_acceptance_criteria_required_without_exact_code():
    data = _data(structure_type="function", change_type="modify", name="foo")
    del data["acceptance_criteria"]
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(data, PATH)


def test_acceptance_criteria_must_be_a_list():
    data = _data(structure_type="function", change_type="modify", name="foo", exact_code="def foo():\n    pass")
    data["acceptance_criteria"] = "not a list"
    with pytest.raises(WorkDocError):
        WorkTask.from_dict(data, PATH)


def test_indent_parses_when_a_non_negative_int():
    task = WorkTask.from_dict(
        _data(structure_type="block", change_type="add", start_anchor="# marker", indent=0),
        PATH,
    )
    assert task.indent == 0


def test_indent_rejects_negative_or_non_int():
    for bad in (-1, "0", 1.5, True):
        with pytest.raises(WorkDocError):
            WorkTask.from_dict(
                _data(structure_type="block", change_type="add", start_anchor="# marker", indent=bad),
                PATH,
            )


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_batch_consolidated_batch_file(tmp_path: Path):
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "init": {"batch_id": "b", "repo_root": ".", "language": "python"},
        "tasks": [
            dict(id="task-001", title="t1", file="f.py", structure_type="function",
                 change_type="modify", name="foo", description="d", exact_code="def foo():\n    pass"),
            dict(id="task-002", title="t2", file="f.py", structure_type="function",
                 change_type="modify", name="bar", description="d", exact_code="def bar():\n    pass"),
        ],
    })
    init, tasks = load_batch(tmp_path)
    assert init.batch_id == "b"
    assert [t.id for t in tasks] == ["task-001", "task-002"]
    assert tasks[0].exact_code == "def foo():\n    pass"


def test_load_batch_consolidated_task_defaults_kind_to_work(tmp_path: Path):
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "init": {"batch_id": "b", "repo_root": ".", "language": "python"},
        "tasks": [
            dict(id="task-001", title="t1", file="f.py", structure_type="function",
                 change_type="modify", name="foo", description="d", exact_code="def foo():\n    pass"),
        ],
    })
    init, tasks = load_batch(tmp_path)
    assert len(tasks) == 1


def test_load_batch_consolidated_requires_non_empty_tasks(tmp_path: Path):
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "init": {"batch_id": "b", "repo_root": ".", "language": "python"},
        "tasks": [],
    })
    with pytest.raises(WorkDocError):
        load_batch(tmp_path)


def test_load_batch_consolidated_requires_init_object(tmp_path: Path):
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "tasks": [dict(id="task-001", title="t1", file="f.py", structure_type="function",
                       change_type="modify", name="foo", description="d", exact_code="x")],
    })
    with pytest.raises(WorkDocError):
        load_batch(tmp_path)


def test_load_batch_rejects_second_init_across_styles(tmp_path: Path):
    _write(tmp_path / "000-init.json", {"kind": "init", "batch_id": "b", "repo_root": ".", "language": "python"})
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "init": {"batch_id": "b2", "repo_root": ".", "language": "python"},
        "tasks": [dict(id="task-001", title="t1", file="f.py", structure_type="function",
                       change_type="modify", name="foo", description="d", exact_code="x")],
    })
    with pytest.raises(WorkDocError):
        load_batch(tmp_path)


def test_load_batch_mixes_batch_file_with_standalone_work_file(tmp_path: Path):
    # A "batch" doc provides its own init inline; a separate standalone
    # "work" file (kind == "work") never touches init at all, so it can
    # coexist and just adds one more task to the same run.
    _write(tmp_path / "000-batch.json", {
        "kind": "batch",
        "init": {"batch_id": "b", "repo_root": ".", "language": "python"},
        "tasks": [dict(id="task-001", title="t1", file="f.py", structure_type="function",
                       change_type="modify", name="foo", description="d", exact_code="x")],
    })
    _write(tmp_path / "task-002.json", dict(kind="work", id="task-002", title="t2", file="f.py",
                                             structure_type="function", change_type="modify", name="bar",
                                             description="d", exact_code="y"))
    init, tasks = load_batch(tmp_path)
    assert init.batch_id == "b"
    assert [t.id for t in tasks] == ["task-001", "task-002"]


def test_load_batch_batch_doc_without_init_rejected(tmp_path: Path):
    _write(tmp_path / "batch.json", {
        "kind": "batch",
        "tasks": [dict(id="task-001", title="t1", file="f.py", structure_type="function",
                       change_type="modify", name="foo", description="d", exact_code="x")],
    })
    with pytest.raises(WorkDocError):
        load_batch(tmp_path)
