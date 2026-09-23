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

### T01: Bootstrap the Python project [x]

**What**: Create the locked Python 3.13 project, source layout and minimal application entry point.
**Where**: `pyproject.toml`
**Depends on**: None
**Requirement**: FND-01
**Done when**: `uv sync --locked` succeeds on a clean environment; the application imports; Python version mismatch fails clearly.
**Tests**: config/import smoke test.
**Gate**: Quick.
**Commit**: `chore(project): bootstrap python workspace`

### T02: Implement local quality commands [x]

**What**: Add canonical `check`, `test`, `pre-push`, `release-check` and `operational-test` targets.
**Where**: `Makefile`
**Depends on**: T01
**Requirement**: REL-01
**Done when**: every target exists, returns non-zero on an intentional fixture failure and passes after the fixture is restored.
**Tests**: command contract smoke tests.
**Gate**: Build.
**Commit**: `build(quality): add canonical verification gates`

### T03: Configure lint, typing and test markers [x]

**What**: Configure Ruff, strict MyPy, Pytest markers and coverage reporting.
**Where**: `pyproject.toml`
**Depends on**: T02
**Requirement**: FND-01, REL-01
**Done when**: an intentional lint, type and coverage violation is detected; no global ignore disables a checker.
**Tests**: checker discrimination smoke tests.
**Gate**: Build.
**Commit**: `build(quality): enforce python checks and test markers`

### T04: Provision local infrastructure [x]

**What**: Define health-checked PostgreSQL, OpenSearch and object-storage containers.
**Where**: `compose.yml`
**Depends on**: T03
**Requirement**: FND-01
**Done when**: a clean `docker compose up` reaches healthy state and teardown preserves only documented volumes.
**Tests**: infrastructure smoke test.
**Gate**: Full.
**Commit**: `build(local): provision core data dependencies`

### T05: Add health and readiness interfaces [x]

**What**: Implement separate liveness and dependency-aware readiness routes.
**Where**: `src/interfaces/http/health.py`
**Depends on**: T04
**Requirement**: FND-01
**Done when**: liveness remains healthy during dependency failure; readiness identifies the unavailable dependency without leaking configuration.
**Tests**: API tests for healthy and failed dependencies.
**Gate**: Full.
**Commit**: `feat(health): expose liveness and readiness`

### T06: Seed the versioned evaluation corpus [x]

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

### T07: Define ingestion domain states [x]

**What**: Model source, document, version and ingestion-job states with guarded transitions.
**Where**: `src/domain/ingestion.py`
**Depends on**: T06
**Requirement**: ING-01, ING-02
**Done when**: every valid transition succeeds and every invalid transition fails deterministically.
**Tests**: unit tests for all state transitions.
**Gate**: Quick.
**Commit**: `feat(ingestion): define versioned ingestion domain`

**Evidence**: `make check` passed, 73 unit tests (64 ingestion), zero failures.
Tests were red on missing public contracts, then green. The domain seam is
immutable models plus `transition`/`delete`. Assumption: retrying can return to
any interrupted active stage; completed/failed are terminal. Names and validation
limits are implementation choices where the specification gives no literal.

| Criterion | Assertion evidence in `tests/unit/domain/test_ingestion.py` | Outcome |
| --- | --- | --- |
| Valid and invalid transitions | 42 `assert changed.state == after`; 50 `pytest.raises(InvalidTransition)`; 52 `assert error.value.event == {...}` | All 49 pairs guarded; safe audit payload |
| Identity and provenance | 44 identity tuple; 74 source tuple; 80 document tuple; 85 version tuple; 90 timestamp/parser/raw tuple; 95 `pytest.raises(FrozenInstanceError)` | Version and policy retained, immutable |
| Bounded inputs | 101, 106, 123, 128, 134, 136, 141 `pytest.raises(ValueError, match=...)` | Invalid identifiers, policy, hash, timezone, references and attempts rejected |
| Deletion lifecycle | 116 `assert tombstone.deleted is True`; 117 `assert tombstone.current_version_id is None`; 118 `assert tombstone.delete() == tombstone` | Idempotent tombstone, no active version |

| Tests / assertions above | Requirement mapping | Keep |
| --- | --- | --- |
| 42-52 | T07 guarded transitions, design audit event | Yes |
| 44, 74-95 | ING-02 provenance and immutable identity | Yes |
| 101-141 validation | Spec input bounds and state integrity | Yes |
| 116-118 | ING-01 deletion | Yes |

Adequacy A-D: PASS. Every criterion has value/state evidence; no internal mocks,
shallow assertions, unclaimed tests, removed tests or suppressions. Followed
`CONSTRAINTS.md` and the task coverage matrix. Packaging and coverage now include
the domain and existing HTTP interface so the gate measures their actual code.

### T08: Persist ingestion metadata [x]

**What**: Implement PostgreSQL persistence and migrations for ingestion entities and idempotency keys.
**Where**: `src/adapters/postgres/ingestion_repository.py`
**Depends on**: T07
**Requirement**: ING-01, ING-02
**Done when**: create, duplicate, update, concurrent-idempotency and rollback paths pass against PostgreSQL.
**Tests**: repository integration tests and migration up/down test.
**Gate**: Full.
**Commit**: `feat(ingestion): persist jobs and document versions`

**Evidence**: `make test` passed: 88 tests, zero failures, coverage >= 86%.
Public seam: transaction-owned repository and `migrate(connection, revision)`.
Files: PostgreSQL adapter/migrations, integration Compose/fixtures/tests,
`pyproject.toml`, `uv.lock`, task/spec status. Assumptions: versions are immutable;
current promotion is explicit; timestamp then source revision orders promotion.
The repository serializes submissions on a document row. SQL and migrations
follow the official SQLAlchemy PostgreSQL and Alembic connection-sharing APIs.

| Criterion | Evidence in `tests/integration/test_ingestion_repository.py` | Outcome |
| --- | --- | --- |
| Create/persist payload | 28 `get_source(...) == SOURCE`; 29 document equality; 33 version equality; 36 job equality; 37-40 exact initial metadata | Full round-trip |
| Duplicate/update/history | 54 `assert duplicate == old`; 61 `current.current_version_id == newer.version.id`; 62 `list_versions(...) == [old.version, newer.version]` | One canonical version; old retained |
| Concurrent idempotency | 92 `assert results == [results[0]] * 4`; 94 `list_versions(...) == [results[0].version]` | Four simultaneous writers, one job/version |
| Rollback | 107 `get_source(...) is None` after aborted transaction | No partial source |
| Tenant/lifecycle guards | 41-44 `... is None`; 66 `... == []`; 68 unchanged document; 71,73 `... == current.delete()`; 74 `pytest.raises(ValueError, match="unknown document version")` | Isolated reads/writes; no resurrection |
| Conflicting source revision | 115 `pytest.raises(ValueError, match="source version content conflict")`; 121 original version equality | No rewrite |
| Migration up/down | 130 `pytest.raises(ProgrammingError)` after downgrade; 138 `result.job.state == JobState.QUEUED` after upgrade | Reversible, usable schema |

| Assertion groups above | Requirement mapping | Keep |
| --- | --- | --- |
| 28-40,54,61-62,92-94,107,115-121 | T08 create/duplicate/update/concurrency/rollback, ING-01/02 | Yes |
| 41-44,66-74 | ING-01 lifecycle, tenant invariant | Yes |
| 130,138 | T08 migration up/down | Yes |

Adequacy A-D: PASS. Real PostgreSQL, observable values, no internal mocks or
weakened tests. Contract: `CONSTRAINTS.md` and coverage matrix. Exact promotion
tie-break is a spec-precision choice; no requirement was dropped.

### T09: Store immutable document artifacts [x]

**What**: Implement content-addressed raw and normalized artifact storage.
**Where**: `src/adapters/object_store/document_store.py`
**Depends on**: T08
**Requirement**: ING-02
**Done when**: identical content reuses an object; corrupted or missing objects fail verification; tenant prefixes remain isolated.
**Tests**: object-store integration tests.
**Gate**: Full.
**Commit**: `feat(ingestion): store immutable document artifacts`

**Evidence**: `make test` passed, 97 tests. Public seam: `DocumentStore.put/get`
against real MinIO. Files: object-store adapter/tests, integration fixture/Compose,
dependencies, task/spec status. Assumptions: SHA-256 keys, 10 MiB artifact limit,
conditional creation; both raw and normalized artifacts are verified on every read.
Conditional writes follow the [S3 PutObject contract](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html).

