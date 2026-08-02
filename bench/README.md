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
Claude (or a human) pre-decide `exact_code`? Short answer: no, not at ≤4B on
CPU. See Results below.

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

## Caveats (read before trusting these numbers)

- n=5-6 per cell, single run, no temperature/seed averaging — real
  run-to-run variance was visible while collecting this (a killed/restarted
  attempt on `gemma3:4b` mid-session showed 2 `exact_match` where the final
  clean run showed 1). Don't treat 0% vs 17% as a meaningful gap; do treat
  "well under half" as a stable conclusion across every run observed.
- Only 2 fixtures, both boilerplate-shaped and both favorable cases (a real
  sibling pattern exists in-file). Real-world tasks without a clean sibling
  pattern would likely do worse, not better.
- CPU inference only. `qwen3:4b`'s practical unusability here may be
  hardware-bound rather than a capability finding — worth re-testing on a
  GPU purely for latency, independent of correctness.

## Trying a bigger / GPU-accelerated model

The whole point of keeping this harness (and this write-up) around: when
running on a machine with a dedicated GPU, larger local models become
practical, and it's worth re-asking the question with real numbers rather
than assumptions.

```bash
ollama pull gemma3:12b        # or whatever larger/newer tag is available by then
python -m runner.cli bench --models gemma3:12b --report bench/results/gemma3-12b-gpu.json
```

Compare the resulting `exact_match` rate against the table above. Rough
guide for what to do with the result:

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
- Also worth trying a **code-specialized** model tag (e.g. a `-coder`
  variant) rather than only scaling up a general chat model — the
  benchmarked models here (`qwen2.5`, `gemma3`, `qwen3`) are all
  general-purpose; a coder-tuned model of similar or even smaller size might
  outperform a bigger general one.
