# GroundedOps SLO alert runbooks

## api-availability-low

**Means:** Users are receiving more than 1% server errors for at least five minutes.
**First check:** Compare errors by operation and inspect a failing trace using its correlation ID; then check the latest deployment and dependency health.
**Escalate to:** Engineering on-call; involve the owning dependency team when one provider dominates the failures.

## retrieval-latency-high

**Means:** Retrieval p95 is above 800 ms and can delay both Search and Ask.
**First check:** Split retrieval duration by outcome and inspect OpenSearch latency, saturation and degraded-mode usage.
**Escalate to:** Engineering on-call; involve search operations if OpenSearch is the bottleneck.

## ask-ttft-high

**Means:** Ask p95 time-to-first-token is above 2.5 seconds.
**First check:** Compare generation and retrieval spans, then inspect provider latency and circuit-breaker state.
**Escalate to:** Engineering on-call; involve the model provider owner if generation dominates.

## ask-completion-latency-high

**Means:** Complete grounded answers exceed the 10-second p95 objective.
**First check:** Inspect a slow trace and compare retrieval, context packing, generation and verification durations.
**Escalate to:** Engineering on-call; involve the slow dependency owner identified by tracing.

## ingestion-stale

**Means:** No successful ingestion has completed within the one-hour Pilot hypothesis.
**First check:** Inspect worker queue age, the latest ingestion audit event and the current retry or DLQ state.
**Escalate to:** Engineering on-call; involve source integration owners if one source type is isolated.

## daily-ai-cost-high

**Means:** Estimated daily model spend is above the initial five-dollar Pilot budget.
**First check:** Break cost down by route and model, then compare request volume and tokens per answer with the prior day.
**Escalate to:** AI platform owner through the weekly operations queue.

## abstention-ratio-high

**Means:** More than 25% of questions abstain for one hour under the initial Pilot hypothesis.
**First check:** Break abstentions down by reason and compare corpus freshness, authorization denials and retrieval failures.
**Escalate to:** Retrieval and AI quality owners through the weekly operations queue.
