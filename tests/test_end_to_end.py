"""Integration test running the replay pipeline and verifying output artifacts.
"""

from pathlib import Path
import json
import pytest

from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.replay_harness import ReplayHarness
from src.models import parse_dt


def test_replay_produces_decisions_actions_and_states(tmp_path):
    """Replay harness runs cleanly, generates valid JSONL records, and persists states."""
    output_dir = tmp_path / "test_run"
    harness = ReplayHarness(pack_dir=DEFAULT_PACK_DIR, output_dir=output_dir)
    harness.initialize()

    # Verify data loaded
    assert len(harness.data_loader.catalogue) > 0
    assert len(harness.data_loader.profiles) > 0
    assert len(harness.data_loader.arms) > 0

    # Run replay
    latency_report = harness.run_replay()

    decisions_file = output_dir / "decisions.jsonl"
    actions_file = output_dir / "actions.jsonl"
    latency_file = output_dir / "latency_report.json"
    state_dir = output_dir / "viewer_state"

    assert decisions_file.exists()
    assert actions_file.exists()
    assert latency_file.exists()
    assert state_dir.exists()

    # Validate decision lines
    decision_count = 0
    holdout_actions = 0
    with open(decisions_file, "r", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            decision_count += 1
            assert "decision_id" in d
            assert "viewer_id" in d
            assert "trigger" in d
            assert "goal" in d
            assert "action" in d
            assert "status" in d
            assert "checks" in d
            assert "evidence" in d
            assert "confidence" in d

    assert decision_count == 2769

    # Verify zero actions published for holdout arm users
    arms = harness.data_loader.arms
    with open(actions_file, "r", encoding="utf-8") as f:
        for line in f:
            act = json.loads(line)
            assert arms.get(act["user_id"]) != "holdout"

    # Verify latency stats
    assert latency_report["sla_violations"] == 0
    assert latency_report["p95_ms"] < 60000
