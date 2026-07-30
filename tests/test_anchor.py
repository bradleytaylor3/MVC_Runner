"""Covers the fiddly index arithmetic in anchor.py: body_start/body_end
resolution (multi-line signatures, brace vs. indentation mode, same-line
and expression bodies), plus marker occurrence disambiguation. This is the
class of bug that would otherwise silently corrupt a file rather than fail
loudly — e.g. the live-tested docstring-placed-outside-the-function bug
this whole redesign targets."""

import pytest

from runner.anchor import AnchorError, resolve_anchor
from runner.work_doc import WorkTask


def _task(**kwargs) -> WorkTask:
    defaults = dict(id="t", title="t", file="f", description="d", acceptance_criteria=["x"])
    defaults.update(kwargs)
    return WorkTask(**defaults)


# ---- Python (indentation mode) ----

def test_python_docstring_body_start_single_line_signature():
    lines = [
        "def foo(a, b):",
        "    return a + b",
    ]
    r = resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="foo"), lines, "python")
    assert r.mode == "insert"
    assert r.start_line == 1  # right after the def line


def test_python_docstring_body_start_multiline_signature():
    lines = [
        "def foo(a,",
        "        b):",
        "    return a + b",
    ]
    r = resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="foo"), lines, "python")
    assert r.mode == "insert"
    assert r.start_line == 2  # after the line with the colon, not decl_line + 1


def test_python_method_body_end_with_nested_block_last_statement():
    lines = [
        "class C:",
        "    def foo(self, a):",
        "        if a:",
        "            return 1",
        "        return 0",
    ]
    r = resolve_anchor(_task(structure_type="method", change_type="add", parent="C"), lines, "python")
    assert r.mode == "insert"
    assert r.start_line == 5  # appended as the class's last member


# ---- Kotlin (brace mode) ----

def test_kotlin_docstring_body_start_single_line_signature():
    lines = [
        "class Foo {",
        "    fun bar(x: Int): Int {",
        "        return x",
        "    }",
        "}",
    ]
    r = resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="bar"), lines, "kotlin")
    assert r.mode == "insert"
    assert r.start_line == 2


def test_kotlin_method_body_end_appends_before_closing_brace():
    lines = [
        "class Foo {",
        "    fun bar() {",
        "        println(1)",
        "    }",
        "}",
    ]
    r = resolve_anchor(_task(structure_type="method", change_type="add", parent="Foo"), lines, "kotlin")
    assert r.mode == "insert"
    assert r.start_line == 4  # before the class's own closing '}', not after it


def test_kotlin_docstring_body_start_multiline_signature():
    lines = [
        "class Foo {",
        "    fun bar(",
        "        x: Int,",
        "        y: Int",
        "    ): Int {",
        "        return x + y",
        "    }",
        "}",
    ]
    r = resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="bar"), lines, "kotlin")
    assert r.mode == "insert"
    assert r.start_line == 5  # right after the line with the opening '{'


def test_kotlin_same_line_body_raises():
    lines = [
        "class Foo {",
        "    fun square(x: Int): Int { return x * x }",
        "}",
    ]
    with pytest.raises(AnchorError):
        resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="square"), lines, "kotlin")


def test_kotlin_expression_body_raises():
    lines = [
        "class Foo {",
        "    fun square(x: Int) = x * x",
        "}",
    ]
    with pytest.raises(AnchorError):
        resolve_anchor(_task(structure_type="docstring", change_type="add", target_name="square"), lines, "kotlin")


# ---- marker resolution / occurrence ----

def test_marker_ambiguous_without_occurrence_raises():
    lines = ["x = 1", "print(x)", "x = 1"]
    with pytest.raises(AnchorError):
        resolve_anchor(_task(structure_type="block", change_type="delete", start_anchor="x = 1"), lines, "python")


def test_marker_occurrence_disambiguates():
    lines = ["x = 1", "print(x)", "x = 1"]
    r = resolve_anchor(
        _task(structure_type="block", change_type="delete", start_anchor="x = 1", occurrence=2), lines, "python"
    )
    assert r.mode == "replace"
    assert r.start_line == 2 and r.end_line == 2


# ---- function/class/method by name (unchanged span resolution, sanity check) ----

def test_function_modify_by_name_resolves_whole_span():
    lines = [
        "def foo(a):",
        "    return a",
        "",
        "def bar():",
        "    pass",
    ]
    r = resolve_anchor(_task(structure_type="function", change_type="modify", name="foo"), lines, "python")
    assert r.mode == "replace"
    assert r.start_line == 0 and r.end_line == 1


def test_method_modify_scoped_to_parent_class():
    lines = [
        "class A:",
        "    def run(self):",
        "        return 1",
        "",
        "class B:",
        "    def run(self):",
        "        return 2",
    ]
    r = resolve_anchor(_task(structure_type="method", change_type="modify", name="run", parent="B"), lines, "python")
    assert r.mode == "replace"
    assert r.start_line == 5 and r.end_line == 6
