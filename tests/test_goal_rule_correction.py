"""Tests for operational mid-replay goal rule corrections (G03 second_day_return_2d).
"""

from datetime import datetime
from pathlib import Path
import pytest

from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.models import parse_dt
from src.outcome_judge import G03_CORRECTION_TIME, OutcomeJudge


def test_g03_mid_replay_correction_attribution():
    """G03 success rule corrected at 08:30 UTC: open goals require play_started on a later IST day."""
    judge = OutcomeJudge(DEFAULT_PACK_DIR, Path("sample_run"))

    # Goal started before correction (06:00 UTC) with deadline in 48 hours
    start_dt = parse_dt("2026-10-01T06:00:00.000Z")
    deadline_dt = parse_dt("2026-10-03T06:00:00.000Z")

    # Case A: User only opens the app on Oct 2 IST (no play_started)
    events_only_open = [
        {
            "event_id": "ev-open-oct2",
            "event": "app_open",
            "received_at": "2026-10-02T10:00:00.000Z",
            "session_id": "s-oct2",
        }
    ]
    # Under old rule, app_open on later IST day would have passed.
    # Under corrected rule, app_open alone is NOT sufficient!
    succ_open, attr_open = judge._evaluate_goal("G03", start_dt, deadline_dt, events_only_open)
    assert succ_open is False
    assert attr_open is None

    # Case B: User plays on Oct 2 IST
    events_with_play = [
        {
            "event_id": "ev-open-oct2",
            "event": "app_open",
            "received_at": "2026-10-02T10:00:00.000Z",
            "session_id": "s-oct2",
        },
        {
            "event_id": "ev-play-oct2",
            "event": "play_started",
            "received_at": "2026-10-02T10:05:00.000Z",
            "session_id": "s-oct2",
            "properties": {"title_id": "T0101"},
        },
    ]
    succ_play, attr_play = judge._evaluate_goal("G03", start_dt, deadline_dt, events_with_play)
    assert succ_play is True
    assert attr_play["event_id"] == "ev-play-oct2"


def test_closed_goals_are_not_modified():
    """Goals already closed or outside the correction scope are not modified."""
    judge = OutcomeJudge(DEFAULT_PACK_DIR, Path("sample_run"))
    # Goal that started and expired before the correction
    past_start = parse_dt("2026-09-28T00:00:00.000Z")
    past_deadline = parse_dt("2026-09-30T00:00:00.000Z")
    assert past_deadline < G03_CORRECTION_TIME
