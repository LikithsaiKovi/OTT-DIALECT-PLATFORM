# AI Collaboration Log: One User, One Agent

> **Engineering Audit of AI-Assisted Implementation**  
> **Author**: Senior Applied AI / Lifecycle Systems Engineer  
> **Evaluation**: Transparent documentation of AI tool utilization, verification protocols, and rejected suggestions.

---

## 1. Overview of AI Utilization

AI tools (Claude, Codex, and LLM coding assistants) were employed as productivity accelerators during the development of this project. Their utility was confined to:
- Rapid prototyping of standard Python dataclass boilerplate (`models.py`).
- Generating baseline string formatting templates and typing annotations.
- Drafting initial regex patterns for catalog prompt injection detection (`data_loader.py`).
- Brainstorming edge-case unit test scenarios for pytest.

**Guiding Engineering Principle**: Generated code is an untrusted draft, never an authoritative implementation. Every line of code, algorithmic design choice, and statistical formula was verified against the project constraints, executed locally, and tested against deterministic test cases.

---

## 2. Key Instances of Rejected AI Recommendations

During the engineering process, AI coding assistants proposed several architectural shortcuts and flawed methodologies that were critically evaluated and **explicitly rejected**:

### Rejection 1: Vector Recommender vs. Lifecycle State Machine
- **What the AI Suggested**: The AI assistant repeatedly attempted to steer the architecture toward a standard collaborative filtering recommender. It proposed embedding title metadata with Sentence-Transformers, constructing user interaction vectors from play history, and generating the home page by computing cosine similarities over the top-k nearest neighbors.
- **Why It Was Rejected**: The candidate brief explicitly cautions: *"This challenge rewards judgment, evidence, timeliness and honest measurement. A clever recommender without a reliable decide, act and measure loop will not score well."* A vector recommender is fundamentally goal-blind: it has no mechanism for committing to a measurable lifecycle target (such as first episode completion within 3 days or series resumption after 3 days of absence), cannot attribute success to an active time window, and lacks the ability to make a principled decision to *do nothing*.
- **Human Implementation**: Designed a stateful, goal-directed lifecycle engine (`ViewerAgent`) that evaluates explicit eligibility rules, sets deterministic deadlines, builds structured home page rows with strict dialect constraints, and tracks attributable outcomes.

---

### Rejection 2: Naive "Served vs. Unserved" Impact Reporting
- **What the AI Suggested**: When drafting the impact analysis module, the AI suggested computing uplift as:
  $$\text{Uplift} = \text{Conversion Rate}(\text{Users Served with Home Page}) - \text{Conversion Rate}(\text{Users Not Served})$$
  Under this formulation, the AI reported a spectacular **+78.28 percentage point uplift** (82.40% play rate for served users vs. 4.12% for unserved users) and drafted marketing copy claiming the agent was a "breakthrough success."
- **Why It Was Rejected**: This is textbook **selection bias (as-treated confounding)**. The agent selectively serves users who open the app during waking hours, possess reliable network connectivity, and have fresh session telemetry. Users who opened the app during quiet hours or whose devices buffered opens offline were intentionally abstained. Comparing served users against unserved users compares highly active, motivated viewers against dormant viewers, entirely confounding the treatment effect with organic activity. This is the exact methodological flaw exposed in the historical pilot report.
- **Human Implementation**: Implemented a rigorous randomized **Intention-To-Treat (ITT)** evaluation comparing all 2,513 viewers assigned to the `agent` arm against all 2,487 viewers assigned to the `holdout` arm, regardless of whether an intervention was published. Causal differences are reported honestly with 95% Wilson/Wald confidence intervals.

---

### Rejection 3: Delegating Goal Eligibility to LLM Reasoning (Trusting Model Output)
- **What the AI Suggested**: The assistant proposed simplifying the `GoalPlanner` by passing the entire viewer snapshot and goal definitions into an LLM prompt:
  ```json
  {"prompt": "Based on the viewer history, determine if the user is eligible for trial_to_paid_7d. Answer JSON."}
  ```
- **Why It Was Rejected**: LLMs hallucinate assertions of fact. This vulnerability was demonstrated by the synthetic replay faults in `replay/model_faults.jsonl`. For triggers `U-01991`, `U-02509`, and `U-04860`, the model hallucinated:
  > *"The user is on an active trial that ends tomorrow (confirmed in billing)."*
  
  In reality, these users were on `never` or `lapsed` plans with zero billing records. Delegating eligibility to model generation would have resulted in catastrophic policy violations and false upsell messaging.
- **Human Implementation**: Enforced a strict trust boundary. Model output is treated as untrusted advice. All goal eligibility is evaluated deterministically against factual state in `src/goal_planner.py`, and verified independently by `src/verifier.py` before any action can be approved.

---

### Rejection 4: Full-Store Reprocessing on Mid-Replay Rule Corrections
- **What the AI Suggested**: When the operational control event arrived at `08:30:00 UTC` correcting `G03` (changing the success criterion from `app_open` to `play_started` on a later IST day), the AI proposed re-running the entire state store, re-evaluating historical decisions from the start of the replay day, and altering past outcome records.
- **Why It Was Rejected**: In a production event-driven architecture, historical decisions published to clients are immutable facts of record. Reprocessing the entire user database is computationally intractable at scale and introduces non-deterministic state corruption. The brief explicitly mandated: *"Apply the new rule only to the affected open goals, version your rules, and don't reprocess every user."*
- **Human Implementation**: Versioned the rule set dynamically (`rule_version = "corrected-2026-10-01T08:30:00Z"`). The `OutcomeJudge` applies the corrected success condition strictly to `G03` goals that were open as of 08:30 UTC, preserving historical integrity while applying updated logic going forward.

---

## 3. Human Quality Control and Verification Protocol

To ensure reliability, safety, and correctness:
1. **Zero LLM Black-Box Execution**: All operational code runs deterministically without requiring third-party API keys, utilizing the replay harness and mock provider for reproducible evaluation.
2. **Comprehensive Automated Testing**: 17 unit and integration tests validate trigger idempotency, dialect conflict resolution, outage handling (HTTP 503), model timeout fallbacks, quiet hours enforcement, and prompt injection neutralization.
3. **Verification Before Action**: Every decision passes through an independent verifier whose code is completely decoupled from the decision generator.
4. **Simulator Validation**: 1,101 actions were submitted to `simulate_outcomes.py`, achieving a **100% platform acceptance rate with zero policy problems or holdout violations**.
