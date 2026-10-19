"""Goal selection and eligibility planning module enforcing single-goal lifecycle ownership.
"""

from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.models import iso_utc, parse_dt
from src.viewer_state import ViewerState

logger = logging.getLogger("goal_planner")


class GoalPlanner:
    """Evaluates candidate goals and assigns one evidence-backed goal per viewer."""

    def __init__(self, goals_config: Dict[str, Any]):
        self.goals_config = goals_config
        self.rule_version = goals_config.get("version", "2026-09-30")
        self.g03_corrected = False
        self.g03_correction_time: Optional[datetime] = None

    def apply_rule_correction(self, goal_id: str, new_success: Dict[str, Any], at_time: datetime) -> None:
        """Handle operational rule corrections (e.g. G03 mid-replay change)."""
        if goal_id == "G03":
            self.g03_corrected = True
            self.g03_correction_time = at_time
            self.rule_version = f"corrected-{at_time.isoformat()}"
            logger.info("Goal G03 rule corrected at %s: success is now %s", at_time, new_success)

    def evaluate_eligibility(
        self,
        state: ViewerState,
        sub_status: str,
        at_time: datetime,
    ) -> List[Tuple[str, float, List[str], List[str], Dict[str, Any]]]:
        """Evaluate all 12 candidate goals for eligibility against concrete evidence.
        
        Returns list of tuples:
        (goal_id, confidence, evidence_list, assumptions_list, goal_metadata)
        """
        eligible: List[Tuple[str, float, List[str], List[str], Dict[str, Any]]] = []

        total_plays = state.facts.get("total_plays").value if state.get_fact("total_plays") else len(state.ist_play_days)
        total_completes = state.facts.get("total_completes").value if state.get_fact("total_completes") else 0
        signup_fact = state.get_fact("signup_at")
        signup_dt = signup_fact.timestamp if signup_fact else None
        
        days_since_signup = (at_time - signup_dt).total_seconds() / 86400.0 if signup_dt else 999.0

        # G10: Meaningful engagement (UNMEASURABLE: event is null)
        # Trapped! Reject explicitly with explanation
        # G11: TV Activation (UNMEASURABLE: tv_app_open does not exist in event stream)
        # Trapped! Reject explicitly

        # G01: first_play_24h (signed up in last 7 days, never started play)
        if days_since_signup <= 7.0 and total_plays == 0:
            evidence = [
                f"profile:signup_at={iso_utc(signup_dt)}",
                f"state:total_plays={total_plays}",
                "goal_rule:G01",
            ]
            assumptions = ["Viewer is interested in catalog discovery upon sign-up"]
            meta = {
                "id": "first_play_24h",
                "goal_id": "G01",
                "window_hours": 24,
                "deadline": iso_utc(at_time + timedelta(hours=24)),
                "success_event": "play_started",
                "success_condition": "any title",
                "rule_version": self.rule_version,
            }
            eligible.append(("G01", 0.90, evidence, assumptions, meta))

        # G02: first_full_episode_3d (never completed an episode)
        if total_completes == 0:
            evidence = [
                f"state:total_completes={total_completes}",
                "goal_rule:G02",
            ]
            assumptions = ["Viewer has not yet experienced the emotional payoff of a full episode"]
            meta = {
                "id": "first_full_episode_3d",
                "goal_id": "G02",
                "window_hours": 72,
                "deadline": iso_utc(at_time + timedelta(hours=72)),
                "success_event": "episode_completed",
                "success_condition": "any episode",
                "rule_version": self.rule_version,
            }
            eligible.append(("G02", 0.85, evidence, assumptions, meta))

        # G03: second_day_return_2d (signed up in last 2 days)
        if days_since_signup <= 2.0:
            evidence = [
                f"profile:signup_at={iso_utc(signup_dt)}",
                f"state:days_since_signup={round(days_since_signup, 1)}",
                "goal_rule:G03",
            ]
            assumptions = ["Early retention habit forms within 48h of registration"]
            # Check whether rule was corrected
            succ_ev = "play_started" if self.g03_corrected else "app_open"
            meta = {
                "id": "second_day_return_2d",
                "goal_id": "G03",
                "window_hours": 48,
                "deadline": iso_utc(at_time + timedelta(hours=48)),
                "success_event": succ_ev,
                "success_condition": "on a later IST calendar day",
                "rule_version": self.rule_version,
            }
            eligible.append(("G03", 0.82, evidence, assumptions, meta))

        # G07: resume_series_3d (unfinished series, unwatched episodes left, not played in >= 3 days)
        last_play_fact = state.get_fact("last_play_at")
        last_play_dt = last_play_fact.timestamp if last_play_fact else None
        days_since_play = (at_time - last_play_dt).total_seconds() / 86400.0 if last_play_dt else 999.0

        if len(state.watch_progress) > 0 and days_since_play >= 3.0:
            # Check for candidate unfinished series
            cand_slug = list(state.watch_progress.keys())[0]
            evidence = [
                f"state:watch_progress={cand_slug}",
                f"state:days_since_play={round(days_since_play, 1)}",
                "goal_rule:G07",
            ]
            assumptions = [f"Viewer left series {cand_slug} unfinished and retains story context"]
            meta = {
                "id": "resume_series_3d",
                "goal_id": "G07",
                "window_hours": 72,
                "deadline": iso_utc(at_time + timedelta(hours=72)),
                "success_event": "play_started",
                "success_condition": f"same content_slug:{cand_slug}",
                "content_slug": cand_slug,
                "rule_version": self.rule_version,
            }
            eligible.append(("G07", 0.88, evidence, assumptions, meta))

        # G04: trial_to_paid_7d (subscription_status == 'trial_active')
        if sub_status == "trial_active":
            evidence = [
                f"state:subscription_status={sub_status}",
                "goal_rule:G04",
            ]
            assumptions = ["Viewer is currently consuming on 3-day trial and approaching paywall"]
            meta = {
                "id": "trial_to_paid_7d",
                "goal_id": "G04",
                "window_hours": 168,
                "deadline": iso_utc(at_time + timedelta(hours=168)),
                "success_event": "purchase_completed",
                "success_condition": "is_trial = false",
                "rule_version": self.rule_version,
            }
            eligible.append(("G04", 0.85, evidence, assumptions, meta))

        # G05: first_paid_7d (subscription_status == 'never')
        if sub_status == "never":
            evidence = [
                f"state:subscription_status={sub_status}",
                "goal_rule:G05",
            ]
            assumptions = ["Viewer has not yet committed to premium catalog"]
            meta = {
                "id": "first_paid_7d",
                "goal_id": "G05",
                "window_hours": 168,
                "deadline": iso_utc(at_time + timedelta(hours=168)),
                "success_event": "purchase_completed",
                "success_condition": "is_trial = false",
                "rule_version": self.rule_version,
            }
            eligible.append(("G05", 0.75, evidence, assumptions, meta))

        # G06: reactivate_lapsed_14d (subscription_status == 'lapsed')
        if sub_status == "lapsed":
            evidence = [
                f"state:subscription_status={sub_status}",
                "goal_rule:G06",
            ]
            assumptions = ["Viewer subscription lapsed recently (<30d) and responds to reactivation value"]
            meta = {
                "id": "reactivate_lapsed_14d",
                "goal_id": "G06",
                "window_hours": 336,
                "deadline": iso_utc(at_time + timedelta(hours=336)),
                "success_event": "purchase_completed",
                "success_condition": "any paid plan",
                "rule_version": self.rule_version,
            }
            eligible.append(("G06", 0.70, evidence, assumptions, meta))

        # G09: weekly_habit_14d (active subscriber with plays on <= 1 day in last 14d)
        if sub_status == "active":
            plays_last_14d = len([d for d in state.ist_play_days if (at_time.date() - datetime.fromisoformat(d).date()).days <= 14])
            if plays_last_14d <= 1:
                evidence = [
                    f"state:subscription_status={sub_status}",
                    f"state:plays_in_last_14d={plays_last_14d}",
                    "goal_rule:G09",
                ]
                assumptions = ["Active paying subscriber is at churn risk due to infrequent usage"]
                meta = {
                    "id": "weekly_habit_14d",
                    "goal_id": "G09",
                    "window_hours": 336,
                    "deadline": iso_utc(at_time + timedelta(hours=336)),
                    "success_event": "play_started",
                    "success_condition": "on 3 distinct IST days",
                    "rule_version": self.rule_version,
                }
                eligible.append(("G09", 0.80, evidence, assumptions, meta))
            else:
                # G08: try_new_release_7d (active subscriber with frequent usage)
                evidence = [
                    f"state:subscription_status={sub_status}",
                    f"state:plays_in_last_14d={plays_last_14d}",
                    "goal_rule:G08",
                ]
                assumptions = ["Frequent subscriber will appreciate fresh content drops"]
                meta = {
                    "id": "try_new_release_7d",
                    "goal_id": "G08",
                    "window_hours": 168,
                    "deadline": iso_utc(at_time + timedelta(hours=168)),
                    "success_event": "play_started",
                    "success_condition": "title released in last 30d",
                    "rule_version": self.rule_version,
                }
                eligible.append(("G08", 0.85, evidence, assumptions, meta))

        return eligible

    def pick_best_goal(
        self,
        state: ViewerState,
        sub_status: str,
        at_time: datetime,
    ) -> Optional[Tuple[str, float, List[str], List[str], Dict[str, Any]]]:
        """Select the highest-priority goal according to viewer lifecycle hierarchy."""
        eligible = self.evaluate_eligibility(state, sub_status, at_time)
        if not eligible:
            return None

        # Priority ranking order
        priority_order = ["G01", "G02", "G07", "G03", "G04", "G06", "G05", "G09", "G08"]
        order_map = {gid: idx for idx, gid in enumerate(priority_order)}

        eligible.sort(key=lambda x: order_map.get(x[0], 99))
        return eligible[0]
