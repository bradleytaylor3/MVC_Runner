---
name: code-edit
description: Author and run a batch of small, precisely-located code edits against a real file using a local model, then report the results. Use when the user wants to make a specific, well-defined code change (e.g. "add a validate_email function to utils.py", "add a docstring to the Foo class") via MVC_Runner instead of editing the file directly.
---

# Code edit

You are the authoring/reporting layer for MVC_Runner's `run` command; a
local Ollama model is the execution layer that actually writes each
fragment. The schema and authoring rules you follow live in
`work_docs/AUTHORING_PROMPT.md` in this repo (see also `work_docs/schema.md`
for the full reference and `organizer_work_docs/` for a real, non-toy
example) — read `AUTHORING_PROMPT.md` now if you haven't already this
session, and use it as the single source of truth for the batch format.
Don't re-derive or duplicate the schema here; if this skill and that file
ever disagree, `AUTHORING_PROMPT.md` wins (update it, not your own
paraphrase of it).

## Steps

1. **Understand the request.** Get from the user (or infer from context)
   exactly what should change and in which file(s), and the repo root the
   edits apply to. Read the actual current content of every file being
   edited yourself — don't ask the user to paste it, and don't guess at
   anchor text or symbol names. If you already know the exact final code
   for a task, use it (`exact_code`) rather than describing it in prose —
   `AUTHORING_PROMPT.md` explains why.

2. **Check the target repo is clean before doing anything else.** Run
   `git status --porcelain` in the target repo root. If it's dirty, tell
   the user and ask whether to proceed with `--allow-dirty` or have them
   commit/stash first — `run` refuses a dirty worktree by default so a bad
   batch is easy to `git checkout --` away from, and that safety net is
   gone if you push past it without asking.

3. **Author the batch** in `work_docs/` following
   `work_docs/AUTHORING_PROMPT.md`: exactly one `000-init.json`
   (`kind: "init"`) plus one `kind: "work"` file per edit, ordered so each
   task's location still exists given every earlier task in the batch has
   already applied. If `work_docs/` already has files from a previous
   batch, ask before overwriting rather than assuming they're stale.

4. **Run it**: `python -m runner.cli run --work-dir work_docs --model qwen3:4b`
   (add `--repo-root` if it needs to differ from the init doc's,
   `--allow-dirty` if step 2 was resolved that way, `--dry-run` if the user
   wants to preview without writing files).

5. **Report results**, not raw JSON. Read the log written to `logs/`, and
   give a one-line verdict per task (`success` / `error` / `parse_error` /
   `splice_error` / `anchor_error` / `skipped`). For anything other than
   `success`, pull the `error` field (and, for `parse_error`/`splice_error`,
   open `raw_response_path` if you need to see what the model actually
   produced) and explain in plain language what happened, referencing the
   task id.

6. **Show the diff.** A `success` status only means the pipeline didn't
   error — never that the generated code is correct. Every non-dry-run
   success wrote to a real file, so run `git diff` in the target repo
   (scoped to the touched files) and walk the user through it against each
   task's `acceptance_criteria` rather than just declaring success. This is
   the one step adb-test doesn't need — a UI test's pass/fail already *is*
   the verdict, but a code edit's isn't.

7. **Triage a failed task before re-running the whole batch.** If a task's
   failure looks like an authoring problem (anchor text that doesn't
   actually match the file, wrong `structure_type`, a `name`/`target_name`
   that doesn't exist) rather than a model-quality problem, fix just that
   task file and re-run. The batch stops at the first failure: nothing
   after it has been attempted yet, and nothing before it needs re-running
   either — those tasks already applied to the real file, so replaying them
   would double-apply.
