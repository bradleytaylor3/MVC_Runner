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
CPU, and still no up to 12B on GPU, though accuracy improves with scale and
with model generation (the best result so far, `gemma4:12b`, gets close —
see Results below — but not close enough, and unevenly across fixtures).

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

## Models tested and removed — don't re-pull without new evidence

After benchmarking, `gemma3:4b`, `qwen2.5-coder:7b`, `qwen3:4b`, and
`gemma3:12b` were all deleted from local disk (`ollama rm`) — none of them
is used as a default anywhere in this tool anymore, and each is either
outright poor (well under 50% exact-match) or, in `gemma3:12b`'s case,
directly superseded by `gemma4:12b` at the identical footprint (55%/27%
mismatch vs. 36%/36%). Their numbers are fully preserved in the tables
above and in `bench/results/*.json` — nothing is lost by not keeping the
weights on disk. Re-pull one only to test a specific new claim about it (a
fine-tune, a newer version of the same tag, a hardware change worth
isolating), not to re-confirm a number already recorded here.

Only two models are installed at all right now, both because they're wired
as CLI defaults (`runner/cli.py`), not because every alternative was purged
indiscriminately:
- `qwen2.5:1.5b` — `DEFAULT_ADB_MODEL` for `test-adb`. Weak at code
  generation (9% exact-match) but that's not what it's used for by
  default; it holds up fine at the classification-shaped action selection
  `test-adb` actually asks of it (see root `README.md`'s "Running on small
  models" section).
- `gemma4:12b` — `DEFAULT_MODEL` for `run`, since 2026-08-03 (previously
  `qwen3:4b`, which scored **0% exact-match** here, the worst of every
  model tested). `run`'s default is only ever exercised by tasks without
  `exact_code`, where output was never trusted unreviewed regardless of
  which model produced it, so defaulting to the best available model
  (55% exact-match, still short of the 60-70%-with-rare-mismatch bar for
  trusting generation outright — see the same-size-class comparison above)
  is a strict upgrade over defaulting to the worst one. Practically every
  model ≤7B tested here scored low enough (0-18% exact-match) to be
  pointless as a default, not just imperfect.

## Same-size-class generation comparison: gemma3:12b vs gemma4:12b

Gemma 4 released 2026-04-02; `gemma4:12b` (7.6GB) is the direct successor
tag to `gemma3:12b` (8.1GB) at the same nominal size, so this isolates
"does the newer generation help" from "does bigger help" (`bench/results/gemma4-12b-gpu-2026-08-03.json`):

| model | pattern_example | exact | whitespace | mismatch | rejected |
|---|---|---|---|---|---|
| gemma3:12b | no | 2/6 | 0/6 | 2/6 | 2/6 |
| gemma3:12b | yes | 2/5 | 0/5 | 2/5 | 1/5 |
| gemma4:12b | no | 3/6 | 0/6 | 3/6 | 0/6 |
| gemma4:12b | yes | 3/5 | 0/5 | 0/5 | 2/5 |

Combined: `gemma3:12b` 36% exact / 36% mismatch → **`gemma4:12b` 55% exact /
27% mismatch** — a real jump at the same footprint, and currently the best
result of anything tested here.

**But it's not an even 55% — it's a complete split by fixture, not a
spread:** `gemma4:12b` got **6/6 exact-match on `kotlin_dao`** (every
trial, both with and without `pattern_example`) and **0/5 on
`python_converters`** (2 `validation_error`, 3 `mismatch` — zero
successes). `gemma3:12b` didn't show this pattern anywhere near as sharply.
With n=6 and n=5 per fixture this could be fixture-specific luck, but a
clean 100%/0% split is different from noise scattered across both — treat
"gemma4:12b is reliable" as fixture-shape-dependent, not general, until
tested against more than 2 fixtures.

Against the decision rubric below (60-70%+ exact-match, mismatch rare):
**55%/27% is close but doesn't clear it** — meaningfully better than every
other model tested, not yet at "trust it for unreviewed generation."

## Knockout tournament: further candidates vs. the reigning champion

Same fixtures, same harness, one challenger at a time against whichever
model currently has the best combined exact-match/mismatch — loser gets
`ollama rm`'d (its numbers stay here and in `bench/results/`; only the
weights are removed), winner stays installed and keeps the belt. Started
after `gemma4:12b` (55% exact / 27% mismatch) became champion above.

| round | challenger | exact | mismatch | result |
|---|---|---|---|---|
| 1 | `qwen3.5:9b` (Alibaba, 2026-03-02, hybrid Gated-DeltaNet+MoE, strong on general/competitive-coding benchmarks e.g. LiveCodeBench v6 82.7%) | 0/11 (0%) | 4/11 (36%) | **lost** — worst `exact_match` of anything tested here; strong competitive-coding benchmarks did not predict this task shape (strict JSON-fragment output against a precise anchor) at all |
| 2 | `yi-coder:9b` (01.AI) | 3/11 (27%, +3/11 whitespace-only) | 4/11 (36%) | **lost** — same rough kotlin_dao-good/python_converters-bad split as `gemma4:12b` showed, but weaker on both axes |
| 3 | `deepcoder:14b` (Agentica/Together, RL-trained on a DeepSeek-R1-distill base specifically for coding correctness) | 0/11 (0%, +3/11 whitespace-only) | 5/11 (45%) | **lost** — worst `mismatch` rate of anything tested despite being explicitly RL-tuned for coding correctness; that training target (agentic/test-passing coding benchmarks) apparently doesn't transfer to "reproduce this exact fragment verbatim" |

Champion remains **`gemma4:12b`, 55%/27%**, unbeaten through round 3.

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
