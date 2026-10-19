"""Data structures and domain models for viewer state, decisions, and interventions.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import json

from src.config import IST, QUIET_HOURS_END_IST, QUIET_HOURS_START_IST, UTC


def parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string into timezone-aware datetime."""
    if not dt_str:
        return None
    cleaned = dt_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime as ISO-8601 UTC string with trailing Z."""
    if dt is None:
        return None
    utc_dt = dt.astimezone(UTC)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ist_day_str(dt: datetime) -> str:
    """Return IST calendar day as YYYY-MM-DD."""
    return dt.astimezone(IST).date().isoformat()


def is_quiet_hours_ist(dt: datetime) -> bool:
    """Check if timestamp falls into IST quiet hours (22:00 to 08:00 IST)."""
    h = dt.astimezone(IST).hour
    return h >= QUIET_HOURS_START_IST or h < QUIET_HOURS_END_IST


@dataclass
class ViewerFact:
    """A granular piece of viewer information with full provenance and freshness."""
    name: str
    value: Any
    source: str
    timestamp: datetime
    freshness_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        val = iso_utc(self.value) if isinstance(self.value, datetime) else self.value
        return {
            "name": self.name,
            "value": val,
            "source": self.source,
            "timestamp": iso_utc(self.timestamp),
            "freshness_s": round(self.freshness_s, 1),
        }


@dataclass
class Decision:
    """Machine-readable record of an agent decision conforming to candidate brief Section 4."""
    decision_id: str
    viewer_id: str
    trigger: Dict[str, Any]
    goal: Dict[str, Any]
    action: str  # HOME_PAGE, PUSH, WHATSAPP, IN_APP_MESSAGE, NO_ACTION
    dialect: Dict[str, Any]  # {"used": str, "source": str, "conflicts": List[str]}
    content: List[str]  # e.g. ["title:T0412", ...]
    confidence: float
    confidence_reason: str
    evidence: List[str]
    assumptions: List[str]
    checks: Dict[str, List[str]]  # {"passed": [...], "failed": [...]}
    decided_in_ms: int
    status: str  # PUBLISHED, ABSTAINED, REJECTED, HOLDOUT_ABSTAINED
    no_action_reason: Optional[str] = None
    fallback_used: bool = False
    model_calls: int = 0
    tool_calls: int = 0
    rule_version: str = "2026-09-30"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "decision_id": self.decision_id,
            "viewer_id": self.viewer_id,
            "trigger": self.trigger,
            "goal": self.goal,
            "action": self.action,
            "dialect": self.dialect,
            "content": self.content,
            "confidence": round(self.confidence, 4),
            "confidence_reason": self.confidence_reason,
            "evidence": self.evidence,
            "assumptions": self.assumptions,
            "checks": self.checks,
            "decided_in_ms": self.decided_in_ms,
            "status": self.status,
            "no_action_reason": self.no_action_reason,
            "fallback_used": self.fallback_used,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "rule_version": self.rule_version,
        }
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class ActionPayload:
    """Specific action published for simulation and execution."""
    action_id: str
    decision_id: str
    user_id: str
    trigger_event_id: str
    action_type: str  # home_page, push, whatsapp, in_app_message
    published_at: str
    # Home page fields
    dialect: Optional[str] = None
    hero_title_id: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None
    # Message fields
    send_at: Optional[str] = None
    title_id: Optional[str] = None
    text: Optional[Dict[str, str]] = None
    deeplink: Optional[str] = None
    template_id: Optional[str] = None
    variables: Optional[Dict[str, str]] = None

    def to_simulator_dict(self) -> Dict[str, Any]:
        """Convert to the exact dictionary format expected by simulate_outcomes.py."""
        d: Dict[str, Any] = {
            "action_id": self.action_id,
            "decision_id": self.decision_id,
            "user_id": self.user_id,
            "trigger_event_id": self.trigger_event_id,
            "type": self.action_type,
            "published_at": self.published_at,
        }
        if self.action_type == "home_page":
            d["dialect"] = self.dialect
            if self.hero_title_id:
                d["hero_title_id"] = self.hero_title_id
            d["rows"] = self.rows or []
        elif self.action_type == "push":
            d["send_at"] = self.send_at or self.published_at
            if self.title_id:
                d["title_id"] = self.title_id
            if self.text:
                d["text"] = self.text
            if self.deeplink:
                d["deeplink"] = self.deeplink
        elif self.action_type == "whatsapp":
            d["send_at"] = self.send_at or self.published_at
            d["template_id"] = self.template_id
            d["variables"] = self.variables or {}
            if self.title_id:
                d["title_id"] = self.title_id
        elif self.action_type == "in_app_message":
            d["send_at"] = self.send_at or self.published_at
            if self.title_id:
                d["title_id"] = self.title_id
        return d
