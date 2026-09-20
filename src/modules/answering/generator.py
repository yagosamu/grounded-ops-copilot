"""Generate structured grounded-answer drafts behind a provider boundary."""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from time import sleep
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
from openai.types.responses.response import Response
from pydantic import BaseModel, ConfigDict, ValidationError

from domain.answering import AnswerUsage, Citation, Claim, GroundedAnswer, Question
from domain.answering import VerificationStatus as AnswerStatus
from modules.answering.context_packer import PackedContext
from modules.security.untrusted_content import (
    GenerationPrompt,
    build_generation_prompt,
)


@dataclass(frozen=True)
class ProposedCitation:
    evidence_id: str
    document_version_id: str
    span: tuple[int, int]


@dataclass(frozen=True)
class ProposedClaim:
    text: str
    citations: tuple[ProposedCitation, ...]


@dataclass(frozen=True)
class GenerationRequest:
    question: Question
    context: PackedContext


@dataclass(frozen=True)
class GenerationResponse:
    claims: tuple[ProposedClaim, ...]
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GenerationDelta:
    text: str


@dataclass(frozen=True)
class GenerationCompleted:
    response: GenerationResponse


GenerationStreamEvent = GenerationDelta | GenerationCompleted


class GenerationFailure(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "provider_unavailable"
    INCOMPLETE = "incomplete"
    MALFORMED_OUTPUT = "malformed_output"


class GenerationProviderError(RuntimeError):
    """A classified, content-free generation failure."""

    def __init__(self, failure: GenerationFailure, *, retryable: bool) -> None:
        super().__init__(f"generation {failure.value}")
        self.failure = failure
        self.retryable = retryable


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResponse: ...


class StreamingGenerationProvider(Protocol):
    def stream(self, request: GenerationRequest) -> Iterator[GenerationStreamEvent]: ...


class _CitationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    document_version_id: str
    span: list[int]


class _ClaimPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    citations: list[_CitationPayload]


class _AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[_ClaimPayload]


class OpenAIGenerationProvider:
    """Responses API adapter with Structured Outputs and application-owned retries."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        max_output_tokens: int = 1200,
    ) -> None:
        if not model.strip() or timeout_seconds <= 0 or max_output_tokens <= 0:
            raise ValueError("invalid generation configuration")
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._model = model
        self._max_output_tokens = max_output_tokens

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        prompt = _prompt(request)
        try:
            result = self._client.responses.parse(
                model=self._model,
                instructions=prompt.instructions,
                input=[
                    {
                        "role": "user",
                        "content": prompt.input_text,
                    }
                ],
                tools=list(prompt.tools),
                tool_choice=prompt.tool_choice,
                parallel_tool_calls=prompt.parallel_tool_calls,
                text_format=_AnswerPayload,
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
        except APITimeoutError as error:
            raise GenerationProviderError(
                GenerationFailure.TIMEOUT, retryable=True
            ) from error
        except (RateLimitError, APIConnectionError) as error:
            raise GenerationProviderError(
                GenerationFailure.UNAVAILABLE, retryable=True
            ) from error
        except APIStatusError as error:
            raise GenerationProviderError(
                GenerationFailure.UNAVAILABLE, retryable=error.status_code >= 500
            ) from error
        except ValidationError as error:
            raise GenerationProviderError(
                GenerationFailure.MALFORMED_OUTPUT, retryable=False
            ) from error
        return _parse_response(result)

    def stream(self, request: GenerationRequest) -> Iterator[GenerationStreamEvent]:
        prompt = _prompt(request)
        try:
            with self._client.responses.stream(
                model=self._model,
                instructions=prompt.instructions,
                input=[{"role": "user", "content": prompt.input_text}],
                tools=list(prompt.tools),
                tool_choice=prompt.tool_choice,
                parallel_tool_calls=prompt.parallel_tool_calls,
                text_format=_AnswerPayload,
                max_output_tokens=self._max_output_tokens,
                store=False,
            ) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield GenerationDelta(event.delta)
                    elif event.type == "response.incomplete":
                        raise GenerationProviderError(
                            GenerationFailure.INCOMPLETE, retryable=False
                        )
                    elif event.type in {"response.failed", "error"}:
                        raise GenerationProviderError(
                            GenerationFailure.UNAVAILABLE, retryable=True
                        )
                result = stream.get_final_response()
        except GenerationProviderError:
            raise
        except APITimeoutError as error:
            raise GenerationProviderError(
                GenerationFailure.TIMEOUT, retryable=True
            ) from error
        except (RateLimitError, APIConnectionError) as error:
            raise GenerationProviderError(
                GenerationFailure.UNAVAILABLE, retryable=True
            ) from error
        except APIStatusError as error:
            raise GenerationProviderError(
                GenerationFailure.UNAVAILABLE, retryable=error.status_code >= 500
            ) from error
        except ValidationError as error:
            raise GenerationProviderError(
                GenerationFailure.MALFORMED_OUTPUT, retryable=False
            ) from error
        except RuntimeError as error:
            raise GenerationProviderError(
                GenerationFailure.INCOMPLETE, retryable=False
            ) from error
        yield GenerationCompleted(_parse_response(result))


class AnswerGenerator:
    def __init__(
        self,
        provider: GenerationProvider,
        *,
        max_attempts: int = 2,
        retry_delays: tuple[float, ...] = (0.05,),
    ) -> None:
        if max_attempts <= 0 or len(retry_delays) < max_attempts - 1:
            raise ValueError("invalid generation retry policy")
        if any(delay < 0 for delay in retry_delays):
            raise ValueError("invalid generation retry policy")
        self._provider = provider
        self._max_attempts = max_attempts
        self._retry_delays = retry_delays

    def generate(self, question: Question, context: PackedContext) -> GroundedAnswer:
        response = self._request(GenerationRequest(question, context))
        return build_grounded_draft(question, context, response)

    def _request(self, request: GenerationRequest) -> GenerationResponse:
        for attempt in range(self._max_attempts):
            try:
                return self._provider.generate(request)
            except GenerationProviderError as error:
                if not error.retryable or attempt + 1 == self._max_attempts:
                    raise GenerationProviderError(
                        error.failure, retryable=error.retryable
                    ) from error
                sleep(self._retry_delays[attempt])
        raise AssertionError("unreachable")


def _prompt(request: GenerationRequest) -> GenerationPrompt:
    return build_generation_prompt(
        request.question.text,
        request.context.evidence,
    )


def _span(values: list[int]) -> tuple[int, int]:
    if len(values) != 2:
        raise ValueError("invalid citation span")
    return values[0], values[1]


def _parse_response(result: Response) -> GenerationResponse:
    if result.error is not None:
        raise GenerationProviderError(GenerationFailure.UNAVAILABLE, retryable=True)
    if result.status != "completed":
        raise GenerationProviderError(GenerationFailure.INCOMPLETE, retryable=False)
    try:
        payload = _AnswerPayload.model_validate_json(result.output_text)
        if not payload.claims or result.usage is None:
            raise ValueError("incomplete structured response")
        claims = tuple(
            ProposedClaim(
                claim.text,
                tuple(
                    ProposedCitation(
                        citation.evidence_id,
                        citation.document_version_id,
                        _span(citation.span),
                    )
                    for citation in claim.citations
                ),
            )
            for claim in payload.claims
        )
    except (ValidationError, ValueError) as error:
        raise GenerationProviderError(
            GenerationFailure.MALFORMED_OUTPUT, retryable=False
        ) from error
    return GenerationResponse(
        claims,
        result.model,
        result.usage.input_tokens,
        result.usage.output_tokens,
    )


def build_grounded_draft(
    question: Question, context: PackedContext, response: GenerationResponse
) -> GroundedAnswer:
    """Build an unverified draft; only CitationVerifier may grant verified status."""
    evidence_identities = {
        (item.chunk_id, item.document_version_id, item.span)
        for item in context.evidence
    }
    try:
        claims = tuple(
            Claim(
                claim.text,
                tuple(
                    Citation(
                        citation.evidence_id,
                        citation.document_version_id,
                        citation.span,
                        resolved=False,
                    )
                    for citation in claim.citations
                ),
            )
            for claim in response.claims
        )
    except ValueError as error:
        raise GenerationProviderError(
            GenerationFailure.MALFORMED_OUTPUT, retryable=False
        ) from error
    if (
        not claims
        or any(not claim.citations for claim in claims)
        or any(
            (
                citation.evidence_id,
                citation.document_version_id,
                citation.span,
            )
            not in evidence_identities
            for claim in claims
            for citation in claim.citations
        )
    ):
        raise GenerationProviderError(
            GenerationFailure.MALFORMED_OUTPUT, retryable=False
        )
    return GroundedAnswer(
        question,
        claims,
        AnswerStatus.UNVERIFIED,
        AnswerUsage(response.model, response.input_tokens, response.output_tokens),
    )
