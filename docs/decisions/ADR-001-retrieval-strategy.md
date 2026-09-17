# ADR-001: Keep BM25 as the production retrieval strategy

- Status: Accepted
- Date: 2026-09-17
- Requirement: RET-02

## Decision

Production: `bm25` with `structural` chunking.

Fallback: `bm25`.

Dense, hybrid RRF and reranking remain explicit experimental candidates. They do
not qualify for production on dataset v1.

## Evidence

- [BM25 baseline](../../evals/retrieval/reports/bm25-v1.json): Recall@10 1.00,
  MRR 1.00, nDCG@10 1.00 and p95 26 ms.
- [Retrieval candidates](../../evals/retrieval/reports/candidates-v1.json): the
  same dataset hash, corpus, public policy and two golden queries are used for all
  candidates. Candidate latency is measured locally and is not comparable with the
  OpenSearch baseline.
- [Chunking candidates](../../evals/retrieval/reports/chunking-v1.json): fixed,
  structural and parent-child all score 1.00 on Recall@10, MRR, nDCG@10 and
  citation-span precision. Their measured index sizes are 636, 408 and 402 bytes.

The reports are immutable inputs referenced by SHA-256 in
[`config/retrieval.yaml`](../../config/retrieval.yaml).

## Promotion calculations

- Dense: 0.00% relative nDCG@10 delta, Recall@10 unchanged. Required: at least
  5.00% relative nDCG@10 gain with no Recall@10 regression. Rejected.
- Hybrid RRF: 0.00% relative nDCG@10 delta, Recall@10 unchanged. Required: at
  least 5.00% relative nDCG@10 gain with no Recall@10 regression. Rejected.
- Reranker: 0.00% relative nDCG@10 delta, Recall@10 unchanged. Required: at least
  3.00% relative nDCG@10 gain and p95 increase no greater than 400 ms. Rejected on
  quality before latency can qualify it.
- Fixed and parent-child chunking: 0.00% quality delta from structural. Rejected
  because the two-query dataset does not demonstrate a quality improvement.

The baseline is already at the metric ceiling on two queries. A 5% or 3% relative
gain is mathematically impossible on this dataset. This is a dataset limitation,
not evidence that semantic retrieval has no value. A larger discriminating dataset
is required before reconsidering promotion.

## Consequences

The evidence search route remains BM25 by default. Dense, RRF and reranking stay
available only as named candidates for future experiments. Embedding or reranker
failure cannot change production behavior. Structural chunking remains selected
because it is the existing provenance-preserving strategy and no alternative showed
a quality gain.
