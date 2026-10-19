# Final Evaluation Report: One User, One Agent

> **Comprehensive Engineering & Research Report**  
> **Author**: Senior Applied AI / Lifecycle Systems Engineer  
> **Platform**: OTT Dialect Streaming Platform  
> **Evaluation Period**: Replay Day (2026-10-01 IST) & 14-Day Simulation Horizon

---

## 1. Executive Summary

This report presents the final evaluation of the **One User, One Agent** production system built for STAGE, a regional-dialect OTT platform streaming films, shows, and micro-dramas across 6 dialects (`d1`–`d6`).

Rather than deploying a generic recommendation model, we engineered an autonomous, stateful lifecycle agent for every viewer. Each agent maintains durable memory, resolves conflicting evidence with explicit precedence rules, selects a single measurable goal with a strict deadline, decides whether to intervene or abstain, and verifies all safety policies independently before publishing.

### Key Results at a Glance:
- **100% Platform Acceptance**: 1,101 published actions across 2,769 triggers were 100% accepted by the backend simulator (`simulate_outcomes.py`) with **zero policy problems, zero quiet-hours violations, and zero holdout leaks**.
- **Real-Time Latency SLA**: Median decision latency was **15 ms**, 95th percentile was **15 ms**, and maximum latency was **61 ms**—achieving **0 SLA violations** against the 60,000 ms threshold.
- **Unmasking Pilot Selection Bias**: We recomputed the historical pilot study using randomized arms, proving that the internal report's claimed **+22.4% uplift was entirely an illusion of selection bias**; true randomized ITT uplift was **-1.30%** (95% CI: [-3.76%, +1.16%]).
- **Honest Causal Measurement**: In the 14-day simulation replay, Intention-To-Treat (ITT) play conversion was **26.42%** in the Agent arm vs. **26.90%** in the Holdout arm (causal difference of **-0.48%**, 95% CI: [-2.93%, +1.97%]), whereas a naive as-treated comparison would have misleadingly claimed a **+78.28 percentage point** gain.
- **Resilience Under Adversity**: The system gracefully navigated simulated model provider outages (HTTP 503), model timeouts, malformed JSON, title withdrawals, catalogue prompt injections, and mid-stream goal rule corrections.

---

## 2. Audit of Data Traps and Handling

The supplied synthetic data pack intentionally incorporates real-world product data imperfections. The table below details every data trap discovered, the empirical evidence from our audit, and the architectural handling implemented:

| Trap | Discovered Evidence in Data Pack | Architectural Handling in Agent Engine |
|---|---|---|
| **Duplicate Events** | 2,176 duplicate `event_id` records in `events.jsonl`; 90 in `triggers.jsonl`. | `read_jsonl_deduplicated` maintains a hash set of observed IDs; agents enforce strict trigger idempotency to prevent duplicate interventions. |
| **Clock Skew & Late Events** | Phone device clocks (`occurred_at`, `sent_at`) drift by hours or days. | All ordering and freshness calculations anchor strictly on server `received_at`. Device timestamps are treated as untrusted metadata. |
| **Offline-Buffered Triggers** | 455 triggers in `triggers.jsonl` were buffered offline (>10 min delay), some days old. | Agents compute `event_age_s`. Stale triggers abstain from real-time session interventions (`stale_offline_trigger`) to avoid publishing expired home pages. |
| **Profile Snapshot Stale & Duplicated** | Snapshot is 1 day stale; 30 users have 2 rows with conflicting statuses. | Resolved deterministically by selecting the profile row with maximum `updated_at`. |
| **Missing Brand-New Users** | 50 users in `events.jsonl` are completely absent from `profiles_snapshot.csv`. | State store dynamically synthesizes profile facts from `signup_completed` and initial `app_open` events. |
| **Dialect Source Conflicts** | 856 triggers conflict between profile and app setting; 159 conflict with campaign deeplink. | App setting (`app_dialect`) strictly determines UI layout; conflicting sources are preserved and surfaced in `decision.dialect.conflicts`. |
| **Language Variant Slugs** | Content exists in multiple dialects under distinct `title_id`s sharing a `content_slug`. | Intervention planner tracks `seen_slugs` globally across the entire page, ensuring no content appears twice. |
| **Operational Title Expiry** | Title `T0001` was withdrawn at 07:00 UTC via control event. | `DataLoader.apply_title_withdrawal` truncates title availability; verifier rejects any layout referencing `T0001` post-withdrawal. |
| **Prompt Injections in Catalogue** | Descriptions for `T0002` and `T0522` contain adversarial instructions to the AI agent. | `detect_prompt_injection` flags directives; data is treated as content only; verifier blocks affected titles. |
| **Unmeasurable Goal Traps** | `G10` has `"event": null`; `G11` specifies `tv_app_open` (0 events in entire telemetry). | Goal planner blacklists `G10` and `G11` with explicit documentation; never assigns unmeasurable goals. |
| **Biased Pilot Summary** | Internal report claimed +22.4% uplift comparing served vs unserved viewers. | Recomputed via randomized Intention-To-Treat (ITT: -1.30%), exposing severe selection bias. |

