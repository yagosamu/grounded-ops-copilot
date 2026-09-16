# Production RAG Platform Development Plan

## Execution Protocol

Implement these tasks with the `tlc-spec-driven` skill. Each task is one atomic, revertible commit. Tests are written with the behavior they verify, never deferred to a later task. A task is complete only when its gate passes.

No `git push` is part of a task. The agent creates the local atomic commit after its gate passes; the user performs every push. Before handing commits off for push, run `make pre-push` from a clean working tree and report the exact result. A failing gate blocks the handoff.

**Spec**: `.specs/features/production-rag-platform/spec.md`
**Design**: `.specs/features/production-rag-platform/design.md`
**Quality contract**: `CONSTRAINTS.md`
**Status**: Ready for execution approval

## Commit and model policy

- Each task produces one local atomic Conventional Commit with a concise,
  professional English message.
- Commit bodies explain intent only when the title and diff do not make it clear.
- The user owns every push; workers and the orchestrator never push.
- Mechanical, settled work uses a faster and cheaper OpenAI model when the
  execution environment supports per-agent model selection.
- Architecture, domain logic, security, retrieval, concurrency and novel
  integrations use a high-reasoning model.
- The independent Verifier uses a mid-to-high tier and never the cheapest model.
- Model tier never changes acceptance criteria, tests or gates.

## Assumed Stack

- Python 3.13, `uv`, FastAPI, Pydantic and SQLAlchemy/Alembic.
- PostgreSQL for authoritative metadata and state.
- S3-compatible object storage for immutable document artifacts.
- OpenSearch for BM25, vector, filtering and hybrid retrieval.
- Pytest, pytest-cov, Ruff and MyPy.
- Docker Compose locally; AWS provisioned with Terraform using economical Pilot and multi-AZ Production HA profiles.
- OpenTelemetry-compatible traces, metrics and structured logs.

These decisions are confirmed and recorded in `.specs/STATE.md` and `context.md`.

## Test Coverage Matrix

> Generated from the specification and `CONSTRAINTS.md`. The repository has no implementation or tests yet, so strong defaults apply.

| Code Layer | Required Test Type | Coverage Expectation | Location Pattern | Run Command |
| --- | --- | --- | --- | --- |
| Domain model and state transitions | unit | Every branch, acceptance criterion and listed edge case | `tests/unit/domain/test_*.py` | `uv run pytest -m unit` |
| Application modules | unit | 1:1 mapping to requirements and failure paths | `tests/unit/modules/test_*.py` | `uv run pytest -m unit` |
| Adapters and repositories | integration | Main query/write path, retries, idempotency and dependency failure | `tests/integration/test_*.py` | `uv run pytest -m integration` |
| HTTP routes | integration/E2E | Happy, validation, authorization, degraded and error paths | `tests/api/test_*.py` | `uv run pytest -m api` |
| Retrieval strategies | offline eval | Golden queries, Recall@k, MRR, nDCG@k and latency | `tests/evals/test_retrieval_*.py` | `uv run pytest -m retrieval_eval` |
| Answering and citations | offline eval | Supported, unsupported, conflicting and adversarial questions | `tests/evals/test_answer_*.py` | `uv run pytest -m answer_eval` |
| Agent workflow | unit + offline eval | Every route, budget exit, tool failure and multi-hop outcome | `tests/agentic/test_*.py` | `uv run pytest -m agentic` |
| Security isolation | E2E/adversarial | Zero cross-tenant disclosure and no policy bypass | `tests/security/test_*.py` | `uv run pytest -m security` |
| Operations | integration/load/recovery | Degraded mode, migration, reindex, backup/restore and SLO evidence | `tests/operational/` | `make operational-test` |
| Configuration and schemas | build gate | Import, validation and generated schema checks | `tests/unit/test_config.py` | `make check` |

## Gate Check Commands

| Gate | When | Planned command | Required result |
| --- | --- | --- | --- |
| Quick | Every task with unit-level behavior | `make check` | format-check, lint, types and unit tests all green within 30 s |
| Full | Every task with external adapters, persistence or routes | `make test` | Quick plus integration/API tests and coverage green; task gate within 90 s |
| Build | End of each phase | `make pre-push` | Full plus secret scan and reproducible build green within 5 min |
| Release | Pull request/release candidate | `make release-check` | Build plus E2E, evals, security scans and migrations green; CI target within 15 min |
| Operational | Before production release | `make operational-test` | load, failure injection, backup/restore and reindex evidence green in a separate pipeline |

## Mandatory Development Loop

For every task:

1. Re-read the mapped requirement and quality constraints.
2. Write or update the test that asserts the required outcome.
3. Implement the smallest behavior that passes it.
4. Run the task gate and record test count and command result.
5. Update requirement traceability and task status.
6. Create one Conventional Commit for the task.

For every phase:

