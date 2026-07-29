"""Splice a model-authored fragment into the real lines of a file at a
resolved anchor. The runner (not the model) owns finding and applying the
edit location; the model only ever sees/returns the small fragment at that
location."""

from runner.anchor import ResolvedAnchor


class SpliceError(ValueError):
    pass


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
    duplicates the line immediately following the anchor in the real file, the
    model likely spilled over into read-only context it was told to omit —
    a small local model doing this silently produces a duplicated/corrupted
    file (e.g. two consecutive closing braces) if left unchecked."""
    if resolved.mode != "replace":
        return None
    next_line_idx = resolved.end_line + 1
    if next_line_idx >= len(lines):
        return None
    fragment_lines = fragment.split("\n")
    trailing = next((line for line in reversed(fragment_lines) if line.strip() != ""), None)
    if trailing is not None and trailing.strip() == lines[next_line_idx].strip():
        return (
            f"model fragment's last non-blank line duplicates the line immediately after the anchor "
            f"({lines[next_line_idx].strip()!r}) — it likely included read-only context it was told to omit"
        )
    return None


def splice(lines: list[str], resolved: ResolvedAnchor, change_type: str, model_fragment: str) -> list[str]:
    fragment_lines = model_fragment.split("\n") if model_fragment else []

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
