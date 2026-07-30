# ADB test batch schema

A **batch** is a directory of JSON files: exactly one **init doc** plus any
number of **work docs**. Each work doc is one independent UI test scenario.
`test-adb` launches the target app, then repeatedly dumps the on-screen
`uiautomator` view-hierarchy, asks the local model for exactly one action
(tap/swipe/type/key/wait), executes it over ADB, and repeats until the model
declares the scenario `pass`/`fail` or a step limit is hit.

Not sure how to write these by hand? See `AUTHORING_PROMPT.md` in this same
directory — a copy-pasteable prompt for turning a plain-English testing
request into a valid batch, usable with any chat AI (Claude, ChatGPT,
Gemini, ...), not just Claude Code.

## Init doc

Exactly one file per batch must have `"kind": "init"`. Convention: name it
`000-init.json` so it sorts first, though the runner finds it by `kind`, not
filename.

```json
{
  "kind": "init",
  "batch_id": "calculator-example",
  "default_package": "com.google.android.calculator",
  "default_serial": null,
  "default_max_steps": 20
}
```

- `batch_id` (required) — a label for this batch, carried into the run log.
- `default_package` (optional) — Android package name used for any work doc
  that doesn't set its own `package`. At least one of the two must be
  present per task (validated at load time).
- `default_serial` (optional) — adb device serial to target if more than
  one device is connected; overridden by `--device` on the CLI. If omitted
  and exactly one device is connected, that device is used automatically.
- `default_max_steps` (optional, default `25`) — step budget for any work
  doc that doesn't set its own `max_steps`. Applies per scenario, not to the
  whole batch.

## Work doc

```json
{
  "kind": "work",
  "id": "task-001",
  "title": "Short human-readable description",
  "goal": "Plain-English description of the scenario to test and what a passing outcome looks like.",
  "acceptance_criteria": [
    "Concrete, checkable condition the model can verify from on-screen elements"
  ],
  "package": "optional.override.package",
  "activity": "optional/.MainActivity",
  "max_steps": 20,
  "reset_app": true
}
```

### Fields

- `id` (required) — unique task identifier; also used to sort execution order.
- `title` (required) — short description, shown in CLI progress output.
- `goal` (required) — what to do and what success looks like. This is the
  entire brief the model gets for the scenario — be concrete about the
  starting point and the expected end state, since the model has no access
  to the wider conversation, only this document plus what it sees on screen.
- `acceptance_criteria` (required, non-empty list) — concrete, on-screen-
  checkable conditions. These aren't machine-verified against the device
  independently — the model itself judges them from the current element
  list before emitting `done` — so phrase them as things that are visible
  in the UI (a label's text, an element being present/absent), not internal
  app state the model can't see.
- `package` (optional) — overrides `default_package` for this task.
- `activity` (optional) — a specific activity to launch (e.g. `.MainActivity`
  or a fully-qualified `com.foo/.bar.Activity`). If omitted, the app's
  default launcher activity is started instead.
- `max_steps` (optional) — overrides `default_max_steps` for this task.
  A scenario that hits this limit without the model emitting `done` is
  logged as `inconclusive`, not `fail` — that usually means the goal was
  ambiguous or the step budget was too small, not that the app is broken.
- `reset_app` (optional, default `true`) — force-stop the app before
  launching it for this task, so each scenario starts from a clean process.
  Set `false` for a scenario that intentionally continues from wherever a
  previous task in the batch left the app.

## Notes for whoever authors these (e.g. a cloud model at the planning stage)

- One batch = one directory = one init doc + N independent work docs. Unlike
  `work_docs/` (code edits, which stop the batch at the first failure),
  every work doc here runs regardless of earlier scenarios' verdicts —
  they're independent tests, not sequential edits.
- Keep one scenario per work doc. A goal like "test the whole settings
  screen" is too broad for a small local model to track reliably in one
  run — split it into several narrow, single-outcome scenarios instead.
- If you don't know the target app's exact package name, `adb shell pm
  list packages | grep <hint>` will find it.
- Logs land in `logs/<timestamp>.json` with a per-scenario `status`
  (`pass`/`fail`/`inconclusive`/`error`) and the full step-by-step action
  history — read a failing scenario's `steps` and `reason` before assuming
  it's a real app bug rather than an ambiguous `goal`.
