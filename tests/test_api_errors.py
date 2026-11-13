"""Error responses every route shares: structured JSON, and nothing from the request."""

import logging
from pathlib import Path
from typing import Any

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from groundline.api import common, errors
from groundline.api.errors import safe_traceback
from groundline.app import app
from groundline.clients.anthropic import AnthropicClient
from tests.anthropic_helpers import api_error
from tests.auth_helpers import SECRET as API_KEY
from tests.auth_helpers import Endpoint, FakeKeys
from tests.auth_helpers import endpoint as endpoint

SECRET = "sk-ant-not-a-real-key"
PRIVATE = "private-error-content"
http = TestClient(
    app,
    raise_server_exceptions=False,
    headers={"Authorization": f"Bearer {API_KEY}"},
)


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_an_unknown_route_is_a_404_saying_which(method: str):
    response = http.request(method, "/v1/nope")

    assert response.status_code == 404
    assert response.json() == {
        "detail": f"no route for {method} /v1/nope; see /docs for the API's routes"
    }


def test_a_routes_own_404_is_unchanged():
    own = FastAPI()
    errors.install(own)

    @own.get("/things/{id}")
    def thing(id: str) -> None:
        raise HTTPException(404, detail="Not Found")

    response = TestClient(own).get("/things/1")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_an_unsupported_method_is_a_405():
    response = http.get("/v1/omissions")

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize(
    "body",
    [
        b'{"source": "' + SECRET.encode() + b'", "summ',
        b"",
        b'["' + SECRET.encode() + b'"]',
        SECRET.encode() + bytes(range(256)),
    ],
    ids=["broken JSON", "empty", "a list", "binary"],
)
def test_a_malformed_request_is_a_4xx_not_a_crash(body: bytes):
    response = http.post(
        "/v1/omissions", content=body, headers={"content-type": "application/json"}
    )

    # FastAPI answers 400 for a body that isn't UTF-8, and 422 for the rest.
    assert response.status_code in (400, 422)
    assert "detail" in response.json()
    assert SECRET not in response.text


@pytest.mark.parametrize(
    "body",
    [b"\xc3\x28", b'{"source": "\xc3\x28", "summary": "b"}'],
    ids=["bad UTF-8", "bad UTF-8 inside JSON"],
)
def test_a_body_that_is_not_utf8_is_a_400_that_repeats_nothing(body: bytes):
    response = http.post(
        "/v1/omissions", content=body, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "There was an error parsing the body"}


@pytest.mark.parametrize("route", ["/v1/omissions", "/v1/commissions"])
@pytest.mark.parametrize(
    ("status", "kind"), [(401, "authentication_error"), (403, "permission_error")]
)
def test_a_rejected_anthropic_key_is_a_503_without_the_servers_env_path(
    route: str, status: int, kind: str, tmp_path: Path
):
    """The real client, as it runs when the key came from a `.env` file: its error
    names that file, which a response must not."""
    env_file = tmp_path / ".env"

    def reject(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=api_error(kind, "invalid x-api-key"))

    def real_client() -> AnthropicClient:
        return AnthropicClient(
            "sk-ant-not-a-real-key",
            key_source=str(env_file),
            http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(reject)),
        )

    app.dependency_overrides[common.model_client] = real_client

    response = http.post(route, json={"source": "a", "summary": "b"})

    assert response.status_code == 503
    detail: Any = response.json()["detail"]
    assert detail.startswith("ANTHROPIC_API_KEY ")
    assert str(tmp_path) not in detail
    assert "Key from" not in detail


def test_an_error_outside_a_route_is_a_structured_500(
    endpoint: Endpoint,
    caplog: pytest.LogCaptureFixture,
):
    def broken() -> None:
        raise RuntimeError(PRIVATE)

    app.dependency_overrides[common.model_client] = broken

    with (
        caplog.at_level(logging.INFO),
        caplog.at_level(logging.DEBUG, logger="groundline"),
    ):
        response = http.post(
            endpoint.path, json={"source": PRIVATE + SECRET, "summary": PRIVATE}
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "unexpected error; see the server log"}
    assert f"unexpected RuntimeError handling POST {endpoint.path}" in caplog.text
    assert "in broken" in caplog.text
    for secret in (SECRET, API_KEY, PRIVATE):
        assert secret not in response.text + caplog.text


def test_a_safe_traceback_has_each_type_and_frame_but_no_message():
    try:
        try:
            raise ValueError(SECRET)
        except ValueError as inner:
            raise KeyError(SECRET) from inner
    except KeyError as error:
        text = safe_traceback(error)

    assert text.index("ValueError") < text.index("KeyError")
    assert "test_a_safe_traceback" in text
    assert SECRET not in text


@pytest.mark.parametrize("route", ["/v1/omissions", "/v1/commissions"])
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("timeout", 504),
        ("hung-transport", 504),
        ("retry-after-check-too-short", 502),
        ("timeout-then-error", 502),
        ("error-then-timeout", 504),
        ("error", 502),
        ("retry-after-too-long", 502),
    ],
)
def test_final_model_stopping_reason_controls_http_status(
    route: str,
    outcome: str,
    expected: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    import asyncio

    monkeypatch.chdir(tmp_path)
    for name, value in {
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS": "0.5"
        if outcome == "retry-after-check-too-short"
        else "3",
        "GROUNDLINE_MODEL_TIMEOUT_SECONDS": "0.3",
        "GROUNDLINE_MODEL_MAX_ATTEMPTS": "2",
        "GROUNDLINE_MODEL_RETRY_TIMEOUT_SECONDS": "5",
        "GROUNDLINE_MODEL_RETRY_BASE_SECONDS": "0.001",
        "GROUNDLINE_MODEL_RETRY_CAP_SECONDS": "0.002",
    }.items():
        monkeypatch.setenv(name, value)
    calls: list[httpx2.Request] = []

    cancelled: list[bool] = []

    async def handler(request: httpx2.Request):
        calls.append(request)
        if outcome == "hung-transport":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
        timeout = (
            outcome == "timeout"
            or (outcome == "timeout-then-error" and len(calls) == 1)
            or (outcome == "error-then-timeout" and len(calls) >= 2)
        )
        if timeout:
            raise httpx2.ReadTimeout(PRIVATE + SECRET, request=request)
        return httpx2.Response(
            529,
            headers=(
                {"Retry-After": "100"}
                if outcome == "retry-after-too-long"
                else {"Retry-After": "1"}
                if outcome == "retry-after-check-too-short"
                else {}
            ),
            json=api_error("overloaded_error", PRIVATE + SECRET),
        )

    async def run():
        model = AnthropicClient(
            SECRET,
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )
        app.dependency_overrides[common.model_client] = lambda: model
        try:
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                started = asyncio.get_running_loop().time()
                task = asyncio.create_task(
                    client.post(
                        route,
                        json={"source": PRIVATE, "summary": PRIVATE},
                        headers={"Authorization": f"Bearer {API_KEY}"},
                    )
                )
                done, _ = await asyncio.wait({task}, timeout=5)
                if not done:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                assert done, "provider failures must stop within configured allowances"
                response = task.result()
                if outcome == "retry-after-check-too-short":
                    assert asyncio.get_running_loop().time() - started < 0.5, (
                        "a wait that cannot fit must be rejected before check expiry"
                    )
                assert response.status_code == expected
                assert len(calls) == (1 if outcome.startswith("retry-after") else 2)
                if outcome == "hung-transport":
                    assert len(cancelled) == 2
                for secret in (SECRET, PRIVATE, API_KEY):
                    assert secret not in response.text + caplog.text
        finally:
            await model.close()

    asyncio.run(run())
