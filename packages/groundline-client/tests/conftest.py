"""A fake groundline API on a local port, for the client's tests (ALL-156).

The fake answers each route with the example from openapi.json, records every
request it receives, and can be told to answer a route differently, to answer
the next requests from a script (ALL-157), or to never answer one. No test
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
class Step:
    """One scripted answer: a status, a JSON body and optional response headers."""

    status: int
    body: Any
    headers: dict[str, str] | None = None


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
        self._replies: dict[tuple[str, str], Step] = {
            ("POST", "/v1/omissions"): Step(200, examples["OmissionsResponse"]),
            ("POST", "/v1/commissions"): Step(200, examples["CommissionsResponse"]),
            ("GET", "/health"): Step(200, PROBE_REPLY),
            ("GET", "/ready"): Step(200, PROBE_REPLY),
        }
        # Scripted answers are used first, one per request, then the route's
        # usual reply. A route in `_hung` never answers.
        self._scripts: dict[tuple[str, str], list[Step]] = {}
        self._hung: set[tuple[str, str]] = set()
        self.release = threading.Event()  # set at teardown, so hung requests end
        self.examples = examples
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    def reply(
        self,
        method: str,
        path: str,
        status: int,
        body: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Answer this route with this status and JSON body from now on."""
        self._replies[(method, path)] = Step(status, body, headers)

    def script(self, method: str, path: str, steps: list[Step]) -> None:
        """Answer the next requests to this route with these steps, in order."""
        self._scripts[(method, path)] = list(steps)

    def hang(self, method: str, path: str) -> None:
        """Accept requests to this route and never answer them."""
        self._hung.add((method, path))

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
                route = (self.command, self.path)
                if route in fake._hung:
                    # Long enough for any limit a test sets; short enough that a
                    # client without a limit fails the test instead of hanging it.
                    fake.release.wait(timeout=5)
                    return
                scripted = fake._scripts.get(route)
                step = (
                    scripted.pop(0)
                    if scripted
                    else fake._replies.get(
                        route, Step(404, {"detail": "no such route"})
                    )
                )
                payload = json.dumps(step.body).encode()
                self.send_response(step.status)
                self.send_header("Content-Type", "application/json")
                for name, value in (step.headers or {}).items():
                    self.send_header(name, value)
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
    server.release.set()
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
