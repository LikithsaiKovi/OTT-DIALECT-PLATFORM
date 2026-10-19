# System Architecture: One User, One Agent

> **Design Document & Production Blueprint**  
> **Author**: Senior Applied AI / Lifecycle Systems Engineer  
> **Platform**: OTT Dialect Streaming Platform

---

## 1. Architectural Philosophy: Lifecycle Agent vs. Recommendation System

Conventional OTT personalization relies on unconstrained machine learning recommenders that rank titles by similarity or predicted click-through rate. In regional dialect entertainment, this approach fails because:
1. **Uncertainty & Scarcity**: New viewers lack rich interaction histories; regional dialect variants have constrained catalogs.
2. **Goal Blindness**: A recommender serves content indiscriminately without understanding whether the viewer needs an onboarding nudge, has abandoned an episodic micro-drama mid-series, or is approaching subscription lapse.
3. **Absence of Accountability**: Recommenders rarely measure whether an intervention actually caused incremental engagement or merely rode the coattails of organic behavior.

The **One User, One Agent** architecture replaces static ranking with an autonomous, stateful lifecycle manager. Every viewer is assigned a dedicated agent that maintains durable memory, selects a measurable goal with a strict deadline, decides whether to act or abstain, and verifies all safety constraints before publishing.

---

## 2. Core System Components