1. Run `make pre-push` from the integrated branch state.
2. Review the diff for weakened tests, suppressions, secrets and unfinished work.
3. Record the benchmark or operational artifact required by the phase.
4. Request user authorization before any push.
5. Push only the exact commit range that passed.

## Execution Plan

The map defines phase-level dependencies. Detailed tasks follow in execution order.

```text
Fase 0  Foundation and gates
   ↓
Fase 1  Corpus and reliable ingestion
   ↓
Fase 2  Authorized BM25 vertical slice
   ↓
Fase 3  Retrieval experimentation and promotion
   ↓
Fase 4  Grounded answering
   ↓
Fase 5  Security and multi-tenancy hardening
   ↓
Fase 6  Bounded agentic investigation
   ↓
Fase 7  Observability, performance and resilience
   ↓
Fase 8  Deployment, recovery and pilot readiness
```

Phases run sequentially. Tasks inside each phase run in the listed order unless `Depends on` explicitly permits otherwise.

---

## Task Breakdown

## Fase 0: Foundation and Enforced Gates

**Outcome**: A reproducible repository where all documented quality commands exist and pass.

### T01: Bootstrap the Python project

**What**: Create the locked Python 3.13 project, source layout and minimal application entry point.
**Where**: `pyproject.toml`
**Depends on**: None
**Requirement**: FND-01
**Done when**: `uv sync --locked` succeeds on a clean environment; the application imports; Python version mismatch fails clearly.
**Tests**: config/import smoke test.
**Gate**: Quick.
**Commit**: `chore(project): bootstrap python workspace`

### T02: Implement local quality commands

**What**: Add canonical `check`, `test`, `pre-push`, `release-check` and `operational-test` targets.
**Where**: `Makefile`
**Depends on**: T01
**Requirement**: REL-01
**Done when**: every target exists, returns non-zero on an intentional fixture failure and passes after the fixture is restored.
**Tests**: command contract smoke tests.
**Gate**: Build.
**Commit**: `build(quality): add canonical verification gates`

### T03: Configure lint, typing and test markers

**What**: Configure Ruff, strict MyPy, Pytest markers and coverage reporting.
**Where**: `pyproject.toml`
**Depends on**: T02
**Requirement**: FND-01, REL-01
**Done when**: an intentional lint, type and coverage violation is detected; no global ignore disables a checker.
**Tests**: checker discrimination smoke tests.
**Gate**: Build.
**Commit**: `build(quality): enforce python checks and test markers`

### T04: Provision local infrastructure

**What**: Define health-checked PostgreSQL, OpenSearch and object-storage containers.
**Where**: `compose.yml`
**Depends on**: T03
**Requirement**: FND-01
**Done when**: a clean `docker compose up` reaches healthy state and teardown preserves only documented volumes.
**Tests**: infrastructure smoke test.
**Gate**: Full.
**Commit**: `build(local): provision core data dependencies`

### T05: Add health and readiness interfaces

**What**: Implement separate liveness and dependency-aware readiness routes.
**Where**: `src/interfaces/http/health.py`
**Depends on**: T04
**Requirement**: FND-01
**Done when**: liveness remains healthy during dependency failure; readiness identifies the unavailable dependency without leaking configuration.
**Tests**: API tests for healthy and failed dependencies.
**Gate**: Full.
**Commit**: `feat(health): expose liveness and readiness`

### T06: Seed the versioned evaluation corpus

**What**: Add the first licensed corpus fixtures, golden questions and dataset manifest.
**Where**: `evals/datasets/v1/manifest.yaml`
**Depends on**: T03
**Requirement**: RET-01, ANS-01
**Done when**: dataset validation proves unique IDs, resolvable sources, expected evidence spans and license metadata.
**Tests**: dataset schema and referential-integrity tests.
**Gate**: Build.
**Commit**: `test(evals): add versioned golden dataset`

**Phase gate**: `make pre-push`; fresh bootstrap on a clean environment; record commands and test counts.

---

## Fase 1: Corpus and Reliable Ingestion

**Outcome**: Documents can be ingested, repeated, updated, failed and removed with complete provenance.

### T07: Define ingestion domain states

**What**: Model source, document, version and ingestion-job states with guarded transitions.
**Where**: `src/domain/ingestion.py`
**Depends on**: T06
**Requirement**: ING-01, ING-02
**Done when**: every valid transition succeeds and every invalid transition fails deterministically.
**Tests**: unit tests for all state transitions.
**Gate**: Quick.
**Commit**: `feat(ingestion): define versioned ingestion domain`

### T08: Persist ingestion metadata

**What**: Implement PostgreSQL persistence and migrations for ingestion entities and idempotency keys.
**Where**: `src/adapters/postgres/ingestion_repository.py`
**Depends on**: T07
**Requirement**: ING-01, ING-02
**Done when**: create, duplicate, update, concurrent-idempotency and rollback paths pass against PostgreSQL.
**Tests**: repository integration tests and migration up/down test.
**Gate**: Full.
**Commit**: `feat(ingestion): persist jobs and document versions`

