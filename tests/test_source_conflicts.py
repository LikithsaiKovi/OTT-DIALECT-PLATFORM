"""Tests for resolving conflicting sources across dialect, subscription status, and consent.
"""

from datetime import datetime
from pathlib import Path
import pytest

from src.config import DEFAULT_PACK_DIR
from src.data_loader import DataLoader
from src.fact_resolver import FactResolver
from src.models import parse_dt
from src.viewer_state import ViewerState


@pytest.fixture
def loader():
    dl = DataLoader(DEFAULT_PACK_DIR)
    dl.load_all()
    return dl


def test_dialect_three_way_conflict_resolution(loader):
    """When app setting, profile, and campaign link name 3 dialects, app setting must win and conflicts stay visible."""
    state = ViewerState("U-CONF-001", arm="agent")
    trigger = {
        "event_id": "trig-conf-1",
        "app_dialect": "d3",
        "properties": {
            "deeplink": "ott://c/diwali_promo/d2/title/T0101",
        },
    }
    profile = {
        "user_id": "U-CONF-001",
        "preferred_dialect": "d1",
        "updated_at": "2026-09-30T00:00:00.000Z",
    }

    used_dialect, dialect_record = FactResolver.resolve_dialect(trigger, state, profile)

    # App setting strictly determines rendering dialect
    assert used_dialect == "d3"
    assert dialect_record["used"] == "d3"
    assert dialect_record["source"] == "app_setting"
    # Conflicts must remain fully visible for audit
    assert "profile:d1" in dialect_record["conflicts"]
    assert "campaign:d2" in dialect_record["conflicts"]


def test_subscription_status_event_precedence_over_stale_snapshot():
    """A newer transactional event strictly overrides an older profile snapshot status."""
    state = ViewerState("U-CONF-002", arm="agent")
    now_dt = parse_dt("2026-10-01T12:00:00.000Z")

    # Stale profile taken on Sept 30 says 'never'
    profile = {
        "user_id": "U-CONF-002",
        "subscription_status": "never",
        "updated_at": "2026-09-30T05:30:00.000Z",
        "snapshot_at": "2026-09-30T05:30:00.000Z",
    }

    # Before event: profile snapshot wins
    status_0, ev_0, _ = FactResolver.resolve_subscription_status(state, profile, now_dt)
    assert status_0 == "never"

    # Transactional purchase event arrives on Oct 1
    event_dt = parse_dt("2026-10-01T08:00:00.000Z")
    state.set_fact("subscription_status", "active", "event:p-999:purchase_completed", event_dt)

    # After event: transactional event beats older profile
    status_1, ev_1, fresh_1 = FactResolver.resolve_subscription_status(state, profile, now_dt)
    assert status_1 == "active"
    assert "purchase_completed" in ev_1
    assert fresh_1 == (now_dt - event_dt).total_seconds()


def test_notification_opt_out_event_overrides_profile_consent():
    """A notification_opt_out event permanently revokes messaging consent."""
    state = ViewerState("U-CONF-003", arm="agent")
    profile = {
        "user_id": "U-CONF-003",
        "notifications_opt_in": "true",
        "whatsapp_opt_in": "true",
    }

    # Initially opt-in is True from profile
    consent_push_0, _ = FactResolver.resolve_consent("push", state, profile)
    consent_wa_0, _ = FactResolver.resolve_consent("whatsapp", state, profile)
    assert consent_push_0 is True
    assert consent_wa_0 is True

    # User opts out of push via event
    state.opt_outs.add("push")
    consent_push_1, ev_p = FactResolver.resolve_consent("push", state, profile)
    consent_wa_1, _ = FactResolver.resolve_consent("whatsapp", state, profile)

    assert consent_push_1 is False
    assert "notification_opt_out" in ev_p
    assert consent_wa_1 is True  # WhatsApp remains consented
