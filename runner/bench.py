"""Offline benchmark: measure how reliably a local model reproduces a
known-correct fragment for a pattern-following code edit.

Runs the exact same build_prompt -> ollama_client.generate -> parse_fragment
-> _validate_fragment_shape -> bleed-detection pipeline executor.py uses for
a real `run`, deliberately bypassing only the exact_code shortcut (each
fixture task carries a known-correct `exact_code`, but it's held back as
ground truth and never shown to the model). Never touches a real repo or
writes any file — each task's anchor is resolved fresh against the pristine
fixture file, and nothing is spliced in.

Point of this: `exact_code` was made mandatory-in-practice by real evidence
(a 75-task batch: 0/75 succeeded when local models were asked to do even
mechanical reindenting). Before routing more tasks to the model instead of
pre-deciding exact_code by hand, this answers "how much better does
pattern_example actually make things, on the models we can actually run?"
with the same numbers, not a guess.
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from runner import anchor as anchor_mod
from runner import contract_check
from runner import executor
from runner import ollama_client
from runner import patch
from runner.work_doc import InitTask, WorkTask

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "bench" / "fixtures"

# Outcomes worth distinguishing when deciding whether a model is trustworthy
# enough to skip exact_code for a given kind of task: exact/whitespace_match
# mean it would have produced a correct file; functional_match means the text
# differs from the ground truth but the task's declarative `contract` (see
# runner/contract_check.py) confirmed it satisfies the task's
# acceptance_criteria anyway (a renamed variable, an equivalent formula,
# differently-quoted SQL); mismatch means it ran cleanly but failed that
# check, or the task has no contract to check against — the dangerous case,
# since nothing would have caught it in a real run; everything else is a
# case the real pipeline's own guards (parse_fragment,
# _validate_fragment_shape, bleed detection) already catch before the bad
# content ever reaches a file.
MATCH_STATUSES = ("exact_match", "whitespace_match", "functional_match")


@dataclass
class BenchOutcome:
    fixture: str
    task_id: str
    model: str
    pattern_example: bool
    status: str
    elapsed_seconds: float
    detail: str = ""
    checker_detail: str = ""


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip("\n").split("\n"))


def _normalize_loose(text: str) -> str:
    return "".join(_normalize(text).split())


def load_fixture(fixture_dir: Path) -> tuple[InitTask, list[tuple[WorkTask, str, dict | None]]]:
    """Returns the fixture's InitTask plus (task, ground_truth_exact_code,
    contract) triples — exact_code and contract are stripped out of every
    task before WorkTask construction so neither is ever accidentally
    available to build_prompt. contract is None for tasks that don't
    declare one (functional scoring falls back to mismatch for those)."""
    meta = json.loads((fixture_dir / "tasks.json").read_text(encoding="utf-8"))
    init = InitTask(batch_id=meta["batch_id"], repo_root=str(fixture_dir), language=meta["language"])

    pairs: list[tuple[WorkTask, str, dict | None]] = []
    for raw in meta["tasks"]:
        raw = dict(raw)
        raw.setdefault("file", meta["file"])
        raw.setdefault("kind", "work")
        ground_truth = raw.pop("exact_code")
        contract = raw.pop("contract", None)
        task = WorkTask.from_dict(raw, fixture_dir / "tasks.json")
        pairs.append((task, ground_truth, contract))
    return init, pairs


def _without_pattern_example(task: WorkTask) -> WorkTask:
    stripped = dict(task.__dict__)
    stripped["pattern_example"] = None
    return WorkTask(**stripped)


def run_one(
    task: WorkTask,
    ground_truth: str,
    init: InitTask,
    fixture_dir: Path,
    model: str,
    host: str,
    think: bool,
    context_lines: int,
    options: dict | None = None,
    contract: dict | None = None,
) -> BenchOutcome:
    start = time.monotonic()
    common = dict(fixture=fixture_dir.name, task_id=task.id, model=model, pattern_example=bool(task.pattern_example))

    lines, _crlf = executor._read_file_lines(fixture_dir, task.file)
    try:
        resolved = anchor_mod.resolve_anchor(task, lines, init.language)
    except anchor_mod.AnchorError as e:
        return BenchOutcome(**common, status="anchor_error", elapsed_seconds=time.monotonic() - start, detail=str(e))

    prompt = executor.build_prompt(task, init, fixture_dir, lines, resolved, context_lines)
    try:
        result = ollama_client.generate(prompt, model=model, host=host, think=think, format=executor.FRAGMENT_SCHEMA,
                                         options=options)
    except ollama_client.OllamaError as e:
        return BenchOutcome(**common, status="error", elapsed_seconds=time.monotonic() - start, detail=str(e))

    elapsed = time.monotonic() - start

    fragment = executor.parse_fragment(result.text)
    if fragment is None:
        return BenchOutcome(**common, status="parse_error", elapsed_seconds=elapsed, detail=result.text[:200])

    shape_issue = executor._validate_fragment_shape(task, fragment)
    if shape_issue:
        return BenchOutcome(**common, status="validation_error", elapsed_seconds=elapsed, detail=shape_issue)

    bleed = patch.detect_trailing_bleed(lines, resolved, fragment) or patch.detect_leading_bleed(lines, resolved, fragment)
    if bleed:
        return BenchOutcome(**common, status="validation_error", elapsed_seconds=elapsed, detail=bleed)

    if _normalize(fragment) == _normalize(ground_truth):
        status = "exact_match"
    elif _normalize_loose(fragment) == _normalize_loose(ground_truth):
        status = "whitespace_match"
    else:
        status = "mismatch"

    checker_detail = ""
    if status == "mismatch" and contract is not None:
        passed, checker_detail = contract_check.check_contract(init.language, fragment, contract)
        if passed:
            status = "functional_match"

    return BenchOutcome(**common, status=status, elapsed_seconds=elapsed, detail=fragment[:200],
                         checker_detail=checker_detail)


def run_bench(models: list[str], host: str, think: bool, context_lines: int, ablate: bool,
              options: dict | None = None) -> list[BenchOutcome]:
    outcomes: list[BenchOutcome] = []
    fixture_dirs = sorted(p for p in FIXTURES_DIR.iterdir() if (p / "tasks.json").is_file())

    for fixture_dir in fixture_dirs:
        init, pairs = load_fixture(fixture_dir)
        for model in models:
            for task, ground_truth, contract in pairs:
                variants = [task]
                if ablate and task.pattern_example:
                    variants.append(_without_pattern_example(task))
                for variant in variants:
                    print(f"[{fixture_dir.name}/{variant.id}] {model} "
                          f"(pattern_example={'yes' if variant.pattern_example else 'no'}) ...", end=" ", flush=True)
                    outcome = run_one(variant, ground_truth, init, fixture_dir, model, host, think, context_lines,
                                       options=options, contract=contract)
                    print(outcome.status)
                    outcomes.append(outcome)

    return outcomes


def _print_report(outcomes: list[BenchOutcome]) -> None:
    by_key: dict[tuple[str, bool], list[BenchOutcome]] = {}
    for o in outcomes:
        by_key.setdefault((o.model, o.pattern_example), []).append(o)

    print("\n=== Bench report ===")
    header = (f"{'model':<16} {'pattern_example':<16} {'n':>3} {'exact':>6} {'whitespace':>11} "
              f"{'functional':>11} {'mismatch':>9} {'rejected':>9}")
    print(header)
    print("-" * len(header))
    for (model, pattern_example), group in sorted(by_key.items()):
        n = len(group)
        exact = sum(1 for o in group if o.status == "exact_match")
        whitespace = sum(1 for o in group if o.status == "whitespace_match")
        functional = sum(1 for o in group if o.status == "functional_match")
        mismatch = sum(1 for o in group if o.status == "mismatch")
        rejected = sum(1 for o in group if o.status not in MATCH_STATUSES and o.status != "mismatch")
        print(f"{model:<16} {'yes' if pattern_example else 'no':<16} {n:>3} {exact:>6} {whitespace:>11} "
              f"{functional:>11} {mismatch:>9} {rejected:>9}")

    print("\nfunctional = text differs from ground truth, but the task's declarative contract confirmed")
    print("             it satisfies the acceptance_criteria anyway (right effect, different wording).")
    print("mismatch = pipeline accepted it, but it was wrong (or the task has no contract to check).")
    print("rejected = parse_error/validation_error/anchor_error: caught before it could reach a file.")


def cmd_bench(args: argparse.Namespace) -> int:
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    options: dict = {}
    if args.temperature is not None:
        options["temperature"] = args.temperature
    if args.seed is not None:
        options["seed"] = args.seed
    outcomes = run_bench(models, args.host, args.think, args.context_lines, ablate=not args.no_ablate,
                          options=options or None)
    _print_report(outcomes)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps([o.__dict__ for o in outcomes], indent=2),
            encoding="utf-8",
        )
        print(f"\nFull results written to {args.report}")

    return 0


def add_bench_subparser(subparsers) -> None:
    p = subparsers.add_parser(
        "bench",
        help="Score local models on known-answer pattern-following fragments (no real repo touched)",
    )
    p.add_argument("--models", default="qwen3:4b",
                    help="Comma-separated Ollama model tags to benchmark (default: qwen3:4b)")
    p.add_argument("--host", default=ollama_client.DEFAULT_HOST,
                    help=f"Ollama server host:port (default: {ollama_client.DEFAULT_HOST})")
    p.add_argument("--think", action="store_true", help="Allow reasoning-capable models to use thinking mode")
    p.add_argument("--context-lines", type=int, default=3,
                    help="Lines of read-only context shown before/after each anchor (default: 3)")
    p.add_argument("--no-ablate", action="store_true",
                    help="Skip the automatic without-pattern_example comparison run for tasks that have one")
    p.add_argument("--temperature", type=float, default=None,
                    help="Ollama sampling temperature to pass via options (default: server/model default, unset)")
    p.add_argument("--seed", type=int, default=None,
                    help="Ollama sampling seed to pass via options (default: unset)")
    p.add_argument("--report", type=Path, default=None,
                    help="Optional path to write the full per-task results as JSON")
    p.set_defaults(func=cmd_bench)
