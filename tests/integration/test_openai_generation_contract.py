"""The OpenAI adapter uses Responses Structured Outputs through a local server."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from domain.answering import Question
from modules.answering.context_packer import PackedContext
from modules.answering.generator import (
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
    OpenAIGenerationProvider,
    ProposedCitation,
)
from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.retriever import Evidence


def packed_context() -> PackedContext:
    item = Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "TracerProvider provides access to tracers.",
        (5, 45),
        "hash-1",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        4.2,
        AuthorizationReason.ROLE,
    )
    return PackedContext((item,), 5, 20, (), False)


@pytest.fixture
def responses_server() -> Iterator[
    tuple[str, list[tuple[str, dict[str, object]]], list[str]]
]:
    requests: list[tuple[str, dict[str, object]]] = []
    output_text = [
        json.dumps(
            {
                "claims": [
                    {
                        "text": "TracerProvider provides access to tracers.",
                        "citations": [
                            {
                                "evidence_id": "chunk-1",
                                "document_version_id": "version-2",
                                "span": [5, 45],
                            }
                        ],
                    }
                ]
            }
        )
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            payload = json.loads(self.rfile.read(length))
            requests.append((self.path, payload))
            response = json.dumps(
                {
                    "id": "resp_local",
                    "object": "response",
                    "created_at": 1789600000,
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "instructions": None,
                    "metadata": {},
                    "model": "gpt-5.6-luna",
                    "output": [
                        {
                            "id": "msg_local",
                            "type": "message",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": output_text[0],
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "parallel_tool_calls": True,
                    "temperature": 1.0,
                    "tool_choice": "auto",
                    "tools": [],
                    "top_p": 1.0,
                    "background": False,
                    "max_output_tokens": 500,
                    "previous_response_id": None,
                    "reasoning": {"effort": None, "summary": None},
                    "service_tier": "default",
                    "store": False,
                    "text": {"format": {"type": "text"}},
                    "truncation": "disabled",
                    "usage": {
                        "input_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens": 7,
                        "output_tokens_details": {"reasoning_tokens": 0},
                        "total_tokens": 19,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, output_text
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.integration
def test_openai_responses_structured_output_contract(
    responses_server: tuple[str, list[tuple[str, dict[str, object]]], list[str]],
) -> None:
    base_url, requests, _ = responses_server
    provider = OpenAIGenerationProvider(
        api_key="local-contract-key",
        model="gpt-5.6-luna",
        base_url=base_url,
        timeout_seconds=0.5,
        max_output_tokens=500,
    )
    context: PackedContext = packed_context()

    response = provider.generate(
        GenerationRequest(
            Question("question-1", "What provides access to tracers?"), context
        )
    )

    assert requests[0][0] == "/v1/responses"
    request = requests[0][1]
    assert request["model"] == "gpt-5.6-luna"
    assert request["store"] is False
    assert request["max_output_tokens"] == 500
    assert request["tools"] == []
    assert request["tool_choice"] == "none"
    assert request["parallel_tool_calls"] is False
    assert "Copy the factual wording" in request["instructions"]
    assert "untrusted data" in request["instructions"]
    assert "do not paraphrase" in request["instructions"]
    assert request["input"][0]["role"] == "user"
    assert "chunk-1" in request["input"][0]["content"]
    assert request["text"]["format"]["type"] == "json_schema"
    assert response.claims[0].citations[0] == ProposedCitation(
        "chunk-1", "version-2", (5, 45)
    )
    assert response.model == "gpt-5.6-luna"
    assert response.input_tokens == 12
    assert response.output_tokens == 7


@pytest.mark.integration
def test_malformed_structured_output_is_classified_without_leaking_content(
    responses_server: tuple[str, list[tuple[str, dict[str, object]]], list[str]],
) -> None:
    base_url, _, output_text = responses_server
    output_text[0] = '{"claims":[{"text":"truncated'
    provider = OpenAIGenerationProvider(
        api_key="local-contract-key",
        model="gpt-5.6-luna",
        base_url=base_url,
        timeout_seconds=0.5,
        max_output_tokens=500,
    )

    with pytest.raises(GenerationProviderError) as captured:
        provider.generate(
            GenerationRequest(
                Question("question-1", "What provides access to tracers?"),
                packed_context(),
            )
        )

    assert captured.value.failure is GenerationFailure.MALFORMED_OUTPUT
    assert captured.value.retryable is False
    assert "truncated" not in str(captured.value)