```
                ┌────────────────────────────────────────────────────────┐
                │                  Chronological Stream                  │
                │   (replay/triggers.jsonl, live_events, control_events)  │
                └───────────────────────────┬────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                     REPLAY HARNESS                                      │
│                                                                                         │
│  ┌───────────────────────┐   ┌──────────────────────────┐   ┌────────────────────────┐  │
│  │     Data Loader       │   │   Operational Control    │   │  Latency SLA Monitor   │  │
│  │ - Deduplication       │   │ - Outages (HTTP 503)     │   │ - Measured Wall Clock  │  │
│  │ - Prompt Inj. Defense │   │ - Title Withdrawal       │   │ - published_at =       │  │
│  │ - Profile Dedup       │   │ - Mid-Replay Corrections │   │   deliver_at + elapsed │  │
│  └───────────────────────┘   └──────────────────────────┘   └────────────────────────┘  │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                               DEDICATED VIEWER AGENT                                    │
│                                (One Instance per User)                                  │
│                                                                                         │
│  1. UNDERSTAND: Fact Resolver & Provenance                                              │
│     - App setting wins dialect; transactional events override snapshot                  │
│     - Freshness decay tracking: freshness_s = now - fact_timestamp                      │
│                                                                                         │
│  2. PLAN GOAL: Goal Planner                                                             │
│     - 1 active goal per user; enforces measurability (rejects G10/G11 traps)            │
│     - Dynamic rule revision on G03 correction                                           │
│                                                                                         │
│  3. DECIDE: Intervention Planner & Model Provider                                       │
│     - Personalized Home Page: Hero + 5 Rows (unique content_slugs, min 3 / max 10)       │
│     - Principled Abstention (Holdout arm, stale offline triggers, quiet hours)          │
│     - Mock Replay Provider: handles timeouts, HTTP 503, malformed JSON                  │
│                                                                                         │
│  4. VERIFY: Independent Verification Engine (Gatekeeper)                                │
│     - Independent safety veto (Holdout, Dialect, Consent, Quiet Hours, Injection)       │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                DURABLE PERSISTENCE & OUTPUT                             │
│                                                                                         │
│  - decisions.jsonl : Complete machine-readable audit trail with evidence & confidence   │
│  - actions.jsonl   : Compliant interventions accepted by backend simulator              │
│  - viewer_state/   : Durable per-viewer JSON states (facts, history, learnings)         │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                 CLOSED-LOOP EVALUATION                                  │
│                                                                                         │
│  - Outcome Simulator (simulate_outcomes.py): Generates 14-day outcome telemetry         │
│  - Outcome Judge   : Attributes telemetry to goal windows, records learnings            │
│  - Impact Reporter : Unbiased Intention-To-Treat (ITT) evaluation vs Holdout Control    │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Subsystem Deep-Dive

### 3.1 Ownership and Identity
- **Strict 1:1 Mapping**: Each `user_id` is assigned exactly one `ViewerAgent` instance. No two components pursue divergent goals for the same viewer.
- **Single Active Goal**: At any given moment, a viewer has at most one active goal (`state.active_goal`). If circumstances change (e.g., an organic `purchase_completed` event arrives mid-session), the agent revises or closes the active goal before committing to a new lifecycle objective.

### 3.2 Memory, Provenance, and Precedence
Viewer state is durable and persisted to disk (`viewer_state/<user_id>.json`). Every stored fact carries provenance:
- `name`: Identifier of the attribute (e.g., `subscription_status`).
- `value`: Authoritative scalar value.
- `source`: Exact source identifier (`events:<eid>:purchase_completed`, `profiles_snapshot`).
- `timestamp`: UTC timestamp when the fact was established.
- `freshness_s`: Calculated decay representing the age of the fact relative to decision time.

#### Explicit Precedence Rules:
1. **Subscription Status**: Transactional event stream records (`purchase_completed`, `subscription_renewed`, `subscription_lapsed`) strictly supersede the `profiles_snapshot.csv` warehouse export whenever the event timestamp is newer than the snapshot `updated_at`.
2. **Dialect Integrity**: The `app_dialect` sent in the trigger represents the physical client rendering context and strictly determines the home page dialect. Historical preference from profile snapshot or campaign deeplink is tracked, and discrepancies are logged as visible `conflicts` in the decision record.
3. **Messaging Consent**: An opt-out event (`notification_opt_out`) permanently revokes consent, regardless of what the snapshot indicates.
4. **Identity Synthesis**: For brand-new viewers missing from `profiles_snapshot.csv`, facts are synthesized on the fly from `signup_completed` and initial `app_open` events.

### 3.3 Goal Planning and Measurability
Candidate goals from `config/goals.json` are evaluated under strict observability constraints:
- **Unmeasurable Goals are Rejected**:
  - `G10` (`meaningful_engagement_7d`): Rejected because `"event": null`. Telemetry cannot measure undefined semantics.
  - `G11` (`tv_activation_14d`): Rejected because `tv_app_open` is nonexistent in mobile event telemetry (0 occurrences in historical data).
- **Ambiguous Rules are Documented**:
  - `G06` (`reactivate_lapsed_14d`): "Recently lapsed" is operationalized as having lapsed within the preceding 30 days.
  - `G07` (`resume_series_3d`): Unfinished series is verified by checking `episodes.csv` for unwatched episodes after the user's latest logged `episode_id`.
- **Dynamic Rule Corrections**:
  - When `G03` was corrected mid-replay (from `app_open` to `play_started` on a later IST day), only open goals were versioned to the new rule. Closed historical goals remained immutable.

### 3.4 Intervention Planning & Home Page Assembly
- **Dialect Alignment**: Every title on the home page must exist in the client's current `app_dialect`.
- **Slug Deduplication**: Content variants across different title IDs are deduplicated by `content_slug`. No slug appears twice on a single page.
- **Row Composition**: Generates up to 6 rows with 3 to 10 titles per row. If a row falls below 3 available titles, it is dropped to satisfy platform rendering constraints.
- **Affinity Boosts**: Rows prioritize `next_episode` (matching viewer progress), `because_you_watched` (matching viewer top genre), `popular_in_dialect`, and `new_releases`.

### 3.5 Independent Verification Engine (Safety Gatekeeper)
Decision output is never treated as proof. An independent `Verifier` inspects the candidate action and can veto publication:
1. `holdout_check`: Vetoes any action directed at users in the holdout arm.
2. `eligibility_check`: Validates goal eligibility against factual state. Specifically catches and rejects unsupported model claims (such as the injected faults in `model_faults.jsonl`).
3. `dialect_check`: Confirms 100% dialect match across hero and rows.
4. `availability_check`: Verifies title availability windows and enforces real-time operational withdrawals (`T0001` withdrawn at 07:00 UTC).
5. `consent_and_quiet_hours`: Confirms opt-in and blocks push/WhatsApp during IST quiet hours (22:00–08:00 IST).
6. `frequency_cap`: Enforces the strict maximum of 1 message per user per IST day.
7. `prompt_injection_defense`: Rejects any action referencing titles whose catalogue descriptions contain prompt injection instructions.

### 3.6 Real-Time Reliability, Latency & Failure Recovery
- **Idempotency**: Triggers are tracked by `event_id`. Duplicate arrivals produce an idempotent `ABSTAINED` decision without publishing redundant actions.
- **Stale Offline Handling**: Triggers with `event_age_s > 600` (offline buffered) are ingested for historical learning, but the agent abstains from publishing home page overrides.
- **Fault Tolerance**:
  - Model Provider HTTP 503 (outage between 10:35 and 10:42 UTC) is caught and handled via fallback to a deterministic rule-based planner.
  - Model timeouts and malformed JSON payloads trigger immediate fallback.
  - User data store degradation (13:10 to 13:17 UTC) adds simulated latency jitter, yet decisions consistently complete within 15–60 ms, well below the 60,000 ms SLA budget.

---

## 4. Audit of Data Traps and Architectural Mitigations

| Trap Category | Specific Issue Discovered in Data Pack | Architectural Handling & Mitigation |
|---|---|---|
| **Event Duplication** | 2,176 duplicate `event_id` records in `events.jsonl`; 90 in `triggers.jsonl`. | `read_jsonl_deduplicated` maintains in-memory bloom filter of seen IDs; agents enforce strict trigger idempotency. |
| **Clock Skew & Late Events** | Phone device clocks (`occurred_at`, `sent_at`) are unreliable or drifted by hours. | All chronological sorting and freshness calculations anchor on trustworthy server `received_at`. |
| **Offline-Buffered Opens** | 455 triggers in `triggers.jsonl` were buffered offline (>10m old), some days old. | `event_age_s` calculated; stale triggers abstain from real-time session interventions (`stale_offline_trigger`). |
| **Profile Multi-Records** | 30 users appear twice in `profiles_snapshot.csv` with conflicting status. | `DataLoader` resolves conflicts deterministically by selecting the record with maximum `updated_at`. |
| **Missing Brand-New Profiles** | 50 users present in events are absent from the warehouse profile snapshot. | State store synthesizes profile records dynamically from `signup_completed` and initial `app_open`. |
| **Dialect Tri-Conflict** | 856 triggers conflict between profile and app setting; 159 conflict with campaign link. | App setting strictly determines rendering dialect; all conflicting sources logged in `dialect.conflicts`. |
| **Language Variant Slugs** | Content exists in multiple dialects under distinct `title_id`s sharing a `content_slug`. | Intervention planner tracks `seen_slugs` globally across page, guaranteeing zero duplicate content on page. |
| **Operational Title Expiry** | Title `T0001` withdrawn mid-day at 07:00 UTC via control event. | `DataLoader.apply_title_withdrawal` truncates `available_until`; verifier vetoes `T0001` after 07:00 UTC. |
| **Catalogue Injections** | Descriptions of `T0002` and `T0522` contain adversarial instructions to the AI agent. | `detect_prompt_injection` flags adversarial patterns; treats data as content only; verifier blocks affected titles. |
| **Unmeasurable Goals** | `G10` has `"event": null`; `G11` has `event: tv_app_open` (absent from telemetry). | Goal planner explicitly blacklists `G10` and `G11` with recorded rationale; never assigns unmeasurable goals. |
| **Pilot Reporting Bias** | Pilot report claimed +22.4% uplift comparing served vs unserved viewers. | Recomputed as true randomized Intention-To-Treat (ITT: -1.30%), exposing severe selection bias. |

---

## 5. Trust Boundaries & Security Model

```
┌─────────────────────────────────────────────────────────────┐
│                       UNTRUSTED ZONE                        │
│                                                             │
│   - Catalogue Descriptions (Prompt Injections T0002, T0522)  │
│   - Search Queries & Deeplinks                              │
│   - Raw Model Predictions (Hallucinated Claims)             │
│   - Client-reported Device Timestamps (occurred_at)         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ [ Sanitize, Tokenize, Validate ]
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                        TRUSTED ZONE                         │
│                                                             │
│   - Server Timestamps (received_at)                         │
│   - Authoritative Billing / Transactional Events            │
│   - Independent Verifier Rules & Safety Policy              │
│   - Durable Viewer State Machine                            │
│   - Randomized Holdout Arm Registry (arms.csv)              │
└─────────────────────────────────────────────────────────────┘
```

1. **Text is Content, Never Instruction**: No user query, deeplink, or catalog description is ever passed into model prompt instruction channels or executed as control flow.
2. **Verification Independence**: The verifier has zero shared state with model prompt generation, ensuring that model hallucinations can never bypass verification checks.
3. **Data Privacy**: No viewer telemetry or synthetic personal profiles are transmitted to third-party endpoints.