---

## 3. Recomputing the Pilot: Selection Bias Explained

### The Flawed Analysis in `pilot/pilot_summary_report.md`
The historical pilot report presented the following headline:
> *"Users served by the agent reached their goal 40.1% of the time, against 17.7% for all other users: a 2.3x uplift (+22.4 points)."*

### Why This Comparison is Statistically Invalid
This comparison commits the classic error of **conditioning on post-treatment variables** (also known as as-treated analysis or survivorship bias). 

To be "served" by the agent:
1. The user had to actively open the app during waking hours.
2. The user had to possess valid network connectivity.
3. The user had to match available content in their dialect.
4. The user had to be outside quiet hours and under the daily frequency cap.

These conditions select for highly active, engaged users who were already intrinsically more likely to reach their lifecycle goals. Meanwhile, the "all other users" pool lumped together unserved treatment users (inactive or offline users) and the holdout group.

### The True Randomized Intention-To-Treat (ITT) Recomputation
In a randomized controlled trial, the only unbiased estimator of causal impact is **Intention-To-Treat (ITT)**, which compares every user randomized into the Treatment arm against every user randomized into the Holdout arm:

$$\Delta_{\text{ITT}} = \frac{\sum_{i \in \text{Treatment}} Y_i}{N_{\text{Treatment}}} - \frac{\sum_{j \in \text{Holdout}} Y_j}{N_{\text{Holdout}}}$$

| Cohort | Total Users ($N$) | Succeeded ($Y$) | Success Rate | 95% Confidence Interval |
|---|---|---|---|---|
| **Treatment Arm (All Users)** | 1,912 | 349 | **18.25%** | [16.54%, 20.06%] |
| **Holdout Arm (All Users)** | 1,979 | 387 | **19.56%** | [17.83%, 21.37%] |
| **True Causal Uplift ($\Delta_{\text{ITT}}$)** | — | — | **-1.30% points** | **[-3.76%, +1.16%]** |

**Empirical Verdict**: The true causal uplift of the pilot agent was **-1.30 percentage points**, with a 95% confidence interval spanning zero. The agent produced no statistically detectable improvement. Rolling out the pilot system based on the +22.4% headline would have deployed a zero-impact agent to the entire customer base.

---

## 4. Replay Day Experimental Results (14-Day Simulation Horizon)

We executed the complete `One User, One Agent` production pipeline across all 5,000 randomized viewers in the replay dataset (2,513 Agent arm, 2,487 Holdout arm). Outcomes were simulated over 14 days using `simulate_outcomes.py`.

### Primary Intention-To-Treat (ITT) Results

| Outcome Metric | Agent Arm ($N=2513$) | Holdout Arm ($N=2487$) | Causal Uplift ($\Delta$) | 95% Confidence Interval |
|---|---|---|---|---|
| **Play Conversion Rate** | 26.42% | 26.90% | **-0.48%** | [-2.93%, +1.97%] |
| **Episode Completion Rate** | 19.42% | 20.18% | **-0.77%** | [-2.98%, +1.44%] |
| **Paid Plan Conversion Rate** | 1.43% | 1.45% | **-0.01%** | [-0.68%, +0.65%] |

### The Selection Bias Mechanism in the Replay Day
To illustrate how easily evaluators can be misled by naive metrics, we evaluated the as-treated conversion rates within the Agent arm:
- **Served Viewers (Received Home Page)**: **82.40%** play conversion rate.
- **Unserved Viewers (Abstained / Stale / Quiet Hours)**: **4.12%** play conversion rate.
- **Naive Disparity**: **+78.28 percentage points**.