| Criterion | Evidence in `tests/integration/test_document_store.py` | Outcome |
| --- | --- | --- |
| Identical content | 21 exact SHA-256 literal; 25 `original.key == f"alpha/{kind}/{original.digest}"`; 26 `store.put(...) == original`; 27 `store.get(...) == b"abc"` | Verified content address, both artifact kinds |
| Corrupt/missing | 36,38 `pytest.raises(ArtifactError, match="artifact integrity failed")`; 53 `pytest.raises(ArtifactError, match="artifact unavailable")` | Failed verification, no repair by overwrite |
| Tenant isolation | 48 `alpha.key != beta.key`; 49 byte equality; 50 unavailable error; 75 invalid-reference error | Separate prefixes, forged refs rejected |
| Dependency/bounds | 60 exact safe error; 61 `"private" not in str(error.value)`; 82 size-limit error | Redacted failure, bounded upload |

| Assertion groups above | Maps to | Keep |
| --- | --- | --- |
| 21-38,53 | T09 reuse and integrity, ING-02 | Yes |
| 48-50,75 | T09 tenant isolation | Yes |
| 60-61,82 | Spec external failure and input bounds | Yes |

Adequacy A-D: PASS under `CONSTRAINTS.md`; all outcomes observed through adapter
results. Direct provider writes only inject corruption. No internal mocks,
weakened tests or suppressions. Exact byte limit is a spec-precision choice.

### T10: Add the initial source adapter [x]

**What**: Ingest Markdown files and metadata from the selected public corpus.
**Where**: `src/adapters/sources/markdown.py`
**Depends on**: T09
**Requirement**: ING-01
**Done when**: new, unchanged, changed, deleted and malformed fixtures produce the specified source events.
**Tests**: adapter unit tests with frozen fixtures.
**Gate**: Quick.
**Commit**: `feat(sources): ingest markdown corpus`

**Evidence**: `make check` passed: 83 unit tests, zero failures. Public seam:
`MarkdownCorpus.scan` over frozen corpus fixtures. Files: Markdown source adapter,
unit tests, runtime YAML dependency and task/spec status. Assumptions: SHA-256 is
the source version; a missing first-seen fixture is malformed, while a previously
seen source that disappears is deleted; snapshot timestamps must be timezone-aware.

| Criterion | Evidence in `tests/unit/modules/test_markdown_source.py` | Outcome |
| --- | --- | --- |
| New, unchanged, changed and deleted | 19 exact new kinds; 28 unchanged kinds; 41-43 changed kind/version/content; 50-52 deleted kind/content/key | Every lifecycle event is deterministic |
| Source metadata | 21-26 exact tenant, policy, license, synthetic flag, timestamp and licensed content | Complete source identity and metadata |
| Malformed fixtures | 67-70 exact safe malformed payload for invalid UTF-8, NUL and oversized input; 86-90 path-escape payload | Invalid input never exposes content |
| Manifest and cursor safety | 100-101 manifest error; 124 deletion tombstone; 125-128 invalid tenant/time cursor errors | Invalid snapshots never become mass deletion |

| Assertion groups above | Maps to | Keep |
| --- | --- | --- |
| 19-52 | T10 lifecycle events, ING-01 | Yes |
| 21-26 | Initial corpus metadata and provenance | Yes |
| 67-101 | Spec input bounds, untrusted input and redacted failure | Yes |
| 124-128 | T10 deletion and snapshot integrity | Yes |

Adequacy A-D: PASS under `CONSTRAINTS.md` and the task coverage matrix. All
payload fields are asserted through the public adapter result. Tests use frozen
fixtures without live network access; none are shallow, skipped, weakened or
unclaimed. Exact manifest and byte bounds are spec-precision choices.

### T11: Normalize and structurally parse documents [x]

**What**: Convert source content into normalized sections and provenance-preserving spans.
**Where**: `src/modules/parsing/parser.py`
**Depends on**: T10
**Requirement**: ING-02
**Done when**: headings, code blocks, tables, links and malformed input retain deterministic span mappings.
**Tests**: parser unit tests and snapshot fixtures.
**Gate**: Quick.
**Commit**: `feat(parsing): normalize structured documents`

**Evidence**: `make check` passed: 88 unit tests, zero failures. Public seam:
`MarkdownParser.parse(bytes) -> ParsedDocument`. Files: parser module, frozen
Markdown/JSON snapshots, unit tests, packaging/coverage config and task/spec status.
Assumptions: normalized spans use zero-based half-open character offsets; line
endings normalize to LF; section text includes its heading for exact provenance.

| Criterion | Evidence in `tests/unit/modules/test_parser.py` | Outcome |
| --- | --- | --- |
| Headings and deterministic snapshot | 21-24 exact parsed snapshot; 41 equality across repeated parses | Stable sections, titles, levels and offsets |
| Complete span mappings | 25-28 exact slice/text equality; 43-50 preamble/heading offsets and complete reassembly | Every normalized character belongs to a source span |
| Links, code blocks and tables | 29 exact Markdown link; 30 fenced code with heading-like content; 31 table row | Structure preserved as traceable text |
| Malformed input | 59-62 exact safe error for invalid UTF-8, NUL and oversized bytes | Rejected without content disclosure |

| Assertion groups above | Maps to | Keep |
| --- | --- | --- |
| 21-31 | T11 structural parsing, ING-02 | Yes |
| 41-50 | T11 determinism, normalization and span completeness | Yes |
| 59-62 | Spec input bounds and redacted failure | Yes |

Adequacy A-D: PASS under `CONSTRAINTS.md` and the task coverage matrix. All
assertions observe the parser's public immutable result; the snapshot literals
derive from the frozen fixture, not parser internals. No shallow, skipped,
weakened or unclaimed tests. Offset units and section ownership are
spec-precision choices.

### T12: Implement baseline structural chunking [x]

**What**: Produce deterministic chunks with token bounds and complete provenance.
**Where**: `src/modules/chunking/structural.py`
**Depends on**: T11
**Requirement**: ING-02
**Done when**: chunks respect configured bounds, never lose source spans and remain stable for identical inputs.
**Tests**: property and unit tests for boundaries, tiny sections and oversized code blocks.
**Gate**: Quick.
**Commit**: `feat(chunking): add provenance-preserving chunks`

**Evidence**: `make check` passed: 94 unit tests, zero failures. Public seam:
`StructuralChunker(max_tokens).chunk(ParsedDocument)`. Files: structural chunker,
unit/property tests and task/spec status. Assumptions: non-whitespace runs are the
deterministic v1 token proxy; `max_tokens` is a hard bound; zero-based half-open
normalized character spans preserve provenance across parser and chunker versions.

| Criterion | Evidence in `tests/unit/modules/test_structural_chunking.py` | Outcome |
| --- | --- | --- |
| Token bounds and versioned provenance | 30-33 exact counts, max bound and parser/chunker versions | Every chunk is bounded and versioned |
| No lost source spans | 12-20 exact text reassembly, ordinals, first/last offsets, adjacency and non-empty slices; invoked at 34, 48, 61 and 74 | Full normalized document provenance |
| Tiny sections | 42-48 one bounded chunk with both exact heading spans | Structural boundaries retained |
| Oversized code blocks | 58-61 exact split counts and fence endpoints | Deterministic bounded split without text loss |
| Stability/property range | 72-74 equality, bound and provenance for 40 document lengths | Identical inputs produce identical chunks |
| Invalid bounds | 79-80 exact validation error | Non-positive configuration rejected |

| Assertion groups above | Maps to | Keep |
| --- | --- | --- |
| 12-34 | T12 bounds, complete provenance and ING-02 versions | Yes |
| 42-61 | T12 tiny-section and oversized-code edge cases | Yes |
| 72-80 | T12 stability, property coverage and configured bounds | Yes |

Adequacy A-D: PASS under `CONSTRAINTS.md` and the task coverage matrix. Tests
exercise parser and chunker public seams with exact output/state assertions; no
internal collaborators are mocked. No shallow, skipped, weakened or unclaimed
tests. The v1 token proxy is a documented spec-precision choice.

### T13: Orchestrate idempotent ingestion [x]

**What**: Coordinate fetch, store, parse, chunk and index preparation with bounded retries and DLQ state.
**Where**: `src/modules/ingestion/pipeline.py`
**Depends on**: T12
**Requirement**: ING-01, ING-02, OPS-01
**Done when**: repeat, update, partial failure, retry exhaustion and resume cases produce the expected states without duplicate versions.
**Tests**: module unit tests plus end-to-end ingestion integration test.
**Gate**: Full.
**Commit**: `feat(ingestion): orchestrate recoverable pipeline`

