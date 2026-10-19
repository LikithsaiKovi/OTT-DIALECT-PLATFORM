# Known Limitations and Intentional Engineering Trade-offs

> **Documented Technical Debt and Scope Boundaries**  
> **Author**: Senior Applied AI / Lifecycle Systems Engineer  
> **Project**: One User, One Agent Platform

---

## 1. Intentional Architectural Trade-offs

In building a reliable, observable, and fully reproducible lifecycle agent within the designated project constraints, several deliberate trade-offs were made. Prioritizing a deterministic, mathematically honest, and well-tested decide-act-verify-measure loop took precedence over premature infrastructure complexity.

The following sections document what was intentionally left out, why, and how each component would be architected for high-scale enterprise deployment.

---

## 2. In-Memory Priority Queue vs. Distributed Stream Processing (Kafka/Flink)

### Current Implementation
The replay harness sorts triggers, live events, and operational control events into an in-memory Python `heapq` priority queue, processing events sequentially in strictly chronological delivery order.

### Trade-off Rationale
For a replay dataset of 5,000 users, 2,769 triggers, and ~143,000 historical events, in-memory stream coordination executes deterministically in seconds, requires zero external services (Docker, JVM, Zookeeper), and guarantees 100% reproducibility across evaluator laptops.

### Production Scale-Out Path (10M Users / 40 Dialects)
- **Ingestion**: Ingest client telemetry via Apache Kafka / Redpanda partitioned by `user_id` (guaranteeing in-order delivery per viewer).
- **Stream Processing**: Deploy Apache Flink or Apache Spark Structured Streaming with Flink-CEP to detect out-of-order bursts, late arrivals, and offline buffering windows.
- **Backpressure**: Distribute replay streams across worker pools using asynchronous consumer groups.

---

## 3. File-Based JSON Persistence vs. Distributed Key-Value Store

### Current Implementation
Viewer state is persisted as individual JSON files in `sample_run/viewer_state/<user_id>.json`.

### Trade-off Rationale
Evaluators need to inspect what the agent knew, decided, and observed for any specific viewer at any point in time. Plain-text JSON files can be inspected instantly via `cat`, `jq`, or any text editor without requiring database connection strings or schemas.

### Production Scale-Out Path (10M Users / 40 Dialects)
- **Distributed KV Store**: Migrate durable state to Amazon DynamoDB, ScyllaDB, or Aerospike with partition key `user_id`.
- **Actor Concurrency Model**: Implement a single-writer actor model (using Ray, Microsoft Orleans, or Akka) where each viewer's dedicated agent lives in an active virtual actor, preventing write contention and guaranteeing state consistency.
- **Cache Layer**: Maintain hot viewer profiles in an in-memory Redis cluster with a 120-minute sliding TTL matching the home page lifecycle.

---

## 4. Conservative Abstention vs. Cross-Dialect Fallback Heuristics

### Current Implementation
When a dialect has fewer than 3 available titles in the catalog, the agent strictly abstains from publishing a home page, recording `no_action_reason: "no_available_content_in_dialect"`.

### Trade-off Rationale
The platform rule states: *"A row with fewer than 3 valid titles is dropped by the platform... A title is valid only if it is in the page dialect."* Forcing cross-dialect content (e.g., showing Bhojpuri titles to a Haryanvi viewer) degrades user trust, risks offensive linguistic mismatches, and is penalized by the simulator (`p[1] != dialect` causes the simulator to reject personalized pages).

### Production Scale-Out Path
- Develop an explicit fallback matrix approved by editorial leadership (e.g., offering Hindi content with dialect subtitles or dubbing tracks where licensing explicitly permits it).
- Track content catalog deficits in telemetry to signal content acquisition teams regarding underserved regional dialects.

---

## 5. Mock / Scripted Model Provider vs. Live LLM Inference API

### Current Implementation
The default execution utilizes `MockReplayModelProvider`, which implements deterministic rule planning and accurately reproduces the scripted faults in `replay/model_faults.jsonl` (unsupported billing claims, JSON syntax corruption, timeouts) and control outages (HTTP 503).

### Trade-off Rationale
1. **Evaluator Independence**: Evaluators must be able to run `python -m src.cli replay` out-of-the-box on a fresh clone without configuring external OpenAI/Anthropic API keys or incurring inference charges.
2. **Determinism & Reproducibility**: LLM API endpoints introduce non-deterministic latency jitter, network outages, and rate limits that would obscure the core system evaluation.

### Production Scale-Out Path
- Deploy self-hosted Small Language Models (SLMs) such as Llama 3.2 3B or Gemma 2 2B running on local GPU infrastructure via vLLM or TensorRT-LLM.
- Constrain model outputs with structured decoding (Outlines / JSON schema guidance) to eliminate JSON syntax errors at generation time.
- Always retain the independent `Verifier` as a non-negotiable safety gatekeeper.

---

## 6. Fixed 14-Day Simulation Horizon vs. Continuous Online Bandit Feedback

### Current Implementation
Outcomes are evaluated across a discrete 14-day observation window generated by `simulate_outcomes.py`.

### Trade-off Rationale
This matches the evaluation framework supplied in the data pack, enabling exact like-for-like comparison against the baseline and holdout groups.

### Production Scale-Out Path
- Transition from batch outcome judging to a continuous Contextual Multi-Armed Bandit (e.g., LinUCB or Thompson Sampling) where daily reward signals update agent exploration policies in real time.
- Implement delayed feedback modeling to attribute conversions that occur weeks or months after the initial intervention.
