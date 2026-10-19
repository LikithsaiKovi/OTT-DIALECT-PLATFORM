"""Tests for independent verification, holdout safety, prompt injection defense, and policy caps.
"""

from datetime import datetime
from pathlib import Path
import pytest

from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.intervention_planner import InterventionPlanner
from src.models import ActionPayload, parse_dt
from src.verifier import Verifier
from src.viewer_state import ViewerState


@pytest.fixture
def loader():
    dl = DataLoader(DEFAULT_PACK_DIR)
    dl.load_all()
    return dl


def test_holdout_arm_receives_zero_actions(loader):
    """Holdout arm users must never have any action approved by the verifier."""
    vf = Verifier(loader)
    state = ViewerState("U-HOLDOUT-001", arm="holdout")
    at_time = parse_dt("2026-10-01T12:00:00.000Z")

    action = ActionPayload(
        action_id="A-9999",
        decision_id="D-9999",
        user_id="U-HOLDOUT-001",
        trigger_event_id="trig-1",
        action_type="home_page",
        published_at="2026-10-01T12:00:00.000Z",
        dialect="d1",
        rows=[{"row_type": "popular_in_dialect", "title_ids": ["T0101", "T0102", "T0103"]}],
    )

    res = vf.verify(action, state, None, at_time, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "holdout" in res.failed


def test_unsupported_model_claim_rejected(loader):
    """When model claims user is eligible for trial_to_paid (G04) without evidence, verifier rejects."""
    vf = Verifier(loader)
    state = ViewerState("U-CLAIM-001", arm="agent")
    at_time = parse_dt("2026-10-01T12:00:00.000Z")

    # Viewer has never subscribed (not trial_active)
    goal_meta = {
        "id": "trial_to_paid_7d",
        "goal_id": "G04",
    }
    res = vf.verify(None, state, goal_meta, at_time, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "eligibility" in res.failed


def test_catalogue_prompt_injection_titles_detected_and_blocked(loader):
    """Titles containing prompt injection instructions in description (T0002, T0522) must be flagged and rejected."""
    t0002 = loader.catalogue.get("T0002")
    assert t0002 is not None
    assert t0002.has_injected_instruction is True

    vf = Verifier(loader)
    state = ViewerState("U-INJ-001", arm="agent")
    at_time = parse_dt("2026-10-01T12:00:00.000Z")

    # Attempt to include injected title T0002 in home page
    action = ActionPayload(
        action_id="A-INJ-1",
        decision_id="D-INJ-1",
        user_id="U-INJ-001",
        trigger_event_id="trig-inj",
        action_type="home_page",
        published_at="2026-10-01T12:00:00.000Z",
        dialect="d1",
        rows=[{"row_type": "popular_in_dialect", "title_ids": ["T0002", "T0102", "T0103"]}],
    )

    res = vf.verify(action, state, None, at_time, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "injection_defense" in res.failed


def test_quiet_hours_messaging_rejected(loader):
    """Messages during 22:00-08:00 IST (16:30-02:30 UTC) must be rejected."""
    vf = Verifier(loader)
    state = ViewerState("U-QUIET-001", arm="agent")
    # 23:30 IST = 18:00 UTC
    night_dt = parse_dt("2026-10-01T18:00:00.000Z")

    action = ActionPayload(
        action_id="A-MSG-1",
        decision_id="D-MSG-1",
        user_id="U-QUIET-001",
        trigger_event_id="trig-msg",
        action_type="push",
        published_at="2026-10-01T18:00:00.000Z",
        send_at="2026-10-01T18:00:00.000Z",
        title_id="T0101",
        text={"title": "Naya Show Dekhiye", "body": "Aapke liye naya show live hai."},
    )

    res = vf.verify(action, state, None, night_dt, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "quiet_hours" in res.failed


def test_frequency_cap_enforcement(loader):
    """At most 1 push or WhatsApp message per user per IST day."""
    vf = Verifier(loader)
    state = ViewerState("U-CAP-001", arm="agent")
    # Daytime: 14:00 IST = 08:30 UTC
    day_dt = parse_dt("2026-10-01T08:30:00.000Z")

    # Record 1 message already sent on 2026-10-01 IST
    state.sent_messages_by_day["2026-10-01"] = 1

    action = ActionPayload(
        action_id="A-CAP-2",
        decision_id="D-CAP-2",
        user_id="U-CAP-001",
        trigger_event_id="trig-cap",
        action_type="push",
        published_at="2026-10-01T08:30:00.000Z",
        send_at="2026-10-01T08:30:00.000Z",
        title_id="T0101",
        text={"title": "Title", "body": "Body"},
    )

    res = vf.verify(action, state, None, day_dt, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "frequency_cap" in res.failed


def test_withdrawn_title_rejected(loader):
    """A title withdrawn via operational control event is rejected if included after withdrawal."""
    vf = Verifier(loader)
    state = ViewerState("U-WITHDRAW-001", arm="agent")

    withdrawal_dt = parse_dt("2026-10-01T07:00:00.000Z")
    loader.apply_title_withdrawal("T0001", withdrawal_dt)

    post_withdrawal_dt = parse_dt("2026-10-01T07:30:00.000Z")
    action = ActionPayload(
        action_id="A-WITH-1",
        decision_id="D-WITH-1",
        user_id="U-WITHDRAW-001",
        trigger_event_id="trig-w",
        action_type="home_page",
        published_at="2026-10-01T07:30:00.000Z",
        dialect="d1",
        hero_title_id="T0001",
        rows=[{"row_type": "popular_in_dialect", "title_ids": ["T0101", "T0102", "T0103"]}],
    )

    res = vf.verify(action, state, None, post_withdrawal_dt, sub_status="never", expected_dialect="d1")
    assert res.is_valid is False
    assert "availability" in res.failed
