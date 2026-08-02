"""Resolve a work task's location against the real current content of its
target file: find the line span (for change_type modify/delete) or
insertion point (for change_type add) that the model's fragment applies to.

Dispatch is driven directly by the task's `structure_type` (see
work_doc.py's field-requirement table) rather than a generic anchor object
— each structure_type picks its own resolution strategy (symbol-by-name,
marker text, or a symbol's body bounds).

This is regex + brace/indentation heuristics, not a real parser. Ambiguous
cases raise AnchorError rather than guessing silently — a wrong silent
anchor is worse than a loud failure the author can fix by narrowing
`start_anchor` or adding `parent`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from runner.work_doc import WorkTask

EXTENSION_LANGUAGE_MODE = {
    ".py": "indentation",
    ".kt": "brace", ".kts": "brace", ".java": "brace",
    ".js": "brace", ".ts": "brace", ".jsx": "brace", ".tsx": "brace",
    ".c": "brace", ".cpp": "brace", ".h": "brace", ".hpp": "brace",
    ".cs": "brace", ".go": "brace", ".rs": "brace", ".swift": "brace",
}
LANGUAGE_MODE = {
    "python": "indentation",
}

FUNCTION_PATTERNS = [
    r"^\s*fun\s+{name}\s*\(",
    r"^\s*def\s+{name}\s*\(",
    r"^\s*function\s+{name}\s*\(",
    r"^\s*(?:public|private|protected|static|final|abstract|\s)*[\w<>\[\],\s]+?\b{name}\s*\(",
]
CLASS_PATTERNS = [
    # Kotlin interfaces are structurally identical to classes for anchor
    # purposes (a name, a brace-delimited body) — matching both here means
    # method/add's parent resolution (which searches with symbol_type=
    # "class") works against interfaces too, not just literal `class`
    # declarations. Found live: every DAO in a real Room-based project is
    # an interface, and method/add against one failed with "symbol not
    # found: class 'X'" before this.
    r"^\s*(?:public|private|open|abstract|data|sealed|final|\s)*(?:class|interface)\s+{name}\b",
]


class AnchorError(ValueError):
    pass


@dataclass
class ResolvedAnchor:
    mode: str  # "replace" (modify/delete) or "insert" (add)
    start_line: int  # 0-indexed
    end_line: int | None = None  # 0-indexed inclusive; only set for mode == "replace"


def _language_mode(file_path: str, default_language: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in EXTENSION_LANGUAGE_MODE:
        return EXTENSION_LANGUAGE_MODE[ext]
    return LANGUAGE_MODE.get(default_language.lower(), "brace")


def _brace_span(lines: list[str], decl_line: int) -> tuple[int, int] | None:
    """Scan forward from decl_line tracking brace depth, treating quoted
    strings and comments as opaque so interior braces (e.g. inside a Kotlin
    string template) never get counted. Returns (open_line, close_line),
    0-indexed, or None if no '{' was ever found."""
    text = "\n".join(lines[decl_line:])
    depth = 0
    open_line = None
    i = 0
    n = len(text)
    while i < n:
        if text.startswith('"""', i):
            end = text.find('"""', i + 3)
            i = end + 3 if end != -1 else n
            continue
        c = text[i]
        if c in ('"', "'"):
            i += 1
            while i < n and text[i] != c:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = end if end != -1 else n
            continue
        if c == "#":
            end = text.find("\n", i)
            i = end if end != -1 else n
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
            continue
        if c == "{":
            if open_line is None:
                open_line = decl_line + text.count("\n", 0, i)
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            if open_line is not None and depth == 0:
                close_line = decl_line + text.count("\n", 0, i)
                return (open_line, close_line)
            continue
        i += 1
    return None


def _brace_span_end(lines: list[str], decl_line: int) -> int | None:
    span = _brace_span(lines, decl_line)
    return span[1] if span else None


def _logical_line_end(lines: list[str], decl_line: int) -> int:
    """Fallback for one-line/no-body declarations: extend while parens are
    unclosed or the line looks like it continues onto the next. Also used
    to find where a (possibly multi-line) Python signature's `:` line is."""
    depth = 0
    i = decl_line
    while i < len(lines):
        line = lines[i]
        depth += line.count("(") - line.count(")")
        if depth <= 0 and not line.rstrip().endswith((",", "=", "+", "-", "&&", "||", "\\")):
            return i
        i += 1
    return len(lines) - 1


def _indentation_span_end(lines: list[str], decl_line: int) -> int:
    base_indent = len(lines[decl_line]) - len(lines[decl_line].lstrip())
    last_body_line = decl_line
    i = decl_line + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base_indent:
            break
        last_body_line = i
        i += 1
    return last_body_line


def _find_span_end(lines: list[str], decl_line: int, mode: str) -> int:
    if mode == "indentation":
        return _indentation_span_end(lines, decl_line)
    end = _brace_span_end(lines, decl_line)
    return end if end is not None else _logical_line_end(lines, decl_line)


