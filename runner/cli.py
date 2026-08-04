"""CLI entry point: python -m runner.cli run --work-dir work_docs/ --model <tag>"""

import argparse
import subprocess
import sys
from pathlib import Path

from runner import adb_agent, adb_client, bench, executor, ollama_client, scaffold, token_savings, work_builder
from runner.adb_task import AdbTaskError, load_adb_batch
from runner.work_doc import WorkDocError, load_batch

# qwen3:4b scored 0/11 exact-match in runner/bench.py's fragment-generation
# benchmark (bench/README.md) -- the worst of every model tested there --
# while gemma4:12b scored 55%, the best. run/build's default is only ever
# used for tasks without exact_code (see executor.py), where output was
# never trusted unreviewed anyway, so this is a strict upgrade even though
# 55%/27% mismatch is still short of "trustworthy for unreviewed generation."
DEFAULT_MODEL = "gemma4:12b"
# Per-step actions during authoring are schema-constrained picks from a
# small enum (see adb_agent.ACTION_SCHEMA), which a small/fast model like
# qwen2.5:1.5b handles fine. But replay's one-shot final judgment (see
# adb_replay.replay_adb_task) is free-form reasoning over acceptance
# criteria, not classification -- confirmed live against a real device,
# qwen2.5:1.5b hallucinated a "fail" verdict against an unambiguous
# on-screen "4" two runs in a row, while gemma4:12b got it right
# immediately on the same prompt. Since --model drives both, and replay
# only spends the larger model's extra latency on that one call per
# scenario (not per step), it defaults to the same capable model as `run`
# rather than adding a second model flag just for the judgment call.
DEFAULT_ADB_MODEL = DEFAULT_MODEL


