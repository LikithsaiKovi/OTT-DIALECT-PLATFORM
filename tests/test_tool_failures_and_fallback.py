"""Tests for model provider failures, timeouts, malformed JSON, and graceful deterministic fallback.
"""

from datetime import datetime
from pathlib import Path
import pytest

from src.agent import ViewerAgent
from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.goal_planner import GoalPlanner
from src.intervention_planner import InterventionPlanner
from src.model_provider import (
    MalformedOutputError,
    MockReplayModelProvider,
    ModelTimeoutError,
    ServiceUnavailableError,
)
from src.models import parse_dt
from src.verifier import Verifier
from src.viewer_state import ViewerState


@pytest.fixture
def agent_and_provider():
    loader = DataLoader(DEFAULT_PACK_DIR)
    loader.load_all()
    state = ViewerState(user_id="U-FAIL-001", arm="agent")
    gp = GoalPlanner(loader.goals_config)
    ip = InterventionPlanner(loader)
    vf = Verifier(loader)
    mp = MockReplayModelProvider(DEFAULT_PACK_DIR)
    agent = ViewerAgent("U-FAIL-001", state, loader, gp, ip, vf, mp)
    return agent, state, mp


def test_model_provider_outage_http_503_fallback(agent_and_provider):
    """During dependency_unavailable window (10:35 to 10:42 UTC), agent handles 503 and falls back gracefully."""
    agent, state, mp = agent_and_provider
    outage_time = parse_dt("2026-10-01T10:38:00.000Z")
    assert mp.is_in_outage(outage_time) is True

    trigger = {
        "event_id": "trig-outage-503",
        "user_id": "U-FAIL-001",
        "event": "app_open",
        "received_at": "2026-10-01T10:38:00.000Z",
        "occurred_at": "2026-10-01T10:38:00.000Z",
        "sent_at": "2026-10-01T10:38:00.000Z",
        "app_dialect": "d1",
        "properties": {},
    }
    profile = {
        "user_id": "U-FAIL-001",
        "subscription_status": "never",
        "preferred_dialect": "d1",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }

    decision, action = agent.process_trigger(trigger, profile, outage_time)

    # Decision must succeed, mark fallback_used, and meet SLA
    assert decision.fallback_used is True
    assert decision.status == "PUBLISHED"
    assert action is not None
    assert decision.decided_in_ms < 60000


def test_model_timeout_fault_fallback(agent_and_provider):
    """When a model call times out (trigger 5edb5b00-0773-4063-8da8-40929553233c), agent falls back safely."""
    agent, state, mp = agent_and_provider
    timeout_trigger = {
        "event_id": "5edb5b00-0773-4063-8da8-40929553233c",  # Defined in model_faults.jsonl
        "user_id": "U-01090",
        "event": "app_open",
        "received_at": "2026-10-01T12:00:00.000Z",
        "occurred_at": "2026-10-01T12:00:00.000Z",
        "sent_at": "2026-10-01T12:00:00.000Z",
        "app_dialect": "d2",
        "properties": {},
    }
    profile = {
        "user_id": "U-01090",
        "subscription_status": "never",
        "preferred_dialect": "d2",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }
    now_dt = parse_dt("2026-10-01T12:00:00.000Z")

    decision, action = agent.process_trigger(timeout_trigger, profile, now_dt)

    assert decision.fallback_used is True
    assert decision.status == "PUBLISHED"
    assert action is not None


def test_malformed_model_output_fallback(agent_and_provider):
    """When model returns corrupted JSON (trigger 76b138f6-0952-456d-904a-bd0d04497cf7), agent falls back safely."""
    agent, state, mp = agent_and_provider
    malformed_trigger = {
        "event_id": "76b138f6-0952-456d-904a-bd0d04497cf7",  # Defined in model_faults.jsonl
        "user_id": "U-02563",
        "event": "app_open",
        "received_at": "2026-10-01T14:00:00.000Z",
        "occurred_at": "2026-10-01T14:00:00.000Z",
        "sent_at": "2026-10-01T14:00:00.000Z",
        "app_dialect": "d3",
        "properties": {},
    }
    profile = {
        "user_id": "U-02563",
        "subscription_status": "never",
        "preferred_dialect": "d3",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }
    now_dt = parse_dt("2026-10-01T14:00:00.000Z")

    decision, action = agent.process_trigger(malformed_trigger, profile, now_dt)

    assert decision.fallback_used is True
    assert decision.status == "PUBLISHED"
    assert action is not None
