"""Classify questions without allowing routing complexity to leak into answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from domain.answering import Question


class Route(StrEnum):
    """Supported top-level workflows."""

    ASK = "ask"
    INVESTIGATE = "investigate"


class RouteReason(StrEnum):
    """Auditable reason for selecting a workflow."""

    SIMPLE = "simple"
    MULTI_HOP = "multi_hop"
    AMBIGUOUS = "ambiguous"
    MALICIOUS = "malicious"
    ROUTER_FAILURE = "router_failure"


class RoutingClassifierError(RuntimeError):
    """Raised when a classifier cannot safely produce a routing reason."""


@dataclass(frozen=True)
class RoutingDecision:
    """Safe, serializable result of routing a question."""

    route: Route
    reason: RouteReason
    fallback_used: bool


class RoutingClassifier(Protocol):
    """Classifier seam used by the router and its deterministic test double."""

    def classify(self, question: Question) -> RouteReason:
        """Return a reason without executing retrieval or generation."""


class HeuristicQueryClassifier:
    """Conservative classifier for the first routing baseline."""

    _MALICIOUS_MARKERS = (
        "ignore previous instructions",
        "ignore all previous instructions",
        "reveal the system prompt",
        "show the system prompt",
        "bypass authorization",
        "disable authorization",
        "act as system",
        "jailbreak",
    )
    _AMBIGUOUS_MARKERS = (
        "what about this",
        "what about that",
        "how do i fix it",
        "what happened",
    )
    _MULTI_HOP_MARKERS = (
        "compare",
        "between",
        "root cause",
        "timeline",
        "what caused",
        "which deploy",
        "and what incident",
        "related incidents",
    )

    def classify(self, question: Question) -> RouteReason:
        """Classify only from the question text using explicit, reviewable rules."""
        text = " ".join(question.text.casefold().split())

        if any(marker in text for marker in self._MALICIOUS_MARKERS):
            return RouteReason.MALICIOUS
        if any(marker in text for marker in self._AMBIGUOUS_MARKERS):
            return RouteReason.AMBIGUOUS
        if any(marker in text for marker in self._MULTI_HOP_MARKERS):
            return RouteReason.MULTI_HOP
        return RouteReason.SIMPLE


class QueryRouter:
    """Select a workflow and fail closed to the simpler Ask path."""

    def __init__(self, classifier: RoutingClassifier | None = None) -> None:
        self._classifier = classifier or HeuristicQueryClassifier()

    def route(self, question: Question) -> RoutingDecision:
        """Return an auditable decision without exposing classifier internals."""
        try:
            reason = self._classifier.classify(question)
        except RoutingClassifierError:
            return RoutingDecision(Route.ASK, RouteReason.ROUTER_FAILURE, True)

        if not isinstance(reason, RouteReason):
            return RoutingDecision(Route.ASK, RouteReason.ROUTER_FAILURE, True)
        if reason is RouteReason.MULTI_HOP:
            return RoutingDecision(Route.INVESTIGATE, reason, False)
        if reason in (RouteReason.AMBIGUOUS, RouteReason.MALICIOUS):
            return RoutingDecision(Route.ASK, reason, True)
        return RoutingDecision(Route.ASK, reason, False)
