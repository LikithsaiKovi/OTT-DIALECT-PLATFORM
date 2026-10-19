"""Durable per-viewer state store maintaining facts with provenance, goals, plans, and history.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.models import ViewerFact, iso_utc, ist_day_str, parse_dt


class ViewerState:
    """The complete, durable mental state and lifecycle history for a single viewer."""

    def __init__(self, user_id: str, arm: str = "agent"):
        self.user_id: str = user_id
        self.arm: str = arm
        self.facts: Dict[str, ViewerFact] = {}
        self.watch_progress: Dict[str, str] = {}  # slug -> latest episode_id
        self.genre_counts: Counter = Counter()
        self.sessions: Set[str] = set()
        self.play_sessions: Set[str] = set()
        self.ist_app_open_days: Set[str] = set()
        self.ist_play_days: Set[str] = set()
        self.opt_outs: Set[str] = set()  # "push", "whatsapp"
        self.active_goal: Optional[Dict[str, Any]] = None
        self.active_plan: Optional[Dict[str, Any]] = None
        self.decisions_history: List[Dict[str, Any]] = []
        self.actions_history: List[Dict[str, Any]] = []
        self.outcomes_history: List[Dict[str, Any]] = []
        self.learnings: List[str] = []
        self.sent_messages_by_day: Dict[str, int] = defaultdict(int)  # ist_day -> count
        self.seen_trigger_ids: Set[str] = set()
        self.last_seen_at: Optional[datetime] = None

    def set_fact(self, name: str, value: Any, source: str, timestamp: datetime, current_time: Optional[datetime] = None) -> None:
        """Set a fact with provenance and compute freshness."""
        ref_time = current_time or timestamp
        freshness_s = max(0.0, (ref_time - timestamp).total_seconds())
        self.facts[name] = ViewerFact(
            name=name,
            value=value,
            source=source,
            timestamp=timestamp,
            freshness_s=freshness_s,
        )

    def get_fact(self, name: str) -> Optional[ViewerFact]:
        return self.facts.get(name)

    def update_freshness(self, current_time: datetime) -> None:
        """Update freshness calculations for all stored facts based on the current evaluation time."""
        for fact in self.facts.values():
            fact.freshness_s = max(0.0, (current_time - fact.timestamp).total_seconds())

    def record_decision(self, decision_dict: Dict[str, Any]) -> None:
        self.decisions_history.append(decision_dict)

    def record_action(self, action_dict: Dict[str, Any]) -> None:
        self.actions_history.append(action_dict)
        if action_dict.get("type") in ("push", "whatsapp"):
            send_dt = parse_dt(action_dict.get("send_at") or action_dict.get("published_at"))
            if send_dt:
                day = ist_day_str(send_dt)
                self.sent_messages_by_day[day] += 1

    def record_outcome(self, outcome_dict: Dict[str, Any]) -> None:
        self.outcomes_history.append(outcome_dict)
        # Record learning
        goal_name = outcome_dict.get("goal_id", "unknown")
        status = outcome_dict.get("status", "unknown")
        learning = f"Goal {goal_name} ended with status {status} on {outcome_dict.get('attributed_at')}"
        self.learnings.append(learning)
        if self.active_goal and self.active_goal.get("id") == outcome_dict.get("goal_id"):
            self.active_goal["status"] = status
            self.active_goal["closed_at"] = outcome_dict.get("attributed_at")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize complete viewer state to JSON-serializable dictionary."""
        return {
            "user_id": self.user_id,
            "arm": self.arm,
            "last_seen_at": iso_utc(self.last_seen_at),
            "facts": {k: v.to_dict() for k, v in self.facts.items()},
            "watch_progress": self.watch_progress,
            "top_genres": [g for g, _ in self.genre_counts.most_common(3)],
            "sessions_count": len(self.sessions),
            "play_sessions_count": len(self.play_sessions),
            "ist_app_open_days": sorted(list(self.ist_app_open_days)),
            "ist_play_days": sorted(list(self.ist_play_days)),
            "opt_outs": sorted(list(self.opt_outs)),
            "active_goal": self.active_goal,
            "active_plan": self.active_plan,
            "decisions_count": len(self.decisions_history),
            "actions_count": len(self.actions_history),
            "outcomes_count": len(self.outcomes_history),
            "outcomes_history": self.outcomes_history,
            "learnings": self.learnings,
            "sent_messages_by_day": dict(self.sent_messages_by_day),
            "recent_decisions": self.decisions_history[-5:],
        }

    def save_to_file(self, output_dir: Path) -> Path:
        """Persist state as an inspectable JSON artifact."""
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{self.user_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        return file_path


class ViewerStateStore:
    """Manages in-memory registry and durable persistence of ViewerState objects."""

    def __init__(self, persistence_dir: Optional[Path] = None):
        self.persistence_dir = Path(persistence_dir) if persistence_dir else None
        self._states: Dict[str, ViewerState] = {}

    def get_or_create(self, user_id: str, arm: str = "agent") -> ViewerState:
        if user_id not in self._states:
            self._states[user_id] = ViewerState(user_id=user_id, arm=arm)
        return self._states[user_id]

    def get(self, user_id: str) -> Optional[ViewerState]:
        return self._states.get(user_id)

    def persist_all(self, directory: Optional[Path] = None) -> None:
        """Write all states to disk for evaluation inspection."""
        target_dir = Path(directory) if directory else self.persistence_dir
        if not target_dir:
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for state in self._states.values():
            state.save_to_file(target_dir)
