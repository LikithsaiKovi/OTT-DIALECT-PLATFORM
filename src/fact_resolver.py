"""Fact resolver implementing explicit precedence, freshness, and conflict-resolution rules.
"""

from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from src.models import ViewerFact, iso_utc, parse_dt
from src.viewer_state import ViewerState


class FactResolver:
    """Resolves authoritative viewer facts from disparate and conflicting sources."""

    @staticmethod
    def resolve_dialect(
        trigger: Dict[str, Any],
        state: ViewerState,
        profile: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Resolve dialect with explicit precedence and conflict logging.
        
        Rule:
        - The current trigger's `app_dialect` determines the physical reality of the client rendering.
        - If trigger has no dialect, fallback to state's current_dialect or profile preferred_dialect, then 'd1'.
        - Conflicts with profile snapshot or campaign deeplink are captured for auditability.
        """
        app_dialect = trigger.get("app_dialect")
        prof_dialect = profile.get("preferred_dialect") if profile else None
        
        # Check deeplink campaign dialect if present
        deeplink = trigger.get("properties", {}).get("deeplink", "")
        campaign_dialect = None
        match = re.search(r"ott://c/[^/]+/([^/]+)/title/", deeplink)
        if match:
            campaign_dialect = match.group(1)

        conflicts: List[str] = []
        source = "app_setting"
        used_dialect = app_dialect

        if not used_dialect:
            fact = state.get_fact("current_dialect")
            if fact:
                used_dialect = fact.value
                source = "viewer_state_history"
            elif prof_dialect:
                used_dialect = prof_dialect
                source = "profile_preferred"
            else:
                used_dialect = "d1"
                source = "fallback_default"

        if prof_dialect and prof_dialect != used_dialect:
            conflicts.append(f"profile:{prof_dialect}")
        if campaign_dialect and campaign_dialect != used_dialect:
            conflicts.append(f"campaign:{campaign_dialect}")

        dialect_record = {
            "used": used_dialect,
            "source": source,
            "conflicts": conflicts,
        }
        return used_dialect, dialect_record

    @staticmethod
    def resolve_subscription_status(
        state: ViewerState,
        profile: Optional[Dict[str, Any]],
        at_time: datetime,
    ) -> Tuple[str, str, float]:
        """Resolve subscription status by comparing profile snapshot vs transactional events.
        
        Rule:
        - Transactional events (purchase_completed, subscription_renewed, subscription_lapsed)
          strictly supersede profiles_snapshot if event received_at > snapshot updated_at.
        - Returns: (status, evidence_str, freshness_s)
        """
        sub_fact = state.get_fact("subscription_status")
        prof_status = profile.get("subscription_status") if profile else "never"
        prof_updated_at = parse_dt(profile.get("updated_at")) if profile else None

        if sub_fact is not None:
            # Check if state's transactional event is newer than snapshot
            if prof_updated_at is None or sub_fact.timestamp >= prof_updated_at:
                freshness = max(0.0, (at_time - sub_fact.timestamp).total_seconds())
                return sub_fact.value, sub_fact.source, freshness

        # Otherwise fallback to profile snapshot
        freshness = max(0.0, (at_time - prof_updated_at).total_seconds()) if prof_updated_at else 86400.0
        return prof_status, f"profile_snapshot:{profile.get('snapshot_at') if profile else 'none'}", freshness

    @staticmethod
    def resolve_consent(
        channel: str,
        state: ViewerState,
        profile: Optional[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """Resolve marketing/messaging consent for push or whatsapp.
        
        Rule:
        - notification_opt_out event in state strictly overrides profile snapshot.
        """
        if channel in state.opt_outs:
            return False, f"event:notification_opt_out:{channel}"

        if not profile:
            return False, "default:no_profile_consent"

        consent_field = "notifications_opt_in" if channel == "push" else "whatsapp_opt_in"
        val = profile.get(consent_field, "false").lower() == "true"
        return val, f"profile:{consent_field}={val}"
