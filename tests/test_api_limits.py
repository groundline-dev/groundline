"""ALL-136 size policy: shared enforcement plus three fresh-process checks."""

import asyncio
import http.client
import json
import logging
import os
import re
import selectors
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.types import Message, Scope

from groundline import settings
from groundline.api import common
from groundline.app import app
from groundline.settings import APILimitsSettings
from tests.auth_helpers import ENDPOINTS, HEADERS, SECRET, Endpoint, FakeKeys
from tests.fakes import FakeClient

ROOT = Path(__file__).resolve().parents[1]
LIMIT_NAMES = (
    "GROUNDLINE_MAX_BODY_BYTES",
    "GROUNDLINE_MAX_SOURCE_CHARS",
    "GROUNDLINE_MAX_SUMMARY_CHARS",
)
PRIVATE = "private-upload-content"
TOO_LARGE = {"detail": "Request body exceeds the configured size limit"}


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Neither local .env nor inherited credentials/timeouts may affect fake checks.
    monkeypatch.chdir(tmp_path)
    for name in (
        *LIMIT_NAMES,
        "ANTHROPIC_API_KEY",
        "DATABASE_URL",
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS",
        "GROUNDLINE_MODEL_MAX_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)


def limits() -> dict[str, int]:
    return APILimitsSettings().model_dump()


def process_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = {
        name: os.environ[name] for name in ("PATH", "SYSTEMROOT") if name in os.environ
    }
    environment.update(
        PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT))), PYTHONNOUSERSITE="1"
    )
    environment.update(overrides or {})
    return environment


def probe(
    code: str, tmp_path: Path, overrides: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=process_environment(overrides),
        capture_output=True,
        text=True,
        timeout=30,
    )


def request_properties(schema: dict[str, Any], path: str) -> dict[str, Any]:
    request = schema["paths"][path]["post"]["requestBody"]
    ref = request["content"]["application/json"]["schema"]["$ref"]
    return schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]["properties"]


def test_default_limits_contract():
    assert limits() == {
        "max_body_bytes": 5_242_880,
        "max_source_chars": 200_000,
        "max_summary_chars": 200_000,
    }


