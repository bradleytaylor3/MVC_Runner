"""Covers WorkTask.from_dict's location-field validation, now driven by
LOCATION_FIELD_SPEC (work_doc.py) rather than an if/elif chain — this is
existing, migration-tested behavior being restructured, not new behavior,
so it's worth locking down directly since it previously had no dedicated
test coverage."""

from pathlib import Path

import pytest

from runner.work_doc import WorkDocError, WorkTask

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