### T09: Store immutable document artifacts

**What**: Implement content-addressed raw and normalized artifact storage.
**Where**: `src/adapters/object_store/document_store.py`
**Depends on**: T08
**Requirement**: ING-02
**Done when**: identical content reuses an object; corrupted or missing objects fail verification; tenant prefixes remain isolated.
**Tests**: object-store integration tests.
**Gate**: Full.
**Commit**: `feat(ingestion): store immutable document artifacts`

### T10: Add the initial source adapter

**What**: Ingest Markdown files and metadata from the selected public corpus.
**Where**: `src/adapters/sources/markdown.py`
**Depends on**: T09
**Requirement**: ING-01
**Done when**: new, unchanged, changed, deleted and malformed fixtures produce the specified source events.
**Tests**: adapter unit tests with frozen fixtures.
**Gate**: Quick.
**Commit**: `feat(sources): ingest markdown corpus`

### T11: Normalize and structurally parse documents

**What**: Convert source content into normalized sections and provenance-preserving spans.
**Where**: `src/modules/parsing/parser.py`
**Depends on**: T10
**Requirement**: ING-02
**Done when**: headings, code blocks, tables, links and malformed input retain deterministic span mappings.
**Tests**: parser unit tests and snapshot fixtures.
**Gate**: Quick.
**Commit**: `feat(parsing): normalize structured documents`

### T12: Implement baseline structural chunking

**What**: Produce deterministic chunks with token bounds and complete provenance.
**Where**: `src/modules/chunking/structural.py`
**Depends on**: T11
**Requirement**: ING-02
**Done when**: chunks respect configured bounds, never lose source spans and remain stable for identical inputs.
**Tests**: property and unit tests for boundaries, tiny sections and oversized code blocks.
**Gate**: Quick.
**Commit**: `feat(chunking): add provenance-preserving chunks`

### T13: Orchestrate idempotent ingestion

**What**: Coordinate fetch, store, parse, chunk and index preparation with bounded retries and DLQ state.
**Where**: `src/modules/ingestion/pipeline.py`
**Depends on**: T12
**Requirement**: ING-01, ING-02, OPS-01
**Done when**: repeat, update, partial failure, retry exhaustion and resume cases produce the expected states without duplicate versions.
**Tests**: module unit tests plus end-to-end ingestion integration test.
**Gate**: Full.
**Commit**: `feat(ingestion): orchestrate recoverable pipeline`

**Phase gate**: `make pre-push`; demonstrate ingest/repeat/update/fail/delete; record provenance audit.

---

## Fase 2: Authorized BM25 Vertical Slice

**Outcome**: A user can retrieve authorized evidence and receive a minimal cited response without embeddings or agents.

### T14: Create versioned lexical index mappings

**What**: Define analyzers, fields, tenant policy fields and aliases for the BM25 index.
**Where**: `src/adapters/opensearch/index_schema.py`
**Depends on**: T13
**Requirement**: RET-01, OPS-02
**Done when**: mapping creation is idempotent and rejects incompatible in-place schema changes.
**Tests**: OpenSearch integration tests.
**Gate**: Full.
**Commit**: `feat(search): define versioned lexical index`

### T15: Index and remove document versions

**What**: Project current authorized chunks into OpenSearch and tombstone removed documents.
**Where**: `src/adapters/opensearch/index_writer.py`
**Depends on**: T14
**Requirement**: ING-02, OPS-02
**Done when**: upsert, repeat, version replacement, delete and bulk partial failure are consistent with PostgreSQL state.
**Tests**: indexing integration tests.
**Gate**: Full.
**Commit**: `feat(search): project document versions into index`

### T16: Implement the policy module

**What**: Resolve tenant, role, group and document decisions outside the model.
**Where**: `src/modules/policy/authorizer.py`
**Depends on**: T08
**Requirement**: SEC-01
**Done when**: allow, deny, unknown principal and cross-tenant cases return explicit decisions and reasons.
**Tests**: exhaustive authorization unit tests.
**Gate**: Quick.
**Commit**: `feat(policy): authorize document access`

### T17: Implement BM25 retrieval

**What**: Return a typed `EvidenceSet` using BM25 and mandatory policy filters.
**Where**: `src/modules/retrieval/retriever.py`
**Depends on**: T15, T16
**Requirement**: RET-01, SEC-01
**Done when**: relevance ordering, metadata filters, current/historical selection, empty results and dependency failure match the spec.
**Tests**: unit tests with a fake adapter and OpenSearch integration tests.
**Gate**: Full.
**Commit**: `feat(retrieval): add authorized bm25 baseline`

### T18: Expose the evidence search route

