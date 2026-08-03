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
│   ├── cli.py                # `python -m runner.cli run|test-adb|build|scaffold|bench`
│   ├── ollama_client.py       # HTTP client for a local Ollama server
│   ├── work_doc.py            # Code-edit batch schema (init doc + work docs)
│   ├── anchor.py / patch.py   # Anchor resolution + fragment splicing
│   ├── executor.py            # `run`: code-editing batch loop
│   ├── work_builder.py        # `build`: interactive work_docs/ batch wizard
│   ├── scaffold.py            # `scaffold`: expand a {{template}} + item list into a batch, no model call
│   ├── bench.py                # `bench`: score local models on known-answer fragments
│   ├── adb_task.py            # ADB-test batch schema (init doc + work docs)
│   ├── adb_client.py          # subprocess wrapper around the `adb` CLI
│   ├── ui_dump.py             # Parses `uiautomator dump` XML into elements
│   └── adb_agent.py           # `test-adb`: ADB UI-testing agent loop
├── work_docs/                # Example code-edit batch + schema + authoring prompt
├── work_docs_adb/            # Example ADB-test batch + schema + authoring prompt
├── organizer_work_docs/      # A real 6-task code-edit batch (not a toy example)
├── medtimingtracker_*_work_docs/  # 12 real batches, 36 tasks, from an actual
│                                    Android/Kotlin build -- see below
├── scaffold_examples/        # Example scaffold spec (see `scaffold` below)
├── bench/fixtures/            # Known-answer fixtures for `bench` (see below)
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
python -m runner.cli run --work-dir work_docs --model gemma4:12b
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
  pulled (`ollama pull gemma4:12b`). No Android/ADB tooling needed.
- `pytest` (from this directory) runs `tests/`, covering location-resolution
  edge cases (multi-line signatures, brace vs. indentation body bounds,
  marker `occurrence` disambiguation) and work-doc field validation.

### Real-world example corpus: `medtimingtracker_*_work_docs/`

`organizer_work_docs/` is one real batch; these are twelve, from actually
building an Android/Kotlin app (a med-timing-tracker) with this tool over
one session — 36 tasks total, almost entirely `exact_code` (deterministic
splices, no model call). Kept as tracked examples because they're a much
larger and more structurally diverse sample than anything else in this
repo: every `structure_type` this schema supports except `function`/
`class`/`docstring` shows up somewhere across them, split across Room
entities/DAOs/database/repositories, a notification pipeline, ViewModels,
and a JSON backup layer — real Kotlin, not toy snippets.

They're also the origin of two real bugs this tool had (both fixed, see
`tests/test_anchor.py` and `tests/test_executor.py` for the regression
tests, and git history for the fixes): `method`/`add`'s parent resolution
didn't recognize Kotlin `interface` declarations (only literal `class`),
and appending into a class/interface body that turned out to be empty
landed at the wrong indentation. Both were found by running batches
against a real project, not by unit testing in isolation — which is
exactly the value a corpus like this adds over synthetic fixtures alone.
Worth mining further if `anchor.py`/`executor.py` need more tuning: these
batches are real anchors resolved against real (if now-already-applied)
Kotlin files, not hypothetical ones.

Batch-numbered by the phase of the app they built (`phase1_*` is the Room
data layer, `phase2_*` the notification pipeline, etc.) — the numbering
also demonstrates the tool's batch-boundary convention: one batch per
architectural layer, sequenced so a later batch's anchors can assume an
earlier batch already landed (e.g. `phase1_daos` references entities from
`phase1_entities`).

## `scaffold` — mechanically expanding repetitive batches

Most of the manual JSON-authoring effort in a real batch (see the
`medtimingtracker_*_work_docs/` corpus below) wasn't deciding *what* to
write — it was retyping the same task shape a dozen times for near-identical
edits (a DAO method per query, an entity per field, ...). `scaffold` factors
that out: write one shared `task_template` plus a short list of per-item
variables, and it expands them into a full batch of validated, ready-to-run
`exact_code` tasks — no model call, not even at authoring time.

```bash
python -m runner.cli scaffold scaffold_examples/profile_dao_methods.json --work-dir work_docs
python -m runner.cli run --work-dir work_docs --model gemma4:12b
```

Template fields use `{{double_braces}}`, not `str.format`'s `{single_braces}`
— a template field is usually source code, and Kotlin/Java/JS all use plain
`{`/`}` for real block syntax that `str.format` would misread as a
placeholder. See `scaffold_examples/profile_dao_methods.json` for a full
worked example (3 Room DAO methods from one shared template).

## `bench` — scoring local models on known-answer fragments

An offline harness: given a fixture (a small real-ish source file plus tasks
with known-correct `exact_code`), it withholds the `exact_code`, asks the
model to generate the fragment for real (the same `build_prompt` →
`ollama_client.generate` → `parse_fragment` → shape-validation → bleed-check
pipeline `run` uses), and scores the result against the known answer. Never
touches a real repo.

```bash
python -m runner.cli bench --models qwen2.5:1.5b,qwen3:4b,gemma4:12b --report logs/bench/results.json
```

This is how `pattern_example` (below) got evaluated rather than assumed to
help — see "Running on small models" below, and `bench/README.md` for full
methodology, results, caveats, and instructions for re-testing with a
larger/GPU-accelerated model.

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

  This was measured directly with `runner/bench.py`, not assumed: on small,
  realistic, well-scoped pattern-following tasks (add a DAO method following
  three existing ones, add a one-line conversion function following two),
  `qwen2.5:1.5b`, `gemma3:4b`, and `qwen3:4b` all landed around **0-15%
  exact-match**, and `pattern_example` — a real sibling instance of the
  pattern shown to the model as a concrete few-shot example, meant to make
  generation more reliable than working from `description` prose alone —
  did **not** reliably move that number; several runs it did the same or
  worse. `qwen3:4b` was also frequently slow enough to hit the request
  timeout on a single fragment (this turned out to be a CPU-vs-GPU artifact,
  not a capability finding — see below). The dominant failure modes were a
  bare signature/name with no body and fluent-but-wrong content (caught by
  `_validate_fragment_shape`/bleed-detection in the first case, invisible to
  the pipeline in the second — this is why "success" never means "correct").
  One real, fixable bug came out of building this: small models frequently
  double-escape newlines in their JSON output (`\n` decodes to the two
  literal characters `\` and `n`, not a line break), which was masking
  otherwise-fine multi-line fragments as bare one-liners; `parse_fragment`
  now unescapes this. Re-tested 2026-08-03 once GPU acceleration turned out
  to already be available on this machine: `qwen3:4b` is no longer
  practically unusable (every call finishes in seconds), but its accuracy
  is still 0% exact-match — speed was never the actual problem. A genuinely
  bigger model (`gemma3:12b`) roughly doubled to quadrupled exact-match
  (~36% combined) versus the 4B-class models, but `mismatch` (silently
  wrong, nothing catches it) stayed just as common — still well under the
  bar for routing real work to the model. A code-specialized 7B
  (`qwen2.5-coder:7b`) did *not* outperform the general-purpose 12B model.
  Net conclusion, still holding after the GPU/bigger-model re-test:
  `pattern_example` and shape validation are worth keeping for
  opportunistic/low-stakes use under human review, but they aren't a
  substitute for `exact_code` — the `scaffold` command (above) is the
  actual lever for cutting down how much JSON a human/Claude has to
  hand-author, since it needs no model call at all. Full write-up, raw
  results, and caveats: `bench/README.md`.