def _is_worktree_dirty(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def cmd_run(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()

    try:
        init, tasks = load_batch(work_dir)
    except WorkDocError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    repo_root = args.repo_root.resolve() if args.repo_root else Path(init.repo_root).resolve()

    if not args.dry_run and not args.allow_dirty and _is_worktree_dirty(repo_root):
        print(
            f"Error: {repo_root} has uncommitted changes. Commit or stash them first, "
            "or pass --allow-dirty to proceed anyway.",
            file=sys.stderr,
        )
        return 2

    try:
        log_data = executor.run_batch(
            init=init,
            tasks=tasks,
            repo_root=repo_root,
            model=args.model,
            host=args.host,
            dry_run=args.dry_run,
            logs_dir=args.logs_dir.resolve(),
            think=args.think,
            context_lines=args.context_lines,
            max_retries=args.max_retries,
        )
    except ollama_client.OllamaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3

    token_savings.update_summary(args.logs_dir.resolve())

    ok_statuses = ("success", "dry_run", "init")
    return 0 if all(e["status"] in ok_statuses for e in log_data["entries"]) else 1


def cmd_test_adb(args: argparse.Namespace) -> int:
    work_dir = args.work_dir.resolve()

    try:
        init, tasks = load_adb_batch(work_dir)
    except AdbTaskError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    try:
        serial = adb_client.require_device(args.device or init.default_serial)
    except adb_client.AdbError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    try:
        log_data = adb_agent.run_adb_batch(
            init=init,
            tasks=tasks,
            model=args.model,
            host=args.host,
            serial=serial,
            dry_run=args.dry_run,
            logs_dir=args.logs_dir.resolve(),
            think=args.think,
            replay=args.replay,
        )
    except ollama_client.OllamaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3

    token_savings.update_adb_summary(args.logs_dir.resolve())

    ok_statuses = ("pass", "init")
    return 0 if all(e["status"] in ok_statuses for e in log_data["entries"]) else 1


def cmd_build(args: argparse.Namespace) -> int:
    work_builder.run_wizard(args.work_dir.resolve(), model=args.model)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute all work documents in a directory")
    run_parser.add_argument("--work-dir", type=Path, default=Path("work_docs"),
                             help="Directory containing work document *.json files -- either one init doc plus "
                                  "N work docs, or a single consolidated 'batch' doc, or a mix (default: work_docs/)")
    run_parser.add_argument("--repo-root", type=Path, default=None,
                             help="Root of the repo whose files will be edited (default: the batch's init "
                                  "doc repo_root; pass this to override it, e.g. for testing)")
    run_parser.add_argument("--model", default=DEFAULT_MODEL,
                             help=f"Ollama model tag to use (default: {DEFAULT_MODEL})")
    run_parser.add_argument("--host", default=ollama_client.DEFAULT_HOST,
                             help=f"Ollama server host:port (default: {ollama_client.DEFAULT_HOST})")
    run_parser.add_argument("--logs-dir", type=Path, default=Path("logs"),
                             help="Directory to write run logs to (default: logs/)")
    run_parser.add_argument("--dry-run", action="store_true",
                             help="Build prompts and parse model output without writing any files")
    run_parser.add_argument("--think", action="store_true",
                             help="Allow reasoning-capable models (e.g. qwen3) to use their thinking mode "
                                  "(default: off, for faster/more deterministic full-file-rewrite output)")
    run_parser.add_argument("--allow-dirty", action="store_true",
                             help="Proceed even if the repo has uncommitted changes")
    run_parser.add_argument("--context-lines", type=int, default=3,
                             help="Lines of read-only context shown before/after each anchor (default: 3)")
    run_parser.add_argument("--max-retries", type=int, default=0,
                             help="Extra attempts for a task whose output fails on model-quality grounds "
                                  "(parse failure, shape validation) before giving up on it — each retry is told "
                                  "why the previous attempt was rejected (default: 0 — no retry). Anchor/splice "
                                  "errors are never retried since they're deterministic given the task and repo "
                                  "state, as are exact_code tasks, which never call the model at all.")
    run_parser.set_defaults(func=cmd_run)

    test_adb_parser = subparsers.add_parser("test-adb", help="Run an ADB-driven UI test batch against a connected device/emulator")
    test_adb_parser.add_argument("--work-dir", type=Path, default=Path("work_docs_adb"),
                                  help="Directory containing ADB test batch *.json files (default: work_docs_adb/)")
    test_adb_parser.add_argument("--model", default=DEFAULT_ADB_MODEL,
                                  help=f"Ollama model tag to use (default: {DEFAULT_ADB_MODEL})")
    test_adb_parser.add_argument("--host", default=ollama_client.DEFAULT_HOST,
                                  help=f"Ollama server host:port (default: {ollama_client.DEFAULT_HOST})")
    test_adb_parser.add_argument("--logs-dir", type=Path, default=Path("logs"),
                                  help="Directory to write run logs to (default: logs/)")
    test_adb_parser.add_argument("--device", default=None,
                                  help="adb device serial to target (default: the init doc's default_serial, "
                                       "or the sole connected device if there's exactly one)")
    test_adb_parser.add_argument("--dry-run", action="store_true",
                                  help="Dump real on-device UI and ask the model for each action, but don't "
                                       "actually tap/swipe/type on the device")
    test_adb_parser.add_argument("--think", action="store_true",
                                  help="Allow reasoning-capable models (e.g. qwen3) to use their thinking mode "
                                       "(default: off, for faster/more deterministic per-step actions)")
    test_adb_parser.add_argument("--replay", action=argparse.BooleanOptionalAction, default=True,
                                  help="Replay a scenario's saved recording (work_docs_adb/recordings/<id>.json, "
                                       "written the first time a scenario is authored) by resolving each step's "
                                       "element selector and executing it directly -- no per-step model call, just "
                                       "one call at the end to judge the final screen. Falls back to full "
                                       "re-authoring for a scenario if a selector can no longer be resolved or the "
                                       "judgment disagrees with the recording (default: on). Pass --no-replay to "
                                       "force full re-authoring for every task, e.g. after changing the app.")
    test_adb_parser.set_defaults(func=cmd_test_adb)

    build_parser = subparsers.add_parser("build", help="Interactively build a work_docs/ batch, one task at a time")
    build_parser.add_argument("--work-dir", type=Path, default=Path("work_docs"),
                               help="Directory to build the batch in (default: work_docs/)")
    build_parser.add_argument("--model", default=DEFAULT_MODEL,
                               help=f"Model tag shown in the closing 'run it with' hint (default: {DEFAULT_MODEL})")
    build_parser.set_defaults(func=cmd_build)

    bench.add_bench_subparser(subparsers)
    scaffold.add_scaffold_subparser(subparsers)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
