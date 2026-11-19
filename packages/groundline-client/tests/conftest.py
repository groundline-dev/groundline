"""A fake groundline API on a local port, for the client's tests.

The fake answers each route with the example from openapi.json, records every
request it receives, and can be told to answer a route differently. No test
reaches the real API.
"""

import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SPEC = json.loads((Path(__file__).resolve().parents[1] / "openapi.json").read_text())
PROBE_REPLY = {"status": "ok"}
URL_VARIABLE = "GROUNDLINE_API_URL"
API_KEY_VARIABLE = "GROUNDLINE_API_KEY"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]  # header names in lower case
    body: Any  # the decoded JSON body, or None when there was none


class FakeServer:
    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        examples = {
            name: schema["examples"][0]
            for name, schema in SPEC["components"]["schemas"].items()
            if "examples" in schema
        }
        self._replies: dict[tuple[str, str], tuple[int, Any]] = {
            ("POST", "/v1/omissions"): (200, examples["OmissionsResponse"]),
            ("POST", "/v1/commissions"): (200, examples["CommissionsResponse"]),
            ("GET", "/health"): (200, PROBE_REPLY),
            ("GET", "/ready"): (200, PROBE_REPLY),
        }
        self.examples = examples
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def reply(self, method: str, path: str, status: int, body: Any) -> None:
        """Answer this route with this status and JSON body from now on."""
        self._replies[(method, path)] = (status, body)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                fake.requests.append(
                    RecordedRequest(
                        method=self.command,
                        path=self.path,
                        headers={k.lower(): v for k, v in self.headers.items()},
                        body=json.loads(raw) if raw else None,
                    )
                )
                status, body = fake._replies.get(
                    (self.command, self.path), (404, {"detail": "no such route"})
                )
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _answer  # noqa: N815
            do_POST = _answer  # noqa: N815

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler


@pytest.fixture
def fake_server() -> Iterator[FakeServer]:
    server = FakeServer()
    thread = threading.Thread(
        target=server._server.serve_forever,  # pyright: ignore[reportPrivateUsage]
        kwargs={"poll_interval": 0.01},  # shutdown waits up to one poll interval
        daemon=True,
    )
    thread.start()
    yield server
    server._server.shutdown()  # pyright: ignore[reportPrivateUsage]
    server._server.server_close()  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def api_key() -> str:
    """An obviously fake key, so a leak of it is easy to search for."""
    # betterleaks:allow a dummy value for the fake server
    return "test-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own settings must not change what a test sees."""
    monkeypatch.delenv(URL_VARIABLE, raising=False)
    monkeypatch.delenv(API_KEY_VARIABLE, raising=False)


@pytest.fixture
def closed_port() -> int:
    """A local port nothing listens on, so a request to it is refused."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