def _resolve_symbol_by_name(
    lines: list[str], symbol_type: str | None, name: str, search_start: int, search_end: int, mode: str
) -> tuple[int, int, int]:
    """Returns (start, end, decl_line). symbol_type of None searches both
    function and class patterns combined (used for docstring's target_name,
    which doesn't say which kind it is)."""
    if symbol_type == "function":
        patterns = FUNCTION_PATTERNS
    elif symbol_type == "class":
        patterns = CLASS_PATTERNS
    else:
        patterns = FUNCTION_PATTERNS + CLASS_PATTERNS
    name_re = re.escape(name)
    compiled = [re.compile(p.format(name=name_re)) for p in patterns]

    matches = [i for i in range(search_start, search_end) if any(pat.match(lines[i]) for pat in compiled)]

    if not matches:
        raise AnchorError(f"symbol not found: {symbol_type or 'function/class'} '{name}'")
    if len(matches) > 1:
        raise AnchorError(
            f"ambiguous symbol '{name}': matched at lines {[m + 1 for m in matches]}; "
            "scope the search with 'parent' or use a more specific name"
        )

    decl_line = matches[0]
    start = decl_line
    j = decl_line - 1
    while j >= search_start and re.match(r"^\s*@\w+", lines[j]):
        start = j
        j -= 1

    end = _find_span_end(lines, decl_line, mode)
    return start, end, decl_line


def _resolve_body_bounds(lines: list[str], decl_line: int, end: int, mode: str) -> tuple[int, int]:
    """Returns (body_start, body_end): the 0-indexed line to insert at for
    'first statement of the body', and for 'last statement of the body'
    (pushed-down content ends up just before the symbol's own closing
    point)."""
    if mode == "indentation":
        sig_end = _logical_line_end(lines, decl_line)
        return sig_end + 1, end + 1

    span = _brace_span(lines, decl_line)
    if span is None:
        raise AnchorError(
            "no brace-delimited body found (expression-bodied or abstract declaration) — "
            "body_start/body_end require a '{ ... }' body"
        )
    open_line, close_line = span
    if open_line == close_line:
        raise AnchorError("body is entirely on one line — body_start/body_end can't target a line inside it")
    return open_line + 1, close_line


def _resolve_marker(lines: list[str], text: str, text_end: str | None, occurrence: int | None = None) -> tuple[int, int]:
    target = text.strip()
    matches = [i for i, line in enumerate(lines) if line.strip() == target]
    if not matches:
        raise AnchorError(f"marker text not found: {text!r}")
    if len(matches) > 1:
        if occurrence is None:
            raise AnchorError(
                f"marker text {text!r} matches multiple lines: {[m + 1 for m in matches]}; "
                "set 'occurrence' (1-based) or narrow the marker text to make it unique"
            )
        idx = occurrence - 1
        if idx < 0 or idx >= len(matches):
            raise AnchorError(f"occurrence {occurrence} out of range ({len(matches)} matches for {text!r})")
        start = matches[idx]
    else:
        start = matches[0]

    end = start
    if text_end:
        end_target = text_end.strip()
        end_matches = [i for i in range(start, len(lines)) if lines[i].strip() == end_target]
        if not end_matches:
            raise AnchorError(f"end_anchor not found on or after line {start + 1}: {text_end!r}")
        end = end_matches[0]

    return start, end


def resolve_anchor(task: WorkTask, lines: list[str], default_language: str) -> ResolvedAnchor:
    lang_mode = _language_mode(task.file, default_language)

    if task.structure_type == "docstring":
        _, end, decl_line = _resolve_symbol_by_name(lines, None, task.target_name, 0, len(lines), lang_mode)
        body_start, _ = _resolve_body_bounds(lines, decl_line, end, lang_mode)
        return ResolvedAnchor(mode="insert", start_line=body_start)

    if task.structure_type == "method":
        parent_start, parent_end, parent_decl_line = _resolve_symbol_by_name(
            lines, "class", task.parent, 0, len(lines), lang_mode
        )
        if task.change_type != "add":
            start, end, _ = _resolve_symbol_by_name(
                lines, "function", task.name, parent_start, parent_end + 1, lang_mode
            )
            return ResolvedAnchor(mode="replace", start_line=start, end_line=end)
        if task.start_anchor:
            m_start, m_end = _resolve_marker(lines, task.start_anchor, None, task.occurrence)
            if not (parent_start <= m_start <= parent_end):
                raise AnchorError(f"start_anchor {task.start_anchor!r} is not inside class {task.parent!r}")
            return ResolvedAnchor(mode="insert", start_line=m_end + 1)
        _, body_end = _resolve_body_bounds(lines, parent_decl_line, parent_end, lang_mode)
        return ResolvedAnchor(mode="insert", start_line=body_end)

    if task.structure_type in ("function", "class"):
        if task.change_type == "add":
            _, m_end = _resolve_marker(lines, task.start_anchor, None, task.occurrence)
            return ResolvedAnchor(mode="insert", start_line=m_end + 1)
        start, end, _ = _resolve_symbol_by_name(lines, task.structure_type, task.name, 0, len(lines), lang_mode)
        return ResolvedAnchor(mode="replace", start_line=start, end_line=end)

    # import, constant, block: always marker-based
    m_start, m_end = _resolve_marker(lines, task.start_anchor, task.end_anchor, task.occurrence)
    if task.change_type == "add":
        return ResolvedAnchor(mode="insert", start_line=m_end + 1)
    return ResolvedAnchor(mode="replace", start_line=m_start, end_line=m_end)
