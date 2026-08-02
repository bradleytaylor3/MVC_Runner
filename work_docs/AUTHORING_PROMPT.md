# Authoring code-edit batches with any AI assistant

This tool (`run`, part of MVC_Runner) applies small, precisely-located code
edits using a local model, given a batch of JSON task files that say
exactly *what kind* of edit it is (`structure_type`), *where* it goes, and
*what* it should do. Someone still has to author that batch — that's what
this prompt is for.

You don't need Claude Code for this part. Copy everything inside the box
below into any AI chat (Claude, ChatGPT, Gemini, etc.) along with the
relevant source file(s), answer its questions, and it will hand back one or
more file blocks. Save each one into `work_docs/` in this repo, using the
filename given in its `===FILE: ...===` marker. Then run:

```
python -m runner.cli run --work-dir work_docs --model qwen3:4b
```

---

```
You are helping me author a batch of JSON task files for an automated code-editing tool. It splices small, precisely-located fragments into real files — it does not rewrite whole files — so every task must pin down an exact location and a specific kind of edit. Ask me what you need, then produce the files.

## What you need to know from me
Ask, if I haven't already told you:
1. The absolute (or repo-relative) path to the repo these edits apply to, and the primary language (e.g. python, kotlin, typescript).
2. What should change, in plain English, and in which file(s). Ask me to paste the actual current content of any file being edited if I haven't already — you must anchor each task on text/names that really exist, not on what you assume is there.
3. If you already know the exact final code for a task (not just its intent), ask for or write out that code — it goes in `exact_code`, not just a description (see below for why).
4. Whether there are any project conventions to fold in (indentation style, naming, etc.) — optional.

## Output format
Produce one JSON file per edit, plus exactly one init file, using this exact block format for each — a marker line, then the file content, then an end marker, with nothing else before/between/after:

===FILE: work_docs/<filename>.json===
<the complete JSON file content>
===END FILE===

### Init file (exactly one, name it work_docs/000-init.json)
{
  "kind": "init",
  "batch_id": "<short-label-for-this-batch>",
  "repo_root": "<path to the repo, from step 1>",
  "language": "<primary language, from step 1>",
  "conventions": []
}

### One work file per edit (name each work_docs/task-NNN-<slug>.json)
Every work file needs: id, title, file, structure_type, change_type, description, acceptance_criteria — plus whichever location field(s) that structure_type/change_type combination requires, per this table:

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

Optional on any work file: exact_code (see below), context_files (files to show read-only for reference, never rewritten), new_file: true (change_type "add" only — skips all location fields, authors a brand-new file from scratch), occurrence: N (1-based — only needed if start_anchor's exact text matches more than one line in the file; otherwise resolution fails loudly rather than guessing which one you meant).

## exact_code — use this whenever you already know the final code
If you already know the exact code a task should produce, put it in exact_code and let description stay a short "why." Do NOT paraphrase known code into description and hope the executing model reconstructs it — this skips the model entirely (the runner reindents exact_code deterministically), and that's a feature: a benchmark against real local models (qwen2.5:1.5b, gemma3:4b, qwen3:4b — see runner/bench.py) found roughly 0-15% exact-match on small, well-scoped generation tasks, even with a concrete pattern_example (below). Only omit exact_code when the exact code genuinely isn't decided yet and you're comfortable with the model's output being unreliable and needing review.

## pattern_example — optional, low-confidence help for genuinely-undecided tasks
If exact_code truly isn't decided yet but a real sibling instance of the same pattern already exists in the file (e.g. the DAO already has three @Query methods and this task adds a fourth), you can paste that sibling into pattern_example as a concrete few-shot example. Don't expect this to make the model reliable, though — it measurably didn't in testing. Only reach for it on low-stakes tasks a human will review anyway, and mutually exclusive with exact_code (pointless together, since exact_code skips the model that would see it).

## Repetitive batches — consider scaffold instead of hand-authoring
If you're about to author several work docs that are all the same shape and differ only in a name/value/query (e.g. a DAO method per query, an entity per field), write a scaffold spec instead: one shared task_template plus a short list of per-item variables, expanded mechanically by `python -m runner.cli scaffold <spec.json> --work-dir work_docs` — no model call, and far less to retype than N full task files. See `scaffold_examples/profile_dao_methods.json` for a worked example.

## Rules for good tasks
- Prefer the most specific structure_type that fits (docstring over block for a docstring, constant over block for a new variable) — the more specific the type, the more targeted the framing the executing model gets, and the smaller the blast radius of what it's allowed to touch. Reach for block only when nothing else fits.
- One work file = one file = one edit. A change touching multiple files or multiple unrelated locations in one file is several work files, not one.
- Only reference names/anchor text you've actually confirmed exist in the current file content (or that an earlier task in this same batch already adds) — if you're guessing, ask me to paste the file first.
- The batch runner stops at the first task that fails to resolve/apply, and later tasks may assume earlier ones already landed — order tasks so each one's location still exists given every earlier task in the batch has already been applied.
- acceptance_criteria should be concrete and checkable, even though they aren't machine-verified — they're a checklist included in the model's prompt, and a human should still skim the resulting diff against them.

## Worked example
Request: "In example_target.py (currently just a one-line comment), add a greet(name) function, then give it a one-line docstring."

===FILE: work_docs/000-init.json===
{
  "kind": "init",
  "batch_id": "example-python-demo",
  "repo_root": ".",
  "language": "python",
  "conventions": []
}
===END FILE===

===FILE: work_docs/task-001-add-greet.json===
{
  "kind": "work",
  "id": "task-001",
  "title": "Add a greet function",
  "file": "work_docs/example_target.py",
  "structure_type": "function",
  "change_type": "add",
  "start_anchor": "# Starter file for work_docs/example_task.json — safe to overwrite/experiment with.",
  "exact_code": "def greet(name: str) -> str:\n    return f'Hello, {name}!'",
  "description": "a function that returns a greeting for the given name",
  "acceptance_criteria": [
    "The file defines a function named greet taking one argument, name",
    "greet('World') returns 'Hello, World!'"
  ]
}
===END FILE===

===FILE: work_docs/task-002-docstring-greet.json===
{
  "kind": "work",
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
===END FILE===

Note task-002 needs no anchor text at all — target_name alone is enough for a docstring, and the runner inserts it at the correct place (the first line of greet's body) automatically.

Now ask me anything you still need, then produce the batch.
```
