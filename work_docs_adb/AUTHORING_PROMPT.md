# Authoring ADB test batches with any AI assistant

This tool (`test-adb`, part of MVC_Runner) runs a batch of UI test scenarios
against a real Android device/emulator, using a small local model to look at
the screen and act. Someone still has to author the batch of JSON task
files describing *what* to test — that's what this prompt is for.

You don't need Claude Code for this part. Copy everything inside the box
below into any AI chat (Claude, ChatGPT, Gemini, etc.), answer its
questions, and it will hand back one or more file blocks. Save each one
into `work_docs_adb/` in this repo, using the filename given in its
`===FILE: ...===` marker. Then run:

```
python -m runner.cli test-adb --work-dir work_docs_adb --model qwen2.5:1.5b
```

---

```
You are helping me author a batch of JSON task files for an automated Android UI testing tool. Ask me what you need, then produce the files.

## What you need to know from me
Ask, if I haven't already told you:
1. What app am I testing? (An Android package name like `com.example.app`. If I don't know it, tell me to run `adb shell pm list packages | grep <hint>` on my machine and paste back the result.)
2. What should be tested? One or more scenarios in plain English (e.g. "creating a task with each difficulty awards the right amount of gold").
3. (Optional) Does a specific scenario need a particular starting screen/activity, or should it just launch normally?

## Output format
Produce one JSON file per scenario, plus exactly one init file, using this exact block format for each — a marker line, then the file content, then an end marker, with nothing else before/between/after:

===FILE: work_docs_adb/<filename>.json===
<the complete JSON file content>
===END FILE===

### Init file (exactly one, name it work_docs_adb/000-init.json)
{
  "kind": "init",
  "batch_id": "<short-label-for-this-batch>",
  "default_package": "<the package name from step 1>",
  "default_max_steps": 25
}

### One work file per scenario (name each work_docs_adb/task-NNN-<slug>.json)
{
  "kind": "work",
  "id": "task-NNN",
  "title": "<short human-readable description>",
  "goal": "<plain-English description of what to do and what a passing outcome looks like>",
  "acceptance_criteria": [
    "<a concrete, on-screen-checkable condition>"
  ]
}

Optional fields on a work file, only include if relevant: "package" (overrides the init doc's default_package for just this task), "activity" (a specific activity to launch instead of the app's default), "max_steps" (overrides default_max_steps), "reset_app": false (only if this scenario should deliberately continue from wherever a previous scenario in the batch left the app, instead of a fresh launch).

## Rules for good scenarios
- One scenario = one narrow, checkable outcome. If a request describes several things to verify (e.g. "test the whole settings screen"), split it into several separate work files rather than one broad one — a small local model executing this loses track of broad, multi-part goals.
- `goal` is the entire brief the executing model gets — it can't see this conversation, only this JSON and what's currently on the device screen. Be concrete about the starting point and what "done, and it passed" looks like.
- `acceptance_criteria` must be things visible on screen (a label's text, an element appearing/disappearing) — not internal app state nothing on screen reflects.
- Task `id`s should be unique and sort in a sensible execution order (task-001, task-002, ...).

## Worked example
Request: "Test that the calculator adds two numbers correctly."

===FILE: work_docs_adb/000-init.json===
{
  "kind": "init",
  "batch_id": "calculator-example",
  "default_package": "com.google.android.calculator",
  "default_max_steps": 20
}
===END FILE===

===FILE: work_docs_adb/task-001-add-two-numbers.json===
{
  "kind": "work",
  "id": "task-001",
  "title": "Adding 2 + 2 shows 4",
  "goal": "Open the Calculator app and confirm that entering 2 + 2 produces a result of 4.",
  "acceptance_criteria": [
    "After tapping 2, +, 2, and equals (or the result updates live), the on-screen result reads 4"
  ]
}
===END FILE===

Now ask me anything you still need, then produce the batch.
```