**Evidence**: `make test` passed: 123 tests, zero failures, 90.07% coverage.
Public seams: `IngestionPipeline.run/resume` and
`IndexPreparationSink.prepare`. The E2E test uses `MarkdownCorpus`, PostgreSQL
and `DocumentStore` adapters against real ephemeral services. Files: pipeline
and retry contract, durable checkpoint migration/repository methods, unit/E2E
tests, task/spec status and Phase 1 handoff. Assumptions: worker invocations
perform one bounded attempt then persist `resume_from`; index preparation is an
idempotent versioned projection seam and does not implement OpenSearch.

| Criterion | Assertion evidence | Outcome |
| --- | --- | --- |
| Repeat and update without duplicates | `tests/integration/test_ingestion_pipeline_e2e.py`: 83-91 completed states, same repeat job, distinct update version and exactly two versions | Canonical repeat; retained history |
| Real artifacts and provenance preparation | E2E 92-108 stored raw/normalized refs, tenant/version identity and non-empty spans | Complete durable and projection provenance |
| Delete lifecycle | E2E 109-112 exact deleted outcome, tombstone and no current version | Removed from active lifecycle |
| Partial failure and resume | `tests/unit/modules/test_ingestion_pipeline.py`: 185-205 retry checkpoint/attempt/error, completed resume, one version, two idempotent sink calls and versioned chunk payload | Resume does not duplicate versions or preparations |
| Retry exhaustion and redacted DLQ | Unit 216-225 terminal failure, two attempts, retry key/error/attempt payload, no private detail and no third call | Bounded observable failure |
| Parsing failure checkpoint | Unit 236-241 parsing resume point, failed/DLQ class and no prepared records | Stage-specific safe failure |
| Deletion without new version | Unit 252-258 completed source, deleted result/tombstone and one retained version | Idempotent lifecycle data retention |

| Assertion groups above | Maps to | Keep |
| --- | --- | --- |
| E2E 83-112 | T13 repeat/update/delete, ING-01/02 real-adapter vertical slice | Yes |
| Unit 185-205 | T13 partial failure/resume, ING-01/02 provenance | Yes |
| Unit 216-241 | T13 bounded retries, redacted DLQ, OPS-01 | Yes |
| Unit 252-258 | ING-01 deletion lifecycle | Yes |

Adequacy A-D: PASS under `CONSTRAINTS.md` and the task coverage matrix. Unit
fakes exist only at storage/index boundaries; the E2E path uses real source,
database and object-store adapters. Assertions target public results and durable
state, not collaborator call counts alone. No shallow, skipped, weakened or
unclaimed tests. Retry scheduling is a documented spec-precision choice.

**Phase gate**: `make pre-push`; demonstrate ingest/repeat/update/fail/delete; record provenance audit.

---

## Fase 2: Authorized BM25 Vertical Slice

**Outcome**: A user can retrieve authorized evidence and receive a minimal cited response without embeddings or agents.

### T14: Create versioned lexical index mappings [x]

**What**: Define analyzers, fields, tenant policy fields and aliases for the BM25 index.
**Where**: `src/adapters/opensearch/index_schema.py`
**Depends on**: T13
**Requirement**: RET-01, OPS-02
**Done when**: mapping creation is idempotent and rejects incompatible in-place schema changes.
**Tests**: OpenSearch integration tests.
**Gate**: Full.
**Commit**: `feat(search): define versioned lexical index`

**Evidence**: `make test` passed: 126 tests, zero failures, 90.28% coverage.
Public seam: `LexicalIndexSchema.ensure`. Real OpenSearch tests prove analyzer and
policy mappings (`test_opensearch_index_schema.py:28-34`), idempotency (`:42`) and
rejection of an incompatible physical index (`:55`). The fingerprint is stored in
mapping metadata; schema changes require a new physical version. Adequacy A-D:
PASS, with exact observable mapping/alias assertions and no mocks or suppressions.

### T15: Index and remove document versions [x]

**What**: Project current authorized chunks into OpenSearch and tombstone removed documents.
**Where**: `src/adapters/opensearch/index_writer.py`
**Depends on**: T14
**Requirement**: ING-02, OPS-02
**Done when**: upsert, repeat, version replacement, delete and bulk partial failure are consistent with PostgreSQL state.
**Tests**: indexing integration tests.
**Gate**: Full.
**Commit**: `feat(search): project document versions into index`

**Evidence**: `make test` passed: 129 tests, zero failures, 90.74% coverage.
Public seam: `OpenSearchIndexWriter.upsert/remove_document`. Real OpenSearch tests
prove idempotent upsert, retained historical versions and policy projection
(`test_opensearch_index_writer.py:68-72`), deletion (`:86`) and partial-bulk
rollback that preserves the prior current version (`:107,115-118`). Adequacy A-D:
PASS; outcomes are queried from the public index and no dependency is mocked.

### T16: Implement the policy module [x]

**What**: Resolve tenant, role, group and document decisions outside the model.
**Where**: `src/modules/policy/authorizer.py`
**Depends on**: T08
**Requirement**: SEC-01
**Done when**: allow, deny, unknown principal and cross-tenant cases return explicit decisions and reasons.
**Tests**: exhaustive authorization unit tests.
**Gate**: Quick.
**Commit**: `feat(policy): authorize document access`

**Evidence**: `make check` passed: 106 unit tests, zero failures. Public seam:
`Authorizer.authorize`. The exhaustive table proves public, principal, role and
group allow reasons (`test_authorizer.py:37-39`); dedicated assertions prove
no-match deny (`:51-52`), unknown principal (`:63-64`), cross-tenant (`:76-77`)
and unsupported action (`:88-89`). Adequacy A-D: PASS; every decision asserts
both boolean outcome and explicit reason through the public interface.

### T17: Implement BM25 retrieval [x]

**What**: Return a typed `EvidenceSet` using BM25 and mandatory policy filters.
**Where**: `src/modules/retrieval/retriever.py`
**Depends on**: T15, T16
**Requirement**: RET-01, SEC-01
**Done when**: relevance ordering, metadata filters, current/historical selection, empty results and dependency failure match the spec.
**Tests**: unit tests with a fake adapter and OpenSearch integration tests.
**Gate**: Full.
**Commit**: `feat(retrieval): add authorized bm25 baseline`

**Evidence**: `make test` passed: 144 tests, zero failures, 91.65% coverage.
Public seam: `BM25Retriever.retrieve(QueryContext) -> EvidenceSet`; adapter seam:
`OpenSearchBM25Adapter.search`. Unit evidence covers ordered scores, version/date/
span/current provenance (`test_retriever.py:65-73`), reauthorization (`:95`),
empty and redacted failure (`:106-123`). Real OpenSearch proves BM25 ordering and
tenant isolation (`test_opensearch_retrieval.py:96-98`), ACL/metadata/history
filters (`:117-121`) and dependency failure (`:138-143`). Adequacy A-D: PASS;
fake only exercises the public adapter contract and query DSL runs on OpenSearch.

### T18: Expose the evidence search route [x]

**What**: Add an authenticated endpoint returning policy-filtered evidence and retrieval diagnostics.
**Where**: `src/interfaces/http/search.py`
**Depends on**: T17
**Requirement**: RET-01, SEC-01
**Done when**: success, validation, pagination, unauthorized, empty and dependency-failure paths pass.
**Tests**: API integration tests.
**Gate**: Full.
**Commit**: `feat(api): expose authorized evidence search`

**Evidence**: `make test` passed: 150 tests, zero failures, 91.87% coverage.
Public seam: `GET /v1/evidence/search`. API tests assert the complete evidence and
diagnostics payload (`test_search.py:78-101`), exact 422 validation (`:107-121`),
pagination/filter/principal propagation (`:139-147`), 401/403 authorization
(`:161-164`), empty 200 (`:173-178`) and redacted 503 (`:186-188`). Adequacy
A-D: PASS; all documented route outcomes have exact status and payload evidence.

### T19: Establish the BM25 benchmark [x]

