"""The reranker adapter exchanges its complete bounded HTTP contract."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from modules.retrieval.reranker import HTTPRerankProvider, RerankRequest


@pytest.fixture
def rerank_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["content-length"])
            requests.append(json.loads(self.rfile.read(length)))
            response = json.dumps(
                {"results": [{"id": "b", "score": 0.9}, {"id": "a", "score": 0.2}]}
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
        yield f"http://127.0.0.1:{server.server_port}/rerank", requests
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.integration
def test_reranker_http_request_and_response_contract(
    rerank_server: tuple[str, list[dict[str, object]]],
) -> None:
    url, requests = rerank_server
    provider = HTTPRerankProvider(url)

    result = provider.rerank(
        RerankRequest("query", (("a", "first"), ("b", "second")), "reranker-v1", 400)
    )

    assert requests == [
        {
            "query": "query",
            "documents": [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}],
            "model": "reranker-v1",
        }
    ]
    assert result.scores == (("b", 0.9), ("a", 0.2))
    assert 0 <= result.latency_ms <= 400
