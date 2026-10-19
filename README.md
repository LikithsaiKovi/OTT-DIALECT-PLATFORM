# One User, One Agent: Production OTT Lifecycle Agent Engine

> **Senior Applied AI / Lifecycle Engineering Challenge**  
> An autonomous agentic architecture where every viewer has one dedicated agent that owns their lifecycle goal, executes verifiable interventions in real time, and measures true causal impact against a randomized holdout.

---

## 🚀 Quick Start (One Command Replay)

The entire pipeline—historical state seeding, real-time chronological event replay with failure injections, outcome simulation, goal judging, and causal impact reporting—runs with a **single command** using standard Python:

```bash
# 1. Run the full end-to-end replay pipeline
python -m src.cli replay
```

This command executes the end-to-end workflow:
1. Loads all datasets, catalogues, profiles, and configurations from `one_user_one_agent_pack/`.
2. Seeds durable per-viewer states from historical telemetry while resolving duplicates and prompt injections.
3. Replays 2,769 triggers in delivery order alongside live events and operational control events.
4. Enforces independent safety verification and writes `sample_run/decisions.jsonl` and `sample_run/actions.jsonl`.
5. Executes the outcome simulator (`simulate_outcomes.py`) over a 14-day horizon.
6. Attributes outcome telemetry to active goal windows and writes `sample_run/outcomes.jsonl`.
7. Computes randomized Intention-To-Treat (ITT) statistics and generates `sample_run/impact_report.md` and `sample_run/latency_report.json`.

---

## 🧪 Automated Test Suite

A comprehensive test suite covers all required operational edge cases, failure recoveries, and policy constraints:

```bash
# Run the complete test suite (17 tests)
python -m pytest tests/
```

### Verified Test Cases:
- **Late & Duplicate Triggers**: Verified strict idempotency (same `event_id` produces zero duplicate actions) and offline-buffered trigger detection (`event_age_s > 600` abstains from foreground override).
- **Source Conflicts**: App setting dialect strictly wins over profile snapshot and campaign deeplink while preserving visible conflicts; transactional purchase events override stale daily profile snapshots.
- **Dependency Failures & Fallback**: Mock provider simulates HTTP 503 during operational outages, model timeouts, and malformed JSON payloads, smoothly falling back to deterministic planning within the 60s SLA.
- **Rule Corrections Mid-Replay**: Mid-stream correction of `G03` (from `app_open` to `play_started` on a later IST day) dynamically updates only open goals without modifying closed historical records.
- **Independent Safety & Verifier**: Independent verification blocks holdout user actions, rejects unevidenced model claims, neutralizes catalogue prompt injections, prevents messaging during IST quiet hours (22:00–08:00), and enforces daily frequency caps.

---

## 📁 Repository Structure

```text
OTT DIALECT PLATFORM/
├── one_user_one_agent_pack/        # Synthetic data pack (source of truth)
│   ├── data/                       # Events, profiles snapshot, catalogue, episodes
│   ├── config/                     # Goals, interventions, pricing, messaging policy
│   ├── replay/                     # Triggers, live events, control events, model faults, arms
│   ├── pilot/                      # Historical pilot assignment, outcomes, internal report
│   └── simulator/                  # Outcome simulator script
├── src/                            # Modular production engine
│   ├── config.py                   # System constants, constraints, SLA budgets
│   ├── models.py                   # Domain dataclasses, decisions schema, action payloads
│   ├── data_loader.py              # Deduplication, profile resolution, injection detection
│   ├── viewer_state.py             # Durable per-viewer state store with provenance
│   ├── fact_resolver.py            # Conflict resolution & precedence engine
│   ├── model_provider.py           # Provider abstraction with mock fault injection
│   ├── goal_planner.py             # Lifecycle hierarchy & goal eligibility planner
│   ├── intervention_planner.py     # Compliant home page & messaging generator
│   ├── verifier.py                 # Independent safety & policy verification engine
│   ├── agent.py                    # Dedicated Single-Viewer Agent
│   ├── replay_harness.py           # Real-time chronological delivery orchestrator
│   ├── outcome_judge.py            # Telemetry attribution & rule versioning
│   ├── impact_reporter.py          # Honest ITT statistical evaluation
│   └── cli.py                      # Unified CLI entrypoint
├── tests/                          # Automated pytest suite
│   ├── test_triggers_and_idempotency.py
│   ├── test_source_conflicts.py
│   ├── test_tool_failures_and_fallback.py
│   ├── test_goal_rule_correction.py
│   ├── test_verifier_and_safety.py
│   └── test_end_to_end.py
├── sample_run/                     # Generated evaluation artifacts
│   ├── decisions.jsonl             # Complete machine-readable decision records
│   ├── actions.jsonl               # Validated simulator action payloads
│   ├── viewer_state/               # Durable per-viewer JSON states
│   ├── sim/                        # Simulator output events & report
│   ├── outcomes.jsonl              # Judged goal outcomes
│   ├── latency_report.json         # SLA and latency distribution
│   ├── impact_report.md            # Causal ITT statistical evaluation
│   └── final_report.md             # Comprehensive submission report
├── README.md                       # Setup and operational guide
├── ARCHITECTURE.md                 # System architecture & design trade-offs
├── AI_COLLABORATION.md             # Authentic log of AI collaboration & rejected ideas
├── KNOWN_LIMITATIONS.md            # Intentional architectural boundaries
├── final_report.md                 # Root copy of comprehensive final report
└── requirements.txt                # Python dependencies (pytest)
```

---

## 🛠️ CLI Reference

The platform CLI supports individual subcommands for targeted experimentation:

```bash
# 1. Run the full replay pipeline (default)
python -m src.cli replay --pack one_user_one_agent_pack --out sample_run --seed oua-practice

# 2. Recompute and analyze the historical pilot study
python -m src.cli pilot

# 3. Re-judge outcomes from existing simulator telemetry
python -m src.cli judge --pack one_user_one_agent_pack --out sample_run

# 4. Re-generate the markdown impact report
python -m src.cli report --pack one_user_one_agent_pack --out sample_run
```

---

## 📊 Evaluation Highlights

| Metric | Result | Benchmark Requirement | Status |
|---|---|---|---|
| **Decisions Published** | 2,769 / 2,769 | Machine-readable JSONL | ✅ Passed |
| **Actions Accepted by Platform** | 1,101 / 1,101 (100%) | Valid home pages / messages | ✅ Passed |
| **Simulator Policy Problems** | **0** | Zero violations allowed | ✅ Passed |
| **Holdout Arm Interventions** | **0** | Strict 100% holdout isolation | ✅ Passed |
| **Decision Latency (p95)** | **15 ms** | < 60,000 ms SLA budget | ✅ Passed |
| **SLA Violations (>60s)** | **0** | Zero violations | ✅ Passed |
| **Pilot Report Bias Unmasked** | +22.4% (biased) vs **-1.30% (true ITT)** | Expose selection bias | ✅ Passed |
| **Causal Uplift Measurement** | Honest ITT reporting with 95% CI | Honest measurement | ✅ Passed |