**What**: Add an authenticated endpoint returning policy-filtered evidence and retrieval diagnostics.
**Where**: `src/interfaces/http/search.py`
**Depends on**: T17
**Requirement**: RET-01, SEC-01
**Done when**: success, validation, pagination, unauthorized, empty and dependency-failure paths pass.
**Tests**: API integration tests.
**Gate**: Full.
**Commit**: `feat(api): expose authorized evidence search`

### T19: Establish the BM25 benchmark

**What**: Run and persist the baseline retrieval report for dataset v1.
**Where**: `evals/retrieval/baseline.py`
**Depends on**: T18
**Requirement**: RET-01, RET-02
**Done when**: report contains config hash, dataset hash, Recall@k, MRR, nDCG@k, p50/p95 and per-query errors.
**Tests**: retrieval eval determinism and metric tests.
**Gate**: Release.
**Commit**: `test(retrieval): establish bm25 benchmark`

**Phase gate**: `make pre-push` and `make release-check`; publish BM25 benchmark artifact.

---

## Fase 3: Retrieval Experimentation and Promotion

**Outcome**: Dense, hybrid, reranking and chunking changes are compared fairly; only qualifying candidates become defaults.

### T20: Add embedding generation with versioned metadata

**What**: Generate passage/query embeddings with batching, timeout, retry and model-version metadata.
**Where**: `src/modules/embeddings/embedder.py`
**Depends on**: T19
**Requirement**: RET-02, OPS-01
**Done when**: deterministic fake, live contract, batching, rate limit and provider failure cases pass.
**Tests**: unit tests plus provider contract integration test.
**Gate**: Full.
**Commit**: `feat(embeddings): add versioned embedding pipeline`

### T21: Build the dense retrieval candidate

**What**: Add vector mappings and dense query execution without changing the default.
**Where**: `src/modules/retrieval/dense.py`
**Depends on**: T20
**Requirement**: RET-02
**Done when**: dense runs against the same policy filters, corpus version and golden queries as BM25.
**Tests**: unit, OpenSearch integration and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add dense candidate`

### T22: Build the hybrid fusion candidate

**What**: Combine lexical and dense rankings using configurable reciprocal-rank fusion.
**Where**: `src/modules/retrieval/hybrid.py`
**Depends on**: T21
**Requirement**: RET-02
**Done when**: duplicate fusion, missing-leg fallback, deterministic ties and policy invariants pass.
**Tests**: unit, integration and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add hybrid fusion candidate`

### T23: Add the reranking candidate

