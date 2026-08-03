# Local-model code-generation benchmark

`runner/bench.py` measures how reliably a local Ollama model can generate a
*correct* code fragment for a small, well-scoped, pattern-following edit —
using the exact same prompt-building/parsing/validation pipeline `run` uses,
against fixtures with a known-correct answer, so the model's own output can
be scored instead of just checked for "didn't crash."

## Why this exists

The project's default posture (`exact_code`, see `work_docs/AUTHORING_PROMPT.md`)
is: if the author already knows the code, skip the model entirely and splice
deterministically. That was itself an empirical finding (`e46e3ba`: a real
75-task batch got 0/75 successes when local models were asked to do even
pure reindenting of text they were handed verbatim).

This benchmark was built to answer a follow-up question directly rather than
guess: if we give the model everything we reasonably can — a concrete
sibling example of the pattern (`pattern_example`), structural sanity
checks, feedback-driven retries — does *generation* (not just transcription)
become reliable enough to route real work to the model instead of having
Claude (or a human) pre-decide `exact_code`? Short answer: no — not at ≤4B on
CPU, and still no up to 12B on GPU, though accuracy does improve with scale.
See Results below.

## Fixtures

- `bench/fixtures/kotlin_dao/` — a Room DAO interface (`ProfileDao.kt`) with
  3 existing methods (`@Insert`, `@Query` x2); 3 tasks add a 4th method
  following one of the existing ones as `pattern_example`.
- `bench/fixtures/python_converters/` — 2 existing one-line unit-conversion
  functions; 2 tasks add a 3rd/4th following that pattern, plus 1 task (no
  pattern available) adding a trivial constant, as a lower bound sanity
  check.

