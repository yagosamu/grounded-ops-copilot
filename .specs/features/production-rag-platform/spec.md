# GroundedOps Production RAG Platform Specification

## Problem Statement

Engineering teams lose time finding reliable, current and authorized information across documentation, runbooks, ADRs, changelogs, issues, pull requests and postmortems. The platform will ingest these sources and provide grounded answers or bounded investigations with exact provenance, measurable quality and production-grade operational controls.

## Goals

- [ ] Deliver a deployable `Ask` path for low-latency grounded answers.
- [ ] Deliver a bounded `Investigate` path for multi-source, multi-hop tasks.
- [ ] Prove every complexity increase against a versioned baseline.
- [ ] Prevent unauthorized documents from entering retrieval context or model input.
- [ ] Make ingestion, retrieval, generation and agent execution observable and recoverable.
- [ ] Enforce tests before every push and full quality gates before merge or release.

## Out of Scope

| Feature | Reason |
| --- | --- |
| General-purpose autonomous agent | The product is limited to engineering knowledge and incidents |
| Kubernetes in the first release | Container orchestration is not required to validate the product |
| Multiple search backends in the first release | A seam becomes real only when a second adapter is required |
| Fine-tuning models | Retrieval and prompting must first establish a measured baseline |
| Automatic production remediation | Initial versions provide evidence and recommendations, not destructive actions |
| Native mobile application | A responsive web client is sufficient for the initial product |

## Assumptions & Open Questions

| Assumption / decision | Chosen default | Rationale | Confirmed? |
| --- | --- | --- | --- |
| Initial stack | Python 3.13 with `uv`; FastAPI/Pydantic; PostgreSQL with SQLAlchemy/Alembic; OpenSearch; S3-compatible storage; Celery/Redis; OpenTelemetry with Langfuse; Pytest/Testcontainers; Ruff/MyPy; Next.js added after the backend vertical slice | Supports production-oriented search, asynchronous work, observability and strict validation without starting with microservices | y |
| Initial corpus | Public OpenTelemetry documentation and GitHub artifacts, complemented by explicitly synthetic company runbooks, ADRs and postmortems | Combines authentic versioned technical knowledge with controlled incidents, permissions and evaluation cases | y |
| Tenancy | Every stored and retrieved artifact carries `tenant_id` and access policy | Security is difficult to retrofit | n |
| Model providers | OpenAI Responses API behind a provider-neutral interface; benchmark GPT-5 nano, GPT-4o Mini and GPT-5.6 Luna on the same suite; use the cheapest model that passes all gates; use Terra only as an upper-bound candidate on difficult cases | Makes cost a promotion criterion without accepting an unmeasured quality regression or adding a second provider in v1 | y |
| Frontend | Minimal web client after the backend vertical slice | Product feedback matters, but retrieval evidence comes first | n |
| Deployment target | AWS provisioned with Terraform in two profiles: an economical Pilot using managed services at reduced redundancy, and a Production HA profile using multi-AZ compute, database and search; local development remains Docker Compose | Preserves a credible production path without paying continuously for high availability before real usage exists | y |
| Quality thresholds | Staged gates and hybrid thresholds from `CONSTRAINTS.md`: fixed invariants, measured ratchets and 30 s / 90 s / 5 min / 15 min execution budgets | Keeps strong checks close to the work without turning slow checks into routinely bypassed friction | y |

**Open questions:** none block planning. Defaults above require confirmation before implementation begins.

## User Stories

The acceptance criteria below define the testable behavior of each story.

### P1: Reproducible platform foundation

**User Story**: As an engineer, I want one reproducible local environment so that I can develop and validate the complete vertical slice.

1. WHEN a clean workstation executes the documented bootstrap command THEN the system SHALL install locked dependencies and start required local infrastructure.
2. IF a required dependency is unavailable THEN the health interface SHALL identify the failed dependency without exposing secrets.
3. The system SHALL expose separate liveness and readiness results.
4. WHEN any push is requested THEN the project workflow SHALL require a successful `make pre-push` result from the current commit.

**Independent Test**: Bootstrap a clean environment, run health checks and execute the pre-push gate.

### P1: Reliable and versioned ingestion

**User Story**: As a knowledge owner, I want sources ingested idempotently with provenance so that answers can be traced and indexes rebuilt.

1. WHEN the same source version is submitted twice THEN the ingestion module SHALL produce one canonical document version.
2. WHEN source content changes THEN the ingestion module SHALL retain the previous version and mark the new version as current.
3. IF parsing or indexing fails after bounded retries THEN the job SHALL end in an observable failed state with a redacted error and retry key.
4. WHEN a document is deleted THEN the system SHALL remove it from active retrieval while retaining only lifecycle data permitted by policy.
5. The system SHALL associate every chunk with tenant, source, document version, parser version and chunker version.

**Independent Test**: Ingest, repeat, update, fail and delete a fixture source while checking state and provenance.

### P1: Measured retrieval

**User Story**: As a user, I want the most relevant authorized evidence so that the answer reflects the current source material.

1. WHEN a query is submitted THEN the retrieval module SHALL apply tenant and document policy filters before returning evidence.
2. WHEN the BM25 baseline is evaluated THEN the system SHALL produce Recall@k, MRR, nDCG@k and latency results tied to dataset and configuration versions.
3. WHERE dense, hybrid or reranking is enabled, the system SHALL record its quality and latency delta against the baseline.
4. IF a candidate strategy does not meet `CONSTRAINTS.md` promotion thresholds THEN the default production configuration SHALL remain unchanged.
5. WHEN current and historical documents conflict THEN the evidence set SHALL preserve version dates and current-status metadata.

**Independent Test**: Execute the versioned retrieval benchmark and inspect policy-filtered evidence.

