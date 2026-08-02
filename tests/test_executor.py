"""Covers _reference_indent, the heuristic that decides how deep an
inserted fragment should be indented. Found live against a real Kotlin
project: appending the first method into a class whose body was still
empty landed at the class declaration's own indent (0) instead of one
level deeper (4), because "the line right before the insert point" was
the declaration line itself, not a body member to match."""

from runner import anchor as anchor_mod
from runner.executor import _reference_indent
from runner.work_doc import WorkTask


def _task(**kwargs) -> WorkTask:
    defaults = dict(id="t", title="t", file="f", description="d", acceptance_criteria=["x"])
    defaults.update(kwargs)
    return WorkTask(**defaults)


def test_indent_matches_sibling_member_when_body_already_has_content():
    lines = [
        "class Foo {",
        "    fun bar() {",
        "        println(1)",
        "    }",
        "}",
    ]
    resolved = anchor_mod.ResolvedAnchor(mode="insert", start_line=4)
    task = _task(structure_type="method", change_type="add", parent="Foo")
    assert _reference_indent(task, lines, resolved) == 4


def test_indent_steps_in_one_level_when_appending_into_an_empty_body():
    lines = [
        "class Foo {",
        "}",
    ]
    resolved = anchor_mod.ResolvedAnchor(mode="insert", start_line=1)
    task = _task(structure_type="method", change_type="add", parent="Foo")
    assert _reference_indent(task, lines, resolved) == 4


def test_indent_steps_in_one_level_after_any_block_opener_not_just_empty_class_body():
    # Same heuristic, different trigger: a start_anchor that itself opens a
    # block (ends with '{') means the insertion becomes that block's first
    # statement, one level deeper -- not a sibling of the opener.
    lines = [
        "fun foo() {",
        "    if (x) {",
        "    }",
        "}",
    ]
    resolved = anchor_mod.ResolvedAnchor(mode="insert", start_line=2)
    task = _task(structure_type="block", change_type="add", start_anchor="if (x) {")
    assert _reference_indent(task, lines, resolved) == 8


def test_replace_mode_still_matches_the_spans_own_first_line_indent():
    # Sanity check the fix doesn't touch replace-mode (modify/delete),
    # which always keeps the same nesting level as what it's replacing.
    lines = [
        "class Foo {",
        "    fun bar() {",
        "        println(1)",
        "    }",
        "}",
    ]
    resolved = anchor_mod.ResolvedAnchor(mode="replace", start_line=1, end_line=3)
    task = _task(structure_type="method", change_type="modify", name="bar", parent="Foo")
    assert _reference_indent(task, lines, resolved) == 4
