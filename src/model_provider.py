"""Model provider abstraction with fault injection replay and fallback handling.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from src.config import MAX_MODEL_CALLS_PER_DECISION
from src.models import parse_dt

logger = logging.getLogger("model_provider")


class ModelProviderException(Exception):
    """Base exception for model provider errors."""
    pass


class ServiceUnavailableError(ModelProviderException):
    """HTTP 503 or transient outage."""
    pass


class ModelTimeoutError(ModelProviderException):
    """Model call timed out."""
    pass


class MalformedOutputError(ModelProviderException):
    """Model output failed syntax or JSON parsing."""
    pass


class ModelProvider(ABC):
    """Abstract interface for LLM calls."""

    @abstractmethod
    def evaluate_goal_and_action(
        self,
        trigger_event_id: str,
        user_id: str,
        user_context: Dict[str, Any],
        at_time: datetime,
    ) -> Dict[str, Any]:
        """Produce a goal recommendation and intervention plan."""
        pass


class MockReplayModelProvider(ModelProvider):
    """Replay model provider simulating healthy inference, script-injected faults, and outages."""

    def __init__(self, pack_dir: Path):
        self.pack_dir = Path(pack_dir)
        self.faults_by_trigger: Dict[str, Dict[str, Any]] = {}
        self.outage_windows: List[Dict[str, Any]] = []
        self._load_faults_and_outages()

    def _load_faults_and_outages(self) -> None:
        faults_file = self.pack_dir / "replay" / "model_faults.jsonl"
        if faults_file.exists():
            with open(faults_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        self.faults_by_trigger[d["trigger_event_id"]] = d

        control_file = self.pack_dir / "replay" / "control_events.jsonl"
        if control_file.exists():
            with open(control_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        if d.get("type") == "dependency_unavailable" and d.get("dependency") == "model_provider":
                            start_dt = parse_dt(d.get("at"))
                            until_dt = parse_dt(d.get("until"))
                            if start_dt and until_dt:
                                self.outage_windows.append({"start": start_dt, "until": until_dt, "detail": d.get("detail")})

    def is_in_outage(self, at_time: datetime) -> bool:
        """Check if current timestamp falls into an active simulated outage window."""
        for w in self.outage_windows:
            if w["start"] <= at_time < w["until"]:
                return True
        return False

    def evaluate_goal_and_action(
        self,
        trigger_event_id: str,
        user_id: str,
        user_context: Dict[str, Any],
        at_time: datetime,
    ) -> Dict[str, Any]:
        """Evaluate trigger, injecting faults or simulated outages when mandated by replay."""
        # 1. Check for operational outage (HTTP 503)
        if self.is_in_outage(at_time):
            logger.warning("Simulated model provider outage active at %s. Raising HTTP 503.", at_time)
            raise ServiceUnavailableError("HTTP 503: Service Unavailable (simulated dependency outage)")

        # 2. Check for scripted model faults by trigger event id
        fault = self.faults_by_trigger.get(trigger_event_id)
        if fault:
            fault_type = fault.get("fault")
            logger.info("Executing scripted model fault '%s' for trigger %s", fault_type, trigger_event_id)
            if fault_type == "timeout":
                # Simulated timeout - raises TimeoutError to test fast fallback within SLA
                raise ModelTimeoutError(f"Model call exceeded deadline ({fault.get('seconds', 45)}s simulated timeout)")
            elif fault_type == "malformed_output":
                raw_output = fault.get("model_output", "")
                try:
                    # Attempt parse to trigger syntax failure
                    return json.loads(raw_output)
                except Exception as e:
                    raise MalformedOutputError(f"Corrupted model JSON payload: {raw_output}") from e
            elif fault_type == "unsupported_claim":
                # Model returns an invalid/hallucinated claim without evidence
                return {
                    "is_fault": True,
                    "fault_type": "unsupported_claim",
                    "payload": fault.get("model_output"),
                }

        # 3. Healthy model output: provides structured recommendation based on context
        top_genre = user_context.get("top_genre")
        dialect = user_context.get("app_dialect", "d1")
        sub_status = user_context.get("subscription_status", "never")

        return {
            "is_fault": False,
            "recommended_goal": self._suggest_goal(sub_status, user_context),
            "suggested_action": "HOME_PAGE",
            "confidence": 0.85,
            "reasoning": f"Viewer in dialect {dialect} with subscription {sub_status}; curated personalized rows for {top_genre or 'popular'}.",
        }

    def _suggest_goal(self, sub_status: str, context: Dict[str, Any]) -> str:
        total_plays = context.get("total_plays", 0)
        total_completes = context.get("total_completes", 0)
        
        if total_plays == 0:
            return "G01"  # first_play_24h
        if total_completes == 0:
            return "G02"  # first_full_episode_3d
        if sub_status == "trial_active":
            return "G04"  # trial_to_paid_7d
        if sub_status == "never":
            return "G05"  # first_paid_7d
        if sub_status == "lapsed":
            return "G06"  # reactivate_lapsed_14d
        return "G08"  # try_new_release_7d