### P1: Grounded answers

**User Story**: As an engineer, I want answers with exact evidence so that I can verify them before acting.

1. WHEN the system makes a factual claim from the corpus THEN the answer SHALL attach a resolvable citation to the supporting document span.
2. IF authorized evidence is insufficient THEN the system SHALL abstain and explain the missing evidence.
3. IF a citation cannot be resolved to the indexed document version THEN the response SHALL fail verification and SHALL not be presented as verified.
4. WHEN generation is streamed THEN the final response SHALL still pass citation verification before receiving verified status.

**Independent Test**: Run supported, unsupported, conflicting and malicious-document golden questions.

### P1: Security and isolation

**User Story**: As an organization administrator, I want tenant and document isolation so that private knowledge cannot leak.

1. WHEN a principal requests an answer THEN the authorization module SHALL restrict evidence to documents the principal can read.
2. IF a document contains instructions addressed to the model THEN the system SHALL treat them as untrusted content and SHALL not expand tool or data access.
3. IF a principal attempts cross-tenant access THEN the system SHALL return no protected content and SHALL emit an audit event.
4. The system SHALL redact secrets and sensitive tokens from logs, traces and error responses.

**Independent Test**: Run cross-tenant and prompt-injection adversarial scenarios and observe audit records.

### P2: Bounded agentic investigation

**User Story**: As an incident responder, I want a bounded investigation across sources so that complex questions produce an auditable report.

1. WHEN a task requires multi-hop retrieval THEN the router SHALL select `Investigate` and record the routing reason.
2. WHILE an investigation is running, the agent SHALL enforce configured limits for steps, duration, tokens, tools and retrieval attempts.
3. IF any budget is exhausted THEN the agent SHALL stop and return partial evidence with the stop reason.
4. WHEN an investigation completes THEN the report SHALL include evidence, tool actions, unresolved questions, cost and duration.
5. IF the agent does not meet promotion thresholds against standard RAG THEN the production router SHALL keep it disabled by default.

**Independent Test**: Run the multi-hop benchmark including timeout, tool failure and insufficient-evidence cases.

### P1: Operability and release safety

**User Story**: As the on-call engineer, I want observable behavior and tested recovery so that failures can be diagnosed and reversed.

1. WHEN a request or ingestion job executes THEN the system SHALL emit correlated logs, metrics and traces without corpus contents or secrets.
2. IF embeddings or the generation provider is unavailable THEN the `Ask` path SHALL enter its documented degraded mode.
3. WHEN an index schema, embedding or chunker changes THEN the system SHALL rebuild a versioned index and switch aliases only after validation passes.
4. WHEN a release candidate is created THEN CI SHALL run lint, types, tests, coverage, migrations, evals, security scans and artifact build.
5. IF any mandatory release gate fails THEN the change SHALL not be merged or deployed.
6. WHEN recovery is tested THEN the project SHALL record achieved RPO and RTO against `CONSTRAINTS.md`.

**Independent Test**: Execute failure injection, blue/green reindex, backup restore and release gates in a staging-equivalent environment.

## Implicit Requirement Dimensions

| Dimension | Resolution |
| --- | --- |
| Input validation and bounds | All external identifiers, uploads, queries and pagination receive explicit limits |
| Failure and partial failure | Jobs use explicit states; online requests return typed failures or degraded responses |
| Idempotency and retries | Ingestion uses source-version keys; retries are bounded and classified |
| Auth and rate limits | Tenant, principal and document policy are mandatory context; quotas are enforced |
| Concurrency and ordering | Current-version promotion and index alias switches use guarded transitions |
| Data lifecycle | Retention, deletion, tombstones and reindex behavior are explicit |
| Observability | Correlation IDs span API, worker, retrieval and model calls |
| External dependency failure | Timeouts, retry policy, circuit breaking and fallback are defined per adapter |
| State transition integrity | Ingestion and investigation states reject invalid transitions |

## Requirement Traceability

| Requirement ID | Capability | Status |
| --- | --- | --- |
| FND-01 | Reproducible foundation and gates | Phase 0 complete (T01-T05) |
| ING-01 | Idempotent ingestion | Phase 1 complete: source lifecycle, canonical versions, bounded retries and resumable orchestration (T07-T13) |
| ING-02 | Versioning and provenance | Phase 1 complete: durable history, verified artifacts, normalized spans, structural chunks and index preparation (T07-T13) |
| RET-01 | Authorized BM25 baseline | In progress: golden dataset and versioned lexical index complete (T06, T14) |
| RET-02 | Experimental retrieval promotion | Pending |
| ANS-01 | Grounded citations | In progress: versioned golden dataset seeded (T06) |
| ANS-02 | Abstention and verification | Pending |
| SEC-01 | Tenant/document isolation | Pending |
| SEC-02 | Untrusted-content and secret controls | Pending |
| AGT-01 | Bounded investigation | Pending |
| AGT-02 | Agent promotion benchmark | Pending |
| OPS-01 | Observability and degraded modes | In progress: ingestion retry checkpoints, redacted failures and DLQ outcomes (T13) |
| OPS-02 | Reindex and recovery | In progress: immutable lexical schema versions and safe aliases complete (T14) |
| REL-01 | Pre-push and release gates | In progress: local gates and checker configuration complete (T02-T03) |

## Success Criteria

- [ ] A new contributor can bootstrap and run the vertical slice from documented commands.
- [ ] The golden dataset and every experiment are versioned and reproducible.
- [ ] All promotion thresholds in `CONSTRAINTS.md` have machine-produced evidence.
- [ ] Adversarial authorization tests show zero cross-tenant disclosure.
- [ ] A clean `make pre-push` run is recorded before every user-authorized push.
- [ ] Staging recovery and rollback exercises meet the documented operational budgets.
