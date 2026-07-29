"""CLI entry point: python -m runner.cli run --work-dir work_docs/ --model <tag>"""

import argparse
import subprocess
import sys
from pathlib import Path

from runner import executor, ollama_client
from runner.work_doc import WorkDocError, load_batch

DEFAULT_MODEL = "qwen3:4b"


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
        )
    except ollama_client.OllamaError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3

    ok_statuses = ("success", "dry_run", "init")
    return 0 if all(e["status"] in ok_statuses for e in log_data["entries"]) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute all work documents in a directory")
    run_parser.add_argument("--work-dir", type=Path, default=Path("work_docs"),
                             help="Directory containing work document *.json files (default: work_docs/)")
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
    run_parser.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
