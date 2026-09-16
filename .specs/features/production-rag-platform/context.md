# GroundedOps Production RAG Platform Context

**Gathered:** 2026-09-16
**Spec:** `.specs/features/production-rag-platform/spec.md`
**Status:** Ready for execution approval

---

## Feature Boundary

Build an Engineering Knowledge & Incident Copilot that ingests authorized,
versioned engineering knowledge and exposes a deterministic `Ask` path plus a
bounded `Investigate` path. Every complexity increase must demonstrate measurable
value over a simpler baseline. Automatic production remediation remains out of
scope.

The product name is **GroundedOps** and the repository slug is
`grounded-ops-copilot`.

---

## Implementation Decisions

### Corpus and product scenario

- The initial real corpus is public OpenTelemetry documentation and GitHub artifacts.
- Synthetic Company runbooks, ADRs and postmortems provide controlled incidents,
  permissions, conflicts and golden evaluation cases.
- Synthetic artifacts are always identified as synthetic in their metadata.

### Application and data stack

- Python 3.13 with `uv`, FastAPI, Pydantic, SQLAlchemy and Alembic.
- PostgreSQL and S3-compatible object storage are authoritative stores.
- OpenSearch is a rebuildable retrieval projection.
- Celery and Redis support asynchronous ingestion and investigation.
- OpenTelemetry and Langfuse provide observability; Pytest/Testcontainers,
  Ruff and MyPy enforce validation.
- Next.js is introduced after the backend vertical slice.

### Model selection

- Generation uses the OpenAI Responses API behind a project-owned interface.
- GPT-5 nano, GPT-4o Mini and GPT-5.6 Luna are benchmarked on the same versioned suite.
- The cheapest candidate that passes every quality gate becomes the default.
- GPT-5.6 Terra is an upper-bound comparator for difficult cases, not the default.

### Deployment

- Local development runs with Docker Compose.
- AWS is the cloud target and Terraform is the infrastructure definition.
- Pilot prioritizes economical managed services with reduced redundancy.
- Production HA uses multi-AZ compute, database and search plus production-grade
  edge protection, queueing, backups and recovery controls.

### Quality contract

- Tests and the applicable task gate must pass before a task is complete.
- Fixed invariants block immediately; workload-dependent metrics are measured and
  then frozen as ratchets.
- Capability-specific gates activate when their underlying feature exists.
- `make check` targets 30 seconds, a task gate 90 seconds, `make pre-push` five
  minutes and `make release-check` fifteen minutes in CI.
- Load, recovery and infrastructure tests run in a separate operational pipeline.
- No push is included in implementation authorization; every push requires an
  explicit user request and a fresh successful `make pre-push` result.

### Development workflow

- The agent writes tests, implementation and one local atomic commit per task.
- Commit messages use concise, professional English Conventional Commits.
- The user performs every push to the remote repository.
- Sequential task-batch workers may use cheaper OpenAI models for mechanical work.
- High-ambiguity work and independent verification use stronger reasoning tiers.
- All model tiers follow the same specification, tests and quality gates.

### Agent's Discretion

- Exact internal package names and file layout within the approved module boundaries.
- The first set of synthetic incident narratives, provided they cover the required
  versioning, authorization, conflict and multi-hop scenarios.
- Visual styling of the later web client within the accessibility and performance gates.

### Declined / Undiscussed Gray Areas -> Assumptions

- Every artifact carries `tenant_id` and document-level policy from its first schema.
  This remains the safe default because authorization cannot be retrofitted reliably.
- The initial frontend is intentionally minimal and follows the validated backend
  vertical slice.

---

## Specific References

- `jamwithai/production-agentic-rag-course` is a learning and sequencing reference,
  not a template to copy.
- OpenTelemetry provides the public technical domain for the portfolio scenario.

---

## Deferred Ideas

- Kubernetes, multiple model providers, fine-tuning and automatic remediation are
  deferred until evidence shows they solve a real limitation.
