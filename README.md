# MVC Runner

A minimum viable context tool for reducing token usage in LLMs.

## Structure

```
MVC_Runner/
├── comparator.py            # Line-by-line file comparator
├── examples/                # Sample files for a quick first run (comparator.py)
│   ├── sample_a.txt
│   └── sample_b.txt
├── runner/                  # Local-model execution pipeline (Ollama)
│   ├── cli.py                # `python -m runner.cli run|test-adb|build`
│   ├── ollama_client.py       # HTTP client for a local Ollama server
│   ├── work_doc.py            # Code-edit batch schema (init doc + work docs)
│   ├── anchor.py / patch.py   # Anchor resolution + fragment splicing
│   ├── executor.py            # `run`: code-editing batch loop
│   ├── work_builder.py        # `build`: interactive work_docs/ batch wizard
│   ├── adb_task.py            # ADB-test batch schema (init doc + work docs)
│   ├── adb_client.py          # subprocess wrapper around the `adb` CLI
│   ├── ui_dump.py             # Parses `uiautomator dump` XML into elements
│   └── adb_agent.py           # `test-adb`: ADB UI-testing agent loop
├── work_docs/                # Example code-edit batch + schema + authoring prompt
├── work_docs_adb/            # Example ADB-test batch + schema + authoring prompt
├── organizer_work_docs/      # A real 6-task code-edit batch (not a toy example)
├── .claude/skills/adb-test/   # Claude Code skill: /adb-test
├── .claude/skills/code-edit/  # Claude Code skill: /code-edit
├── tests/                    # pytest suite (anchor resolution, work-doc validation)
├── logs/                     # JSON run logs (gitignored)
├── requirements.txt
├── pytest.ini
└── README.md
```

## Comparator

Compares two files line by line and reports where they differ.

```bash
# Run with the bundled example files
python comparator.py

# Or compare your own files
python comparator.py path/to/file_a.txt path/to/file_b.txt
```

Exit code is `0` if the files are identical, `1` if differences were found.

## `run` — local-model code editing

Applies small, precisely-located code edits (not whole-file rewrites) using
a local Ollama model, driven by a batch of JSON task files in `work_docs/`.
Each task names a `structure_type` (`function`/`class`/`method`/
`docstring`/`import`/`constant`/`block`) — this picks both where the edit
resolves to and how the model is prompted, so a docstring insertion asks
for *only* the docstring line (resolved automatically to the start of the
target's body) instead of handing the model a whole function to reproduce.

```bash
python -m runner.cli run --work-dir work_docs --model qwen3:4b
```

- Schema: `work_docs/schema.md` (see also `organizer_work_docs/` for a real,
  non-toy 6-task batch covering most structure_types).
- **Authoring a batch with an AI assistant** (Claude Code or otherwise):
  `work_docs/AUTHORING_PROMPT.md` is a copy-pasteable prompt that turns a
  plain-English change request into a valid batch — works with any chat AI.
  If you already know the exact final code for a task, put it in
  `exact_code` rather than describing it in prose — small/medium models
  reliably transcribe already-decided code but unreliably invent it from a
  description (especially one quoting a signature in backticks).
- **From Claude Code**: the `/code-edit` skill (`.claude/skills/code-edit/`)
  authors a batch from a plain-English change request, runs it, and reports
  results and a diff back — no manual copy/paste needed.
- **Authoring a batch interactively**: `python -m runner.cli build
  --work-dir work_docs` walks through one task at a time — prompts exactly
  the fields your chosen `structure_type`/`change_type` needs, validating
  each task immediately (same rules `run` enforces) instead of surfacing a
  mistake later as a run failure. Loops until you say you're done.
- Requires: a local Ollama server (`ollama serve`) with the target model
  pulled (`ollama pull qwen3:4b`). No Android/ADB tooling needed.
- `pytest` (from this directory) runs `tests/`, covering location-resolution
  edge cases (multi-line signatures, brace vs. indentation body bounds,
  marker `occurrence` disambiguation) and work-doc field validation.

## `test-adb` — ADB-driven Android UI testing

Drives a real Android device or emulator over ADB, using a local model to
read the on-screen `uiautomator` view-hierarchy and act (tap/swipe/type),
one action per turn, until it can tell whether a test scenario passes or
fails. Scenarios are defined in a batch of JSON task files in
`work_docs_adb/`.

```bash
python -m runner.cli test-adb --work-dir work_docs_adb --model qwen2.5:1.5b
```

- Schema: `work_docs_adb/schema.md`.
- **Authoring a batch with an AI assistant**:
  `work_docs_adb/AUTHORING_PROMPT.md` (same idea as above, for test
  scenarios instead of code edits).
- **From Claude Code**: the `/adb-test` skill (`.claude/skills/adb-test/`)
  authors a batch from a plain-English testing request, runs it, and
  reports pass/fail results back — no manual copy/paste needed.
- Requires, in addition to Ollama: **Android SDK Platform Tools** (`adb`)
  on PATH, and a device or emulator connected with USB debugging authorized
  (`adb devices` should show it in `device` state). `--dry-run` still reads
  the real device screen and asks the model for each action, but doesn't
  actually perform any tap/swipe/type on the device.

Every run of either command writes a JSON log to `logs/`.

## Running on small models

Both `run` and `test-adb` pass a JSON Schema to Ollama's `format` parameter
(`ollama_client.generate(..., format=...)`), which constrains decoding at
the grammar level — the model *cannot* emit invalid JSON or an out-of-enum
action/field, regardless of size. This is what makes small models usable at
all here; without it, small models frequently wrap their answer in
commentary or drift from the delimiter format the parser expects.

That said, schema-constrained output only fixes *shape*, not *judgment* or
*content quality* — tested here with `qwen2.5:1.5b`:
- `test-adb` picks one discrete action per turn from a 6-item enum, closer
  to classification than generation — this held up well at 1.5B: valid,
  schema-conforming actions referencing real on-screen elements. It won't
  always make the *right* choice (e.g. it may keep exploring instead of
  emitting `done` once the goal is already met), so treat `inconclusive`
  results as normal at this size, not a bug.
- `run` requires generating a correct code fragment — early testing hit two
  real failures at this size, both since fixed structurally rather than by
  prompting harder: (1) a docstring inserted as its own statement *outside*
  the function it documented instead of as the function's first statement —
  fixed by resolving a `docstring` task's insertion point to the target's
  body automatically (the runner decides *where*, not the model, so this
  class of placement error can't recur regardless of model size); (2) the
  model guessing the wrong indentation depth for an inserted fragment —
  fixed by computing the exact expected indent from the surrounding file
  and stating it explicitly in the prompt, instead of relying on the model
  to eyeball it from shown context. What's *not* structurally fixable is
  code-generation quality itself (an invented function body being
  incorrect, not just misplaced) — `exact_code` sidesteps this whenever the
  author already knows the answer; for genuinely-generative tasks, bigger
  models remain more reliable, and a "success" status only means the
  pipeline didn't error, never that the resulting code is correct. Review
  the diff regardless of model size.
