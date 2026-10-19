"""Configuration and constants for the One User, One Agent platform.
"""

from datetime import timedelta, timezone
from pathlib import Path

# Timezones
UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

# Operational and SLA Constraints
MAX_DECISION_LATENCY_SECONDS = 60.0  # Decisions must be published within 60s of trigger
FAIL_DECISION_LATENCY_SECONDS = 300.0  # More than 5 minutes counts as outright failure
MAX_MODEL_CALLS_PER_DECISION = 8  # Configurable budget
MAX_TOOL_CALLS_PER_DECISION = 20  # Configurable budget

# Home Page Intervention Constraints
PAGE_TTL_MINUTES = 120
PAGE_TTL = timedelta(minutes=PAGE_TTL_MINUTES)
MAX_HOME_ROWS = 6
MIN_TITLES_PER_ROW = 3
MAX_TITLES_PER_ROW = 10
MAX_HERO_TITLES = 1

# Messaging Constraints
QUIET_HOURS_START_IST = 22  # 22:00 IST
QUIET_HOURS_END_IST = 8    # 08:00 IST
MAX_MESSAGES_PER_IST_DAY = 1  # Push + WhatsApp combined

PUSH_MAX_TITLE_CHARS = 40
PUSH_MAX_BODY_CHARS = 120
FORBIDDEN_PUSH_PHRASES = [
    "last chance",
    "hurry",
    "only today",
    "free forever",
    "guaranteed",
]

# Stale trigger threshold
STALE_TRIGGER_THRESHOLD_SECONDS = 600.0  # 10 minutes

# Valid pricing plans
VALID_PLANS = {
    "P_TRIAL": {"price_inr": 1, "days": 3, "renews_to": "P_MONTH"},
    "P_MONTH": {"price_inr": 149, "days": 30},
    "P_QUARTER": {"price_inr": 399, "days": 90},
}
VALID_PRICES_INR = {1, 149, 399}

# Approved WhatsApp Templates
APPROVED_WA_TEMPLATES = {
    "wa_new_episode_v2": {
        "language": "hin",
        "variables": ["title_name", "episode_number"],
        "text": "Naya episode aa gaya! {{title_name}} ka episode {{episode_number}} ab dekhiye.",
    },
    "wa_recommend_title_v1": {
        "language": "hin",
        "variables": ["title_name", "dialect_name"],
        "text": "Aapke liye {{dialect_name}} mein: {{title_name}}. Abhi dekhiye.",
    },
    "wa_trial_ending_v1": {
        "language": "hin",
        "variables": ["plan_price"],
        "text": "Aapka trial jald khatam ho raha hai. {{plan_price}} mein dekhte rahiye.",
    },
}

# Dialect Name Mapping for WhatsApp Templates
DIALECT_NAMES = {
    "d1": "Bhojpuri",
    "d2": "Haryanvi",
    "d3": "Rajasthani",
    "d4": "Maithili",
    "d5": "Magahi",
    "d6": "Malvi",
}

# Base Paths (default to one_user_one_agent_pack)
DEFAULT_PACK_DIR = Path("one_user_one_agent_pack")
DEFAULT_SAMPLE_RUN_DIR = Path("sample_run")
