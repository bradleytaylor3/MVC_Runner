# Work document schema

A **batch** is a directory of JSON files, in either (or a mix) of two
shapes: the per-task style below (one **init doc** plus any number of
**work docs**), or a single **consolidated batch doc** — see
"Consolidated batch doc" further down — holding the same init doc and every
task inline in one file. Prefer the consolidated shape when authoring by
hand or via a chat model (see `AUTHORING_PROMPT.md`): same fields, same
validation, just one file/tool-call instead of N+1. Each work doc/task names
one small, well-defined edit —
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
  "acceptance_criteria": ["Concrete, checkable condition — required unless exact_code is set"],

  "name": "existing symbol name (modify/delete on function/class/method)",
  "parent": "containing class name (method only)",
  "target_name": "function or class this docstring belongs to (docstring only)",
  "start_anchor": "exact existing line of text to anchor on",
  "end_anchor": "exact existing line ending a multi-line span (optional)",
  "occurrence": "1-based index (optional, only if start_anchor's text matches more than one line)",

  "exact_code": "optional: the literal final code, when already fully decided",
  "pattern_example": "optional: a real sibling instance of this same pattern, for the model to follow",
  "contract": "optional: a declarative in/out spec the model's fragment must satisfy (see below) — the model chooses the implementation",
  "new_file": false,
  "context_files": ["optional/relative/path/for_reference_only.py"]
}
```

### Always required

- `id`, `title`, `file`, `structure_type`, `change_type`, `description`.
- `acceptance_criteria` (non-empty list) — required *unless* `exact_code` is
  set. When `exact_code` is set the model is never called, so criteria have
  nothing to constrain; they become optional reviewer notes (still fine to
  include if a human should double-check something specific).

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
here instead of relying on `description` prose. This skips the model
entirely: the runner reindents `exact_code` to fit the splice point and
writes it deterministically (see `runner.patch.reindent_to`), with no model
call at all. `description` stays required in all cases as the human-readable
"why," even when `exact_code` is set — but `acceptance_criteria` becomes
optional, since there's no model output left for it to constrain; keep
`description` to a short clause rather than a paragraph, since nothing reads
it besides a human skimming the diff. This matters because small/medium
local models turned out to be unreliable even at the mechanical job of
reindenting known-final code verbatim (a real 75-task batch: 0/75 succeeded
when the model was asked to just reindent `exact_code`, 75/75 once that step
was made deterministic instead) — so `exact_code` is for content that's
genuinely already decided, not a way to "help" the model with a hint.

### `pattern_example` (optional, mutually exclusive with `exact_code`)

When the exact code *isn't* decided yet, but a real sibling instance of the
same pattern already exists elsewhere in the file/codebase (e.g. the DAO
already has three `@Query` methods and this task adds a fourth), paste that
one sibling here. It's shown to the model as a concrete "follow this shape"
example alongside `description` — small local models are far more reliable
at extending a pattern they can see a real instance of than at inventing
structure from prose alone. Combine with `contract` where the shape fits it
(a checkable contract catches a wrong-but-plausible fragment that
`pattern_example` alone wouldn't). For the first instance of a new pattern,
or for anything requiring real judgment/business logic, either write
`exact_code` yourself or leave everything but `description` +
`acceptance_criteria` unset.

### `contract` (optional, mutually exclusive with `exact_code`)

An alternative to `exact_code` for tasks where the final code isn't
decided, but the *effect* is fully specified — a UML-flavored operation
signature (name, params, return type, an annotation/`stereotype`, plus
behavioral `cases` where the language is executable) that the model's
fragment is checked against (`runner/contract_check.py`) after the usual
shape/bleed checks, before it's ever spliced in. A failure is retried like
any other `validation_error`, with the mismatch fed back to the model. The
model is free to choose names, formulas, exact SQL formatting — anything
not pinned by the contract — as long as the required effect holds; this is
the main lever for *not* writing `exact_code` by hand without falling back
to unverified generation. Only two shapes are wired up so far
(`bench/fixtures/*/tasks.json` are the reference examples):

- Python `{"kind": "function", "name": "...", "cases": [{"args": [...], "expect": ..., "rel_tol": 1e-4}]}`
  or `{"kind": "constant", "name": "...", "expect": ..., "rel_tol": 1e-4}` — runs the fragment in a
  subprocess and checks the real behavior/value.
- Kotlin `{"stereotype": "Insert"|"Delete"|"Update"|"Query", "name": "...", "params": [{"type": "..."}], "return_type": "..."|null, "suspend": true|false, "sql": {"verb": "...", "table": "...", "aggregate": "...", "where_param": "..."}}`
  (`sql` only for `stereotype: "Query"`) — checks the annotation/SQL/signature *shape*, not real execution (no Room/DB
  involved), so it can't catch a subtly wrong `WHERE` clause the regex shape still matches.

If a task's shape doesn't fit either of these, `contract` isn't usable yet
— fall back to `exact_code` (if the code is decided) or `pattern_example`
(if it isn't). Benchmarked so far only on small, boilerplate-shaped fixtures
(`bench/README.md`'s "Functional scoring" and "Rechecking smaller models"
sections) — real batches should still have someone skim the diff, same as
any non-`exact_code` task.

### `context_files` (optional, list)

Files the model should read for reference but must never rewrite — shown
in full, read-only, regardless of what the model outputs for them.

## Consolidated batch doc

A single file that holds an init doc plus every task inline, instead of
spreading them across N+1 files:

```json
{
  "kind": "batch",
  "init": {
    "batch_id": "example-python-demo",
    "repo_root": ".",
    "language": "python",
    "conventions": []
  },
  "tasks": [
    { "id": "task-001", "title": "...", "file": "...", "structure_type": "...", "change_type": "...", "description": "...", "..." : "..." },
    { "id": "task-002", "...": "..." }
  ]
}
```

- `init` is the same object as a standalone init doc's fields (`kind` omitted
  — it's implied).
- Each entry in `tasks` is the same object as a standalone work doc (`kind`
  omitted — implied to be `"work"`), validated identically, field for field.
- `tasks` must be a non-empty list.
- A directory can hold exactly one init doc total, whether it comes from a
  standalone `kind: "init"` file or a `"batch"` doc's inline `init` — mixing
  a `"batch"` doc with standalone `kind: "work"` files (which don't carry
  their own init) is fine; mixing two sources of `init` is not.
- This is purely a loading-time convenience — `runner.work_doc.load_batch`
  expands a `"batch"` doc into the exact same `InitTask`/`WorkTask` objects
  a per-task-file batch produces. Nothing downstream (anchor resolution,
  prompting, splicing) knows or cares which shape a task came from.

## Notes for whoever authors these (e.g. a cloud model at the planning stage)

- One batch = one directory = one init doc + N tasks (spread across files,
  or inlined in one `"batch"` doc, or both). Unlike a batch of independent
  test scenarios, code-edit batches are sequential — the batch stops at the
  first task that fails, and later tasks may assume earlier ones already
  landed (e.g. a `docstring` task with `target_name: "foo"` assumes an
  earlier task already added `foo`).
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
  If the code isn't decided but the task's effect fits one of `contract`'s
  supported shapes (see above), prefer `contract` over hand-writing
  `exact_code` — it costs fewer authoring tokens (a compact spec instead of
  full source) and the runner verifies the effect before ever writing the
  file, instead of just trusting generation unreviewed.
- Keep `description` focused on intent even when `exact_code` is absent;
  let `structure_type`/`change_type`/the location fields carry the "where"
  and "what kind of edit."
