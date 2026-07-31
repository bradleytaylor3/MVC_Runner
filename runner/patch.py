"""Splice a model-authored fragment into the real lines of a file at a
resolved anchor. The runner (not the model) owns finding and applying the
edit location; the model only ever sees/returns the small fragment at that
location."""

from runner.anchor import ResolvedAnchor


class SpliceError(ValueError):
    pass


def reindent_to(content: str, target_indent: int) -> str:
    """Re-anchor a known-final code fragment's indentation to target_indent
    spaces, preserving each line's indentation *relative to the fragment's
    own first line*. Purely mechanical — no model involved — since exact_code
    means the content itself is already fully decided; the only remaining
    unknown is how deep the surrounding file nests at the splice point, and
    that's measured directly from the file (see executor._reference_indent),
    not guessed by a model doing a verbatim copy."""
    lines = content.split("\n")
    first_indent = len(lines[0]) - len(lines[0].lstrip())
    out = []
    for line in lines:
        if line.strip() == "":
            out.append("")
            continue
        relative = max(len(line) - len(line.lstrip()) - first_indent, 0)
        out.append(" " * (target_indent + relative) + line.lstrip())
    return "\n".join(out)


def extract_fragment(lines: list[str], resolved: ResolvedAnchor) -> str | None:
    """The exact current content at a replace-mode anchor, or None for an
    insert-mode anchor (nothing exists there yet)."""
    if resolved.mode != "replace":
        return None
    return "\n".join(lines[resolved.start_line:resolved.end_line + 1])


def context_window(lines: list[str], resolved: ResolvedAnchor, n: int = 3) -> tuple[list[str], list[str]]:
    """Up to n lines immediately before/after the anchor, clipped to file
    bounds, for read-only orientation in the prompt."""
    if resolved.mode == "replace":
        before_end = resolved.start_line
        after_start = resolved.end_line + 1
    else:
        before_end = resolved.start_line
        after_start = resolved.start_line
    before = lines[max(0, before_end - n):before_end]
    after = lines[after_start:after_start + n]
    return before, after


def detect_trailing_bleed(lines: list[str], resolved: ResolvedAnchor, fragment: str) -> str | None:
    """Heuristic guard: if the model's fragment ends with a line that exactly
    duplicates the line immediately following the edit point in the real
    file, the model likely spilled over into read-only context it was told
    to omit — a small local model doing this silently produces a
    duplicated/corrupted file (e.g. two consecutive closing braces) if left
    unchecked. For insert-mode anchors (e.g. body_end, where the following
    line is the symbol's own closing brace), this is exactly how the
    fragment could otherwise duplicate that closing line."""
    next_line_idx = resolved.end_line + 1 if resolved.mode == "replace" else resolved.start_line
    if next_line_idx >= len(lines):
        return None
    fragment_lines = fragment.split("\n")
    trailing = next((line for line in reversed(fragment_lines) if line.strip() != ""), None)
    if trailing is not None and trailing.strip() == lines[next_line_idx].strip():
        return (
            f"model fragment's last non-blank line duplicates the line immediately after the edit point "
            f"({lines[next_line_idx].strip()!r}) — it likely included read-only context it was told to omit"
        )
    return None


def detect_leading_bleed(lines: list[str], resolved: ResolvedAnchor, fragment: str) -> str | None:
    """Symmetric guard for insert-mode anchors: if the fragment's first line
    duplicates the line immediately before the insertion point (e.g.
    body_start's preceding `def`/`fun` line), the model likely re-echoed
    read-only 'before' context instead of omitting it — structurally how the
    docstring-placed-outside-the-function bug happened live."""
    if resolved.mode != "insert":
        return None
    prev_line_idx = resolved.start_line - 1
    if prev_line_idx < 0:
        return None
    fragment_lines = fragment.split("\n")
    leading = next((line for line in fragment_lines if line.strip() != ""), None)
    if leading is not None and leading.strip() == lines[prev_line_idx].strip():
        return (
            f"model fragment's first non-blank line duplicates the line immediately before the insertion point "
            f"({lines[prev_line_idx].strip()!r}) — it likely included read-only context it was told to omit"
        )
    return None


def _strip_wrapping_blank_lines(fragment_lines: list[str]) -> list[str]:
    """Small models reliably pad a fragment with a stray leading/trailing
    blank line even when asked for exactly one line. Left unchecked, every
    'add' task in a chained batch pushes one more blank line down the file
    (each task's insertion point is the line the previous task just added),
    so a long batch ends with a growing wall of blank lines before whatever
    followed the last insertion. Blank lines *inside* a real multi-line
    fragment are untouched — only ones wrapping the whole fragment are noise."""
    start, end = 0, len(fragment_lines)
    while end - start > 1 and fragment_lines[end - 1] == "":
        end -= 1
    while end - start > 1 and fragment_lines[start] == "":
        start += 1
    return fragment_lines[start:end]


def splice(lines: list[str], resolved: ResolvedAnchor, change_type: str, model_fragment: str) -> list[str]:
    fragment_lines = _strip_wrapping_blank_lines(model_fragment.split("\n")) if model_fragment else []

    if change_type == "delete":
        if resolved.mode != "replace":
            raise SpliceError("delete requires a replace-mode anchor")
        return lines[:resolved.start_line] + lines[resolved.end_line + 1:]

    if change_type == "modify":
        if resolved.mode != "replace":
            raise SpliceError("modify requires a replace-mode anchor")
        return lines[:resolved.start_line] + fragment_lines + lines[resolved.end_line + 1:]

    if change_type == "add":
        if resolved.mode != "insert":
            raise SpliceError("add requires an insert-mode anchor")
        return lines[:resolved.start_line] + fragment_lines + lines[resolved.start_line:]

    raise SpliceError(f"unknown change_type: {change_type}")