**What**: Run and persist the baseline retrieval report for dataset v1.
**Where**: `evals/retrieval/baseline.py`
**Depends on**: T18
**Requirement**: RET-01, RET-02
**Done when**: report contains config hash, dataset hash, Recall@k, MRR, nDCG@k, p50/p95 and per-query errors.
**Tests**: retrieval eval determinism and metric tests.
**Gate**: Release.
**Commit**: `test(retrieval): establish bm25 benchmark`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 153 tests in the coverage run, 43 release-marker tests, 91.87% total
coverage, 98% diff coverage, zero leaks and a clean floor guard. Public seam:
`run_benchmark(...) -> BenchmarkReport`. Literal metric/error assertions are at
`test_retrieval_baseline.py:53-61`; determinism and config/dataset hashes at
`:78-84`; the persisted real-OpenSearch report is verified at `:95-109`.
Dataset v1 baseline: Recall@10 1.00, MRR 1.00, nDCG@10 1.00, p50 17 ms,
p95 26 ms, zero errors (two queries). Adequacy A-D: PASS; the report is a measured
baseline, not an unearned improvement claim.

**Phase gate**: `make pre-push` and `make release-check`; publish BM25 benchmark artifact.

---

## Fase 3: Retrieval Experimentation and Promotion

**Outcome**: Dense, hybrid, reranking and chunking changes are compared fairly; only qualifying candidates become defaults.

### T20: Add embedding generation with versioned metadata [x]

**What**: Generate passage/query embeddings with batching, timeout, retry and model-version metadata.
**Where**: `src/modules/embeddings/embedder.py`
**Depends on**: T19
**Requirement**: RET-02, OPS-01
**Done when**: deterministic fake, live contract, batching, rate limit and provider failure cases pass.
**Tests**: unit tests plus provider contract integration test.
**Gate**: Full.
**Commit**: `feat(embeddings): add versioned embedding pipeline`

**Evidence**: `make test` passed: 160 tests, zero failures, 91.34% coverage.
Public seams: `Embedder` and `EmbeddingProvider`; the OpenAI adapter disables SDK
retries so batching and retries remain explicit and bounded. Unit assertions prove
batch contents, vectors and model/version/dimension/content-hash metadata
(`test_embedder.py:46-62`), the query contract (`:72-76`), three-attempt retry
boundedness (`:97-98`), terminal safe errors (`:120-123`) and input bounds
(`:132-135`). A deterministic local HTTP server verifies the real `/embeddings`
request and indexed response ordering (`test_openai_embedding_contract.py:68-78`),
without an API key or paid call. Adequacy A-D: PASS; all done-when outcomes have
observable public-interface evidence, no private seams, skips or suppressions.

### T21: Build the dense retrieval candidate [x]

**What**: Add vector mappings and dense query execution without changing the default.
**Where**: `src/modules/retrieval/dense.py`
**Depends on**: T20
**Requirement**: RET-02
**Done when**: dense runs against the same policy filters, corpus version and golden queries as BM25.
**Tests**: unit, OpenSearch integration and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add dense candidate`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed after one transient Docker restart failure: 164 tests in the coverage run,
46 release-marker tests, 90.86% total coverage, 88% diff coverage, zero leaks and
a clean floor guard. Public seams: `DenseRetriever` and `OpenSearchDenseAdapter`.
The unit contract asserts the complete tenant/ACL/corpus/filter request
(`test_dense_retrieval.py:83-94`), typed evidence (`:96-99`), reauthorization and
safe failure (`:117-119`). Real OpenSearch proves vector ordering, ACL and tenant
isolation (`test_opensearch_dense_retrieval.py:110-112`). The eval uses the exact
dataset hash and golden query IDs from the BM25 baseline with Recall@10 and
nDCG@10 both 1.00 (`test_dense_candidate.py:42-50`). Adequacy A-D: PASS; the
candidate is explicit and does not modify the BM25 production default.

### T22: Build the hybrid fusion candidate [x]

**What**: Combine lexical and dense rankings using configurable reciprocal-rank fusion.
**Where**: `src/modules/retrieval/hybrid.py`
**Depends on**: T21
**Requirement**: RET-02
**Done when**: duplicate fusion, missing-leg fallback, deterministic ties and policy invariants pass.
**Tests**: unit, integration and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add hybrid fusion candidate`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 171 tests in the coverage run, 48 release-marker tests, 90.35% total
coverage, 90% diff coverage, zero leaks and a clean floor guard. Public seam:
`HybridRetriever.retrieve`. Exact RRF ordering, duplicate identity fusion, scores
and latency are asserted at `test_hybrid_retrieval.py:59-63`; both missing-leg
fallbacks at `:78-80`; deterministic ties and policy recheck at `:100-101`; and
both-leg failure at `:111-112`. The composition test proves public BM25+dense
deduplication (`test_hybrid_composition.py:94-96`), while the eval pins the shared
dataset hash and quality (`test_hybrid_candidate.py:43-47`). Adequacy A-D: PASS;
no private seam, skipped test or automatic promotion was introduced.

### T23: Add the reranking candidate [x]

**What**: Rerank a bounded candidate set while preserving evidence identity and policy decisions.
**Where**: `src/modules/retrieval/reranker.py`
**Depends on**: T22
**Requirement**: RET-02
**Done when**: timeout fallback, stable evidence IDs, candidate bounds and latency accounting pass.
**Tests**: unit, contract and retrieval eval tests.
**Gate**: Release.
**Commit**: `feat(retrieval): add bounded reranker candidate`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 176 tests in the coverage run, 50 release-marker tests, 90.06% total
coverage, 89% diff coverage, zero leaks and a clean floor guard. Public seams:
`Reranker` and `RerankProvider`. Candidate bounds and exact provider payload are
asserted at `test_reranker.py:60-67`; stable IDs, scores, policy/provenance and
latency at `:68-74`; exact timeout fallback and added latency at `:84-86`; invalid
identity sets at `:95-96`. The deterministic local HTTP contract verifies request,
scores and measured latency (`test_reranker_contract.py:55-63`). The eval pins the
baseline dataset and quality (`test_reranker_candidate.py:43-47`). Adequacy A-D:
PASS; the candidate remains bounded and unpromoted.

### T24: Compare chunking candidates [x]

**What**: Evaluate fixed, structural and parent-child chunking on the same corpus and query set.
**Where**: `evals/retrieval/chunking_experiment.py`
**Depends on**: T23
**Requirement**: RET-02
**Done when**: report includes retrieval quality, citation-span precision, index size, ingestion time and query latency.
**Tests**: experiment reproducibility and metric tests.
**Gate**: Release.
**Commit**: `test(retrieval): compare chunking strategies`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 179 tests in the coverage run, 53 release-marker tests, 90.06% total
coverage, 89% diff coverage, zero leaks and a clean floor guard. Public seam:
`run_chunking_experiment`. The metric test pins dataset/query count and all three
strategies (`test_chunking_experiment.py:34-42`), then asserts config hashes,
Recall@10, MRR, nDCG@10, citation-span precision, index bytes, ingest/query latency
and the two-query limitation (`:44-53`). Repeatability is exact with a controlled
clock (`:63-64`); the immutable real-clock report is validated at `:75-86`.
Measured v1 quality is tied at 1.00 for every strategy; index sizes are 636 bytes
fixed, 408 structural and 402 parent-child. Adequacy A-D: PASS; timings are measured,
not promotion claims, and the small dataset is explicit.

### T25: Promote the qualifying retrieval configuration [x]

**What**: Select the default only from candidates meeting `CONSTRAINTS.md`; record rejected alternatives.
**Where**: `docs/decisions/ADR-001-retrieval-strategy.md`
**Depends on**: T24
**Requirement**: RET-02
**Done when**: ADR links immutable reports, calculates deltas and names the production and fallback configurations.
**Tests**: configuration regression eval and schema validation.
**Gate**: Release.
**Commit**: `docs(architecture): select measured retrieval strategy`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 183 tests in the coverage run, 57 release-marker tests, 89.68% total
coverage, 88% diff coverage, zero leaks and a clean floor guard. Public seams:
`select_candidate` and `load_retrieval_config`. Literal threshold application,
zero deltas, rejection reasons and BM25 production/fallback are asserted at
`test_retrieval_promotion.py:32-44`; config strategy, structural chunking, dataset
and immutable report hashes at `:52-60`; measured candidate report and limitations
at `:73-91`; ADR decision, deltas and links at `:100-107`. No candidate qualifies:
dense and hybrid gain 0.00% versus the required 5%; reranker gains 0.00% versus
3%. Recall@10 remains 1.00. Adequacy A-D: PASS; BM25 remains production and
fallback, with no manual promotion.

**Phase gate**: `make release-check`; archive experiment artifacts and ADR; no candidate is promoted manually.

---

## Fase 4: Grounded Answering

**Outcome**: `Ask` produces verified citations or abstains, while remaining usable in degraded modes.

