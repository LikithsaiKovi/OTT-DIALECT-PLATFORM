"""Tests for trigger idempotency, duplicate suppression, and stale offline-buffered triggers.
"""

from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.agent import ViewerAgent
from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.goal_planner import GoalPlanner
from src.intervention_planner import InterventionPlanner
from src.model_provider import MockReplayModelProvider
from src.models import iso_utc, parse_dt
from src.verifier import Verifier
from src.viewer_state import ViewerState


@pytest.fixture
def agent_fixture():
    loader = DataLoader(DEFAULT_PACK_DIR)
    loader.load_all()
    state = ViewerState(user_id="U-TEST-001", arm="agent")
    gp = GoalPlanner(loader.goals_config)
    ip = InterventionPlanner(loader)
    vf = Verifier(loader)
    mp = MockReplayModelProvider(DEFAULT_PACK_DIR)
    agent = ViewerAgent("U-TEST-001", state, loader, gp, ip, vf, mp)
    return agent, state, loader


def test_duplicate_trigger_idempotency(agent_fixture):
    """Processing the exact same trigger event_id twice must produce zero duplicate actions."""
    agent, state, loader = agent_fixture
    trigger = {
        "event_id": "test-trig-dup-12345",
        "user_id": "U-TEST-001",
        "event": "app_open",
        "received_at": "2026-10-01T10:00:00.000Z",
        "occurred_at": "2026-10-01T10:00:00.000Z",
        "sent_at": "2026-10-01T10:00:00.000Z",
        "app_dialect": "d1",
        "properties": {},
    }
    profile = {
        "user_id": "U-TEST-001",
        "subscription_status": "never",
        "preferred_dialect": "d1",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }
    at_time = parse_dt("2026-10-01T10:00:00.000Z")

    # First call: publishes action
    dec1, act1 = agent.process_trigger(trigger, profile, at_time)
    assert dec1.status == "PUBLISHED"
    assert act1 is not None

    # Second call with same event_id: idempotent abstention
    dec2, act2 = agent.process_trigger(trigger, profile, at_time)
    assert dec2.status == "ABSTAINED"
    assert act2 is None
    assert dec2.no_action_reason == "duplicate_trigger_idempotency"
    assert "idempotency" in dec2.checks["passed"]


def test_stale_offline_buffered_trigger_handling(agent_fixture):
    """A trigger buffered offline for days or hours must not trigger a live home-page override."""
    agent, state, loader = agent_fixture
    # occurred 2 days ago, received now
    trigger = {
        "event_id": "test-trig-stale-99999",
        "user_id": "U-TEST-001",
        "event": "app_open",
        "occurred_at": "2026-09-28T12:00:00.000Z",
        "sent_at": "2026-10-01T09:59:50.000Z",
        "received_at": "2026-10-01T10:00:00.000Z",
        "app_dialect": "d1",
        "properties": {},
    }
    profile = {
        "user_id": "U-TEST-001",
        "subscription_status": "never",
        "preferred_dialect": "d1",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }
    at_time = parse_dt("2026-10-01T10:00:00.000Z")

    decision, action = agent.process_trigger(trigger, profile, at_time)
    assert action is None
    assert decision.status == "ABSTAINED"
    assert decision.no_action_reason == "stale_offline_trigger"
    assert decision.trigger["event_age_s"] > 600
