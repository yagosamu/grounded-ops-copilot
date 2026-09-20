"""The OpenAI adapter consumes the documented Responses SSE stream locally."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from domain.answering import Question
from modules.answering.context_packer import PackedContext
from modules.answering.generator import (
    GenerationCompleted,
    GenerationDelta,
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
    OpenAIGenerationProvider,
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


def response_payload(output: str) -> dict[str, object]:
    return {
        "id": "resp_stream",
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
                "id": "msg_stream",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output, "annotations": []}],
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


@pytest.fixture
def stream_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            payload = json.loads(self.rfile.read(length))
            requests.append(payload)
            output = json.dumps(
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
            created_response = response_payload("")
            created_response["status"] = "in_progress"
            created_response["usage"] = None
            created = {
                "type": "response.created",
                "response": created_response,
                "sequence_number": 0,
            }
            if payload["model"] == "gpt-incomplete":
                incomplete = response_payload("")
                incomplete["status"] = "incomplete"
                incomplete["incomplete_details"] = {"reason": "max_output_tokens"}
                events = [
                    created,
                    {
                        "type": "response.incomplete",
                        "response": incomplete,
                        "sequence_number": 1,
                    },
                ]
            elif payload["model"] == "gpt-error":
                events = [
                    created,
                    {
                        "type": "error",
                        "code": "server_error",
                        "message": "private upstream detail",
                        "param": None,
                        "sequence_number": 1,
                    },
                ]
            else:
                events = [
                    created,
                    {
                        "type": "response.output_text.delta",
                        "content_index": 0,
                        "delta": "TracerProvider provides ",
                        "item_id": "msg_stream",
                        "logprobs": [],
                        "output_index": 0,
                        "sequence_number": 1,
                    },
                    {
                        "type": "response.completed",
                        "response": response_payload(output),
                        "sequence_number": 2,
                    },
                ]
            body = (
                b"".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
                    for event in events
                )
                + b"data: [DONE]\n\n"
            )
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.integration
def test_openai_responses_stream_yields_delta_and_parsed_terminal_usage(
    stream_server: tuple[str, list[dict[str, object]]],
) -> None:
    base_url, requests = stream_server
    provider = OpenAIGenerationProvider(
        api_key="local-contract-key",
        model="gpt-5.6-luna",
        base_url=base_url,
        timeout_seconds=0.5,
        max_output_tokens=500,
    )

    stream = tuple(
        provider.stream(
            GenerationRequest(
                Question("question-1", "What provides access to tracers?"),
                packed_context(),
            )
        )
    )

    assert requests[0]["stream"] is True
    assert requests[0]["store"] is False
    assert requests[0]["tools"] == []
    assert requests[0]["tool_choice"] == "none"
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["text"]["format"]["type"] == "json_schema"
    assert stream[0] == GenerationDelta("TracerProvider provides ")
    assert isinstance(stream[1], GenerationCompleted)
    assert stream[1].response.claims[0].citations[0].evidence_id == "chunk-1"
    assert stream[1].response.model == "gpt-5.6-luna"
    assert stream[1].response.input_tokens == 12
    assert stream[1].response.output_tokens == 7


@pytest.mark.integration
@pytest.mark.parametrize(
    ("model", "failure", "retryable"),
    [
        ("gpt-incomplete", GenerationFailure.INCOMPLETE, False),
        ("gpt-error", GenerationFailure.UNAVAILABLE, True),
    ],
)
def test_openai_responses_stream_classifies_terminal_failure_events(
    stream_server: tuple[str, list[dict[str, object]]],
    model: str,
    failure: GenerationFailure,
    retryable: bool,
) -> None:
    base_url, _ = stream_server
    provider = OpenAIGenerationProvider(
        api_key="local-contract-key",
        model=model,
        base_url=base_url,
        timeout_seconds=0.5,
    )

    with pytest.raises(GenerationProviderError) as caught:
        tuple(
            provider.stream(
                GenerationRequest(
                    Question("question-1", "What provides access?"),
                    packed_context(),
                )
            )
        )

    assert caught.value.failure is failure
    assert caught.value.retryable is retryable
    assert "private upstream detail" not in str(caught.value)