### T26: Define grounded-answer contracts [x]

**What**: Model questions, evidence, claims, citations, verification status and abstention reasons.
**Where**: `src/domain/answering.py`
**Depends on**: T25
**Requirement**: ANS-01, ANS-02
**Done when**: invalid or unresolved citations cannot create a verified answer.
**Tests**: domain invariant unit tests.
**Gate**: Quick.
**Commit**: `feat(answering): define grounded answer contracts`

**Evidence**: `make check` passed: 135 unit tests, zero failures. Public seam:
immutable `Question`, `Citation`, `Claim`, `AnswerUsage` and `GroundedAnswer`
contracts. A verified answer's complete payload and immutability are asserted at
`test_answering.py:32-39`; missing claims, missing citations and unresolved
citations are rejected at `:57`; invalid spans at `:69`; abstention state/reason
coherence at `:86-103`. Adequacy A-D: PASS under `CONSTRAINTS.md` and the task
coverage matrix. Every assertion maps to ANS-01/ANS-02 or the done-when invariant;
no internal mocks, shallow assertions, skipped tests or suppressions.

### T27: Build the bounded context packer [x]

**What**: Select evidence within token budget while preserving source diversity and provenance.
**Where**: `src/modules/answering/context_packer.py`
**Depends on**: T26
**Requirement**: ANS-01
**Done when**: token overflow, duplicate evidence, conflicting versions and empty context cases are deterministic.
**Tests**: unit and property tests.
**Gate**: Quick.
**Commit**: `feat(answering): pack bounded evidence context`

**Evidence**: `make check` passed: 141 unit tests, zero failures. Public seam:
`ContextPacker.pack(EvidenceSet) -> PackedContext`. Exact diverse ordering, hard
budget and version/span/hash provenance are asserted at
`test_context_packer.py:50-56`; versioned deduplication at `:76-78`; preserved
conflicts at `:103-104`; empty determinism at `:112-116`; and the hard-bound
property for every budget 1-15 at `:130-133`. Adequacy A-D: PASS under
`CONSTRAINTS.md`; all done-when cases have observable value evidence and every
test maps to ANS-01 or an explicit bound, with no mocks, skips or suppressions.

### T28: Implement provider-neutral generation [x]

**What**: Generate structured answers with usage, timeout and provider-failure reporting.
**Where**: `src/modules/answering/generator.py`
**Depends on**: T27
**Requirement**: ANS-01, OPS-01
**Done when**: structured success, malformed output, timeout, retryable failure and deterministic fake pass.
**Tests**: unit and provider contract tests.
**Gate**: Full.
**Commit**: `feat(answering): generate structured grounded answers`

**Evidence**: `make test` passed: 205 tests, zero failures and 89.53% total
coverage (the coverage run took 121.64 s on local Docker). Public seams:
`GenerationProvider` and `AnswerGenerator`. Structured claims, exact proposed
citations, unverified status and usage are asserted at `test_generator.py:81-91`;
bounded retry and stable failures at `:108-109,132-134`; fabricated output at
`:155-156`. The local HTTP contract proves `POST /v1/responses`, model,
`store=false`, max output, evidence input, `text.format` JSON Schema and parsed
usage at `test_openai_generation_contract.py:156-169`, without a paid call.
Adequacy A-D: PASS under `CONSTRAINTS.md`; every done-when failure is typed and
redacted, generation never grants verified status, and no test is skipped or
suppressed. The local Docker duration exceeded the 90 s target and remains visible.

### T29: Verify claim-level citations [x]

**What**: Resolve citations to exact versioned spans and assign verified, unverified or abstained status.
**Where**: `src/modules/answering/verifier.py`
**Depends on**: T28
**Requirement**: ANS-01, ANS-02
**Done when**: missing, fabricated, mismatched-version and unsupported citations fail; supported claims pass.
**Tests**: unit tests mapped to every citation criterion.
**Gate**: Quick.
**Commit**: `feat(answering): verify claim-level citations`

**Evidence**: `make check` passed: 154 unit tests, zero failures. Public seam:
`CitationVerifier.verify(GroundedAnswer, EvidenceSet)`. Exact supported resolution,
verified status, resolved citation and retained usage are asserted at
`test_verifier.py:64-70`; missing citation at `:79-80`; fabricated evidence ID at
`:95-96`; mismatched version at `:111-112`; mismatched span at `:127-128`; and
unsupported claim at `:143-144`. Empty evidence remains unverified at `:159-161`.
Adequacy A-D: PASS under `CONSTRAINTS.md`; every citation criterion has a dedicated
value assertion, failures are typed, and no mock, shallow assertion, skip or
suppression substitutes for verification.

### T30: Implement evidence-based abstention [x]

**What**: Abstain on insufficient, unauthorized or irreconcilably conflicting evidence.
**Where**: `src/modules/answering/abstention.py`
**Depends on**: T29
**Requirement**: ANS-02
**Done when**: each abstention reason is stable, user-facing and measured separately from provider failures.
**Tests**: unit and answer-eval tests.
**Gate**: Release.
**Commit**: `feat(answering): add calibrated abstention`

**Evidence**: `make release-check BASE=origin/main GITLEAKS=<approved-path>`
passed: 219 tests in coverage, 60 release-marker tests, 90.02% total coverage,
92% diff coverage, zero leaks and a clean floor guard. Public seams:
`AbstentionDecider`, `AbstentionDecision` and `measure_outcomes`. Insufficient
reason/message and abstained answer payload are asserted at
`test_abstention.py:67-78`; unauthorized at `:87-91`; conflict at `:100-104`;
verification failure and sufficient evidence at `:119-125`. The answer eval proves
separate counters for all three reasons, provider failure and success at
`test_answer_abstention.py:41-45`, and rejects conflation at `:50-57`. Adequacy
A-D: PASS under `CONSTRAINTS.md`; all reasons are stable and user-facing, every
test maps to ANS-02, and no failure is hidden by skip or suppression.

### T31: Expose the streaming Ask route [x]

**What**: Add authenticated streaming with final verification status, sources and degraded-mode metadata.
**Where**: `src/interfaces/http/ask.py`
**Depends on**: T30
**Requirement**: ANS-01, ANS-02, OPS-01
**Done when**: supported, abstained, disconnected-client, timeout and BM25-only degraded paths pass.
**Tests**: API/E2E streaming tests.
**Gate**: Full.
**Commit**: `feat(api): expose verified streaming ask`

**Evidence**: `make test` passed in the final T31 state: 229 tests, zero failures,
89.62% total coverage; the coverage run completed in 75.24 s. Public seams:
`POST /v1/ask`, `AskService.start -> AskSession` and
`OpenAIGenerationProvider.stream`. The API proves unverified delta followed by a
verified terminal answer with exact citations, sources, usage and auth context at
`test_ask.py:144-180`; abstention without generation at `:193-201`; typed timeout
outside abstention at `:215-222`; explicit BM25-only degradation at `:242-246`;
401/403/422 paths at `:262-266`; and ASGI disconnect cancellation at `:342-344`.
The local SSE contract proves `stream=true`, `store=false`, JSON Schema, parsed
terminal claims/usage at `test_openai_generation_stream_contract.py:212-220`,
plus redacted `incomplete`/`error` classification at `:245-257`. Adequacy A-D:
PASS under `CONSTRAINTS.md`; every route/failure criterion has observable payload
evidence and generation cannot emit verified status before deterministic verification.

### T32: Establish the answer benchmark [x]

**What**: Compare GPT-5 nano, GPT-4o Mini and GPT-5.6 Luna on citation coverage, citation validity, abstention, task success, latency and cost; run Terra only as an upper bound on the difficult subset.
**Where**: `evals/answering/benchmark.py`
**Depends on**: T31
**Requirement**: ANS-01, ANS-02
**Done when**: deterministic checks and pinned judge results produce a versioned per-model report with per-case evidence and identify the cheapest candidate that passes `CONSTRAINTS.md`.
**Tests**: evaluator metric and repeatability tests.
**Gate**: Release.
**Commit**: `test(answering): establish grounded answer benchmark`

**Evidence**: `make release-check` passed with 159 unit tests, 234 tests in the
coverage run, 75 release-marker tests, 89.68% total coverage, 91% diff coverage,
zero leaks and a clean floor guard. The live Responses API report covers four
versioned cases with deterministic `expected-facts-v1` judgments and exact
per-case citations. GPT-4o Mini qualified with 100% task success, 100% citation
coverage, zero invalid citations, 100% abstention accuracy, $0.0002979 total
measured cost and 2.508 s p95. It reduced cost by 45.76% versus the GPT-5.6 Luna
baseline while preserving quality. GPT-5 nano was rejected on cost, and Terra
remained an upper bound for the difficult subset. The evaluator and promotion
policy are exercised at `test_answer_benchmark.py:78-181`; a stratified manual
inspection confirmed grounded direct, abstention and difficult multi-source cases.