**What**: Rerank a bounded candidate set while preserving evidence identity and policy decisions.
**Where**: `src/modules/retrieval/reranker.py`
**Depends on**: T22
**Requirement**: RET-02
**Done when**: timeout fallback, stable evidence IDs, candidate bounds and latency accounting pass.
**Tests**: unit, contract and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add bounded reranker candidate`

### T24: Compare chunking candidates

**What**: Evaluate fixed, structural and parent-child chunking on the same corpus and query set.
**Where**: `evals/retrieval/chunking_experiment.py`
**Depends on**: T23
**Requirement**: RET-02
**Done when**: report includes retrieval quality, citation-span precision, index size, ingestion time and query latency.
**Tests**: experiment reproducibility and metric tests.
**Gate**: Release.
**Commit**: `test(retrieval): compare chunking strategies`

### T25: Promote the qualifying retrieval configuration

**What**: Select the default only from candidates meeting `CONSTRAINTS.md`; record rejected alternatives.
**Where**: `docs/decisions/ADR-001-retrieval-strategy.md`
**Depends on**: T24
**Requirement**: RET-02
**Done when**: ADR links immutable reports, calculates deltas and names the production and fallback configurations.
**Tests**: configuration regression eval and schema validation.
**Gate**: Release.
**Commit**: `docs(architecture): select measured retrieval strategy`

**Phase gate**: `make release-check`; archive experiment artifacts and ADR; no candidate is promoted manually.

---

## Fase 4: Grounded Answering

**Outcome**: `Ask` produces verified citations or abstains, while remaining usable in degraded modes.

### T26: Define grounded-answer contracts

**What**: Model questions, evidence, claims, citations, verification status and abstention reasons.
**Where**: `src/domain/answering.py`
**Depends on**: T25
**Requirement**: ANS-01, ANS-02
**Done when**: invalid or unresolved citations cannot create a verified answer.
**Tests**: domain invariant unit tests.
**Gate**: Quick.
**Commit**: `feat(answering): define grounded answer contracts`

### T27: Build the bounded context packer

**What**: Select evidence within token budget while preserving source diversity and provenance.
**Where**: `src/modules/answering/context_packer.py`
**Depends on**: T26
**Requirement**: ANS-01
**Done when**: token overflow, duplicate evidence, conflicting versions and empty context cases are deterministic.
**Tests**: unit and property tests.
**Gate**: Quick.
**Commit**: `feat(answering): pack bounded evidence context`

### T28: Implement provider-neutral generation

**What**: Generate structured answers with usage, timeout and provider-failure reporting.
**Where**: `src/modules/answering/generator.py`
**Depends on**: T27
**Requirement**: ANS-01, OPS-01
**Done when**: structured success, malformed output, timeout, retryable failure and deterministic fake pass.
**Tests**: unit and provider contract tests.
**Gate**: Full.
**Commit**: `feat(answering): generate structured grounded answers`

### T29: Verify claim-level citations

**What**: Resolve citations to exact versioned spans and assign verified, unverified or abstained status.
**Where**: `src/modules/answering/verifier.py`
**Depends on**: T28
**Requirement**: ANS-01, ANS-02
**Done when**: missing, fabricated, mismatched-version and unsupported citations fail; supported claims pass.
**Tests**: unit tests mapped to every citation criterion.
**Gate**: Quick.
**Commit**: `feat(answering): verify claim-level citations`

### T30: Implement evidence-based abstention

**What**: Abstain on insufficient, unauthorized or irreconcilably conflicting evidence.
**Where**: `src/modules/answering/abstention.py`
**Depends on**: T29
**Requirement**: ANS-02
**Done when**: each abstention reason is stable, user-facing and measured separately from provider failures.
**Tests**: unit and answer-eval tests.
**Gate**: Release.
**Commit**: `feat(answering): add calibrated abstention`

### T31: Expose the streaming Ask route

**What**: Add authenticated streaming with final verification status, sources and degraded-mode metadata.
**Where**: `src/interfaces/http/ask.py`
**Depends on**: T30
**Requirement**: ANS-01, ANS-02, OPS-01
**Done when**: supported, abstained, disconnected-client, timeout and BM25-only degraded paths pass.
**Tests**: API/E2E streaming tests.
**Gate**: Full.
**Commit**: `feat(api): expose verified streaming ask`

### T32: Establish the answer benchmark

**What**: Compare GPT-5 nano, GPT-4o Mini and GPT-5.6 Luna on citation coverage, citation validity, abstention, task success, latency and cost; run Terra only as an upper bound on the difficult subset.
**Where**: `evals/answering/benchmark.py`
**Depends on**: T31
**Requirement**: ANS-01, ANS-02
**Done when**: deterministic checks and pinned judge results produce a versioned per-model report with per-case evidence and identify the cheapest candidate that passes `CONSTRAINTS.md`.
**Tests**: evaluator metric and repeatability tests.
**Gate**: Release.
**Commit**: `test(answering): establish grounded answer benchmark`

**Phase gate**: `make release-check`; manually inspect a stratified sample; publish the answer benchmark.

---

## Fase 5: Security and Multi-tenancy Hardening

**Outcome**: Authorization is enforced before retrieval and generation, with adversarial evidence of isolation.

### T33: Authenticate principals and tenant context

**What**: Validate identity and construct immutable principal context for every protected route.
**Where**: `src/interfaces/http/auth.py`
**Depends on**: T32
**Requirement**: SEC-01
**Done when**: valid, expired, malformed, wrong-audience and missing credentials produce specified outcomes.
**Tests**: API authentication tests.
**Gate**: Full.
**Commit**: `feat(security): authenticate tenant principals`

### T34: Enforce document policies in storage and search

**What**: Apply the same authorization decision to metadata reads, index writes, retrieval and citation resolution.
**Where**: `src/modules/policy/enforcement.py`
**Depends on**: T33
**Requirement**: SEC-01
**Done when**: cross-tenant IDs, forged filters, stale permissions and citation lookup cannot bypass policy.
**Tests**: unit, integration and adversarial E2E tests.
**Gate**: Release.
**Commit**: `feat(security): enforce document policy end to end`

### T35: Treat corpus content as untrusted

**What**: Add prompt construction and tool policies that prevent document instructions from changing authority.
**Where**: `src/modules/security/untrusted_content.py`
**Depends on**: T34
**Requirement**: SEC-02
**Done when**: injection fixtures cannot reveal hidden context, alter tenant scope or invoke unauthorized tools.
**Tests**: adversarial prompt-injection tests.
**Gate**: Release.
**Commit**: `feat(security): isolate untrusted corpus instructions`

### T36: Add redacted audit events

**What**: Record authorization, ingestion, index promotion and investigation decisions without storing document content or secrets.
**Where**: `src/modules/audit/recorder.py`
**Depends on**: T35
**Requirement**: SEC-01, SEC-02, OPS-01
**Done when**: required events are queryable; redaction tests prove sensitive fixtures never appear.
**Tests**: unit and integration audit tests.
**Gate**: Full.
**Commit**: `feat(audit): record redacted security events`

### T37: Add quotas and rate limits

**What**: Enforce per-principal and per-tenant limits for queries, ingestion and investigations.
**Where**: `src/modules/policy/quotas.py`
**Depends on**: T36
**Requirement**: SEC-01, OPS-01
**Done when**: concurrency, burst, reset and backend-failure behavior are deterministic and observable.
**Tests**: unit, integration and API tests.
**Gate**: Full.
**Commit**: `feat(policy): enforce tenant quotas`

**Phase gate**: `make release-check`; zero cross-tenant disclosure in the adversarial suite; review threat assumptions before push.

---

## Fase 6: Bounded Agentic Investigation

**Outcome**: Complex investigations run asynchronously with explicit budgets and only become default when they beat standard RAG.

### T38: Implement query routing

**What**: Route questions to `Ask` or `Investigate` with an auditable reason and deterministic fallback.
**Where**: `src/modules/routing/query_router.py`
**Depends on**: T37
**Requirement**: AGT-01
**Done when**: simple, multi-hop, ambiguous, malicious and router-failure cases select the specified path.
**Tests**: unit tests and routing golden set.
**Gate**: Quick.
**Commit**: `feat(routing): classify ask and investigate requests`

### T39: Expose bounded investigation tools

**What**: Wrap retrieval, version comparison and incident search as policy-aware typed tools.
**Where**: `src/modules/investigation/tools.py`
**Depends on**: T38
**Requirement**: AGT-01, SEC-01
**Done when**: schemas, authorization, timeout, result bounds and tool errors are enforced for every tool.
**Tests**: unit and contract tests.
**Gate**: Full.
**Commit**: `feat(agent): add policy-aware investigation tools`

### T40: Implement the bounded investigation graph

**What**: Add plan, retrieve, compare, verify and report nodes with durable state and stopping rules.
**Where**: `src/modules/investigation/workflow.py`
**Depends on**: T39
**Requirement**: AGT-01
**Done when**: success, insufficient evidence, max steps, timeout, token budget, tool failure, cancellation and resume paths pass.
**Tests**: graph transition unit tests and agentic integration tests.
**Gate**: Full.
**Commit**: `feat(agent): orchestrate bounded investigations`

### T41: Expose asynchronous investigation routes

**What**: Add create, status, cancel and report endpoints without exposing internal chain-of-thought.
**Where**: `src/interfaces/http/investigations.py`
**Depends on**: T40
**Requirement**: AGT-01
**Done when**: lifecycle, ownership, idempotency, cancellation and terminal-state routes pass.
**Tests**: API/E2E lifecycle tests.
**Gate**: Full.
**Commit**: `feat(api): expose asynchronous investigations`

### T42: Benchmark agentic value

**What**: Compare `Investigate` with `Ask` on frozen multi-hop tasks, cost, latency and failure rate.
**Where**: `evals/agentic/benchmark.py`
**Depends on**: T41
**Requirement**: AGT-02
**Done when**: report calculates success delta, cost ratio, p95 ratio and failure categories against promotion thresholds.
**Tests**: evaluator correctness and repeatability tests.
**Gate**: Release.
**Commit**: `test(agent): compare investigation against rag baseline`

### T43: Decide agent production routing

**What**: Record whether the agent qualifies, plus its enabled task classes and budgets.
**Where**: `docs/decisions/ADR-002-agent-routing.md`
**Depends on**: T42
**Requirement**: AGT-02
**Done when**: ADR links benchmark evidence and configuration tests enforce the decision.
**Tests**: routing configuration regression tests.
**Gate**: Release.
**Commit**: `docs(architecture): decide agent production routing`

**Phase gate**: `make release-check`; agent remains opt-in unless every promotion criterion passes.

---

## Fase 7: Observability, Performance and Resilience

**Outcome**: Production behavior is measurable, bounded and diagnosable under dependency and traffic failures.

### T44: Instrument correlated telemetry

**What**: Emit structured logs, metrics and traces across API, worker, retrieval, generation and agent flows.
**Where**: `src/observability/telemetry.py`
**Depends on**: T43
**Requirement**: OPS-01
**Done when**: correlation propagates end to end; redaction tests find no raw corpus, token or secret content.
**Tests**: telemetry unit and integration tests.
**Gate**: Full.
**Commit**: `feat(observability): instrument end-to-end telemetry`

### T45: Implement safe degraded modes

**What**: Add timeouts, circuit breakers and documented fallbacks for external dependencies.
**Where**: `src/modules/resilience/policies.py`
**Depends on**: T44
**Requirement**: OPS-01
**Done when**: embedding, generation, object store and telemetry failures match the design without retry storms.
**Tests**: unit and failure-injection integration tests.
**Gate**: Full.
**Commit**: `feat(resilience): enforce dependency failure policies`

### T46: Add version-safe caching

**What**: Cache only eligible retrieval and answer results using tenant, policy, corpus and model versions in the key.
**Where**: `src/modules/cache/cache_policy.py`
**Depends on**: T45
**Requirement**: OPS-01
**Done when**: invalidation, permission changes, document updates, model changes and cache failure cannot serve stale or unauthorized data.
**Tests**: unit, integration and adversarial cache tests.
**Gate**: Full.
**Commit**: `feat(cache): add version-safe response caching`

### T47: Establish load and capacity benchmarks

**What**: Measure throughput, p50/p95/p99, saturation and error behavior for `Search`, `Ask` and ingestion.
**Where**: `tests/operational/load/benchmark.py`
**Depends on**: T46
**Requirement**: OPS-01
**Done when**: a reproducible report states hardware, dataset, concurrency, warmup, limits and first bottleneck.
**Tests**: load test self-checks and result schema validation.
**Gate**: Operational.
**Commit**: `test(performance): establish capacity baseline`

### T48: Define SLO dashboards and alerts

**What**: Provision dashboards and actionable alerts for availability, latency, errors, cost, retrieval, abstention and ingestion freshness.
**Where**: `ops/observability/slo.yaml`
**Depends on**: T47
**Requirement**: OPS-01
**Done when**: synthetic failure tests trigger every paging alert and dashboard queries resolve their stated metrics.
**Tests**: observability configuration and alert simulation tests.
**Gate**: Operational.
**Commit**: `ops(observability): define slos and alerts`

**Phase gate**: `make operational-test`; publish load profile, degraded-mode evidence and alert simulation results.

---

## Fase 8: Deployment, Recovery and Pilot Readiness

**Outcome**: A release can be built, deployed, rolled back, restored and demonstrated with production evidence.

### T49: Build the continuous-integration pipeline

**What**: Run `make release-check` with service dependencies, immutable reports and branch protection status.
**Where**: `.github/workflows/ci.yml`
**Depends on**: T48
**Requirement**: REL-01
**Done when**: intentional failures in lint, types, tests, evals, migrations, secrets and security scans each block CI.
**Tests**: CI discrimination matrix.
**Gate**: Release.
**Commit**: `ci(github): enforce release quality gates`

### T50: Build immutable deployment artifacts

**What**: Produce least-privilege API and worker images with SBOM, provenance and vulnerability scan.
**Where**: `Dockerfile`
**Depends on**: T49
**Requirement**: REL-01
**Done when**: images run as non-root, contain no development secrets, have pinned base digest and pass high/critical scan policy.
**Tests**: container structure and startup tests.
**Gate**: Release.
**Commit**: `build(container): create hardened runtime images`

### T51: Provision the AWS Pilot environment

**What**: Define the Terraform-managed AWS Pilot profile with ECS Fargate, RDS PostgreSQL, S3, reduced Amazon OpenSearch Service, load balancing, secrets and telemetry.
**Where**: `infra/staging/`
**Depends on**: T50
**Requirement**: OPS-01, REL-01
**Done when**: plan is repeatable, least-privilege checks pass, no secret appears in state or logs and the environment can be created and destroyed from documented commands.
**Tests**: infrastructure validation, policy and deployment smoke tests.
**Gate**: Release.
**Commit**: `infra(staging): provision production-like environment`

### T51B: Define the AWS Production HA profile

**What**: Extend the Terraform modules with multi-AZ ECS, RDS Multi-AZ, OpenSearch Multi-AZ with Standby, managed queueing, WAF, backups and recovery controls.
**Where**: `infra/production/`
**Depends on**: T51
**Requirement**: OPS-01, OPS-02, REL-01
**Done when**: Terraform validation and policy tests prove that production redundancy, encryption, private networking, backup and least-privilege requirements are represented without requiring the profile to remain continuously deployed.
**Tests**: infrastructure validation, security policy and plan assertions.
**Gate**: Release.
**Commit**: `infra(production): define high-availability profile`

### T52: Implement blue-green index rebuild and rollback

**What**: Rebuild into a new index, validate it, atomically switch aliases and roll back on failure.
**Where**: `src/modules/indexing/rebuild.py`
**Depends on**: T51B
**Requirement**: OPS-02
**Done when**: successful switch, failed validation, interrupted build and rollback preserve the last valid index.
**Tests**: staging integration and failure-injection tests.
**Gate**: Operational.
**Commit**: `feat(indexing): add validated blue-green rebuild`

### T53: Test backup and disaster recovery

**What**: Automate backup verification and a staging restore exercise for authoritative stores.
**Where**: `ops/recovery/runbook.md`
**Depends on**: T52
**Requirement**: OPS-02
**Done when**: a clean environment is restored, checksums match, indexes rebuild and measured RPO/RTO meet `CONSTRAINTS.md`.
**Tests**: witnessed recovery exercise with machine-readable results.
**Gate**: Operational.
**Commit**: `ops(recovery): verify backup and restore procedure`

### T54: Create the minimal production UI

**What**: Provide authenticated `Ask`, evidence inspection, investigation status and feedback flows.
**Where**: `web/`
**Depends on**: T53
**Requirement**: ANS-01, AGT-01, SEC-01
**Done when**: primary flows, empty/error states, keyboard use and citation navigation pass E2E tests.
**Tests**: frontend unit, accessibility and browser E2E tests.
**Gate**: Release.
**Commit**: `feat(web): add evidence-first production interface`

### T55: Publish operational and portfolio documentation

**What**: Document setup, architecture, threat assumptions, experiments, costs, limitations, runbooks and demo flow.
**Where**: `README.md`
**Depends on**: T54
**Requirement**: FND-01, REL-01
**Done when**: every command is executed from the document, every metric links to an artifact and no unsupported production claim remains.
**Tests**: documentation command smoke test and link checker.
**Gate**: Release.
**Commit**: `docs(project): publish production evidence and demo`

### T56: Execute the release-readiness review

**What**: Verify requirements, threat model, gates, recovery, SLOs, costs and known limitations before pilot release.
**Where**: `.specs/features/production-rag-platform/validation.md`
**Depends on**: T55
**Requirement**: all
**Done when**: an independent verifier records PASS with file/line and artifact evidence for every acceptance criterion; remaining gaps block release.
**Tests**: full Release and Operational gates plus independent discrimination tests.
**Gate**: Release and Operational.
**Commit**: `chore(release): validate pilot readiness`

**Phase gate**: clean `make release-check` and `make operational-test`; independent verification; explicit user authorization before tag, push or deployment.

---

## Requirement-to-Phase Traceability

| Requirement | Primary phase | Evidence |
| --- | --- | --- |
| FND-01 | 0 | clean bootstrap and health checks |
| ING-01 | 1 | idempotency and recovery tests |
| ING-02 | 1-2 | provenance and index projection tests |
| RET-01 | 2 | BM25 baseline report |
| RET-02 | 3 | comparative experiments and ADR-001 |
| ANS-01 | 4 | citation benchmark |
| ANS-02 | 4 | abstention benchmark |
| SEC-01 | 2 and 5 | cross-tenant adversarial suite |
| SEC-02 | 5 | injection and redaction suite |
| AGT-01 | 6 | bounded workflow tests |
| AGT-02 | 6 | agent comparison and ADR-002 |
| OPS-01 | 7 | telemetry, failure and load evidence |
| OPS-02 | 8 | reindex and recovery exercises |
| REL-01 | 0 and 8 | local pre-push and CI discrimination |

## Diagram-Definition Cross-Check

| Phase | Dependency shown | Task definitions | Status |
| --- | --- | --- | --- |
| 0 | None | T01 starts without dependencies | Pass |
| 1 | Fase 0 | T07 depends on T06 | Pass |
| 2 | Fase 1 | T14 depends on T13 | Pass |
| 3 | Fase 2 | T20 depends on T19 | Pass |
| 4 | Fase 3 | T26 depends on T25 | Pass |
| 5 | Fase 4 | T33 depends on T32 | Pass |
| 6 | Fase 5 | T38 depends on T37 | Pass |
| 7 | Fase 6 | T44 depends on T43 | Pass |
| 8 | Fase 7 | T49 depends on T48 | Pass |

## Test Co-location Validation

| Task group | Layer | Matrix requires | Plan provides | Status |
| --- | --- | --- | --- | --- |
| T01-T06 | configuration, route and dataset | build, API and dataset validation | tests inside each task | Pass |
| T07-T13 | domain, adapters and orchestration | unit plus integration | tests inside each task | Pass |
| T14-T19 | search, route and eval | integration, API and offline eval | tests inside each task | Pass |
| T20-T25 | retrieval candidates and experiments | contract, integration and offline eval | tests inside each task | Pass |
| T26-T32 | answering, routes and eval | unit, API/E2E and offline eval | tests inside each task | Pass |
| T33-T37 | auth, policy and security | API, integration and adversarial E2E | tests inside each task | Pass |
| T38-T43 | routing and agent workflow | unit, integration and agentic eval | tests inside each task | Pass |
| T44-T48 | telemetry, resilience and performance | integration, load and operational | tests inside each task | Pass |
| T49-T56 | CI, deployment, recovery and UI | discrimination, operational and E2E | tests inside each task | Pass |

## Decisions Required Before Execution

1. [x] Initial corpus and working product concept confirmed.
2. [x] Application and data stack confirmed.
3. [x] Generation model evaluation strategy confirmed.
4. [x] AWS/Terraform deployment profiles confirmed.
5. [x] Quality and operational thresholds confirmed in `CONSTRAINTS.md`.
6. [x] Use sequential task-batch sub-agents, choosing cheaper models for mechanical work and stronger models for high-ambiguity or adversarial work.
