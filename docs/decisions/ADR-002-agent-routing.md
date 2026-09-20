# ADR-002: Keep agentic investigation opt-in

- Status: Accepted
- Date: 2026-09-20
- Requirement: AGT-02

## Decision

The production router keeps `Investigate` disabled by default. All questions,
including multi-hop questions, use `Ask` until a later benchmark qualifies the
agent and the enabled task classes are explicitly changed in
[`config/agentic.yaml`](../../config/agentic.yaml).

The bounded workflow keeps these configured budgets for a future opt-in:

- 12 steps;
- 120 seconds;
- 20,000 tokens;
- 8 tool calls;
- 5 retrieval attempts.

## Evidence

The [agentic v1 report](../../evals/agentic/reports/agentic-v1.json) compares the
same eight multi-hop cases. `Investigate` improves success from 50% to 62.5%, a
25% relative gain, but costs 2.7x the `Ask` path. The approved thresholds are at
least 10% relative success gain and at most 2.5x cost, so the agent fails the
cost criterion. Its 3.94x p95 ratio remains informational because no agentic
latency threshold has been approved. The dataset also records timeout, tool
failure and insufficient-evidence outcomes.

The report is frozen replay data, not production traffic. Its SHA-256 is pinned
in the routing config and verified by the config loader. A new representative
benchmark must replace this evidence before enabling a task class.

## Consequences

Simple questions retain predictable cost and latency. Multi-hop classification is
still recorded for auditability, but a disabled agent produces an explicit Ask
fallback instead of silently paying for an unqualified workflow. The trade-off is
that some complex questions will initially receive a less capable path; this is
intentional until quality and cost are demonstrated together.

The routing configuration tests assert the disabled default, budgets, benchmark
hash and fallback behavior. Enabling `Investigate` requires a config change,
fresh evidence and a new ADR review rather than a code-only toggle.