**Phase gate**: `make release-check`; manually inspect a stratified sample; publish the answer benchmark.

---

## Fase 5: Security and Multi-tenancy Hardening

**Outcome**: Authorization is enforced before retrieval and generation, with adversarial evidence of isolation.

### T33: Authenticate principals and tenant context [x]

**What**: Validate identity and construct immutable principal context for every protected route.
**Where**: `src/interfaces/http/auth.py`
**Depends on**: T32
**Requirement**: SEC-01
**Done when**: valid, expired, malformed, wrong-audience and missing credentials produce specified outcomes.
**Tests**: API authentication tests.
**Gate**: Full.
**Commit**: `feat(security): authenticate tenant principals`

**Evidence**: `make test` and `make pre-push` pass in the final T33 state. The
Bearer boundary validates signature, a configured algorithm allowlist, expiration,
issuer and audience before constructing the frozen `Principal`; identity, tenant,
roles and groups can no longer be supplied through trusted-looking request headers.
API tests prove valid immutable context plus missing, malformed, expired,
wrong-audience, wrong-issuer, wrong-signature, disallowed-algorithm and invalid-claim
outcomes in `test_auth.py`. Existing `Search` and `Ask` API suites now use signed
credentials, while `test_config.py` proves the application refuses to mount either
protected route without an authenticator. Adequacy A-D: PASS under
`CONSTRAINTS.md`; authentication fails closed with a generic 401 and Bearer
challenge without disclosing validation details.

### T34: Enforce document policies in storage and search [x]

**What**: Apply the same authorization decision to metadata reads, index writes, retrieval and citation resolution.
**Where**: `src/modules/policy/enforcement.py`
**Depends on**: T33
**Requirement**: SEC-01
**Done when**: cross-tenant IDs, forged filters, stale permissions and citation lookup cannot bypass policy.
**Tests**: unit, integration and adversarial E2E tests.
**Gate**: Release.
**Commit**: `feat(security): enforce document policy end to end`

**Evidence**: `make release-check` passed with 167 unit tests, 253 tests in the
coverage run, 90.03% total coverage, 94% diff coverage and 86 release tests;
gitleaks and floor guard were clean. `PolicyEnforcer` is the shared public seam for
authoritative metadata reads and index projection validation. Unit tests prove
cross-tenant references are excluded before the store query and document ACLs are
resolved in one batch (`test_policy_enforcement.py:82-85`), stale roles are denied
from current metadata (`:101-102`) and forged projections fail closed (`:122-123`).
The PostgreSQL/OpenSearch adversarial flow proves a permission revocation blocks
retrieval and metadata without reindexing (`test_policy_enforcement_e2e.py:159-160`),
invalidates an old citation (`:161-164`), rejects a forged index ACL (`:166-167`)
and discloses no foreign document through a forged filter or direct reference
(`:176-182`). Adequacy A-D: PASS; all four done-when attacks have outcome assertions
through public seams and run against real infrastructure where persistence matters.

### T35: Treat corpus content as untrusted [x]

**What**: Add prompt construction and tool policies that prevent document instructions from changing authority.
**Where**: `src/modules/security/untrusted_content.py`
**Depends on**: T34
**Requirement**: SEC-02
**Done when**: injection fixtures cannot reveal hidden context, alter tenant scope or invoke unauthorized tools.
**Tests**: adversarial prompt-injection tests.
**Gate**: Release.
**Commit**: `feat(security): isolate untrusted corpus instructions`

**Evidence**: `make release-check` passed with 167 unit tests, 256 tests in the
coverage run, 90.26% total coverage, 100% diff coverage and 89 release tests;
gitleaks and floor guard were clean. The prompt envelope keeps injected JSON fields
inside evidence data and outside trusted instructions
(`test_untrusted_content.py:105-111`). The deny-by-default tool policy rejects both an unlisted tool and a
cross-tenant request while preserving the configured tenant-local grant (`:123-128`).
The adversarial Ask flow proves an injected hidden-context marker never reaches a
delta or final response, ends in abstention, and leaves the principal tenant and
authorized evidence unchanged (`:166-170`). Local Responses API contracts prove
both generation modes send no tools, `tool_choice=none` and disable parallel tool
calls (`test_openai_generation_contract.py:167-171` and
`test_openai_generation_stream_contract.py:214-218`). Adequacy A-D: PASS; each
done-when attack has an exact outcome assertion and the legitimate stream contract
still passes.

### T36: Add redacted audit events [x]

**What**: Record authorization, ingestion, index promotion and investigation decisions without storing document content or secrets.
**Where**: `src/modules/audit/recorder.py`
**Depends on**: T35
**Requirement**: SEC-01, SEC-02, OPS-01
**Done when**: required events are queryable; redaction tests prove sensitive fixtures never appear.
**Tests**: unit and integration audit tests.
**Gate**: Full.
**Commit**: `feat(audit): record redacted security events`

**Evidence**: `make pre-push` passed with 170 unit tests, 260 tests in the coverage
run, 90.48% total coverage and 96% diff coverage; gitleaks and floor guard were
clean. The recorder exposes four typed decisions and no
free-form payload: authorization, ingestion, index promotion and investigation
(`test_audit_recorder.py:69-98`). HMAC references keep tenant, actor, resource and
correlation identifiers queryable from their original values without persisting
them in clear text (`:100-103`, `:134-136`). PostgreSQL integration proves all four
categories survive migration and remain filterable while the raw database row
contains none of the sensitive fixture values (`test_audit_store.py:77-87`). The
existing public seams now emit denied cross-tenant authorization events
(`test_policy_enforcement.py:100-105`) and ingestion/promotion lifecycle events
(`test_ingestion_pipeline.py:212-223`). Adequacy A-D: PASS under `CONSTRAINTS.md`;
every done-when outcome is asserted through returned state or persisted state, and
the empty-key test prevents silently weakening pseudonymization.

### T37: Add quotas and rate limits [x]

**What**: Enforce per-principal and per-tenant limits for queries, ingestion and investigations.
**Where**: `src/modules/policy/quotas.py`
**Depends on**: T36
**Requirement**: SEC-01, OPS-01
**Done when**: concurrency, burst, reset and backend-failure behavior are deterministic and observable.
**Tests**: unit, integration and API tests.
**Gate**: Full.
**Commit**: `feat(policy): enforce tenant quotas`

**Evidence**: `make pre-push` passed with 175 unit tests, 268 tests in the
coverage run and 86% diff coverage after the quota files were staged; gitleaks
and floor guard were clean. The policy enforces fixed-window burst limits,
tenant-wide sharing, concurrency release and deterministic reset outcomes
(`test_quotas.py:43-65`, `:76-79`). Backend failure returns a generic
`backend_unavailable` decision without provider details (`:93-95`), while the
HTTP boundary maps exhaustion to 429 plus `Retry-After` and backend failure to
503 (`test_quota_routes.py:57-75`). PostgreSQL row locks and the migration are
covered by a real integration flow for concurrent denial, release and shared
tenant exhaustion (`test_quota_backend.py:43-45`). Adequacy A-D: PASS under
`CONSTRAINTS.md`; fixed-window is an explicit pilot trade-off and the backend
interface leaves room for Redis without changing policy callers.

**Phase gate**: `make release-check` passed with 93 API, integration, evaluation
and security tests; the 268-test pre-push suite, gitleaks and floor guard also
remained green.

**Phase gate**: passed; zero cross-tenant disclosure in the adversarial suite and threat assumptions reviewed before push.

---

## Fase 6: Bounded Agentic Investigation

**Outcome**: Complex investigations run asynchronously with explicit budgets and only become default when they beat standard RAG.

### T38: Implement query routing [x]

**What**: Route questions to `Ask` or `Investigate` with an auditable reason and deterministic fallback.
**Where**: `src/modules/routing/query_router.py`
**Depends on**: T37
**Requirement**: AGT-01
**Done when**: simple, multi-hop, ambiguous, malicious and router-failure cases select the specified path.
**Tests**: unit tests and routing golden set.
**Gate**: Quick.
**Commit**: `feat(routing): classify ask and investigate requests`

