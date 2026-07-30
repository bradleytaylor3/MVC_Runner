# Work document schema

A **batch** is a directory of JSON files: exactly one **init doc** plus any
number of **work docs**. Each work doc names one small, well-defined edit —
`structure_type` says *what kind* of thing is being added/changed
(`function`, `class`, `method`, `docstring`, `import`, `constant`, or the
generic `block` fallback), and together with `change_type` it determines
both *where* the edit resolves to and *how* the local model is prompted.
This isn't a generic "anchor + freeform description" pair — each
structure_type gets prompt framing tailored to the mistake it's actually
prone to (a bare function/class/method must be a complete definition, never
just a restated signature; a docstring must be *only* the docstring line,
never a reproduction of the whole function it belongs to).

Not sure how to write these by hand? See `AUTHORING_PROMPT.md` in this same
directory — a copy-pasteable prompt for turning a plain-English change
request into a valid batch, usable with any chat AI (Claude, ChatGPT,
Gemini, ...), not just Claude Code.

## Init doc

Exactly one file per batch must have `"kind": "init"`. Convention: name it
`000-init.json` so it sorts first, though the runner finds it by `kind`, not
filename.

```json
{
  "kind": "init",
  "batch_id": "example-python-demo",
  "repo_root": ".",
  "language": "python",
  "conventions": []
}
```

- `batch_id` (required) — a label for this batch, carried into the run log.
- `repo_root` (required) — absolute (or cwd-relative) path to the repo the
  work docs' `file` paths are relative to.
- `language` (required) — used as prompt framing text, and as the fallback
  anchor-resolution mode (indentation vs. brace) when a file extension isn't
  recognized.
- `conventions` (optional, default `[]`) — prose bullets folded into every
  work task's prompt once (indentation style, naming conventions, etc.).

## Work doc

```json
{
  "kind": "work",
  "id": "task-001",
  "title": "Short human-readable description",
  "file": "relative/path/to/file.py",
  "structure_type": "function | class | method | docstring | import | constant | block",
  "change_type": "add | modify | delete",
  "description": "Plain-English intent — the logic/content, and why.",
  "acceptance_criteria": ["Concrete, checkable condition"],

  "name": "existing symbol name (modify/delete on function/class/method)",
  "parent": "containing class name (method only)",
  "target_name": "function or class this docstring belongs to (docstring only)",
  "start_anchor": "exact existing line of text to anchor on",
  "end_anchor": "exact existing line ending a multi-line span (optional)",
  "occurrence": "1-based index (optional, only if start_anchor's text matches more than one line)",

  "exact_code": "optional: the literal final code, when already fully decided",
  "new_file": false,
  "context_files": ["optional/relative/path/for_reference_only.py"]
}
```

### Always required

- `id`, `title`, `file`, `structure_type`, `change_type`, `description`,
  `acceptance_criteria` (non-empty list).

### Location fields — which ones you need depends on `(structure_type, change_type)`

| structure_type | change_type | required location field(s) | resolves to |
|---|---|---|---|
| `function` / `class` | `add` | `start_anchor` | inserted right after that line |
| `function` / `class` | `modify` / `delete` | `name` | the whole existing definition (found by name; its span — decorators through closing body — is resolved automatically, you don't need to know line numbers) |
| `method` | `add` | `parent` (class name); `start_anchor` optional | if `start_anchor` given: inserted after that line (must be inside the class). If omitted: appended at the end of the class's body |
| `method` | `modify` / `delete` | `name` + `parent` | the whole existing method inside that class |
| `docstring` | `add` (only) | `target_name` (the function or class it documents) | inserted as the first statement of that symbol's body — **no anchor text needed at all** |
| `import` | `add` | `start_anchor` | inserted right after that line |
| `import` | `modify` | `start_anchor` | that one line is replaced |
| `constant` | `add` | `start_anchor` | inserted right after that line |
| `constant` | `modify` / `delete` | `start_anchor` (+ `end_anchor` if it spans multiple lines) | that span is replaced/removed |
| `block` | `modify` / `delete` | `start_anchor` (+ `end_anchor` if it spans multiple lines) | that span is replaced/removed — the escape hatch for an edit that isn't cleanly a named function/class/method/import/constant |

If `start_anchor`'s exact text matches more than one line in the file (e.g.
the same loop-header text appears twice), resolution fails loudly rather
than guessing — set `occurrence` (1-based) to pick which match, or narrow
the text so it's unique.

`new_file: true` (only with `change_type: "add"`) skips all location fields
entirely — the model authors the whole file's content from scratch, and
the runner errors if the file already exists.

### `exact_code` (optional, any structure_type/change_type except `delete`)

When you already know the exact final code — not just its intent — put it
here instead of relying on `description` prose. The model's job shrinks to
fitting it into place (adjusting only indentation to match the surrounding
context), not inventing it. `description` stays required in all cases as
the human-readable "why," even when `exact_code` is set. This matters:
small/medium local models reliably transcribe already-decided code, but
unreliably synthesize it from a description — especially one that quotes a
signature in backticks, which gets misread as literal output to copy
rather than a spec to implement.

### `context_files` (optional, list)

Files the model should read for reference but must never rewrite — shown
in full, read-only, regardless of what the model outputs for them.

## Notes for whoever authors these (e.g. a cloud model at the planning stage)

- One batch = one directory = one init doc + N work docs. Unlike a batch of
  independent test scenarios, code-edit batches are sequential — the batch
  stops at the first task that fails, and later tasks may assume earlier
  ones already landed (e.g. a `docstring` task with `target_name: "foo"`
  assumes an earlier task already added `foo`).
- Read the real target file before authoring a task — `name`/`target_name`/
  `start_anchor` must match something that actually exists (or, for the
  first task touching a brand-new symbol, exists once the tasks before it
  have applied).
- Prefer the most specific `structure_type` that fits (`docstring` over
  `block` for adding a docstring, `constant` over `block` for a new
  variable) — the more specific the type, the more targeted the model's
  framing and the smaller the blast radius of what it's allowed to touch.
  Reach for `block` only when nothing else fits.
- If you already know the exact code, use `exact_code` — don't paraphrase
  it into `description` and hope the model reconstructs it faithfully.
- Keep `description` focused on intent even when `exact_code` is absent;
  let `structure_type`/`change_type`/the location fields carry the "where"
  and "what kind of edit."
