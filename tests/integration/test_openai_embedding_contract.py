"""The OpenAI adapter exchanges the documented embeddings HTTP contract."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from modules.embeddings.embedder import EmbeddingRequest, OpenAIEmbeddingProvider


@pytest.fixture
def embedding_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            payload = json.loads(self.rfile.read(length))
            requests.append(payload)
            response = json.dumps(
                {
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": 1, "embedding": [0.0, 1.0]},
                        {"object": "embedding", "index": 0, "embedding": [1.0, 0.0]},
                    ],
                    "model": "text-embedding-3-small",
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
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
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.integration
def test_openai_embedding_request_and_response_contract(
    embedding_server: tuple[str, list[dict[str, object]]],
) -> None:
    base_url, requests = embedding_server
    provider = OpenAIEmbeddingProvider(
        api_key="local-contract-key",
        base_url=base_url,
        timeout_seconds=0.5,
    )

    response = provider.embed(
        EmbeddingRequest(("alpha", "beta"), "text-embedding-3-small", 2)
    )

    assert requests == [
        {
            "input": ["alpha", "beta"],
            "model": "text-embedding-3-small",
            "dimensions": 2,
            "encoding_format": "float",
        }
    ]
    assert response.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert response.model == "text-embedding-3-small"
    assert response.usage_tokens == 4
