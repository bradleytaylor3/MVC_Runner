"""Covers aggregate_adb: it must distinguish test-adb batch logs (tagged
"kind": "adb" by adb_agent.run_adb_batch) from run's code-edit logs sharing
the same logs_dir, split entries into replayed vs fully-authored by the
"replayed" flag adb_replay/adb_agent set on every entry, and only produce
estimated_tokens_saved once there's at least one run on each side to compare
-- a number that looks measured but isn't would be worse than none."""

import json
from pathlib import Path

from runner import token_savings


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _adb_entry(model_calls: int, prompt: int, eval_: int, replayed: bool, escalated: bool = False) -> dict:
    entry = {
        "id": "task-001", "title": "t", "status": "pass",
        "model_calls": model_calls, "prompt_eval_count": prompt, "eval_count": eval_,
        "replayed": replayed, "steps": [],
    }
    if escalated:
        entry["escalated"] = True
    return entry


def test_aggregate_adb_ignores_non_adb_logs_in_the_same_dir(tmp_path: Path):
    _write(tmp_path / "20260101T000000Z.json", {
        "batch_id": "b", "started_at": "x", "stopped_early": False,
        "entries": [{"id": "init", "status": "init"},
                    {"id": "t1", "status": "success", "model_called": True,
                     "prompt_eval_count": 999, "eval_count": 999}],
    })
    stats = token_savings.aggregate_adb(tmp_path)
    assert stats == {
        "runs": 0, "replayed_runs": 0, "authored_runs": 0, "escalated_runs": 0,
        "replayed_model_calls": 0, "replayed_tokens": 0,
        "authored_model_calls": 0, "authored_tokens": 0,
        "avg_replayed_tokens_per_run": 0, "avg_authored_tokens_per_run": 0,
        "estimated_tokens_saved": None,
    }


def test_aggregate_adb_splits_replayed_from_authored_and_counts_escalations(tmp_path: Path):
    _write(tmp_path / "20260101T000000Z.json", {
        "kind": "adb", "batch_id": "b", "started_at": "x", "dry_run": False,
        "entries": [
            {"id": "init", "status": "init"},
            _adb_entry(model_calls=1, prompt=100, eval_=50, replayed=True),
            _adb_entry(model_calls=5, prompt=500, eval_=80, replayed=False),
            _adb_entry(model_calls=4, prompt=400, eval_=60, replayed=False, escalated=True),
        ],
    })
    stats = token_savings.aggregate_adb(tmp_path)

    assert stats["runs"] == 1
    assert stats["replayed_runs"] == 1
    assert stats["authored_runs"] == 2
    assert stats["escalated_runs"] == 1
    assert stats["replayed_model_calls"] == 1
    assert stats["replayed_tokens"] == 150
    assert stats["authored_model_calls"] == 9
    assert stats["authored_tokens"] == 1040
    assert stats["avg_replayed_tokens_per_run"] == 150
    assert stats["avg_authored_tokens_per_run"] == 520
    # (520 - 150) * 1 replayed run
    assert stats["estimated_tokens_saved"] == 370


def test_aggregate_adb_skips_entries_without_model_calls(tmp_path: Path):
    _write(tmp_path / "20260101T000000Z.json", {
        "kind": "adb", "batch_id": "b", "started_at": "x", "dry_run": False,
        "entries": [{"id": "init", "status": "init"}, {"id": "t1", "status": "skipped"}],
    })
    stats = token_savings.aggregate_adb(tmp_path)
    assert stats["replayed_runs"] == 0
    assert stats["authored_runs"] == 0


def test_render_adb_summary_with_no_runs():
    stats = token_savings.aggregate_adb(Path("does-not-exist"))
    summary = token_savings.render_adb_summary(stats)
    assert "No `test-adb` scenario runs logged yet." in summary


def test_render_adb_summary_reports_estimated_savings():
    stats = {
        "runs": 1, "replayed_runs": 2, "authored_runs": 1, "escalated_runs": 1,
        "replayed_model_calls": 2, "replayed_tokens": 300,
        "authored_model_calls": 5, "authored_tokens": 500,
        "avg_replayed_tokens_per_run": 150, "avg_authored_tokens_per_run": 500,
        "estimated_tokens_saved": 700,
    }
    summary = token_savings.render_adb_summary(stats)
    assert "Estimated tokens saved by replay: ~700" in summary
    assert "1 of them escalated from a failed replay" in summary
