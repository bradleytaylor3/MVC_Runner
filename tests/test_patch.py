"""Covers runner.patch.reindent_to directly -- previously only exercised
indirectly through executor's exact_code integration, with no unit coverage
of its own. Added alongside the fix for a real bug: a tab used for a
fragment's *relative* (beyond-its-own-first-line) indentation was silently
collapsing to a single space, because the old implementation measured
indentation purely as a character count and always re-emitted spaces. Found
live reindenting a Kconfig `config` block's tab-indented body lines."""

from runner.patch import reindent_to


def test_shifts_flat_fragment_to_target_indent():
    assert reindent_to("obj-y += foo.o", 0) == "obj-y += foo.o"
    assert reindent_to("x = 1", 4) == "    x = 1"


def test_preserves_relative_space_indentation_beyond_first_line():
    fragment = "def foo():\n    return 1"
    assert reindent_to(fragment, 4) == "    def foo():\n        return 1"


def test_preserves_relative_tab_indentation_beyond_first_line():
    # The regression this test guards: a tab used for the fragment's own
    # relative nesting must survive reindent, not collapse to one space.
    fragment = "config FOO\n\ttristate \"Foo\"\n\thelp\n\t  Say Y here."
    assert reindent_to(fragment, 0) == fragment


def test_relative_tab_indentation_survives_a_nonzero_base_shift():
    fragment = "config FOO\n\ttristate \"Foo\"\n\thelp\n\t  Say Y here."
    expected = "  config FOO\n  \ttristate \"Foo\"\n  \thelp\n  \t  Say Y here."
    assert reindent_to(fragment, 2) == expected


def test_blank_lines_stay_blank_regardless_of_target_indent():
    assert reindent_to("a\n\nb", 4) == "    a\n\n    b"


def test_dedented_line_falls_back_to_character_count_not_negative():
    # Second line has less leading whitespace than the first -- its
    # relative whitespace doesn't start with the first line's, so this
    # falls back to the old count-based behavior (clamped at 0) rather than
    # erroring or producing something nonsensical.
    fragment = "    if x:\n  # dedented comment"
    assert reindent_to(fragment, 0) == "if x:\n# dedented comment"