**Evidence**: `make check` passed with 180 unit tests; formatting, Ruff and strict
Mypy were clean. The router uses explicit, reviewable heuristics and records the
selected path, reason and fallback flag (`src/modules/routing/query_router.py:12-111`).
The golden cases prove simple questions stay on `Ask`, clear multi-hop questions
select `Investigate`, and ambiguous, malicious or classifier-failure cases fail
closed to `Ask` without exposing private error details
(`tests/unit/modules/test_query_router.py:16-68`). Adequacy A-D: PASS under
`CONSTRAINTS.md`; no LLM is needed for this baseline, keeping routing deterministic
and cheap while the later agent workflow remains bounded.

### T39: Expose bounded investigation tools [x]

**What**: Wrap retrieval, version comparison and incident search as policy-aware typed tools.
**Where**: `src/modules/investigation/tools.py`
**Depends on**: T38
**Requirement**: AGT-01, SEC-01
**Done when**: schemas, authorization, timeout, result bounds and tool errors are enforced for every tool.
**Tests**: unit and contract tests.
**Gate**: Full.
**Commit**: `feat(agent): add policy-aware investigation tools`

**Evidence**: `make test` passed with 189 unit tests, 283 tests in the full
coverage run and 89.85% total coverage. The three typed tools centralize policy
checks, hard result limits, a shared timeout budget and redacted error categories
(`src/modules/investigation/tools.py:26-337`). Retrieval is capped, version
comparison reauthorizes both versions and incident search filters every returned
document by the current policy (`tests/unit/modules/test_investigation_tools.py:151-285`).
The public contract also proves all tools remain tenant-scoped and use the same
deadline (`tests/integration/test_investigation_tools_contract.py:77-112`).
Adequacy A-D: PASS under `CONSTRAINTS.md`; the synchronous thread deadline is a
portable baseline, while adapters can later replace it with native client timeouts
without changing graph-facing tool contracts. The pre-push gate also passed with
91% diff coverage; gitleaks and floor guard were clean.

### T40: Implement the bounded investigation graph [x]

**What**: Add plan, retrieve, compare, verify and report nodes with durable state and stopping rules.
**Where**: `src/modules/investigation/workflow.py`
**Depends on**: T39
**Requirement**: AGT-01
**Done when**: success, insufficient evidence, max steps, timeout, token budget, tool failure, cancellation and resume paths pass.
**Tests**: graph transition unit tests and agentic integration tests.
**Gate**: Full.
**Commit**: `feat(agent): orchestrate bounded investigations`

**Evidence**: `make test` passed with 199 unit tests, 294 tests in the full
coverage run and 89.43% total coverage. The state machine persists one revision
after each `plan`, `retrieve`, `compare`, `verify` and `report` node, and the final
report carries evidence, tool actions, unresolved questions, tokens, cost, duration
and a typed stop reason (`test_investigation_workflow.py:184-216`). Dedicated
assertions cover insufficient evidence (`:219-238`), step, duration and token
budgets (`:241-277`), tool and retrieval budgets (`:280-307`), redacted tool
failure (`:310-324`), cancellation (`:327-336`) and resume (`:339-353`). A real
PostgreSQL checkpoint resumes in a new transaction and preserves owner, revision
and terminal state (`test_investigation_persistence.py:65-103`). Adequacy A-D:
PASS under `CONSTRAINTS.md`; every done-when path has an exact state/stop-reason
assertion, no test is shallow, and every test maps to AGT-01 or the task's durable
resume requirement. The pre-push gate also passed with 91% diff coverage;
gitleaks and floor guard were clean.

### T41: Expose asynchronous investigation routes [x]

**What**: Add create, status, cancel and report endpoints without exposing internal chain-of-thought.
**Where**: `src/interfaces/http/investigations.py`
**Depends on**: T40
**Requirement**: AGT-01
**Done when**: lifecycle, ownership, idempotency, cancellation and terminal-state routes pass.
**Tests**: API/E2E lifecycle tests.
**Gate**: Full.
**Commit**: `feat(api): expose asynchronous investigations`

**Evidence**: `make test` passed with 199 unit tests, 300 tests in the full
coverage run and 89.89% total coverage. Creation persists a bounded job and only
enqueues its opaque id; status, cancellation and report reads never execute graph
work inside the request (`src/interfaces/http/investigations.py:72-135`). The API
exposes progress and final evidence but omits the question, plan and internal graph
node (`:182-341`). Lifecycle tests prove asynchronous create/status/report behavior
(`tests/api/test_investigations.py:100-145`), durable idempotent identity and payload
conflict (`:148-167`), indistinguishable cross-principal and cross-tenant misses
(`:170-190`), cancellation semantics (`:193-222`) and redacted queue failure
(`:225-251`). Adequacy A-D: PASS under `CONSTRAINTS.md`; every done-when path has
an exact HTTP status/body assertion, ownership covers every resource route, and no
test relies on chain-of-thought or internal planner state.
The pre-push gate also passed with 98% diff coverage; gitleaks and floor guard
were clean.

### T42: Benchmark agentic value [x]

**What**: Compare `Investigate` with `Ask` on frozen multi-hop tasks, cost, latency and failure rate.
**Where**: `evals/agentic/benchmark.py`
**Depends on**: T41
**Requirement**: AGT-02
**Done when**: report calculates success delta, cost ratio, p95 ratio and failure categories against promotion thresholds.
**Tests**: evaluator correctness and repeatability tests.
**Gate**: Release.
**Commit**: `test(agent): compare investigation against rag baseline`

**Evidence**: `make release-check` passed with 199 unit tests, 305 tests in the
coverage run, 89.85% total coverage and 106 release-selected API, integration,
eval, agentic and security tests. The evaluator calculates absolute and relative
success deltas, mean-cost ratio, p95 ratio and per-path failure categories, then
applies the approved 10% relative success and 2.5x cost thresholds
(`evals/agentic/benchmark.py:88-179`). Exact assertions cover every metric and a
qualifying candidate (`tests/agentic/test_benchmark.py:66-76`), each independent
threshold rejection (`:79-120`), repeatable report generation and the required
timeout, tool-failure and insufficient-evidence categories (`:123-144`), plus
invalid measurement sets (`:147-175`). The frozen v1 replay measured Ask at 50%
success and Investigate at 62.5%: +12.5 percentage points, +25% relative, 2.7x
cost and 3.94x p95. Investigate is not qualified because cost exceeds 2.5x.
Adequacy A-D: PASS under `CONSTRAINTS.md`; the committed report equals a fresh run,
all tests map to AGT-02/T42, and limitations explicitly prevent synthetic replay
latency from being presented as production traffic. Gitleaks and floor guard were
clean.

### T43: Decide agent production routing [x]

**What**: Record whether the agent qualifies, plus its enabled task classes and budgets.
**Where**: `docs/decisions/ADR-002-agent-routing.md`
**Depends on**: T42
**Requirement**: AGT-02
**Done when**: ADR links benchmark evidence and configuration tests enforce the decision.
**Tests**: routing configuration regression tests.
**Gate**: Release.
**Commit**: `docs(architecture): decide agent production routing`

**Evidence**: `make release-check` passed with 203 unit tests, 309 tests in the
coverage run, 89.70% total coverage and 106 release-selected tests. ADR-002 links
the immutable benchmark, explains the 2.7x cost rejection and records the five
bounded budgets (`docs/decisions/ADR-002-agent-routing.md:7-47`). The loader checks
config version, task classes, positive budgets, benchmark hash and thresholds
(`src/modules/routing/config.py:26-78`). Configuration tests prove the disabled
production default, every budget and threshold, Ask fallback, tamper detection and
the invariant that a disabled agent cannot enable a task class
(`tests/unit/modules/test_routing_config.py:15-66`). Query-router tests preserve
explicit opt-in for multi-hop and verify default fallback to Ask
(`tests/unit/modules/test_query_router.py:23-42`). Adequacy A-D: PASS under
`CONSTRAINTS.md`; the ADR and executable config express the same decision, and no
code-only toggle can silently promote the agent. Gitleaks and floor guard were
clean. The final pre-push run confirmed 86% diff coverage; no secrets were found
and the floor guard was clean.

**Phase gate**: `make release-check`; agent remains opt-in unless every promotion criterion passes.

---

## Fase 7: Observability, Performance and Resilience

**Outcome**: Production behavior is measurable, bounded and diagnosable under dependency and traffic failures.

### T44: Instrument correlated telemetry [x]

