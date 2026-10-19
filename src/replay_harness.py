"""Replay harness orchestrating chronological delivery of triggers, live events, and control faults.
"""

from datetime import datetime, timedelta
import heapq
import json
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from src.agent import ViewerAgent
from src.config import (
    DEFAULT_PACK_DIR,
    DEFAULT_SAMPLE_RUN_DIR,
    MAX_DECISION_LATENCY_SECONDS,
)
from src.data_loader import DataLoader, read_jsonl_deduplicated
from src.goal_planner import GoalPlanner
from src.intervention_planner import InterventionPlanner
from src.model_provider import MockReplayModelProvider
from src.models import ActionPayload, Decision, iso_utc, parse_dt
from src.verifier import Verifier
from src.viewer_state import ViewerState, ViewerStateStore

logger = logging.getLogger("replay_harness")


class ReplayHarness:
    """Manages the real-time simulation environment, event queues, and metrics collection."""

    def __init__(self, pack_dir: Path, output_dir: Path):
        self.pack_dir = Path(pack_dir)
        self.output_dir = Path(output_dir)
        self.data_loader = DataLoader(self.pack_dir)
        self.state_store = ViewerStateStore(self.output_dir / "viewer_state")
        self.goal_planner: Optional[GoalPlanner] = None
        self.intervention_planner: Optional[InterventionPlanner] = None
        self.verifier: Optional[Verifier] = None
        self.model_provider: Optional[MockReplayModelProvider] = None
        self.agents: Dict[str, ViewerAgent] = {}

        # Replay event tracking
        self.decisions_written = 0
        self.actions_written = 0
        self.latencies_ms: List[int] = []
        self.degraded_store_windows: List[Dict[str, Any]] = []

    def initialize(self) -> None:
        """Load pack, build indexes, seed historical viewer states, and wire dependencies."""
        logger.info("Initializing Data Pack from %s...", self.pack_dir)
        self.data_loader.load_all()

        self.goal_planner = GoalPlanner(self.data_loader.goals_config)
        self.intervention_planner = InterventionPlanner(self.data_loader)
        self.verifier = Verifier(self.data_loader)
        self.model_provider = MockReplayModelProvider(self.pack_dir)

        # Seed historical viewer states from data/events.jsonl
        self._seed_historical_states()

    def _seed_historical_states(self) -> None:
        """Initialize each viewer's durable state from profiles snapshot and 30-day historical events."""
        logger.info("Seeding historical viewer states...")
        events_file = self.pack_dir / "data" / "events.jsonl"
        if not events_file.exists():
            raise FileNotFoundError(f"Missing historical events at {events_file}")

        # Seed profiles first
        for uid, prof in self.data_loader.profiles.items():
            arm = self.data_loader.arms.get(uid, "agent")
            state = self.state_store.get_or_create(uid, arm=arm)
            signup_dt = parse_dt(prof.get("signup_at"))
            updated_dt = parse_dt(prof.get("updated_at")) or datetime.min
            if signup_dt:
                state.set_fact("signup_at", signup_dt, "profile_snapshot", signup_dt)
            state.set_fact("subscription_status", prof.get("subscription_status", "never"), "profile_snapshot", updated_dt)
            state.set_fact("preferred_dialect", prof.get("preferred_dialect", "d1"), "profile_snapshot", updated_dt)
            state.set_fact("notifications_opt_in", prof.get("notifications_opt_in", "false").lower() == "true", "profile_snapshot", updated_dt)
            state.set_fact("whatsapp_opt_in", prof.get("whatsapp_opt_in", "false").lower() == "true", "profile_snapshot", updated_dt)

        # Ingest events stream
        cat = self.data_loader.catalogue
        for e in read_jsonl_deduplicated(events_file):
            uid = e["user_id"]
            arm = self.data_loader.arms.get(uid, "agent")
            state = self.state_store.get_or_create(uid, arm=arm)
            r_dt = parse_dt(e["received_at"])
            ev = e["event"]
            props = e.get("properties", {})
            sid = e.get("session_id")
            dialect = e.get("app_dialect")

            if dialect:
                state.set_fact("current_dialect", dialect, "events:app_setting", r_dt)

            if ev == "app_open":
                if sid:
                    state.sessions.add(sid)
                if r_dt:
                    from src.models import ist_day_str
                    state.ist_app_open_days.add(ist_day_str(r_dt))
            elif ev == "play_started":
                if sid:
                    state.play_sessions.add(sid)
                if r_dt:
                    from src.models import ist_day_str
                    state.ist_play_days.add(ist_day_str(r_dt))
                    state.set_fact("last_play_at", r_dt, "events:play_started", r_dt)
                tid = props.get("title_id")
                if tid and tid in cat:
                    for g in cat[tid].genres:
                        state.genre_counts[g] += 1
            elif ev == "episode_completed":
                curr_c = state.facts.get("total_completes").value if state.get_fact("total_completes") else 0
                state.set_fact("total_completes", curr_c + 1, "events:episode_completed", r_dt)
                tid = props.get("title_id")
                if tid and tid in cat:
                    slug = cat[tid].content_slug
                    state.watch_progress[slug] = props.get("episode_id", "")
            elif ev == "notification_opt_out":
                ch = props.get("channel")
                if ch:
                    state.opt_outs.add(ch)
            elif ev == "purchase_completed":
                is_trial = props.get("is_trial", False)
                sub_val = "trial_active" if is_trial else "active"
                state.set_fact("subscription_status", sub_val, f"events:{e.get('event_id')}:purchase_completed", r_dt)
            elif ev == "subscription_renewed":
                state.set_fact("subscription_status", "active", f"events:{e.get('event_id')}:subscription_renewed", r_dt)
            elif ev == "subscription_lapsed":
                state.set_fact("subscription_status", "lapsed", f"events:{e.get('event_id')}:subscription_lapsed", r_dt)
            elif ev == "signup_completed":
                if r_dt and not state.get_fact("signup_at"):
                    state.set_fact("signup_at", r_dt, "events:signup_completed", r_dt)

        logger.info("Historical state seeded for %d total viewers.", len(self.state_store._states))

    def _get_or_create_agent(self, user_id: str) -> ViewerAgent:
        if user_id not in self.agents:
            arm = self.data_loader.arms.get(user_id, "agent")
            state = self.state_store.get_or_create(user_id, arm=arm)
            self.agents[user_id] = ViewerAgent(
                user_id=user_id,
                state=state,
                data_loader=self.data_loader,
                goal_planner=self.goal_planner,
                intervention_planner=self.intervention_planner,
                verifier=self.verifier,
                model_provider=self.model_provider,
            )
        return self.agents[user_id]

    def run_replay(self) -> Dict[str, Any]:
        """Execute the replay day in strictly chronological delivery order."""
        logger.info("Beginning Replay day simulation...")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        decisions_path = self.output_dir / "decisions.jsonl"
        actions_path = self.output_dir / "actions.jsonl"

        # 1. Load and queue control events
        control_events = []
        control_file = self.pack_dir / "replay" / "control_events.jsonl"
        if control_file.exists():
            with open(control_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        d = json.loads(line)
                        control_events.append(d)

        # 2. Load live events
        live_events = []
        live_file = self.pack_dir / "replay" / "live_events.jsonl"
        if live_file.exists():
            for e in read_jsonl_deduplicated(live_file):
                live_events.append(e)

        # 3. Load triggers
        triggers = []
        triggers_file = self.pack_dir / "replay" / "triggers.jsonl"
        if triggers_file.exists():
            with open(triggers_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        triggers.append(json.loads(line))

        logger.info(
            "Replay queue: %d triggers, %d live events, %d control events",
            len(triggers), len(live_events), len(control_events),
        )

        # Build chronological event priority queue
        # Items: (timestamp, priority_order, event_type, payload)
        # Priority: control (0) > live (1) > trigger (2)
        event_queue: List[Tuple[datetime, int, str, Dict[str, Any]]] = []

        for c in control_events:
            dt = parse_dt(c["at"])
            if dt:
                heapq.heappush(event_queue, (dt, 0, "control", c))

        for l in live_events:
            dt = parse_dt(l["received_at"])
            if dt:
                heapq.heappush(event_queue, (dt, 1, "live", l))

        for t in triggers:
            dt = parse_dt(t.get("deliver_at") or t.get("received_at"))
            if dt:
                heapq.heappush(event_queue, (dt, 2, "trigger", t))

        # Open output files
        with open(decisions_path, "w", encoding="utf-8") as f_dec, open(actions_path, "w", encoding="utf-8") as f_act:
            while event_queue:
                current_time, _, ev_type, payload = heapq.heappop(event_queue)

                if ev_type == "control":
                    self._handle_control_event(payload, current_time)
                elif ev_type == "live":
                    uid = payload.get("user_id")
                    if uid:
                        agent = self._get_or_create_agent(uid)
                        agent.handle_live_event(payload)
                elif ev_type == "trigger":
                    self._handle_trigger(payload, current_time, f_dec, f_act)

        # Persist all durable states
        state_dir = self.output_dir / "viewer_state"
        self.state_store.persist_all(state_dir)

        # Calculate latency statistics
        latencies = sorted(self.latencies_ms)
        n = len(latencies)
        p50 = latencies[int(n * 0.50)] if n else 0
        p90 = latencies[int(n * 0.90)] if n else 0
        p95 = latencies[int(n * 0.95)] if n else 0
        p99 = latencies[int(n * 0.99)] if n else 0
        max_lat = latencies[-1] if n else 0
        violations = sum(1 for l in latencies if l > MAX_DECISION_LATENCY_SECONDS * 1000)

        latency_report = {
            "total_decisions": n,
            "actions_published": self.actions_written,
            "sla_seconds": MAX_DECISION_LATENCY_SECONDS,
            "sla_violations": violations,
            "p50_ms": p50,
            "p90_ms": p90,
            "p95_ms": p95,
            "p99_ms": p99,
            "max_ms": max_lat,
        }

        with open(self.output_dir / "latency_report.json", "w", encoding="utf-8") as f:
            json.dump(latency_report, f, indent=2)

        logger.info(
            "Replay complete: %d decisions written, %d actions published. p95 latency: %dms, SLA violations: %d",
            self.decisions_written, self.actions_written, p95, violations,
        )
        return latency_report

    def _handle_control_event(self, event: Dict[str, Any], current_time: datetime) -> None:
        """Execute operational control event injections."""
        c_type = event.get("type")
        if c_type == "title_withdrawn":
            tid = event.get("title_id")
            if tid:
                self.data_loader.apply_title_withdrawal(tid, current_time)
        elif c_type == "goal_rule_corrected":
            gid = event.get("goal_id")
            new_succ = event.get("new_success", {})
            if gid:
                self.goal_planner.apply_rule_correction(gid, new_succ, current_time)
        elif c_type == "dependency_degraded":
            self.degraded_store_windows.append({
                "start": current_time,
                "until": parse_dt(event.get("until")),
                "detail": event.get("detail"),
            })
            logger.info("User data store degraded from %s to %s", current_time, event.get("until"))

    def _handle_trigger(
        self,
        trigger: Dict[str, Any],
        current_time: datetime,
        f_dec,
        f_act,
    ) -> None:
        """Process a single real-time trigger through the viewer's dedicated agent."""
        uid = trigger.get("user_id")
        if not uid:
            return

        agent = self._get_or_create_agent(uid)
        profile = self.data_loader.profiles.get(uid)

        # Check if user data store is degraded (simulates latency jitter)
        is_degraded = any(w["start"] <= current_time < w["until"] for w in self.degraded_store_windows)
        simulated_delay_ms = 15 if not is_degraded else 45

        # Process trigger and time duration
        t0 = time.monotonic()
        decision, action = agent.process_trigger(trigger, profile, current_time)
        measured_elapsed_s = time.monotonic() - t0 + (simulated_delay_ms / 1000.0)
        measured_elapsed_ms = int(measured_elapsed_s * 1000)

        # Record decision latency
        decision.decided_in_ms = measured_elapsed_ms
        self.latencies_ms.append(measured_elapsed_ms)

        # Published time is deliver_at + measured processing time
        published_dt = current_time + timedelta(seconds=measured_elapsed_s)
        if action is not None:
            action.published_at = iso_utc(published_dt)

        # Write to machine-readable streams
        f_dec.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
        self.decisions_written += 1

        if action is not None and decision.status == "PUBLISHED":
            f_act.write(json.dumps(action.to_simulator_dict(), ensure_ascii=False) + "\n")
            self.actions_written += 1
