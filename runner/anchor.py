"""Resolve a work task's anchor against the real current content of its
target file: find the line span (for symbol/marker anchors used with
change_type modify/delete) or insertion point (for change_type add) that the
model's fragment applies to.

This is regex + brace/indentation heuristics, not a real parser. Ambiguous
cases raise AnchorError rather than guessing silently — a wrong silent
anchor is worse than a loud failure the planning model can fix by adding
`parent` or switching to a marker anchor.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from runner.work_doc import Anchor, WorkTask

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
    r"^\s*(?:public|private|open|abstract|data|sealed|final|\s)*class\s+{name}\b",
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


def _brace_span_end(lines: list[str], decl_line: int) -> int | None:
    """Scan forward from decl_line tracking brace depth, treating quoted
    strings and comments as opaque so interior braces (e.g. inside a Kotlin
    string template) never get counted. Returns the 0-indexed line the
    matching closing brace is on, or None if no '{' was ever found."""
    text = "\n".join(lines[decl_line:])
    depth = 0
    found_open = False
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
            depth += 1
            found_open = True
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            if found_open and depth == 0:
                return decl_line + text.count("\n", 0, i)
            continue
        i += 1
    return None


def _logical_line_end(lines: list[str], decl_line: int) -> int:
    """Fallback for one-line/no-body declarations: extend while parens are
    unclosed or the line looks like it continues onto the next."""
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


def _resolve_symbol(lines: list[str], anchor: Anchor, search_start: int, search_end: int, mode: str) -> tuple[int, int]:
    patterns = FUNCTION_PATTERNS if anchor.symbol_type == "function" else CLASS_PATTERNS
    name_re = re.escape(anchor.name)
    compiled = [re.compile(p.format(name=name_re)) for p in patterns]

    matches = []
    for i in range(search_start, search_end):
        if any(pat.match(lines[i]) for pat in compiled):
            matches.append(i)

    if not matches:
        raise AnchorError(f"symbol not found: {anchor.symbol_type} '{anchor.name}'")
    if len(matches) > 1:
        raise AnchorError(
            f"ambiguous symbol '{anchor.name}': matched at lines {[m + 1 for m in matches]}; "
            "use 'parent' to scope the search or switch to a marker anchor"
        )

    decl_line = matches[0]
    start = decl_line
    j = decl_line - 1
    while j >= search_start and re.match(r"^\s*@\w+", lines[j]):
        start = j
        j -= 1

    end = _find_span_end(lines, decl_line, mode)
    return start, end


def _resolve_marker(lines: list[str], anchor: Anchor) -> tuple[int, int]:
    target = anchor.text.strip()
    matches = [i for i, line in enumerate(lines) if line.strip() == target]
    if not matches:
        raise AnchorError(f"marker text not found: {anchor.text!r}")

    if len(matches) > 1:
        if anchor.occurrence is None:
            raise AnchorError(
                f"marker text {anchor.text!r} matches multiple lines: {[m + 1 for m in matches]}; "
                "set anchor.occurrence to disambiguate"
            )
        idx = anchor.occurrence - 1
        if idx < 0 or idx >= len(matches):
            raise AnchorError(f"anchor.occurrence {anchor.occurrence} out of range ({len(matches)} matches)")
        start = matches[idx]
    else:
        start = matches[0]

    end = start
    if anchor.text_end:
        end_target = anchor.text_end.strip()
        end_matches = [i for i in range(start, len(lines)) if lines[i].strip() == end_target]
        if not end_matches:
            raise AnchorError(f"marker text_end not found on or after line {start + 1}: {anchor.text_end!r}")
        end = end_matches[0]

    return start, end


def resolve_anchor(task: WorkTask, lines: list[str], default_language: str) -> ResolvedAnchor:
    anchor = task.anchor
    if anchor is None:
        raise AnchorError("resolve_anchor called with no anchor (new_file task)")

    if anchor.type == "file_start":
        start, end = 0, -1
    elif anchor.type == "file_end":
        start, end = len(lines), len(lines) - 1
    elif anchor.type == "symbol":
        lang_mode = _language_mode(task.file, default_language)
        search_start, search_end = 0, len(lines)
        if anchor.parent:
            parent_anchor = Anchor(type="symbol", symbol_type="class", name=anchor.parent)
            parent_start, parent_end = _resolve_symbol(lines, parent_anchor, 0, len(lines), lang_mode)
            search_start, search_end = parent_start, parent_end + 1
        start, end = _resolve_symbol(lines, anchor, search_start, search_end, lang_mode)
    elif anchor.type == "marker":
        start, end = _resolve_marker(lines, anchor)
    else:
        raise AnchorError(f"unknown anchor type: {anchor.type}")

    if task.change_type == "add":
        if anchor.type == "file_start":
            insert_at = 0
        elif anchor.type == "file_end":
            insert_at = len(lines)
        else:
            insert_at = end + 1 if anchor.position == "after" else start
        return ResolvedAnchor(mode="insert", start_line=insert_at)

    return ResolvedAnchor(mode="replace", start_line=start, end_line=end)
