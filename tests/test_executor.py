"""Covers _reference_indent, the heuristic that decides how deep an
inserted fragment should be indented. Found live against a real Kotlin
project: appending the first method into a class whose body was still
empty landed at the class declaration's own indent (0) instead of one
level deeper (4), because "the line right before the insert point" was
the declaration line itself, not a body member to match.

Also covers _validate_fragment_shape (catches a model echoing a bare
signature/name instead of writing real content) and build_prompt's
pattern_example / retry_feedback sections, added alongside the authoring
workflow change to route more pattern-following boilerplate to the model
instead of always pre-deciding exact_code."""

from pathlib import Path

from runner import anchor as anchor_mod
from runner.executor import _reference_indent, _validate_fragment_shape, build_prompt, parse_fragment
from runner.work_doc import InitTask, WorkTask


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


def test_validate_fragment_shape_flags_bare_signature_with_no_body():
    task = _task(structure_type="function", change_type="add", start_anchor="# marker")
    issue = _validate_fragment_shape(task, "def greet(name):")
    assert issue is not None and "bare signature" in issue


def test_validate_fragment_shape_allows_a_real_definition():
    task = _task(structure_type="function", change_type="add", start_anchor="# marker")
    issue = _validate_fragment_shape(task, "def greet(name):\n    return f'Hello, {name}!'")
    assert issue is None


def test_validate_fragment_shape_flags_multiline_docstring():
    task = _task(structure_type="docstring", change_type="add", target_name="greet")
    issue = _validate_fragment_shape(task, '"""Greets someone.\n\nReturns a greeting."""')
    assert issue is not None and "docstring" in issue


def test_validate_fragment_shape_flags_echoed_start_anchor():
    task = _task(structure_type="constant", change_type="add", start_anchor="X = 1")
    issue = _validate_fragment_shape(task, "X = 1")
    assert issue is not None and "restated verbatim" in issue


def test_validate_fragment_shape_allows_normal_block_fragment():
    task = _task(structure_type="block", change_type="add", start_anchor="# marker")
    issue = _validate_fragment_shape(task, "x = compute_value()")
    assert issue is None


def _init(**kwargs) -> InitTask:
    defaults = dict(batch_id="b", repo_root=".", language="python")
    defaults.update(kwargs)
    return InitTask(**defaults)


def test_build_prompt_includes_pattern_example_when_set():
    task = _task(
        structure_type="method", change_type="add", parent="Foo", new_file=False,
        pattern_example="fun bar(): Int = 1",
    )
    task.new_file = True  # short-circuits build_prompt before it needs real file lines/resolved anchor
    prompt = build_prompt(task, _init(), Path("."), lines=None, resolved=None, context_lines=3)
    assert "fun bar(): Int = 1" in prompt
    assert "Follow this real example" in prompt


def test_build_prompt_includes_retry_feedback_when_set():
    task = _task(structure_type="method", change_type="add", parent="Foo")
    task.new_file = True
    prompt = build_prompt(
        task, _init(), Path("."), lines=None, resolved=None, context_lines=3,
        retry_feedback="fragment was a bare signature with no body",
    )
    assert "fragment was a bare signature with no body" in prompt
    assert "previous attempt was rejected" in prompt


def test_build_prompt_omits_pattern_example_and_retry_sections_when_unset():
    task = _task(structure_type="method", change_type="add", parent="Foo")
    task.new_file = True
    prompt = build_prompt(task, _init(), Path("."), lines=None, resolved=None, context_lines=3)
    assert "Follow this real example" not in prompt
    assert "previous attempt was rejected" not in prompt


def test_parse_fragment_unescapes_literal_backslash_n_when_no_real_newlines():
    # Found live via runner/bench.py: small models told "encode newlines as
    # \n" sometimes double-escape, so json.loads correctly yields the two
    # literal characters '\' + 'n' instead of a line break -- a real
    # multi-statement fragment then looked like one bare line to
    # _validate_fragment_shape.
    text = '{"fragment": "@Delete\\\\n    suspend fun delete(profile: Profile)"}'
    fragment = parse_fragment(text)
    assert fragment == "@Delete\n    suspend fun delete(profile: Profile)"


def test_parse_fragment_leaves_real_newlines_untouched():
    text = '{"fragment": "line one\\nline two"}'
    fragment = parse_fragment(text)
    assert fragment == "line one\nline two"
