"""Apply retrieval promotion thresholds without changing production by default."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateMeasurement:
    strategy: str
    recall_at_10: float
    ndcg_at_10: float
    query_p95_ms: float


@dataclass(frozen=True)
class RetrievalThresholds:
    retrieval_ndcg_gain: float
    reranker_ndcg_gain: float
    reranker_p95_increase_ms: float


@dataclass(frozen=True)
class PromotionDecision:
    production: str
    fallback: str
    relative_ndcg_deltas: dict[str, float]
    rejections: dict[str, str]


def select_candidate(
    baseline: CandidateMeasurement,
    candidates: tuple[CandidateMeasurement, ...],
    thresholds: RetrievalThresholds,
) -> PromotionDecision:
    deltas = {
        candidate.strategy: _relative_delta(candidate.ndcg_at_10, baseline.ndcg_at_10)
        for candidate in candidates
    }
    rejections: dict[str, str] = {}
    qualified: list[CandidateMeasurement] = []
    for candidate in candidates:
        required = (
            thresholds.reranker_ndcg_gain
            if candidate.strategy == "reranker"
            else thresholds.retrieval_ndcg_gain
        )
        if candidate.recall_at_10 < baseline.recall_at_10:
            rejections[candidate.strategy] = "Recall@10 regression"
            continue
        if deltas[candidate.strategy] < required:
            rejections[candidate.strategy] = f"ndcg gain below {required:.2%}"
            continue
        if (
            candidate.strategy == "reranker"
            and candidate.query_p95_ms - baseline.query_p95_ms
            > thresholds.reranker_p95_increase_ms
        ):
            rejections[candidate.strategy] = "reranker p95 increase above 400 ms"
            continue
        qualified.append(candidate)
    production = (
        min(qualified, key=lambda item: item.query_p95_ms).strategy
        if qualified
        else baseline.strategy
    )
    return PromotionDecision(production, baseline.strategy, deltas, rejections)


def _relative_delta(candidate: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else float("inf")
    return (candidate - baseline) / baseline
