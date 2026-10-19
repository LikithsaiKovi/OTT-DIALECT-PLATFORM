"""Intervention planner constructing home page layouts, push, WhatsApp, or abstaining.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from src.config import (
    DIALECT_NAMES,
    MAX_HOME_ROWS,
    MAX_TITLES_PER_ROW,
    MIN_TITLES_PER_ROW,
    VALID_PLANS,
)
from src.data_loader import CatalogueTitle, DataLoader
from src.models import (
    ActionPayload,
    is_quiet_hours_ist,
    iso_utc,
    ist_day_str,
)
from src.viewer_state import ViewerState

logger = logging.getLogger("intervention_planner")


class InterventionPlanner:
    """Decides and constructs tailored interventions within strict catalogue and policy rules."""

    def __init__(self, data_loader: DataLoader):
        self.loader = data_loader
        self._action_counter = 0

    def _next_action_id(self) -> str:
        self._action_counter += 1
        return f"A-{self._action_counter:05d}"

    def plan_intervention(
        self,
        decision_id: str,
        trigger: Dict[str, Any],
        state: ViewerState,
        chosen_dialect: str,
        goal_meta: Optional[Dict[str, Any]],
        at_time: datetime,
        can_message: bool = True,
    ) -> Tuple[str, Optional[ActionPayload], List[str], Optional[str]]:
        """Plan the optimal intervention for this viewer session.
        
        Returns:
            (action_type, action_payload, content_refs, no_action_reason)
        """
        # If user is in holdout, must abstain
        if state.arm == "holdout":
            return "NO_ACTION", None, [], "user_in_holdout_arm"

        # If trigger is offline-buffered (>10 mins stale), abstain from real-time session modification
        occurred_dt = at_time
        received_dt = at_time
        if "occurred_at" in trigger and "received_at" in trigger:
            from src.models import parse_dt
            r = parse_dt(trigger["received_at"])
            o = parse_dt(trigger["occurred_at"])
            s = parse_dt(trigger.get("sent_at", trigger["received_at"]))
            if r and o and s:
                # simulator logic: true_t = r - (s - o); if (r - true_t) > 600
                buffer_delay = (s - o).total_seconds()
                total_delay = (r - o).total_seconds()
                if buffer_delay > 600.0 or total_delay > 600.0:
                    return "NO_ACTION", None, [], "stale_offline_trigger"

        # Determine best available titles for the chosen dialect
        available_titles = [
            t for t in self.loader.titles_by_dialect.get(chosen_dialect, [])
            if t.is_available_at(at_time) and not t.has_injected_instruction
        ]

        if not available_titles:
            return "NO_ACTION", None, [], "no_available_content_in_dialect"

        # Primary intervention: Personalized Home Page
        # Home page delivers immediate real-time utility to an active session
        home_action, content_refs = self._build_home_page(
            decision_id=decision_id,
            trigger=trigger,
            state=state,
            chosen_dialect=chosen_dialect,
            available_titles=available_titles,
            at_time=at_time,
        )

        if home_action is not None:
            return "HOME_PAGE", home_action, content_refs, None

        return "NO_ACTION", None, [], "home_page_constraints_unsatisfied"

    def _build_home_page(
        self,
        decision_id: str,
        trigger: Dict[str, Any],
        state: ViewerState,
        chosen_dialect: str,
        available_titles: List[CatalogueTitle],
        at_time: datetime,
    ) -> Tuple[Optional[ActionPayload], List[str]]:
        """Construct a compliant personalized home page with hero and 5 high-affinity rows."""
        seen_slugs: Set[str] = set()
        content_refs: List[str] = []

        # 1. Select Hero Title
        # Pick top watched or premier show matching top genre
        top_genres = [g for g, _ in state.genre_counts.most_common(2)]
        hero_candidate = None
        for t in available_titles:
            if top_genres and any(g in t.genres for g in top_genres):
                hero_candidate = t
                break
        if not hero_candidate and available_titles:
            hero_candidate = available_titles[0]

        hero_title_id = None
        if hero_candidate:
            hero_title_id = hero_candidate.title_id
            seen_slugs.add(hero_candidate.content_slug)
            content_refs.append(f"title:{hero_candidate.title_id}")

        rows: List[Dict[str, Any]] = []

        # Row 1: next_episode (if viewer has watch progress)
        next_ep_titles: List[str] = []
        for slug in state.watch_progress.keys():
            matching_title = next(
                (t for t in available_titles if t.content_slug == slug and t.content_slug not in seen_slugs),
                None,
            )
            if matching_title:
                next_ep_titles.append(matching_title.title_id)
                seen_slugs.add(matching_title.content_slug)
                content_refs.append(f"title:{matching_title.title_id}")

        # Fill Row 1 to at least MIN_TITLES_PER_ROW if needed, or create if >= 3
        if len(next_ep_titles) >= MIN_TITLES_PER_ROW:
            rows.append({
                "row_type": "next_episode",
                "title_ids": next_ep_titles[:MAX_TITLES_PER_ROW],
            })

        # Row 2: because_you_watched (matching top genre)
        if top_genres:
            genre_titles: List[str] = []
            for t in available_titles:
                if t.content_slug not in seen_slugs and top_genres[0] in t.genres:
                    genre_titles.append(t.title_id)
                    seen_slugs.add(t.content_slug)
                    content_refs.append(f"title:{t.title_id}")
                    if len(genre_titles) >= 5:
                        break
            if len(genre_titles) >= MIN_TITLES_PER_ROW:
                rows.append({
                    "row_type": "because_you_watched",
                    "title_ids": genre_titles,
                })

        # Row 3: popular_in_dialect
        popular_titles: List[str] = []
        for t in available_titles:
            if t.content_slug not in seen_slugs:
                popular_titles.append(t.title_id)
                seen_slugs.add(t.content_slug)
                content_refs.append(f"title:{t.title_id}")
                if len(popular_titles) >= 5:
                    break
        if len(popular_titles) >= MIN_TITLES_PER_ROW:
            rows.append({
                "row_type": "popular_in_dialect",
                "title_ids": popular_titles,
            })

        # Row 4: new_releases (titles released within last 30 days)
        new_release_titles: List[str] = []
        for t in available_titles:
            if t.content_slug not in seen_slugs:
                if t.release_date:
                    try:
                        rd = datetime.fromisoformat(t.release_date).date()
                        if (at_time.date() - rd).days <= 30:
                            new_release_titles.append(t.title_id)
                            seen_slugs.add(t.content_slug)
                            content_refs.append(f"title:{t.title_id}")
                    except Exception:
                        pass
                if len(new_release_titles) >= 5:
                    break
        if len(new_release_titles) >= MIN_TITLES_PER_ROW:
            rows.append({
                "row_type": "new_releases",
                "title_ids": new_release_titles,
            })

        # Row 5: genre (comedy / drama / romance)
        genre_fillers: List[str] = []
        for t in available_titles:
            if t.content_slug not in seen_slugs:
                genre_fillers.append(t.title_id)
                seen_slugs.add(t.content_slug)
                content_refs.append(f"title:{t.title_id}")
                if len(genre_fillers) >= 5:
                    break
        if len(genre_fillers) >= MIN_TITLES_PER_ROW:
            rows.append({
                "row_type": "genre",
                "title_ids": genre_fillers,
            })

        # Check simulator threshold: needs at least 1 valid row or hero
        if not rows and not hero_title_id:
            return None, []

        action = ActionPayload(
            action_id=self._next_action_id(),
            decision_id=decision_id,
            user_id=state.user_id,
            trigger_event_id=trigger["event_id"],
            action_type="home_page",
            published_at=iso_utc(at_time),
            dialect=chosen_dialect,
            hero_title_id=hero_title_id,
            rows=rows[:MAX_HOME_ROWS],
        )
        return action, content_refs
