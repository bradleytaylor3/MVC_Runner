---
name: adb-test
description: Author and run an ADB-driven Android UI test batch against a connected device/emulator, then report pass/fail results. Use when the user wants to test an Android app's UI automatically (e.g. "test that creating a task awards gold", "run the login flow and check it works").
---

# ADB test

You are the authoring/reporting layer for MVC_Runner's `test-adb` command; a
local Ollama model is the execution layer that actually looks at the device
screen and acts. The schema and authoring rules you follow live in
`work_docs_adb/AUTHORING_PROMPT.md` in this repo — read that file now if you
haven't already this session, and use it as the single source of truth for
the batch format. Don't re-derive or duplicate the schema here; if this
skill and that file ever disagree, `AUTHORING_PROMPT.md` wins (update it,
not your own paraphrase of it).

## Steps

1. **Understand the request.** Get from the user (or infer from context: a
   README, TESTING.md, or similar doc in their target repo if they point you
   at one): the target app's package name, and what to test. If the package
   name isn't known, run `adb shell pm list packages | grep <hint>` to find
   candidates and confirm with the user rather than guessing.

2. **Check for a device before doing anything else.** Run `adb devices`. If
   nothing shows up in `device` state, stop and tell the user what's wrong
   (adb not installed / no device connected / unauthorized) rather than
   authoring tasks nobody can run yet.

3. **Author the batch** in `work_docs_adb/` following
   `work_docs_adb/AUTHORING_PROMPT.md`: exactly one `000-init.json`
   (`kind: "init"`) with the shared package/device defaults, plus one
   `kind: "work"` file per test scenario. Keep each scenario narrow — one
   checkable outcome per file, per that document's "Rules for good
   scenarios" section. If `work_docs_adb/` already has files from a
   previous run, ask before overwriting rather than assuming they're stale.

4. **Run it**: `python -m runner.cli test-adb --work-dir work_docs_adb --model qwen2.5:1.5b`
   (add `--device <serial>` if multiple devices are connected and the user
   cares which one).

5. **Report results**, not raw JSON. Read the log written to `logs/`, and
   for each scenario give a one-line verdict (pass/fail/inconclusive/error).
   For anything other than `pass`, pull the relevant `reason` and the last
   few `steps` out of the log and explain in plain language what happened
   and where — don't just dump the log file at the user.

6. **Triage before re-running the whole batch.** If a scenario's failure
   looks like an authoring problem (ambiguous `goal`, wrong package/
   activity, acceptance criteria that reference state not visible on
   screen) rather than a real app bug, say so, offer to revise just that
   one task file, and rerun only that scenario — not the whole batch —
   unless the user wants everything rerun.