An undisciplined analysis would boast a "20x uplift." In truth, the agent published home pages for viewers who opened the app live during high-propensity evening sessions, while abstaining on stale triggers from offline buffering. The +78.28 point gap reflects user session context, not agent magic.

---

## 5. Real-Time Reliability, Outage Handling & Latency Benchmark

During the replay day, multiple synthetic failures were injected:
- **Model Provider Outage (HTTP 503)**: `2026-10-01T10:35:00Z` to `10:42:00Z`.
- **User Data Store Degradation**: `2026-10-01T13:10:00Z` to `13:17:00Z` (p95 latency spike).
- **Model Timeouts & Malformed JSON**: Scripted faults at triggers `5edb5b00...` and `76b138f6...`.
- **Title Withdrawal**: `T0001` withdrawn at `07:00:00Z`.
- **Goal Rule Correction**: `G03` corrected at `08:30:00Z`.

### Latency Performance Summary:
- **Total Triggers Processed**: 2,769
- **Actions Published**: 1,101
- **Latency Budget**: 60,000 ms (1 minute)
- **SLA Violations (>60s)**: **0 (100% compliance)**
- **Median Latency (p50)**: **15 ms**
- **95th Percentile (p95)**: **15 ms**
- **99th Percentile (p99)**: **31 ms**
- **Maximum Latency**: **61 ms**

When dependencies failed or degraded, the agent's circuit-breaker tripped immediately, falling back to deterministic planning without exceeding the SLA budget.

---

## 6. Technical Discussion Preparation (60-Minute Deep Dive)

Below are detailed, battle-tested engineering answers to the 7 core technical discussion questions:

### Q1: How does your agent decide that what it knows about a viewer is current enough to act on?
**Answer**:  
Every fact in `ViewerState` stores its authoritative timestamp, source, and a computed freshness decay metric:
$$\text{freshness\_s} = \max(0, \text{decision\_time} - \text{fact\_timestamp})$$
We apply explicit precedence rules:
1. **Subscription Status**: Transactional event telemetry (`purchase_completed`, `subscription_renewed`, `subscription_lapsed`) strictly supersedes warehouse profile snapshots if the event timestamp is newer than the snapshot `updated_at`.
2. **Session Freshness**: Phone device clocks are untrusted. We compare `sent_at` and `occurred_at` against server `received_at`. If $\text{event\_age\_s} > 600$ (10 minutes), the trigger is flagged as offline-buffered. The agent updates historical facts but strictly abstains from publishing a real-time home page override, because the user is no longer actively in foreground session.

---

### Q2: When should the agent do nothing, and how do you know that was the right call?
**Answer**:  
Abstention is treated as a first-class, evidenced decision. The agent abstains under five explicit conditions:
1. **Holdout Assignment**: Viewer belongs to `holdout` arm. Doing nothing preserves the randomized control integrity.
2. **Stale / Offline Triggers**: The trigger arrived late from phone buffer. Publishing a 120-minute home page would expire unseen.
3. **Policy Boundaries**: During IST quiet hours (22:00–08:00) or when daily frequency caps (1 msg/day) are reached.
4. **Catalogue Inadequacy**: When fewer than 3 available titles exist in the viewer's dialect, preventing a valid platform row.
5. **Duplicate Ingestion**: Idempotent deduplication when an `event_id` was already processed.

We know it was the right call because the simulator reported **zero policy problems, zero empty pages dropped, and zero holdout contamination**.

---

### Q3: How do you show that the agent caused a change rather than selecting viewers who were already likely to convert?
**Answer**:  
We enforce randomized **Intention-To-Treat (ITT)** analysis. We never evaluate the agent by comparing served users against unserved users, because serving is endogenous to user activity. 

Instead, we evaluate all viewers randomly assigned to the Agent arm against all viewers in the Holdout arm. Because randomization balances both observable and unobservable confounders (such as natural engagement propensity and diurnal activity cycles), any statistically significant divergence between the two arms can be causally attributed to the agent's interventions.

---

### Q4: What happens when 1,000 viewers open the app in the same minute?
**Answer**:  
In our design:
1. **Decoupled Architecture**: Trigger ingestion is separated from decision computation. In production, triggers publish to an Apache Kafka topic partitioned by `user_id`.
2. **Single-Writer Actor Model**: Because each viewer has their own independent agent state, decision processing is embarrassingly parallel across partitions. There is zero global lock contention between distinct viewers.
3. **Low Latency Footprint**: Our deterministic planner executes in under 20 ms. Even on a single core, 1,000 requests complete in <20 seconds. Across 10 worker threads, 1,000 requests process in ~2 seconds, well within the 60-second SLA.
4. **Graceful Degradation**: Under extreme load, optional LLM reasoning calls are bypassed via load-shedding circuit breakers, executing the deterministic rule planner.

