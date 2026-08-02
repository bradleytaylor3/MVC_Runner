"""Covers runner.scaffold: expanding a compact {{template}} + item-list spec
into a validated work_docs/ batch, entirely mechanically (no model call).
Built to replace routing more work to the local model -- a real benchmark
(runner/bench.py) showed <=4B models get roughly 0-15% exact-match even with
few-shot pattern_example grounding, so the actual lever for cutting down how
much JSON Claude hand-authors is factoring out repetition, not delegating
generation to an unreliable model."""

from pathlib import Path

import pytest

from runner.scaffold import ScaffoldError, expand, write_batch
from runner.work_doc import WorkDocError, load_batch


def _spec(**overrides):
    base = {
        "batch_id": "b",
        "repo_root": ".",
        "language": "python",
        "task_template": {
            "title": "Add {{name}}",
            "file": "converters.py",
            "structure_type": "constant",
            "change_type": "add",
            "start_anchor": "# marker",
            "description": "{{description}}",
            "acceptance_criteria": ["{{name}} is defined"],
            "exact_code": "{{name}} = {{value}}",
        },
        "items": [
            {"name": "A", "description": "a constant", "value": "1"},
            {"name": "B", "description": "another constant", "value": "2"},
        ],
    }
    base.update(overrides)
    return base


def test_expand_renders_shared_and_per_item_fields():
    init_doc, work_docs = expand(_spec())
    assert init_doc == {"kind": "init", "batch_id": "b", "repo_root": ".", "language": "python", "conventions": []}
    assert len(work_docs) == 2
    assert work_docs[0]["id"] == "task-001"
    assert work_docs[0]["title"] == "Add A"
    assert work_docs[0]["exact_code"] == "A = 1"
    assert work_docs[0]["acceptance_criteria"] == ["A is defined"]
    assert work_docs[1]["exact_code"] == "B = 2"


def test_expand_uses_id_prefix():
    _init_doc, work_docs = expand(_spec(id_prefix="dao"))
    assert [d["id"] for d in work_docs] == ["dao-001", "dao-002"]


def test_single_braces_in_template_pass_through_untouched():
    # exact_code is source code -- Kotlin/Java/JS use single braces for real
    # block syntax, which must never be read as a placeholder.
    spec = _spec(task_template={
        "title": "Add {{name}}",
        "file": "f.kt",
        "structure_type": "method",
        "change_type": "add",
        "parent": "Foo",
        "description": "{{description}}",
        "acceptance_criteria": ["ok"],
        "exact_code": "fun {{name}}() { return 1 }",
    })
    _init_doc, work_docs = expand(spec)
    assert work_docs[0]["exact_code"] == "fun A() { return 1 }"


def test_missing_item_variable_raises_scaffold_error():
    spec = _spec(items=[{"name": "A", "description": "d"}])  # no "value"
    with pytest.raises(ScaffoldError, match="value"):
        expand(spec)


def test_expand_requires_non_empty_items():
    with pytest.raises(ScaffoldError):
        expand(_spec(items=[]))


def test_write_batch_writes_validated_files(tmp_path: Path):
    written = write_batch(_spec(), tmp_path)
    assert len(written) == 3  # init + 2 work docs
    for path in written:
        assert path.is_file()

    init, tasks = load_batch(tmp_path)
    assert init.batch_id == "b"
    assert [t.exact_code for t in tasks] == ["A = 1", "B = 2"]


def test_write_batch_refuses_existing_files_without_force(tmp_path: Path):
    write_batch(_spec(), tmp_path)
    with pytest.raises(ScaffoldError, match="force"):
        write_batch(_spec(), tmp_path)


def test_write_batch_force_overwrites(tmp_path: Path):
    write_batch(_spec(), tmp_path)
    written = write_batch(_spec(items=[{"name": "C", "description": "d", "value": "3"}]), tmp_path, force=True)
    assert len(written) == 2  # init + 1 work doc this time
    _init, tasks = load_batch(tmp_path)
    assert len(tasks) == 1 and tasks[0].exact_code == "C = 3"


def test_write_batch_raises_before_writing_anything_on_invalid_item(tmp_path: Path):
    # structure_type/change_type combo here requires start_anchor, which
    # this template omits -- should fail validation, and fail loudly enough
    # that nothing gets written for a batch that's partially broken.
    spec = _spec(task_template={
        "title": "Add {{name}}",
        "file": "f.py",
        "structure_type": "constant",
        "change_type": "add",
        "description": "{{description}}",
        "acceptance_criteria": ["ok"],
        "exact_code": "{{name}} = {{value}}",
    })
    with pytest.raises((ScaffoldError, WorkDocError)):
        write_batch(spec, tmp_path)
    assert list(tmp_path.glob("*.json")) == []
