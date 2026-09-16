# Reference analysis: Production Agentic RAG Course

Analyzed on 2026-09-16 as input for defining an original AI Engineering portfolio project.

## What the reference project does well

- It teaches search before generation: infrastructure and ingestion come first, followed by BM25, hybrid retrieval, RAG, observability/caching, and finally an agentic workflow.
- It covers a broad production-oriented stack: FastAPI, PostgreSQL, OpenSearch, Airflow, Ollama, Redis, Langfuse, LangGraph, Docker Compose, Ruff, MyPy, and Pytest.
- It treats document processing, retrieval, generation, and operations as separate concerns instead of hiding everything behind a framework.
- It includes unit, API, and integration tests and presents incremental weekly releases.

Primary source: [project README](https://github.com/jamwithai/production-agentic-rag-course#readme).

## Gaps that create room for differentiation

These are observations from the repository, not claims that the course promises to cover them.

- There is no visible, versioned evaluation dataset or automated benchmark for retrieval quality and answer groundedness. The README mentions precision and recall conceptually, but the repository does not expose an eval harness with metrics such as Recall@k, MRR/nDCG, citation correctness, or abstention quality.
- Tests validate code behavior, but the repository does not contain a visible GitHub Actions workflow that runs quality gates on every change.
- The repository does not visibly demonstrate load testing, SLOs, authentication, authorization, tenant isolation, or document-level access control.
- Agentic behavior is implemented, but there is no visible baseline comparison showing when the agent improves quality enough to justify its additional latency, cost, and failure modes.
- The type-checker configuration sets `ignore_errors = true`, weakening the value of MyPy as a quality gate.
- The system is broad and educational, which makes it a strong syllabus but also means a portfolio derivative needs a distinct problem, dataset, product constraint, and evidence trail.

Primary sources: [repository tree and code](https://github.com/jamwithai/production-agentic-rag-course), [dependency and tooling configuration](https://github.com/jamwithai/production-agentic-rag-course/blob/main/pyproject.toml), and [agent workflow](https://github.com/jamwithai/production-agentic-rag-course/blob/main/src/services/agents/agentic_rag.py).

## Recommended portfolio thesis

Use the reference project's progression as a competency map, not as a blueprint to copy. Build an evidence-driven RAG product in which every major layer must beat a simpler baseline on a versioned evaluation set.

The portfolio should make five capabilities easy to verify:

1. Product judgment: a concrete user, costly failure mode, and measurable success criteria.
2. Retrieval engineering: lexical, dense, hybrid, filters, and reranking compared experimentally.
3. Reliable AI behavior: citations, abstention, structured outputs, bounded agent loops, and adversarial tests.
4. Production engineering: idempotent ingestion, observability, retries, caching, security, CI/CD, and load tests.
5. Technical communication: ADRs, experiment reports, diagrams, cost/latency/quality trade-offs, and a concise demo.

## Suggested sequence

1. Define the product problem, threat model, golden questions, and baseline metrics.
2. Build a thin end-to-end vertical slice with deterministic lexical retrieval.
3. Add robust, versioned, idempotent ingestion with provenance and failure recovery.
4. Run retrieval experiments: BM25, dense, hybrid fusion, reranking, and chunking variants.
5. Add grounded generation, sentence-level citations, abstention, and answer evaluations.
6. Add an agent only for tasks that require routing, decomposition, or multiple tools; compare it to the non-agentic pipeline.
7. Add production gates: tracing, dashboards, SLOs, auth/RBAC, prompt-injection defenses, CI, load tests, and deployment.
8. Package the evidence: architecture and ADRs, eval dashboard, live demo, short video, and an interview-ready README.

## A strong original concept

An engineering knowledge and incident copilot is a good fit: ingest versioned documentation, runbooks, ADRs, changelogs, issues, and postmortems; answer with exact citations; distinguish current from historical guidance; and use bounded tools to investigate an incident. This adds temporal retrieval, heterogeneous sources, access-control concerns, and actionable workflows while remaining demoable with public open-source data.

Two other viable domains are regulatory-change intelligence and technical-support triage. Whichever domain is chosen, the differentiator should be its evaluation and operational story rather than the number of frameworks used.
