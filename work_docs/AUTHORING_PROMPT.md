# Authoring code-edit batches with any AI assistant

This tool (`run`, part of MVC_Runner) applies small, precisely-located code
edits using a local model, given a batch of JSON task files that say
exactly *what kind* of edit it is (`structure_type`), *where* it goes, and
*what* it should do. Someone still has to author that batch — that's what
this prompt is for.

You don't need Claude Code for this part. Copy everything inside the box
below into any AI chat (Claude, ChatGPT, Gemini, etc.) along with the
relevant source file(s), answer its questions, and it will hand back a
single file block. Save it into `work_docs/` in this repo, using the
filename given in its `===FILE: ...===` marker. Then run:

```
python -m runner.cli run --work-dir work_docs --model gemma4:12b
```

---

```
You are helping me author a batch of JSON task files for an automated code-editing tool. It splices small, precisely-located fragments into real files — it does not rewrite whole files — so every task must pin down an exact location and a specific kind of edit. Ask me what you need, then produce the files.

## What you need to know from me
Ask, if I haven't already told you:
1. The absolute (or repo-relative) path to the repo these edits apply to, and the primary language (e.g. python, kotlin, typescript).
2. What should change, in plain English, and in which file(s). Ask me to paste the actual current content of any file being edited if I haven't already — you must anchor each task on text/names that really exist, not on what you assume is there.
3. If you already know the exact final code for a task (not just its intent), ask for or write out that code — it goes in `exact_code`, not just a description (see below for why). If you don't know the exact code but the task's *effect* is fully pinned down (a function/method with clear inputs, outputs, and — for Kotlin — an annotation/SQL shape), consider `contract` instead (see below) — it costs fewer tokens to write than `exact_code` and the runner still verifies the effect before writing anything.
4. Whether there are any project conventions to fold in (indentation style, naming, etc.) — optional.

## Output format
Produce exactly one file, using this exact block format — a marker line, then the file content, then an end marker, with nothing else before/between/after:

===FILE: work_docs/batch.json===
<the complete JSON file content>
===END FILE===

Its content is one JSON object holding the init doc plus every task inline — this is the entire point of this format: one file/tool-call instead of N+1, no repeated wrapper boilerplate per task.

{
  "kind": "batch",
  "init": {
    "batch_id": "<short-label-for-this-batch>",
    "repo_root": "<path to the repo, from step 1>",
    "language": "<primary language, from step 1>",
    "conventions": []
  },
  "tasks": [
    { "id": "task-001", "title": "...", ... },
    { "id": "task-002", "title": "...", ... }
  ]
}

Each entry in `tasks` is one edit — you don't need `"kind": "work"` on them, it's implied by being inside `tasks`. Every task needs: id, title, file, structure_type, change_type, description — plus whichever location field(s) that structure_type/change_type combination requires, per this table:

| structure_type | change_type | required location field(s) |
|---|---|---|
| function / class | add | start_anchor (exact existing line to insert after) |
| function / class | modify / delete | name (the existing symbol's name — its full span is found automatically, you don't need line numbers) |
| method | add | parent (class name); start_anchor optional (omit it to just append at the end of the class) |
| method | modify / delete | name + parent |
| docstring | add (only) | target_name (the function or class it documents) — no anchor text at all, it's inserted as that symbol's first statement automatically |
| import | add | start_anchor |
| import | modify | start_anchor (the one line being replaced) |
| constant | add | start_anchor |
| constant | modify / delete | start_anchor (+ end_anchor if it spans multiple lines) |
| block | modify / delete | start_anchor (+ end_anchor if it spans multiple lines) — use this only when nothing more specific fits |

acceptance_criteria (non-empty list of concrete, checkable conditions) is required *unless* exact_code is set (below) — with exact_code the model is never called, so criteria have nothing to constrain and become optional reviewer notes.

Optional on any task: exact_code (see below), contract (see below), context_files (files to show read-only for reference, never rewritten), new_file: true (change_type "add" only — skips all location fields, authors a brand-new file from scratch), occurrence: N (1-based — only needed if start_anchor's exact text matches more than one line in the file; otherwise resolution fails loudly rather than guessing which one you meant).

(If you'd rather produce the older one-file-per-task layout instead — a `work_docs/000-init.json` with `"kind": "init"`, plus one `work_docs/task-NNN-<slug>.json` per task with `"kind": "work"` — the runner still accepts that too. The single-file `"batch"` shape above is just less to type/emit for the same result, so prefer it unless you have a specific reason not to.)

## exact_code — use this whenever you already know the final code
If you already know the exact code a task should produce, put it in exact_code and let description stay a short "why." Do NOT paraphrase known code into description and hope the executing model reconstructs it — this skips the model entirely (the runner reindents exact_code deterministically). This remains the right call whenever the code is genuinely already decided, or its shape doesn't fit `contract` below (see runner/bench.py and bench/README.md for why local generation isn't trusted unverified: literal-text matching against a benchmark found roughly 0-15% exact-match at ≤4B and 55% even for the best model tested, gemma4:12b — though a later pass found a meaningful chunk of that gap was reworded-but-correct output being scored wrong, not real errors; see `contract` below for the fix and its own, narrower limits).

Since nothing (model or runner) reads description or acceptance_criteria for an exact_code task — only a human skimming the diff later might — keep description to one short clause and skip acceptance_criteria entirely unless there's something specific worth flagging for review. Don't spend effort restating what exact_code already shows unambiguously.

## contract — verified generation for tasks whose effect (not code) is decided
When the code isn't decided but the task's *effect* is fully specified, `contract` lets the model choose the implementation while the runner verifies it actually did the right thing before ever writing the file (a failed check is retried with feedback, like any other validation failure) — cheaper to author than `exact_code` since you write a compact spec instead of full source, and safer than unverified generation. Mutually exclusive with `exact_code` (pointless together — `exact_code` skips the model that would see the contract). Only two shapes are wired up so far:

- Python: `{"kind": "function", "name": "...", "cases": [{"args": [...], "expect": ...}]}` (runs it, calls it, checks the real return value) or `{"kind": "constant", "name": "...", "expect": ...}`.
- Kotlin (Room-style DAO methods): `{"stereotype": "Insert"|"Delete"|"Update"|"Query", "name": "...", "params": [{"type": "..."}], "return_type": "..."|null, "suspend": true|false, "sql": {"verb": "...", "table": "...", "aggregate": "...", "where_param": "..."}}` — this one checks annotation/SQL/signature shape, not real DB execution, so it's a weaker guarantee than the Python case.

If the task doesn't fit either shape, don't force it — fall back to `exact_code` or `pattern_example`. See `work_docs/schema.md`'s `contract` section for the full field reference.

## pattern_example — optional, low-confidence help for genuinely-undecided tasks
If exact_code truly isn't decided yet but a real sibling instance of the same pattern already exists in the file (e.g. the DAO already has three @Query methods and this task adds a fourth), you can paste that sibling into pattern_example as a concrete few-shot example. Don't expect this alone to make the model reliable — it measurably didn't in testing; pair it with `contract` where the task's shape supports one, so a wrong-but-plausible fragment gets caught rather than just discouraged. Mutually exclusive with exact_code (pointless together, since exact_code skips the model that would see it).

## Repetitive batches — consider scaffold instead of hand-authoring
If you're about to author several work docs that are all the same shape and differ only in a name/value/query (e.g. a DAO method per query, an entity per field), write a scaffold spec instead: one shared task_template plus a short list of per-item variables, expanded mechanically by `python -m runner.cli scaffold <spec.json> --work-dir work_docs` — no model call, and far less to retype than N full task files. See `scaffold_examples/profile_dao_methods.json` for a worked example.

## Rules for good tasks
- Prefer the most specific structure_type that fits (docstring over block for a docstring, constant over block for a new variable) — the more specific the type, the more targeted the framing the executing model gets, and the smaller the blast radius of what it's allowed to touch. Reach for block only when nothing else fits.
- One task = one edit. A change touching multiple files or multiple unrelated locations in one file is several entries in `tasks`, not one.
- Only reference names/anchor text you've actually confirmed exist in the current file content (or that an earlier task in this same batch already adds) — if you're guessing, ask me to paste the file first.
- The batch runner stops at the first task that fails to resolve/apply, and later tasks may assume earlier ones already landed — order tasks so each one's location still exists given every earlier task in the batch has already been applied.
- When exact_code is absent, acceptance_criteria should be concrete and checkable — they're a checklist included in the model's prompt, and a human should still skim the resulting diff against them. When exact_code is set, skip acceptance_criteria unless there's something specific worth flagging for review — the model never sees it, so it's not pulling its usual weight.
- If you're about to write out exact_code for a task whose effect fits one of `contract`'s supported shapes, prefer `contract` — same reliability guarantee (the runner verifies it, not a human trusting the model), fewer tokens spent authoring it.

## Worked example
Request: "In example_target.py (currently just a one-line comment), add a greet(name) function, then give it a one-line docstring."

===FILE: work_docs/batch.json===
{
  "kind": "batch",
  "init": {
    "batch_id": "example-python-demo",
    "repo_root": ".",
    "language": "python",
    "conventions": []
  },
  "tasks": [
    {
      "id": "task-001",
      "title": "Add a greet function",
      "file": "work_docs/example_target.py",
      "structure_type": "function",
      "change_type": "add",
      "start_anchor": "# Starter file for work_docs/example_task.json — safe to overwrite/experiment with.",
      "exact_code": "def greet(name: str) -> str:\n    return f'Hello, {name}!'",
      "description": "returns a greeting for the given name"
    },
    {
      "id": "task-002",
      "title": "Add a docstring to greet",
      "file": "work_docs/example_target.py",
      "structure_type": "docstring",
      "change_type": "add",
      "target_name": "greet",
      "description": "explains that it returns a greeting for the given name",
      "acceptance_criteria": [
        "greet has a one-line docstring as its first statement"
      ]
    }
  ]
}
===END FILE===

Note task-002 needs no anchor text at all — target_name alone is enough for a docstring, and the runner inserts it at the correct place (the first line of greet's body) automatically. Also note task-001 has no acceptance_criteria (exact_code is set, so nothing reads it) while task-002 does (no exact_code, so the model needs a checklist). Neither task repeats `"kind": "work"` — that's implied by living inside `tasks`.

Now ask me anything you still need, then produce the batch.
```
