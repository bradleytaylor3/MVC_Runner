"""Aggregate logs/*.json run logs into a running token-savings summary.

Every run already records, per task, how many characters were spliced into
the target file (fragment_chars) and, when a local model was actually
called, its prompt_eval_count/eval_count. This turns those per-run numbers
into a running total so the savings from using MVC_Runner instead of having
a cloud model read/rewrite whole files inline stays visible over time,
instead of being buried in one run's log at a time.
"""

import json
from pathlib import Path

# Rough, clearly-approximate chars-per-token ratio for English/code text.
# Not a real tokenizer -- just enough to make estimated_cloud_tokens_avoided
# comparable to a cloud model's token-based pricing/limits.
CHARS_PER_TOKEN_ESTIMATE = 4

# Only "success" counts toward savings -- "dry_run" entries never wrote
# anything, so counting them would overstate what MVC_Runner actually saved.
COUNTED_STATUSES = ("success",)


def _iter_run_logs(logs_dir: Path):
    if not logs_dir.is_dir():
        return
    for path in sorted(logs_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # Pre-redesign logs (2026-07-27 and earlier) were a bare list of
        # entries rather than {"entries": [...]} -- skip those; they predate
        # fragment_chars/model_called and have nothing to aggregate anyway.
        if isinstance(data, dict):
            yield data


def aggregate(logs_dir: Path) -> dict:
    """Scan every run log in logs_dir and return aggregate token-savings stats."""
    runs = 0
    tasks_completed = 0
    deterministic_tasks = 0
    model_tasks = 0
    local_prompt_tokens = 0
    local_eval_tokens = 0
    spliced_chars = 0

    for log_data in _iter_run_logs(logs_dir):
        runs += 1
        for entry in log_data.get("entries", []):
            if entry.get("id") == "init" or entry.get("status") not in COUNTED_STATUSES:
                continue
            tasks_completed += 1
            spliced_chars += entry.get("fragment_chars") or 0
            if entry.get("model_called"):
                model_tasks += 1
                local_prompt_tokens += entry.get("prompt_eval_count") or 0
                local_eval_tokens += entry.get("eval_count") or 0
            else:
                deterministic_tasks += 1

    return {
        "runs": runs,
        "tasks_completed": tasks_completed,
        "deterministic_tasks": deterministic_tasks,
        "model_tasks": model_tasks,
        "local_prompt_tokens": local_prompt_tokens,
        "local_eval_tokens": local_eval_tokens,
        "spliced_chars": spliced_chars,
        "estimated_cloud_tokens_avoided": spliced_chars // CHARS_PER_TOKEN_ESTIMATE,
    }


def render_summary(stats: dict) -> str:
    local_tokens = stats["local_prompt_tokens"] + stats["local_eval_tokens"]
    return (
        "# Token savings\n\n"
        "Auto-updated after every `run`. Regenerate by hand with "
        "`python -m runner.token_savings [logs_dir]`.\n\n"
        f"- Runs logged: {stats['runs']}\n"
        f"- Tasks completed: {stats['tasks_completed']} "
        f"({stats['deterministic_tasks']} deterministic exact_code splices, "
        f"{stats['model_tasks']} local-model-generated)\n"
        f"- Local model tokens consumed: {local_tokens} "
        f"({stats['local_prompt_tokens']} prompt + {stats['local_eval_tokens']} eval) "
        "-- spent on your machine, not a cloud API\n"
        f"- Content spliced into files: {stats['spliced_chars']} chars\n"
        f"- Estimated cloud-model tokens avoided: ~{stats['estimated_cloud_tokens_avoided']} "
        f"(spliced_chars / {CHARS_PER_TOKEN_ESTIMATE}, a rough proxy for the output tokens a "
        "cloud model would have spent emitting this content inline instead of MVC_Runner "
        "splicing it deterministically/locally)\n"
    )


def update_summary(logs_dir: Path) -> Path:
    """Recompute the summary from every log in logs_dir and write it to logs_dir/TOKEN_SAVINGS.md."""
    stats = aggregate(logs_dir)
    summary_path = logs_dir / "TOKEN_SAVINGS.md"
    summary_path.write_text(render_summary(stats), encoding="utf-8")
    return summary_path


def aggregate_adb(logs_dir: Path) -> dict:
    """Scan every `test-adb` batch log (adb_agent.run_adb_batch tags these
    "kind": "adb", distinguishing them from `run`'s logs in the same
    logs_dir) and compare model cost between scenarios that replayed a saved
    recording (adb_replay.replay_adb_task) and scenarios that ran the full
    per-step authoring loop (adb_agent.run_adb_task) -- either freshly, or
    because a replay attempt hit drift and escalated. Unlike
    estimated_cloud_tokens_avoided above (a char-count proxy), this is real
    prompt_eval_count/eval_count from Ollama on both sides."""
    runs = 0
    replayed_runs = 0
    authored_runs = 0
    escalated_runs = 0
    replayed_model_calls = 0
    replayed_tokens = 0
    authored_model_calls = 0
    authored_tokens = 0

    for log_data in _iter_run_logs(logs_dir):
        if log_data.get("kind") != "adb":
            continue
        runs += 1
        for entry in log_data.get("entries", []):
            if entry.get("id") == "init" or "model_calls" not in entry:
                continue
            tokens = (entry.get("prompt_eval_count") or 0) + (entry.get("eval_count") or 0)
            if entry.get("replayed"):
                replayed_runs += 1
                replayed_model_calls += entry["model_calls"]
                replayed_tokens += tokens
            else:
                authored_runs += 1
                authored_model_calls += entry["model_calls"]
                authored_tokens += tokens
                if entry.get("escalated"):
                    escalated_runs += 1

    avg_replayed_tokens = replayed_tokens / replayed_runs if replayed_runs else 0
    avg_authored_tokens = authored_tokens / authored_runs if authored_runs else 0
    # Only a real, measured estimate once there's at least one run on each
    # side to compare -- with nothing to compare against, report None rather
    # than a number that looks measured but isn't.
    estimated_tokens_saved = (
        round((avg_authored_tokens - avg_replayed_tokens) * replayed_runs)
        if replayed_runs and authored_runs else None
    )

    return {
        "runs": runs,
        "replayed_runs": replayed_runs,
        "authored_runs": authored_runs,
        "escalated_runs": escalated_runs,
        "replayed_model_calls": replayed_model_calls,
        "replayed_tokens": replayed_tokens,
        "authored_model_calls": authored_model_calls,
        "authored_tokens": authored_tokens,
        "avg_replayed_tokens_per_run": avg_replayed_tokens,
        "avg_authored_tokens_per_run": avg_authored_tokens,
        "estimated_tokens_saved": estimated_tokens_saved,
    }


def render_adb_summary(stats: dict) -> str:
    if not stats["replayed_runs"] and not stats["authored_runs"]:
        return (
            "# ADB test token savings\n\n"
            "Auto-updated after every `test-adb`. Regenerate by hand with "
            "`python -m runner.token_savings --adb [logs_dir]`.\n\n"
            "No `test-adb` scenario runs logged yet.\n"
        )

    escalated_note = f", {stats['escalated_runs']} of them escalated from a failed replay" if stats["escalated_runs"] else ""
    lines = [
        "# ADB test token savings\n",
        "Auto-updated after every `test-adb`. Regenerate by hand with "
        "`python -m runner.token_savings --adb [logs_dir]`.\n",
        f"- Batches logged: {stats['runs']}",
        f"- Replayed scenario runs: {stats['replayed_runs']} "
        f"({stats['replayed_model_calls']} model calls, {stats['replayed_tokens']} tokens total)",
        f"- Fully authored scenario runs: {stats['authored_runs']} "
        f"({stats['authored_model_calls']} model calls, {stats['authored_tokens']} tokens total{escalated_note})",
    ]
    if stats["replayed_runs"]:
        lines.append(f"- Avg tokens per replayed run: {stats['avg_replayed_tokens_per_run']:.0f}")
    if stats["authored_runs"]:
        lines.append(f"- Avg tokens per fully authored run: {stats['avg_authored_tokens_per_run']:.0f}")
    if stats["estimated_tokens_saved"] is not None:
        lines.append(
            f"- Estimated tokens saved by replay: ~{stats['estimated_tokens_saved']} "
            "((avg authored tokens/run - avg replayed tokens/run) x replayed runs -- measured from this "
            "project's own logged runs, not a char-count guess)"
        )
    else:
        lines.append(
            "- Estimated tokens saved by replay: not yet available -- needs at least one authored run and "
            "one replayed run of the same kind of scenario logged to compare"
        )
    return "\n".join(lines) + "\n"


def update_adb_summary(logs_dir: Path) -> Path:
    """Recompute the adb-replay savings summary and write it to logs_dir/ADB_TOKEN_SAVINGS.md."""
    stats = aggregate_adb(logs_dir)
    summary_path = logs_dir / "ADB_TOKEN_SAVINGS.md"
    summary_path.write_text(render_adb_summary(stats), encoding="utf-8")
    return summary_path


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    adb_mode = "--adb" in args
    if adb_mode:
        args.remove("--adb")
    target_logs_dir = Path(args[0]) if args else Path("logs")

    if adb_mode:
        written_path = update_adb_summary(target_logs_dir)
    else:
        written_path = update_summary(target_logs_dir)
    print(f"Wrote {written_path}")