---

### Q5: Which decisions should never be automated?
**Answer**:  
1. **Pricing and Commercial Terms**: An agent must never invent, modify, or negotiate subscription prices, discounts, or trial lengths. All prices must come strictly from `config/pricing.json`.
2. **Policy and Consent Overrides**: An agent must never override marketing opt-outs or quiet-hour restrictions, regardless of predicted engagement lift.
3. **Content Censorship / Ingestion**: An agent must never alter catalog metadata, availability windows, or rights clearance.
4. **Autonomous Prompt Alteration from Untrusted Inputs**: Text in catalog descriptions, search queries, or deeplinks must never be executed as instructions to alter agent behavior.

---

### Q6: How would the system work for 10 million viewers and 40 dialects?
**Answer**:  
1. **Streaming Layer**: Apache Kafka / Redpanda with 1,000+ partitions keyed on `user_id`, guaranteeing in-order event delivery per viewer.
2. **State Storage**: Migrate `ViewerState` from local JSON files to a distributed, partitioned NoSQL store (e.g. ScyllaDB, DynamoDB, or Aerospike). Hot user states are cached in Redis clusters with a 120-minute sliding TTL.
3. **Compute Mesh**: Deploy viewer agents as lightweight virtual actors using Ray or Microsoft Orleans, providing distributed stateful computation with automatic horizontal scaling.
4. **Catalogue & Dialect Sharding**: Pre-index available titles, episodes, and genres in an in-memory Redis cluster sharded by dialect. Checking availability across 40 dialects becomes a sub-millisecond set intersection.
5. **Edge LLM Inference**: Deploy quantized, distilled Small Language Models (3B parameters) on GPU clusters via vLLM with structured output decoding (JSON grammar enforcement), guaranteeing sub-50ms inference.

---

### Q7: What did an AI coding assistant suggest that you chose not to accept?
**Answer**:  
The most critical rejection was the AI assistant's insistence on reporting **served vs. unserved uplift**. 

The assistant drafted code that compared the 82.40% conversion rate of served viewers against the 4.12% conversion rate of unserved viewers, celebrating a +78.28% gain. We rejected this suggestion completely because it exemplifies **as-treated selection bias**. We replaced it with a rigorous Intention-To-Treat (ITT) evaluation framework, exposing the exact statistical flaw that contaminated the company's earlier pilot study.

---

## 7. Submission Checklist Verification

| Deliverable | Location | Status | Verified Details |
|---|---|---|---|
| **Runnable CLI / Replay Command** | `src/cli.py` | ✅ Complete | `python -m src.cli replay` runs full pipeline |
| **Durable Per-Viewer State** | `sample_run/viewer_state/` | ✅ Complete | 5,000 JSON state files with provenance |
| **Decision Output (JSONL)** | `sample_run/decisions.jsonl` | ✅ Complete | 2,769 decisions with evidence & checks |
| **Published Actions (JSONL)** | `sample_run/actions.jsonl` | ✅ Complete | 1,101 compliant actions accepted by simulator |
| **Outcome Telemetry & Judgments** | `sample_run/outcomes.jsonl` | ✅ Complete | Attributable closed-loop evaluation |
| **Latency Report (JSON)** | `sample_run/latency_report.json` | ✅ Complete | p95: 15ms, max: 61ms, 0 SLA violations |
| **Impact Report (Markdown)** | `sample_run/impact_report.md` | ✅ Complete | ITT comparison and pilot bias deconstruction |
| **Automated Test Suite** | `tests/` | ✅ Complete | 17 pytest tests passing in ~12 seconds |
| **Architecture Document** | `ARCHITECTURE.md` | ✅ Complete | System design, ownership, trust boundaries |
| **AI Collaboration Log** | `AI_COLLABORATION.md` | ✅ Complete | 4 major rejections and verification protocols |
| **Known Limitations Note** | `KNOWN_LIMITATIONS.md` | ✅ Complete | Documented trade-offs and scale-out path |
| **Final Evaluation Report** | `final_report.md` | ✅ Complete | Complete audit, results, and interview prep |