def test_missing_and_empty_limits_ignore_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    expected = limits()
    (tmp_path / ".env").write_text(
        "".join(f"{name}=invalid-private-value\n" for name in LIMIT_NAMES)
        + "GROUNDLINE_CHECK_TIMEOUT_SECONDS=37\nANTHROPIC_API_KEY=sk-dummy-dotenv\n"
    )
    for value in (None, ""):
        for name in LIMIT_NAMES:
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        assert limits() == expected
    # API-only limits must not break existing .env settings for CLI/Python use.
    assert settings.Settings().check_timeout_seconds == 37
    assert settings.Settings().anthropic_key() == "sk-dummy-dotenv"


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-1",
        "+5",
        "bad-private-value",
        " ",
        " 5 ",
        "5.0",
        "1e6",
        "5_000",
        "５",
        "٥",
    ],
)
def test_invalid_limit_values_are_rejected(value: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDLINE_MAX_BODY_BYTES", value)
    with pytest.raises(ValidationError):
        limits()


def test_invalid_limit_stops_startup_without_leaking_values(tmp_path: Path):
    name, value = "GROUNDLINE_MAX_BODY_BYTES", "invalid-private-value"
    result = probe("import groundline.app", tmp_path, {name: value})
    assert result.returncode != 0, "API import accepted an invalid body limit"
    assert name in result.stderr
    for private in (
        value,
        str(tmp_path),
        ".env",
        "ValidationError",
        "input_value",
        "During handling",
        "direct cause",
    ):
        assert private not in result.stdout + result.stderr


async def asgi_request(
    path: str, chunks: list[bytes | Exception | Message], headers: dict[str, str]
) -> dict[str, Any]:
    messages: list[Message] = []
    reads = 0
    disconnected = False

    async def receive() -> Message:
        nonlocal reads, disconnected
        if disconnected:
            return {"type": "http.disconnect"}
        if reads == len(chunks):
            await asyncio.Event().wait()  # Keep an ordinary client connected.
        chunk = chunks[reads]
        reads += 1
        if isinstance(chunk, Exception):
            raise chunk
        if isinstance(chunk, dict):
            disconnected = chunk["type"] == "http.disconnect"
            return chunk
        return {"type": "http.request", "body": chunk, "more_body": reads < len(chunks)}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    starts = [m for m in messages if m["type"] == "http.response.start"]
    assert len(starts) <= 1 if disconnected else len(starts) == 1, messages
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return {
        "status": starts[0]["status"] if starts else None,
        "body": json.loads(body) if body else None,
        "reads": reads,
    }


def exercise(
    route: Endpoint,
    chunks: list[bytes | Exception | Message],
    caplog: pytest.LogCaptureFixture,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One real route request; local spies prove parsing, auth and work order."""
    keys = FakeKeys()
    model = FakeClient(route.not_flagged())
    caplog.clear()
    with (
        patch.object(app.state, "api_keys", keys, create=True),
        patch.dict(app.dependency_overrides, {common.model_client: lambda: model}),
        patch.object(
            route.module,
            "evaluate",
            new_callable=AsyncMock,
            wraps=route.module.evaluate,
        ) as evaluate,
        patch.object(Request, "json", autospec=True, side_effect=Request.json) as parse,
        caplog.at_level(logging.DEBUG, logger="groundline"),
    ):
        row = asyncio.run(
            asgi_request(
                route.path,
                chunks,
                {"Content-Type": "application/json", **HEADERS, **(headers or {})},
            )
        )
    row.update(
        parses=parse.await_count,
        evaluations=evaluate.await_count,
        models=len(model.requests),
        auth=len(keys.calls),
        log=caplog.text,
    )
    return row


def assert_private_rejection(row: dict[str, Any]) -> None:
    assert row["status"] == 413, row
    assert row["body"] == TOO_LARGE
    assert row["parses"] == row["evaluations"] == row["models"] == row["auth"] == 0
    assert PRIVATE not in json.dumps(row) and SECRET not in json.dumps(row)


def test_declared_oversize_is_rejected_before_reading(caplog: pytest.LogCaptureFixture):
    for route in ENDPOINTS:
        row = exercise(
            route,
            [(PRIVATE + SECRET).encode()],
            caplog,
            {"Content-Length": str(limits()["max_body_bytes"] + 1)},
        )
        assert_private_rejection(row)
        assert row["reads"] == 0


def test_streamed_body_boundary_and_actual_byte_count(caplog: pytest.LogCaptureFixture):
    limit = limits()["max_body_bytes"]
    raw = json.dumps({"source": PRIVATE, "summary": PRIVATE}).encode()
    exact = raw + b" " * (limit - len(raw))
    for route in ENDPOINTS:
        row = exercise(route, [exact[:100], exact[100:]], caplog)
        assert row["status"] == 200, row
        assert row["parses"] == row["evaluations"] == row["auth"] == 1
        for headers in ({}, {"Content-Length": "1"}):
            row = exercise(route, [exact, b" ", b"unread tail"], caplog, headers)
            assert_private_rejection(row)
            assert row["reads"] == 2, "stop on the first chunk over the allowance"


def test_both_default_field_maxima_fit_with_escaped_emoji(
    caplog: pytest.LogCaptureFixture,
):
    for route in ENDPOINTS:
        properties = request_properties(app.openapi(), route.path)
        body = {
            field: "😀" * properties[field]["maxLength"]
            for field in ("source", "summary")
        }
        row = exercise(route, [json.dumps(body, ensure_ascii=True).encode()], caplog)
        assert row["status"] == 200, row
        assert row["parses"] == row["evaluations"] == 1
        assert row["models"] == route.calls


def test_unexpected_read_failure_is_a_safe_server_error(
    caplog: pytest.LogCaptureFixture,
):
    row = exercise(ENDPOINTS[0], [RuntimeError(PRIVATE + SECRET)], caplog)
    assert row["status"] == 500, row
    assert row["body"] == {"detail": "unexpected error; see the server log"}
    assert row["parses"] == row["evaluations"] == row["models"] == 0
    assert "RuntimeError" in row["log"]
    assert PRIVATE not in json.dumps(row) and SECRET not in json.dumps(row)


def test_disconnect_stops_without_parsing_or_evaluation(
    caplog: pytest.LogCaptureFixture,
):
    row = exercise(ENDPOINTS[0], [b'{"source":', {"type": "http.disconnect"}], caplog)
    assert row["reads"] == 2
    assert row["parses"] == row["evaluations"] == row["models"] == 0
    assert "unexpected" not in row["log"].lower()
    assert "ClientDisconnect" not in row["log"]


def check_custom_limits() -> None:
    """Run in a fresh process so overrides precede app/model imports."""
    app.state.api_keys = FakeKeys()
    schema = app.openapi()
    for route in ENDPOINTS:
        properties = request_properties(schema, route.path)
        assert properties["source"]["maxLength"] == 9, properties["source"]
        assert properties["summary"]["maxLength"] == 7, properties["summary"]
        operation = schema["paths"][route.path]["post"]
        documentation = json.dumps([schema["info"]["description"], operation]).lower()
        assert re.search(r"\b256\s+bytes\b", documentation), documentation
        app.dependency_overrides[common.model_client] = lambda: FakeClient(
            route.not_flagged()
        )
        client = TestClient(app)
        body = {"source": "😀" * 9, "summary": "😀" * 7}
        response = client.post(
            route.path,
            content=json.dumps(body, ensure_ascii=True).encode(),
            headers={**HEADERS, "Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text
        for field in ("source", "summary"):
            response = client.post(
                route.path, json={**body, field: body[field] + "😀"}, headers=HEADERS
            )
            assert response.status_code == 422, response.text
            problem = response.json()["detail"][0]
            assert problem["loc"] == ["body", field], problem
            assert "input" not in problem, problem
    print("custom limits reached docs and validation")


def test_custom_limits_reach_docs_and_validation(tmp_path: Path):
    result = probe(
        "from tests.test_api_limits import check_custom_limits; check_custom_limits()",
        tmp_path,
        dict(zip(LIMIT_NAMES, ("000256", "0009", "0007"))),
    )
    assert result.returncode == 0, result.stderr
    assert "custom limits reached docs and validation" in result.stdout


def serve_streaming_test(descriptor: int) -> None:
    """Run the real app on an inherited socket, with no DB or model access."""
    import uvicorn

    def forbidden_model() -> None:
        raise AssertionError("a rejected upload must not request a model")

    app.state.api_keys = FakeKeys()
    app.dependency_overrides[common.model_client] = forbidden_model
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="warning", access_log=False)
    )

    async def run() -> None:
        with socket.socket(fileno=descriptor) as listener:
            task = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("uvicorn stopped before becoming ready")
                await asyncio.sleep(0.01)
            print("ready", flush=True)
            await task

    asyncio.run(run())


def test_real_uvicorn_delivers_json_for_chunked_oversized_uploads(tmp_path: Path):
    """A chunked upload over real HTTP receives the JSON 413 response."""
    with socket.socket() as listener, (tmp_path / "uvicorn.log").open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = (
            "from tests.test_api_limits import serve_streaming_test; "
            f"serve_streaming_test({listener.fileno()})"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            pass_fds=(listener.fileno(),),
            cwd=tmp_path,
            env=process_environment({"GROUNDLINE_MAX_BODY_BYTES": "256"}),
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=30), "uvicorn startup watchdog expired"
            line = process.stdout.readline()
            log.flush()
            log.seek(0)
            assert line.strip() == "ready", log.read()
            small = json.dumps({"source": "a", "summary": "b"}).encode()
            padded = small + b" " * (256 - len(small))
            responses: list[tuple[str, int, str, bytes]] = []
            for route in ENDPOINTS:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        route.path,
                        body=iter([padded[:128], padded[128:], b" "]),
                        headers={"Content-Type": "application/json"},
                        encode_chunked=True,
                    )
                    response = connection.getresponse()
                    responses.append(
                        (
                            route.path,
                            response.status,
                            response.getheader("Content-Type", ""),
                            response.read(),
                        )
                    )
                finally:
                    connection.close()
            for path, status, content_type, body in responses:
                assert status == 413, (path, status, body)
                assert content_type.startswith("application/json"), path
                assert json.loads(body) == TOO_LARGE, path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert process.stdout is not None
            process.stdout.close()