Both mirror the real shape of the `medtimingtracker_*_work_docs/` corpus
(boilerplate that's almost entirely "one more of these, following the
pattern already in the file") — this is close to the *best case* for a
small model, not an adversarial one.

## Running it

```bash
python -m runner.cli bench --models qwen2.5:1.5b,gemma3:4b,qwen3:4b --report bench/results/my-run.json
```

`--no-ablate` skips the automatic without-`pattern_example` comparison.
Nothing here ever touches a real repo or writes a real file.

## Results (2026-08-01/02, CPU-only, this machine)

11 trials per model (6 without `pattern_example`, 5 with — one fixture task
has no sibling pattern available). **Sample size is small; treat these as
directional, not precise percentages** — see Caveats.

| model | pattern_example | exact_match | mismatch | rejected* |
|---|---|---|---|---|
| gemma3:4b | no | 1/6 | 2/6 | 3/6 |
| gemma3:4b | yes | 0/5 | 3/5 | 2/5 |
| qwen2.5:1.5b | no | 0/6 | 2/6 | 4/6 |
| qwen2.5:1.5b | yes | 0/5 | 1/5 | 4/5 |
| qwen3:4b | — | frequently timed out (>600s/call) or produced unparseable/invalid output; no clean full run completed |

\* `rejected` = caught by `parse_error`/`validation_error` before reaching a
file — safe but useless. `mismatch` = the pipeline accepted it, but the
content was wrong; nothing catches this automatically, it's the dangerous
case.

Raw reports: `bench/results/gemma3-4b-cpu.json`, `qwen2.5-1.5b-cpu.json`.
`bench/results/pre-fix-4b-cpu.json` is an earlier run (all 3 models) from
*before* `parse_fragment`'s literal-`\n` unescaping fix — kept for
reference, not as a result to trust; that bug was silently turning some
real successes into false `validation_error`s.

**`pattern_example` did not reliably help.** In both models above it matched
or underperformed plain `description`-only generation. It is not the lever
that makes small local models trustworthy for this.

## Results (2026-08-03, GPU-accelerated, this machine)

Same machine, same fixtures, same harness — the difference is Ollama is now
using the GPU (confirmed via `ollama ps` showing `100% GPU` for the smaller
models; `gemma3:12b` at 8.1GB doesn't fully fit this GPU's 8GB VRAM, so it's
majority-GPU with some CPU spillover — its ~24s/call average is far closer
to the fully-GPU 7B model's ~9s/call than to the CPU-only run's >600s/call
timeouts, so it's not running CPU-only either). This wasn't a deliberate
re-test — it was discovered mid-session that the CPU-only framing of the
run above was itself stale; the GPU has apparently been usable on this
machine for a while.

**Re-running the original 3 models under GPU** (`bench/results/gpu-rerun-2026-08-03.json`):

| model | pattern_example | exact | whitespace | mismatch | rejected |
|---|---|---|---|---|---|
| gemma3:4b | no | 2/6 | 1/6 | 2/6 | 1/6 |
| gemma3:4b | yes | 0/5 | 1/5 | 2/5 | 2/5 |
| qwen2.5:1.5b | no | 1/6 | 1/6 | 1/6 | 3/6 |
| qwen2.5:1.5b | yes | 0/5 | 0/5 | 2/5 | 3/5 |
| qwen3:4b | no | 0/6 | 0/6 | 1/6 | 5/6 |
| qwen3:4b | yes | 0/5 | 0/5 | 2/5 | 3/5 |

GPU didn't change what a model outputs, only how fast — as expected, since
it's the same weights and the same sampling. The concrete payoff: `qwen3:4b`
is no longer practically unusable (every trial now completes in seconds,
not timing out at >600s), which finally answers the CPU run's open
question — and the answer is accuracy, not speed, was never the problem for
it: 0/11 exact-match even once it can actually finish.

**Bigger + code-specialized models** (`bench/results/gpu-bigger-models-2026-08-03.json`):

| model | pattern_example | exact | whitespace | mismatch | rejected |
|---|---|---|---|---|---|
| gemma3:12b | no | 2/6 | 0/6 | 2/6 | 2/6 |
| gemma3:12b | yes | 2/5 | 0/5 | 2/5 | 1/5 |
| qwen2.5-coder:7b | no | 2/6 | 0/6 | 3/6 | 1/6 |
| qwen2.5-coder:7b | yes | 0/5 | 1/5 | 3/5 | 1/5 |

Combined exact-match: **`gemma3:12b` 4/11 (36%)**, roughly 2-4x the 4B
CPU/GPU runs above — scale clearly helps. **`qwen2.5-coder:7b` 2/11 (18%)**,
no better than the general-purpose 4B models and worse on `mismatch` (6/11,
55% — the highest of any model tested). Being code-specialized didn't help
here; general capability/size did more work than domain tuning, at least
for this fragment-in-JSON output shape and at 7B.

**Verdict, against this doc's own decision rubric below: still no.**
`gemma3:12b`'s 36% exact-match is well under the 60-70% bar, and `mismatch`
(the dangerous, silently-wrong case) stayed at 36% — worse than useless if
unreviewed, since it's indistinguishable from a correct result without a
human or test suite checking. `exact_code` + `scaffold` stays the default;
model generation stays opportunistic/human-reviewed only. The trend with
scale is real, though — worth another data point at 27B+ before concluding
this is a dead end rather than "needs more scale than fits in 8GB VRAM."

## Caveats (read before trusting these numbers)

- n=5-6 per cell, single run, no temperature/seed averaging — real
  run-to-run variance was visible while collecting this (a killed/restarted
  attempt on `gemma3:4b` mid-session showed 2 `exact_match` where the final
  clean run showed 1). Don't treat small point differences (e.g. 9% vs 18%)
  as a meaningful gap; do treat "well under half, mismatch common" as the
  stable conclusion across every run observed, CPU or GPU, 1.5B through 12B.
- Only 2 fixtures, both boilerplate-shaped and both favorable cases (a real
  sibling pattern exists in-file). Real-world tasks without a clean sibling
  pattern would likely do worse, not better.
- `gemma3:12b` didn't fully fit in this GPU's 8GB VRAM (see above) — its
  numbers may understate what a card with more headroom would produce, if
  partial CPU offload affects output quality (it shouldn't in theory —
  compute placement doesn't change the math — but hasn't been isolated
  here).

## Trying an even bigger model

12B (partially GPU-resident on this 8GB card) still didn't clear the bar —
worth trying next once more VRAM is available, or a smaller/quantized 20B+
tag that fits:

```bash
ollama pull gemma3:27b        # or whatever larger/newer tag fits available VRAM by then
python -m runner.cli bench --models gemma3:27b --report bench/results/gemma3-27b-gpu.json
```

Compare the resulting `exact_match` rate against the tables above. Same
rubric as before:

- **Still well under ~50% exact-match, or `mismatch` stays common** — the
  conclusion in this doc still holds: keep `exact_code` + `scaffold` (see
  repo root README) as the default path, and treat model generation as
  opportunistic/human-reviewed only.
- **Meaningfully higher (rough eyeball: 60-70%+) with `mismatch` rare** —
  worth revisiting `work_docs/AUTHORING_PROMPT.md` and
  `.claude/skills/code-edit/SKILL.md`'s current "prefer `exact_code`"
  guidance for boilerplate-with-a-sibling-pattern cases specifically (not
  novel/business logic — that's a separate, harder question this benchmark
  doesn't test). Also worth widening the fixtures here first (more
  languages/patterns, more items per pattern) so the number is trustworthy
  before changing a default that affects every batch.
- A `-coder` tag didn't outperform a same-class general model here
  (`qwen2.5-coder:7b` underperformed `gemma3:12b` and had the worst
  `mismatch` rate of anything tested) — worth trying a different
  coder-tuned family before concluding that lever is dead, but it's not the
  free win it looked like on paper.
