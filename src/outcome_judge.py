"""Outcome judging engine evaluating goal fulfillment from attributable telemetry signals.
"""

from collections import defaultdict
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.data_loader import DataLoader
from src.models import (
    iso_utc,
    ist_day_str,
    parse_dt,
)
from src.viewer_state import ViewerStateStore

logger = logging.getLogger("outcome_judge")

G03_CORRECTION_TIME = parse_dt("2026-10-01T08:30:00.000Z")


class OutcomeJudge:
    """Attributes outcome events to specific goal windows and computes goal status."""

    def __init__(self, pack_dir: Path, run_dir: Path):
        self.pack_dir = Path(pack_dir)
        self.run_dir = Path(run_dir)
        self.loader = DataLoader(self.pack_dir)
        self.state_store = ViewerStateStore(self.run_dir / "viewer_state")

    def judge_all(self) -> List[Dict[str, Any]]:
        """Judge all active goals from recorded decisions against simulator outcome events."""
        self.loader.load_all()
        decisions_file = self.run_dir / "decisions.jsonl"
        outcomes_events_file = self.run_dir / "sim" / "outcome_events.jsonl"
        outcomes_output_file = self.run_dir / "outcomes.jsonl"

        if not decisions_file.exists():
            raise FileNotFoundError(f"Decisions file not found at {decisions_file}")
        if not outcomes_events_file.exists():
            raise FileNotFoundError(f"Outcome events file not found at {outcomes_events_file}")

        # Ingest outcome events grouped by user_id
        events_by_user: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        with open(outcomes_events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    events_by_user[e["user_id"]].append(e)

        # Ingest open goals from decisions.jsonl
        # User may have multiple decisions, take the latest goal committed
        goals_by_user: Dict[str, Dict[str, Any]] = {}
        with open(decisions_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    g = d.get("goal")
                    if g and g.get("id") and g.get("id") != "none":
                        goals_by_user[d["viewer_id"]] = {
                            "decision_id": d["decision_id"],
                            "goal_id": g["id"],
                            "deadline": g["deadline"],
                            "started_at": d.get("trigger", {}).get("received_at"),
                            "status": d.get("status"),
                            "rule_version": d.get("rule_version", "2026-09-30"),
                        }

        judged_records: List[Dict[str, Any]] = []

        for uid, g_info in goals_by_user.items():
            start_dt = parse_dt(g_info["started_at"])
            deadline_dt = parse_dt(g_info["deadline"])
            gid = g_info["goal_id"]

            user_events = events_by_user.get(uid, [])
            # Filter events strictly to attributable goal window [start_dt, deadline_dt]
            window_events = [
                e for e in user_events
                if start_dt and deadline_dt and start_dt <= parse_dt(e["received_at"]) <= deadline_dt
            ]

            succeeded, attr_event = self._evaluate_goal(
                gid=gid,
                start_dt=start_dt,
                deadline_dt=deadline_dt,
                window_events=window_events,
            )

            status = "SUCCEEDED" if succeeded else "EXPIRED_UNMET"
            attr_eid = attr_event.get("event_id") if attr_event else None
            attr_ev_name = attr_event.get("event") if attr_event else None
            attr_at = attr_event.get("received_at") if attr_event else None

            rec = {
                "user_id": uid,
                "decision_id": g_info["decision_id"],
                "goal_id": gid,
                "goal_started_at": g_info["started_at"],
                "deadline": g_info["deadline"],
                "status": status,
                "succeeded": succeeded,
                "attributed_event_id": attr_eid,
                "attributed_event": attr_ev_name,
                "attributed_at": attr_at,
            }
            judged_records.append(rec)

            # Update durable viewer state
            state_file = self.run_dir / "viewer_state" / f"{uid}.json"
            if state_file.exists():
                try:
                    with open(state_file, "r", encoding="utf-8") as sf:
                        s_data = json.load(sf)
                    s_data.setdefault("outcomes_history", []).append(rec)
                    s_data.setdefault("learnings", []).append(
                        f"Evaluated goal {gid} -> {status} (attributed to {attr_ev_name or 'none'})"
                    )
                    with open(state_file, "w", encoding="utf-8") as sf:
                        json.dump(s_data, sf, indent=2, ensure_ascii=False)
                except Exception as ex:
                    logger.warning("Could not update state file for %s: %s", uid, ex)

        # Write outcomes.jsonl
        with open(outcomes_output_file, "w", encoding="utf-8") as f:
            for rec in judged_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        logger.info(
            "Judged %d goals: %d succeeded (%.1f%%)",
            len(judged_records),
            sum(1 for r in judged_records if r["succeeded"]),
            100.0 * sum(1 for r in judged_records if r["succeeded"]) / max(1, len(judged_records)),
        )
        return judged_records

    def _evaluate_goal(
        self,
        gid: str,
        start_dt: datetime,
        deadline_dt: datetime,
        window_events: List[Dict[str, Any]],
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """Evaluate goal against attributable window events."""
        start_ist_day = ist_day_str(start_dt)

        if gid in ("first_play_24h", "G01"):
            for e in window_events:
                if e["event"] == "play_started":
                    return True, e
            return False, None

        elif gid in ("first_full_episode_3d", "G02"):
            for e in window_events:
                if e["event"] == "episode_completed":
                    return True, e
            return False, None

        elif gid in ("second_day_return_2d", "G03"):
            # Mid-replay correction: if goal was started before correction time or still open,
            # new success condition is play_started on a later IST calendar day!
            was_open_at_correction = (start_dt <= G03_CORRECTION_TIME < deadline_dt)
            target_event = "play_started" if (was_open_at_correction or start_dt >= G03_CORRECTION_TIME) else "app_open"

            for e in window_events:
                if e["event"] == target_event:
                    e_dt = parse_dt(e["received_at"])
                    if e_dt and ist_day_str(e_dt) > start_ist_day:
                        return True, e
            return False, None

        elif gid in ("trial_to_paid_7d", "G04"):
            for e in window_events:
                if e["event"] == "purchase_completed":
                    if not e.get("properties", {}).get("is_trial", False):
                        return True, e
            return False, None

        elif gid in ("first_paid_7d", "G05"):
            for e in window_events:
                if e["event"] == "purchase_completed":
                    if not e.get("properties", {}).get("is_trial", False):
                        return True, e
            return False, None

        elif gid in ("reactivate_lapsed_14d", "G06"):
            for e in window_events:
                if e["event"] == "purchase_completed":
                    return True, e
            return False, None

        elif gid in ("resume_series_3d", "G07"):
            for e in window_events:
                if e["event"] == "play_started":
                    return True, e
            return False, None

        elif gid in ("try_new_release_7d", "G08"):
            for e in window_events:
                if e["event"] == "play_started":
                    return True, e
            return False, None

        elif gid in ("weekly_habit_14d", "G09"):
            play_days: Set[str] = set()
            last_ev = None
            for e in window_events:
                if e["event"] == "play_started":
                    e_dt = parse_dt(e["received_at"])
                    if e_dt:
                        play_days.add(ist_day_str(e_dt))
                        last_ev = e
                        if len(play_days) >= 3:
                            return True, last_ev
            return False, None

        return False, None
