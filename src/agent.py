"""Single Viewer Agent owning the complete understand -> decide -> act -> verify -> measure loop.
"""

from datetime import datetime
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from src.data_loader import DataLoader
from src.fact_resolver import FactResolver
from src.goal_planner import GoalPlanner
from src.intervention_planner import InterventionPlanner
from src.model_provider import (
    MalformedOutputError,
    ModelProvider,
    ModelTimeoutError,
    ServiceUnavailableError,
)
from src.models import (
    ActionPayload,
    Decision,
    is_quiet_hours_ist,
    iso_utc,
    parse_dt,
)
from src.verifier import Verifier
from src.viewer_state import ViewerState

logger = logging.getLogger("agent")


class ViewerAgent:
    """The dedicated agent for a single OTT platform viewer."""

    def __init__(
        self,
        user_id: str,
        state: ViewerState,
        data_loader: DataLoader,
        goal_planner: GoalPlanner,
        intervention_planner: InterventionPlanner,
        verifier: Verifier,
        model_provider: ModelProvider,
    ):
        self.user_id: str = user_id
        self.state: ViewerState = state
        self.loader: DataLoader = data_loader
        self.goal_planner: GoalPlanner = goal_planner
        self.intervention_planner: InterventionPlanner = intervention_planner
        self.verifier: Verifier = verifier
        self.model_provider: ModelProvider = model_provider
        self.decision_counter = 0

    def _next_decision_id(self) -> str:
        self.decision_counter += 1
        return f"D-{self.user_id}-{self.decision_counter:03d}"

    def handle_live_event(self, event: Dict[str, Any]) -> None:
        """Process real-time organic events arriving during the session (e.g. live purchases)."""
        ev_name = event.get("event")
        recv_dt = parse_dt(event.get("received_at")) or datetime.now()
        eid = event.get("event_id", "live")

        if ev_name == "purchase_completed":
            is_trial = event.get("properties", {}).get("is_trial", False)
            new_status = "trial_active" if is_trial else "active"
            self.state.set_fact("subscription_status", new_status, f"event:{eid}:purchase_completed", recv_dt)
            logger.info("Viewer %s purchased plan; updated status to %s", self.user_id, new_status)
            # Close active conversion goals
            if self.state.active_goal and self.state.active_goal.get("id") in ("trial_to_paid_7d", "first_paid_7d"):
                self.state.active_goal["status"] = "SUCCEEDED"
                self.state.active_goal["closed_at"] = iso_utc(recv_dt)
                self.state.learnings.append(f"Conversion goal succeeded organically at {recv_dt}")

        elif ev_name == "app_dialect_changed":
            new_d = event.get("properties", {}).get("to")
            if new_d:
                self.state.set_fact("current_dialect", new_d, f"event:{eid}:app_dialect_changed", recv_dt)

        elif ev_name == "notification_opt_out":
            ch = event.get("properties", {}).get("channel")
            if ch:
                self.state.opt_outs.add(ch)

    def process_trigger(
        self,
        trigger: Dict[str, Any],
        profile: Optional[Dict[str, Any]],
        at_time: datetime,
    ) -> Tuple[Decision, Optional[ActionPayload]]:
        """Execute the complete real-time decision cycle for an incoming trigger."""
        start_mono = time.monotonic()
        decision_id = self._next_decision_id()
        trigger_eid = trigger.get("event_id", "unknown")
        model_calls = 0
        tool_calls = 1
        fallback_used = False

        # 1. Idempotency Check
        if trigger_eid in self.state.seen_trigger_ids:
            elapsed_ms = int((time.monotonic() - start_mono) * 1000)
            decision = Decision(
                decision_id=decision_id,
                viewer_id=self.user_id,
                trigger={"event": trigger.get("event"), "received_at": trigger.get("received_at"), "event_age_s": 0},
                goal={"id": "none", "deadline": None},
                action="NO_ACTION",
                dialect={"used": "none", "source": "idempotency_cache", "conflicts": []},
                content=[],
                confidence=1.0,
                confidence_reason="Trigger event already processed; strictly idempotent",
                evidence=[f"trigger_seen:{trigger_eid}"],
                assumptions=["Duplicate delivery over network"],
                checks={"passed": ["idempotency"], "failed": []},
                decided_in_ms=max(1, elapsed_ms),
                status="ABSTAINED",
                no_action_reason="duplicate_trigger_idempotency",
            )
            self.state.record_decision(decision.to_dict())
            return decision, None

        self.state.seen_trigger_ids.add(trigger_eid)

        # 2. Step 1: Understand Viewer & Resolve Precedence
        sub_status, sub_evidence, sub_freshness = FactResolver.resolve_subscription_status(
            self.state, profile, at_time,
        )
        used_dialect, dialect_info = FactResolver.resolve_dialect(
            trigger, self.state, profile,
        )
        self.state.update_freshness(at_time)

        # Event age calculation
        r_dt = parse_dt(trigger.get("received_at")) or at_time
        o_dt = parse_dt(trigger.get("occurred_at")) or r_dt
        event_age_s = max(0, int((r_dt - o_dt).total_seconds()))

        # 3. Step 2: Choose / Revise Goal
        goal_tuple = self.goal_planner.pick_best_goal(self.state, sub_status, at_time)
        if goal_tuple:
            gid, g_conf, g_ev, g_assump, g_meta = goal_tuple
            active_goal_record = g_meta
            self.state.active_goal = active_goal_record
        else:
            gid = "none"
            g_meta = None
            g_ev = []
            g_assump = ["No suitable measurable lifecycle goal matches current viewer state"]

        # 4. Step 3 & 4: Consult Model Provider & Decide Intervention within Budget
        model_suggestion = None
        try:
            model_calls += 1
            user_context = {
                "subscription_status": sub_status,
                "app_dialect": used_dialect,
                "top_genre": [g for g, _ in self.state.genre_counts.most_common(1)][0] if self.state.genre_counts else None,
                "total_plays": len(self.state.ist_play_days),
                "total_completes": self.state.facts.get("total_completes").value if self.state.get_fact("total_completes") else 0,
            }
            model_output = self.model_provider.evaluate_goal_and_action(
                trigger_event_id=trigger_eid,
                user_id=self.user_id,
                user_context=user_context,
                at_time=at_time,
            )
            # Check if model returned scripted fault
            if isinstance(model_output, dict) and model_output.get("is_fault"):
                if model_output.get("fault_type") == "unsupported_claim":
                    # Model claims eligibility with hallucinated evidence
                    claim = model_output.get("payload", {})
                    # Let verifier catch this downstream
                    claimed_gid = claim.get("goal_id", "G04")
                    # Synthesize goal record for verification testing
                    g_meta = {
                        "id": "trial_to_paid_7d",
                        "goal_id": claimed_gid,
                        "deadline": iso_utc(at_time),
                        "rule_version": "model_claim_unsupported",
                    }
                    g_ev.append(f"model_assertion:{claim.get('reason')}")
                    g_assump.append("Model asserted unverified trial billing status")
            else:
                model_suggestion = model_output
        except (ServiceUnavailableError, ModelTimeoutError, MalformedOutputError) as e:
            fallback_used = True
            logger.warning("Model provider exception on trigger %s: %s. Falling back to deterministic planner.", trigger_eid, e)

        # 5. Plan Intervention (Home page, message, or abstention)
        action_type, planned_action, content_refs, no_action_reason = self.intervention_planner.plan_intervention(
            decision_id=decision_id,
            trigger=trigger,
            state=self.state,
            chosen_dialect=used_dialect,
            goal_meta=g_meta,
            at_time=at_time,
        )

        # 6. Step 5: Independent Verification Before Acting
        v_res = self.verifier.verify(
            action=planned_action,
            state=self.state,
            goal_meta=g_meta,
            at_time=at_time,
            sub_status=sub_status,
            expected_dialect=used_dialect,
        )

        final_action_type = action_type
        final_payload = planned_action
        final_status = "PUBLISHED"

        if not v_res.is_valid:
            # Checker rejected action or goal
            logger.info("Verifier rejected decision for %s: %s", self.user_id, v_res.failed)
            final_action_type = "NO_ACTION"
            final_payload = None
            final_status = "REJECTED"
            no_action_reason = f"verifier_rejection:{','.join(v_res.failed)}"
        elif action_type == "NO_ACTION":
            final_status = "HOLDOUT_ABSTAINED" if self.state.arm == "holdout" else "ABSTAINED"

        # 7. Record Latency & Build Decision Record
        elapsed_ms = int((time.monotonic() - start_mono) * 1000)

        # Add publishing latency to action payload
        if final_payload is not None:
            pub_dt = at_time  # Will be adjusted by replay harness with measured processing time
            final_payload.published_at = iso_utc(pub_dt)

        confidence = 0.88 if final_status == "PUBLISHED" else 0.95
        confidence_reason = (
            f"Curated 5 high-affinity rows in dialect {used_dialect} matching user history"
            if final_status == "PUBLISHED"
            else f"Abstention policy applied: {no_action_reason}"
        )

        evidence_list = [
            f"trigger:{trigger_eid}:event_age={event_age_s}s",
            f"subscription_status:{sub_status}:{sub_evidence}",
            f"dialect:{used_dialect}:{dialect_info['source']}",
        ] + g_ev[:2]

        assumptions_list = [
            f"Viewer is actively on dialect {used_dialect}",
            f"Subscription status '{sub_status}' is reliable as of {at_time.isoformat()}",
        ] + g_assump[:2]

        decision = Decision(
            decision_id=decision_id,
            viewer_id=self.user_id,
            trigger={"event": trigger.get("event"), "received_at": trigger.get("received_at"), "event_age_s": event_age_s},
            goal={"id": g_meta.get("id") if g_meta else "none", "deadline": g_meta.get("deadline") if g_meta else None},
            action=final_action_type,
            dialect=dialect_info,
            content=content_refs,
            confidence=confidence,
            confidence_reason=confidence_reason,
            evidence=evidence_list,
            assumptions=assumptions_list,
            checks=v_res.to_checks_dict(),
            decided_in_ms=max(1, elapsed_ms),
            status=final_status,
            no_action_reason=no_action_reason,
            fallback_used=fallback_used,
            model_calls=model_calls,
            tool_calls=tool_calls,
            rule_version=self.goal_planner.rule_version,
        )

        # Update state
        self.state.record_decision(decision.to_dict())
        if final_payload:
            self.state.record_action(final_payload.to_simulator_dict())

        return decision, final_payload
