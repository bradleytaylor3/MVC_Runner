# Work document schema

A **batch** is a directory of JSON files: exactly one **init doc** plus any
number of **work docs**. This is the "minimum viable context" contract — a
cloud model doing the planning should scope each work doc as tightly as
possible: one file, one location (`anchor`), one small fragment of new
content, not a whole-file rewrite and not a prose description of where the
change belongs.

The runner resolves each work doc's `anchor` against the *real* current
content of `file` and shows the local model only the fragment at that
location (plus a little read-only context) — the model's job is to author
just the replacement fragment, which the runner then splices back in. This
is what keeps both the prompt and the expected output small, even for edits
inside large files.

## Init doc

Exactly one file per batch must have `"kind": "init"`. Convention: name it
`000-init.json` so it sorts first, though the runner finds it by `kind`, not
filename.

```json
{
  "kind": "init",
  "batch_id": "goblin-2026-07-27",
  "repo_root": "/home/bradleyt/Documents/Documentation/goblin-loot-engine",
  "language": "kotlin",
  "conventions": ["4-space indentation"]
}
```

- `batch_id` (required) — a label for this batch, carried into the run log.
- `repo_root` (required) — absolute (or cwd-relative) path to the repo the
  work docs' `file` paths are relative to. This is the primary source of
  truth for where files live; `--repo-root` on the CLI is only an override
  (e.g. for testing against an alternate checkout).
- `language` (required) — used as prompt framing text, and as the fallback
  anchor-resolution mode when a work doc's file extension isn't recognized.
- `conventions` (optional, default `[]`) — prose bullets folded into every
  work task's prompt once (indentation style, naming conventions, etc.).

The init doc is never sent to the model as a task; it's pure runner config.

## Work doc

```json
{
  "kind": "work",
  "id": "task-001",
  "title": "Short human-readable description",
  "file": "relative/path/to/file_to_edit.kt",
  "goal": "The intent of the change — the runner supplies the location, so this doesn't need to describe where.",
  "change_type": "modify",
  "anchor": {
    "type": "marker",
    "text": "exact existing line of text to anchor on",
    "text_end": "optional: exact existing line to end a multi-line span on"
  },
  "acceptance_criteria": [
    "Concrete, checkable condition"
  ],
  "context_files": ["optional/relative/path/for_reference_only.py"]
}
```

### Fields

- `id` (required) — unique task identifier; also used to sort execution order.
- `title` (required) — short description, shown in CLI progress output.
- `file` (required) — the one file this task edits. Paths are relative to
  the init doc's `repo_root`. One work task = one file = one anchor = one
  splice; a multi-file change is expressed as multiple work tasks.
- `goal` (required) — the intent behind the change. Keep this short — it's
  the "why", not the "where" (the anchor carries the "where") or a full
  spec of the exact new text.
- `change_type` (required) — one of `add`, `modify`, `delete` (see table
  below).
- `anchor` (required unless `change_type: "add"` with `new_file: true`) —
  where in `file` the change applies. See "Anchor types" below.
- `new_file` (optional, default `false`) — set `true` with `change_type: "add"`
  and no `anchor` to author a brand-new file from scratch. The runner errors
  if the file already exists.
- `acceptance_criteria` (required, list) — concrete conditions the result
  should satisfy. Included in the prompt as a checklist; not machine-verified.
- `context_files` (optional, list) — files the local model should read for
  reference but must not rewrite. Shown in full, read-only, regardless of
  what the model outputs for them.

### `change_type`

A rename is expressed as `modify` (replace a symbol's declaration span with
text that changes its identifier) — there's no separate `rename` value.
Renames that also require updating call sites elsewhere are out of scope for
a single-anchor/single-file task; express those as multiple `modify` tasks.

| `change_type` | anchor meaning | what the model sees | what the runner does with the fragment |
|---|---|---|---|
| `add` | An insertion point. `position: "before"\|"after"` (required for `symbol`/`marker` anchors) says where relative to the resolved anchor to insert. `file_start`/`file_end` insert at the start/end of the file. No `anchor` + `new_file: true` means author the whole file. | No current content (nothing exists there yet) — just goal, change_type, and a little surrounding context for orientation. `new_file` tasks see no file content at all. | Inserts the fragment at the resolved point. `new_file`: the fragment *is* the whole file. |
| `modify` | An existing span: a symbol's full body (incl. leading decorators), or a marker's `text`..`text_end` bounded lines. | The exact current span, labeled "replace this exactly," plus read-only context before/after. | Replaces the span with the fragment. |
| `delete` | Same span resolution as `modify`. | The exact current span (so the model can confirm what's being removed). | Model must return an **empty** fragment to confirm; a non-empty fragment is a parse error. The span is removed. |

### Anchor types

- **`symbol`** — `{"type": "symbol", "symbol_type": "function"|"class", "name": "...", "parent": "...(optional)"}`.
  Finds a `fun`/`def`/`function`/typed-C-family function declaration, or a
  `class` declaration, by name. `parent` scopes the search inside a named
  class (for a method that might not be unique at the top level). More than
  one match anywhere in the search range is a hard error — add `parent` or
  switch to a `marker` anchor instead of relying on the runner to guess.
- **`marker`** — `{"type": "marker", "text": "...", "text_end": "...(optional)", "occurrence": 1 (optional)}`.
  Matches a line by its exact stripped content. This is the general-purpose
  anchor: use it for precise multi-line spans inside a function, and for
  locations outside any named symbol (e.g. "below imports" — anchor on the
  exact text of the last `import` line rather than a special "structural"
  anchor type). If `text` matches more than one line, set `occurrence`
  (1-based) to disambiguate, or the runner errors out rather than guessing.
- **`file_start`** / **`file_end`** — no fields; only valid with
  `change_type: "add"`. Inserts at line 0 / end of file.

Anchor resolution is regex + brace/indentation heuristics, not a real
parser — it handles Python (indentation-based spans) and C-family/brace
languages including Kotlin (brace-depth spans that treat quoted strings and
comments as opaque, so string interpolation braces don't corrupt counting).
Ambiguous or unresolvable anchors fail loudly rather than guessing.

## Notes for whoever authors these (e.g. a cloud model at the planning stage)

- One batch = one directory = one init doc + N work docs.
- Read the real target file before authoring a work doc — the anchor's
  `text`/`name` must match something that actually exists in `repo_root`.
- Keep `goal` short; let `anchor` + `change_type` carry the "where" and
  "what kind of edit."
- A batch stops at the first work task that fails (parse error, anchor
  error, or splice error) — later tasks may assume earlier ones already
  landed, so don't rely on execution continuing past a failure.
