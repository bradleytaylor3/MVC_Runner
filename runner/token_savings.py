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


if __name__ == "__main__":
    import sys

    target_logs_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs")
    written_path = update_summary(target_logs_dir)
    print(f"Wrote {written_path}")