**What**: Emit structured logs, metrics and traces across API, worker, retrieval, generation and agent flows.
**Where**: `src/observability/telemetry.py`
**Depends on**: T43
**Requirement**: OPS-01
**Done when**: correlation propagates end to end; redaction tests find no raw corpus, token or secret content.
**Tests**: telemetry unit and integration tests.
**Gate**: Full.
**Commit**: `feat(observability): instrument end-to-end telemetry`

**Evidence**: `make test` passed with 315 tests, zero failures and 90.13% total
coverage. `tests/unit/observability/test_telemetry.py` proves allowlisted JSON
events, bounded metric labels, normalized external correlation values and absence
of corpus text, tokens, secrets and exception messages. The integration suite in
`tests/integration/test_telemetry_propagation.py` proves one W3C trace and
correlation ID across API, worker, retrieval, generation and agent spans, plus
HTTP error classification. `Telemetry` keeps logs, metrics and traces on the same
operation envelope while preserving the existing module boundaries. Adequacy A-D:
PASS; assertions inspect emitted public signals and redaction behavior, not only
collaborator calls. The full pre-push result is recorded in the commit handoff.

### T45: Implement safe degraded modes [x]

**What**: Add timeouts, circuit breakers and documented fallbacks for external dependencies.
**Where**: `src/modules/resilience/policies.py`
**Depends on**: T44
**Requirement**: OPS-01
**Done when**: embedding, generation, object store and telemetry failures match the design without retry storms.
**Tests**: unit and failure-injection integration tests.
**Gate**: Full.
**Commit**: `feat(resilience): enforce dependency failure policies`

**Evidence**: `make pre-push` passed with 329 tests, zero failures and 90.50% total
coverage. Circuit-breaker unit tests prove bounded failure thresholds, fail-fast
open state, one half-open probe, recovery and rejected timeout/attempt settings
(`tests/unit/modules/test_resilience_policies.py`). Failure-injection integration
tests prove embedding falls back to marked BM25 with one provider call, generation
returns authorized evidence without synthesis and stops calling an open provider,
object storage returns a redacted error without transport retry multiplication,
and telemetry failure preserves both business success and the original business
error (`tests/integration/test_resilience_failures.py`). Real MinIO and ingestion
tests pass with one S3 transport attempt, leaving retries to the durable job.
Adequacy A-D: PASS under `CONSTRAINTS.md`; every failure maps to OPS-01/T45,
assertions inspect returned state and payloads, and no test was skipped or weakened.

### T46: Add version-safe caching [x]

**What**: Cache only eligible retrieval and answer results using tenant, policy, corpus and model versions in the key.
**Where**: `src/modules/cache/cache_policy.py`
**Depends on**: T45
**Requirement**: OPS-01
**Done when**: invalidation, permission changes, document updates, model changes and cache failure cannot serve stale or unauthorized data.
**Tests**: unit, integration and adversarial cache tests.
**Gate**: Full.
**Commit**: `feat(cache): add version-safe response caching`

**Evidence**: `make pre-push` passed with 340 tests, zero failures and 90.66% total
coverage. Unit tests prove opaque deterministic keys change across tenant,
principal scope, policy, corpus, model and request boundaries, while only
authorized non-degraded retrieval and verified answers are eligible. Integration
tests prove valid hits avoid recomputation, version changes force fresh work,
answer hits rebind the request identity and backend failures fail open as misses.
The adversarial test replays Alice's record for Bob and proves it is rejected.
The final full suite took 87.96 seconds and diff coverage reached 98%. Adequacy
A-D: PASS under `CONSTRAINTS.md`; no test or assertion was removed or weakened.

### T47: Establish load and capacity benchmarks [x]

**What**: Measure throughput, p50/p95/p99, saturation and error behavior for `Search`, `Ask` and ingestion.
**Where**: `tests/operational/load/benchmark.py`
**Depends on**: T46
**Requirement**: OPS-01
**Done when**: a reproducible report states hardware, dataset, concurrency, warmup, limits and first bottleneck.
**Tests**: load test self-checks and result schema validation.
**Gate**: Operational.
**Commit**: `test(performance): establish capacity baseline`

**Evidence**: the versioned local report records hardware, dataset hash, 100
warmups, 5,000 measured requests per concurrency level, timeout, p50/p95/p99,
throughput, errors and saturation for Search, Ask and ingestion. All 60,000 measured
operations completed without errors. The first local plateau was Ask at concurrency
2; the report explicitly excludes network and managed dependencies, so this is an
application-orchestration baseline rather than a production SLO claim. Five
self-checks validate counters, error redaction, plateau detection and the committed
report schema. `make operational-test` passed with 5 selected tests and `make
pre-push` passed with 345 tests, zero failures and 90.66% total coverage. Adequacy
A-D: PASS under `CONSTRAINTS.md`; no check was skipped or weakened.

### T48: Define SLO dashboards and alerts [x]

**What**: Provision dashboards and actionable alerts for availability, latency, errors, cost, retrieval, abstention and ingestion freshness.
**Where**: `ops/observability/slo.yaml`
**Depends on**: T47
**Requirement**: OPS-01
**Done when**: synthetic failure tests trigger every paging alert and dashboard queries resolve their stated metrics.
**Tests**: observability configuration and alert simulation tests.
**Gate**: Operational.
**Commit**: `feat(observability): define slos and alerts`

**Evidence**: `ops/observability/slo.yaml` defines five SLOs, seven operational
dashboard questions and seven symptom-based alerts across availability, latency,
errors, cost, retrieval, abstention and ingestion freshness. Five paging alerts
have thresholds, sustained durations, synthetic healthy/failure samples and
runbook sections; all five remained quiet for healthy samples and fired for their
failure sample. Query validation rejects metrics outside the catalog and the
versioned simulation report is bound to the SLO config and T47 load profile by
SHA-256. The catalog distinguishes two metrics emitted today from five required
before Pilot, preventing unavailable telemetry from being presented as live.
`make operational-test` passed with 10 selected tests and the five degraded-mode
integration tests passed separately. `make pre-push` passed with 350 tests, zero
failures and 90.66% total coverage. Adequacy A-D: PASS under `CONSTRAINTS.md`;
assertions cover query resolution, paging behavior, actionability, runbooks and
immutable evidence without skipped or weakened tests.

**Phase gate**: `make operational-test`; publish load profile, degraded-mode evidence and alert simulation results.

---

## Fase 8: Deployment, Recovery and Pilot Readiness

**Outcome**: A release can be built, deployed, rolled back, restored and demonstrated with production evidence.

### T49: Build the continuous-integration pipeline [x]

**What**: Run `make release-check` with service dependencies, immutable reports and branch protection status.
**Where**: `.github/workflows/ci.yml`
**Depends on**: T48
**Requirement**: REL-01
**Done when**: intentional failures in lint, types, tests, evals, migrations, secrets and security scans each block CI.
**Tests**: CI discrimination matrix.
**Gate**: Release.
**Commit**: `ci(github): enforce release quality gates`

**Evidence**: `.github/workflows/ci.yml` runs the canonical release gate against
ephemeral PostgreSQL, OpenSearch and MinIO definitions, then runs pinned Gitleaks,
Semgrep and OSV Scanner versions in a separate least-privilege job. All actions are
pinned to full commit SHAs and `release / required` fails unless both upstream jobs
succeed. The checked-in branch-protection contract names that single stable status
and explicitly records that repository-side enforcement is still pending. Release
reports are uploaded with the commit SHA in the immutable artifact name and a
manifest binds seven reports to their SHA-256 hashes. The 16-test discrimination
suite makes each of lint, types, tests, evals, migrations, secrets and security fail
the aggregate independently, detects modified evidence and enforces portable report
bytes plus an OSV-compatible toolchain. The first remote run exposed CRLF-dependent
report hashing and Go 1.25 incompatibility with OSV Scanner 2.6.0. The second run
confirmed the security correction and exposed the same CRLF dependency in the
retrieval and SLO evidence links. Every versioned report writer now emits LF, text
evidence hashes normalize platform line endings, and the committed references bind
to the LF bytes stored by Git. Regression tests cover all seven reports and both
hash-validation paths. The corrected local `make release-check` passed with 368
tests, 90.67% total coverage, zero secrets and 120 release-marker tests. Adequacy
A-D: PASS under `CONSTRAINTS.md`; no gate is non-blocking and no scanner rule was
suppressed. At this point, the final remote rerun was still pending.
The user confirmed that the rerun for commit `80d41be` passed on GitHub, closing
the cross-platform validation loop. Repository-side branch protection remains
pending and must separately require `release / required` before merges to `main`.

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
