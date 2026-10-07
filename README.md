# GroundedOps

GroundedOps is a production-oriented engineering knowledge and incident copilot.
It is designed to answer operational questions with versioned evidence and run
bounded multi-source investigations when a standard retrieval flow is not enough.

The project is also an evidence-driven AI engineering portfolio. No layer of RAG
or agentic complexity is promoted until it demonstrates measurable value over a
simpler baseline.

## Project status

The backend vertical slice and local evidence-first web preview are implemented.
AWS Pilot deployment remains a separate, time-boxed validation task.

The specification, architecture, task breakdown and quality contract are already
versioned so that implementation decisions remain traceable:

- [Product specification](.specs/features/production-rag-platform/spec.md)
- [Architecture design](.specs/features/production-rag-platform/design.md)
- [Development plan](.specs/features/production-rag-platform/tasks.md)
- [Quality contract](CONSTRAINTS.md)
- [Reference-project analysis](docs/reference-project-analysis.md)

## Product scope

GroundedOps will ingest:

- technical documentation and runbooks;
- architecture decision records and changelogs;
- issues and pull requests;
- incident postmortems;
- versioned API documentation.

It will support two intentionally separate paths:

- `Ask`: deterministic, latency-bounded grounded answers with exact citations;
- `Investigate`: asynchronous, budgeted multi-hop investigation with an auditable
  report.

## Engineering principles

- Authorization is applied before evidence reaches retrieval context or a model.
- PostgreSQL and object storage are durable sources of truth.
- OpenSearch is a versioned, rebuildable retrieval projection.
- Abstention is preferred when authorized evidence is insufficient.
- BM25 is the first measurable baseline.
- Embeddings, hybrid search, reranking, caching and agents require benchmark
  evidence before promotion.
- Cost, latency and answer quality are evaluated together.
- Every implementation task includes tests and an explicit quality gate.

## Stack

- Python 3.13, `uv`, FastAPI, Pydantic, SQLAlchemy and Alembic;
- PostgreSQL, OpenSearch and S3-compatible object storage;
- Celery and Redis for asynchronous work;
- OpenTelemetry and Langfuse for observability;
- Pytest, Testcontainers, Ruff and MyPy for verification;
- Next.js for the web client after the backend vertical slice;
- Docker Compose locally and AWS with Terraform for deployment.

## Local web preview

The web client is in [`web/`](web/). Configure `API_BASE_URL` in a local
`web/.env.local` (see `web/.env.example`), then run `npm ci --prefix web` and
`npm --prefix web run dev`. The local API must have a configured JWT issuer and
verification key; a valid API JWT is entered once in the session form and kept
in a server-set HttpOnly cookie. No token belongs in source control or browser
storage. The API validates authorization on every request.

The runtime currently exposes Ask and evidence search. New investigations remain
disabled until their benchmark gates and production composition are ready; the
UI can inspect status by ID on runtimes that expose the investigation API.
Feedback for verified answers is persisted without prompts, answer text or raw
principal identifiers. Run `make web-check` for types, unit tests and build;
`make web-e2e` adds browser, Axe and Lighthouse preview checks.

## Corpus

The initial corpus combines public OpenTelemetry documentation and GitHub
artifacts with clearly identified synthetic company runbooks, ADRs and
postmortems. This keeps the project reproducible while allowing controlled
authorization, version-conflict and incident scenarios.
