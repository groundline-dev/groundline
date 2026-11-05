"""Offline API contracts through real settings, pipelines and Anthropic SDK.

Only API-key storage and Anthropic's HTTP service are faked. Existing unit
tests exercise finer details; these scenarios prove the layers work together.
"""

import importlib
import json
import logging
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Never
from unittest.mock import AsyncMock

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient
from fastapi.testclient import TestClient

from groundline.api.common import MAX_CHARS
from groundline.api_keys import APIKeyStorageError
from groundline.app import app
from groundline.clients import AnthropicClient
from tests.auth_helpers import BODY, ENDPOINTS, HEADERS, SECRET, Endpoint, FakeKeys
from tests.commissions_helpers import DOSE, DOSE_ISSUE, EXAM, EXAM_ISSUE, issues_reply
from tests.commissions_helpers import SUMMARY as COMMISSION_SUMMARY
from tests.commissions_helpers import Script as CommissionScript
from tests.omission_helpers import (
    SOURCE,
    SUMMARY,
    check_reply,
    flagged_script,
    not_flagged_script,
)

ANTHROPIC_KEY = "sk-ant-offline-contract-test"
PRIVATE = "private-source-summary-sentinel"


class Wire:
    """Local Anthropic HTTP service with recorded requests and scripted replies."""

    def __init__(self, replies: Callable[[str], str]):
        self.replies = replies
        self.requests: list[httpx2.Request] = []
        self.failure: str | None = None

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {ANTHROPIC_KEY}"
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        if self.failure == "connection":
            raise httpx2.ConnectError(PRIVATE, request=request)
        if self.failure in {"rejected", "service"}:
            rejected = self.failure == "rejected"
            return httpx2.Response(
                401 if rejected else 500,
                json={
                    "type": "error",
                    "error": {
                        "type": "authentication_error" if rejected else "api_error",
                        "message": "invalid x-api-key" if rejected else PRIVATE,
                    },
                },
            )
        body = json.loads(request.content)
        text = self.replies(body["messages"][0]["content"])
        return httpx2.Response(
            200,
            json={
                "id": "msg_offline",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )


@pytest.fixture(params=ENDPOINTS, ids=lambda e: e.path)
def endpoint(request: pytest.FixtureRequest) -> Endpoint:
    return request.param


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)  # Never read the developer's .env.
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for variable in (
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(app, "dependency_overrides", {})

    def no_network(*args: object, **kwargs: object) -> Never:
        raise AssertionError("API contract tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> FakeKeys:
    storage = FakeKeys()
    monkeypatch.setattr(app.state, "api_keys", storage, raising=False)
    return storage


@pytest.fixture
def wire(endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch) -> Wire:
    wire = Wire(endpoint.not_flagged())

    def client(api_key: str, *, key_source: str) -> AnthropicClient:
        return AnthropicClient(
            api_key,
            key_source=key_source,
            max_retries=0,  # SDK backoff is covered separately; keep failures instant.
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(wire.respond), trust_env=False
            ),
        )

    monkeypatch.setattr(
        importlib.import_module("groundline.evaluate"), "AnthropicClient", client
    )
    return wire


@pytest.fixture
def evaluation(endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spy = AsyncMock(wraps=endpoint.module.evaluate)
    monkeypatch.setattr(endpoint.module, "evaluate", spy)
    return spy


@pytest.fixture
def http(keys: FakeKeys) -> TestClient:
    # Deliberately skip the PostgreSQL-owning lifespan; storage is injected above.
    return TestClient(app, headers=HEADERS)


@pytest.mark.parametrize("outcome", ["flagged", "findings-below-threshold", "clear"])
def test_success_has_expected_values_and_uses_environment_key(
    endpoint: Endpoint, outcome: str, wire: Wire, http: TestClient
):
    omissions = endpoint.path.endswith("omissions")
    request_body = {**BODY, "summary": SUMMARY if omissions else COMMISSION_SUMMARY}
    if omissions:
        script = flagged_script() if outcome == "flagged" else not_flagged_script()
        if outcome == "clear":
            request_body["summary"] += "\nPain is worse with movement."
            checked = json.loads(check_reply())
            for row in checked["facts"]:
                row.update(verdict="present", missing_confidence=0)
                if row["id"] == "f2":
                    row["quote"] = "Pain is worse with movement."
            script.replies["check"] = [json.dumps(checked)]
        wire.replies = script
    else:
        issues = {
            "flagged": (DOSE_ISSUE, EXAM_ISSUE),
            "findings-below-threshold": (EXAM_ISSUE,),
            "clear": (),
        }[outcome]
        wire.replies = CommissionScript(commissions_check=issues_reply(*issues))

    response = http.post(endpoint.path, json=request_body)

    assert response.status_code == 200
    body = response.json()
    assert body["flagged"] is (outcome == "flagged")
    preset = "omission_all_opus" if omissions else "commission_quick"
    assert body["pipeline"] == body["validated_preset"] == preset
    assert body["alarm"] == {
        "score": 0.95 if outcome == "flagged" else 0.0,
        "threshold": 0.9 if omissions else 0.85,
        "level": "strict" if omissions else "default",
    }
    findings = body["omissions" if omissions else "commissions"]
    assert (
        len(findings)
        == {
            "flagged": 3 if omissions else 2,
            "findings-below-threshold": 2 if omissions else 1,
            "clear": 0,
        }[outcome]
    )
    if omissions:
        assert body["counts"] == (
            {"total": 3, "present": 3, "partial": 0, "absent": 0}
            if outcome == "clear"
            else {
                "total": 3,
                "present": int(outcome != "flagged"),
                "partial": 1,
                "absent": 2 if outcome == "flagged" else 1,
            }
        )
        if outcome == "flagged":
            assert findings[0] == {
                "fact": "Motrin 600 mg every 6 hours for a week.",
                "severity": "critical",
                "verdict": "absent",
                "missing_confidence": 0.95,
                "quote": None,
            }
    elif findings:
        assert findings[0]["claim"] == (DOSE if outcome == "flagged" else EXAM)
        assert findings[0]["unsupported_confidence"] == (
            0.95 if outcome == "flagged" else 0.7
        )
    calls = 3 if omissions and outcome == "clear" else endpoint.calls
    assert len(wire.requests) == calls
    assert body["usage"]["calls"] == calls
    assert body["usage"]["input_tokens"] == 100 * calls
    assert body["usage"]["output_tokens"] == 20 * calls
    assert body["usage"]["cost_usd"] > 0


@pytest.mark.parametrize(
    "state", ["missing", "malformed", "unknown", "revoked", "expired"]
)
def test_rejected_authorization_never_starts_evaluation(
    endpoint: Endpoint,
    state: str,
    keys: FakeKeys,
    wire: Wire,
    evaluation: AsyncMock,
):
    if state in {"revoked", "expired"}:
        keys.info = None  # Storage reports an ineligible API key.
    headers = {
        "missing": {},
        "malformed": {"Authorization": "Bearer malformed"},
        "unknown": {"Authorization": "Bearer gl_" + "z" * 43},
        "revoked": HEADERS,
        "expired": HEADERS,
    }[state]
    response = TestClient(app).post(endpoint.path, json=BODY, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["www-authenticate"] == "Bearer"
    evaluation.assert_not_called()
    assert wire.requests == []


def test_revoking_an_accepted_api_key_stops_the_next_request(
    endpoint: Endpoint,
    keys: FakeKeys,
    wire: Wire,
    http: TestClient,
    evaluation: AsyncMock,
):
    assert http.post(endpoint.path, json=BODY).status_code == 200
    keys.info = None
    response = http.post(endpoint.path, json=BODY)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert keys.calls == [SECRET, SECRET]
    assert evaluation.await_count == 1
    assert len(wire.requests) == endpoint.calls


@pytest.mark.parametrize(
    "authenticated", [True, False], ids=["active-key", "missing-key"]
)
@pytest.mark.parametrize("field", ["source", "summary"])
@pytest.mark.parametrize("invalid", ["missing", "empty", "blank", "type", "too-long"])
def test_invalid_fields_follow_authentication_and_never_call_model(
    endpoint: Endpoint,
    authenticated: bool,
    field: str,
    invalid: str,
    keys: FakeKeys,
    wire: Wire,
    evaluation: AsyncMock,
):
    body: dict[str, object] = dict(BODY)
    values: dict[str, object] = {
        "empty": "",
        "blank": " \n ",
        "type": {"text": PRIVATE},
        "too-long": PRIVATE + "x" * MAX_CHARS,
    }
    if invalid == "missing":
        del body[field]
    else:
        body[field] = values[invalid]
    response = TestClient(app).post(
        endpoint.path, json=body, headers=HEADERS if authenticated else {}
    )
    assert response.status_code == (422 if authenticated else 401)
    if authenticated:
        assert response.json()["detail"][0]["loc"] == ["body", field]
    assert PRIVATE not in response.text
    evaluation.assert_not_called()
    assert wire.requests == []


@pytest.mark.parametrize(
    "authenticated", [True, False], ids=["active-key", "missing-key"]
)
@pytest.mark.parametrize(
    "raw,status", [(b'{"source":', 422), (b"\xff", 400), (b"[]", 422)]
)
def test_malformed_bodies_never_start_evaluation(
    endpoint: Endpoint,
    authenticated: bool,
    raw: bytes,
    status: int,
    keys: FakeKeys,
    wire: Wire,
    evaluation: AsyncMock,
):
    response = TestClient(app).post(
        endpoint.path,
        content=raw,
        headers={
            "Content-Type": "application/json",
            **(HEADERS if authenticated else {}),
        },
    )
    # A JSON array parses successfully, so authentication precedes its validation.
    expected = 401 if raw == b"[]" and not authenticated else status
    assert response.status_code == expected
    assert "detail" in response.json()
    evaluation.assert_not_called()
    assert wire.requests == []


@pytest.mark.parametrize("option", ["extra-field", "threshold"])
def test_invalid_options_are_rejected(
    endpoint: Endpoint,
    option: str,
    wire: Wire,
    http: TestClient,
    evaluation: AsyncMock,
):
    body = {**BODY, (PRIVATE if option == "extra-field" else "threshold"): PRIVATE}
    response = http.post(endpoint.path, json=body)
    assert response.status_code == 422
    assert PRIVATE not in response.text
    evaluation.assert_not_called()
    assert wire.requests == []


@pytest.mark.parametrize("endpoint", ENDPOINTS[:1], ids=lambda e: e.path, indirect=True)
@pytest.mark.parametrize("level,threshold", [("strict", 0.9), ("moderate", 0.425)])
def test_omissions_accepts_each_named_threshold(
    wire: Wire, http: TestClient, endpoint: Endpoint, level: str, threshold: float
):
    wire.replies = flagged_script()
    response = http.post(endpoint.path, json={**BODY, "threshold": level})
    assert response.status_code == 200
    assert response.json()["alarm"] == {
        "score": 0.95,
        "level": level,
        "threshold": threshold,
    }


@pytest.mark.parametrize(
    "failure,status",
    [
        ("auth-storage", 503),
        ("missing-key", 503),
        ("rejected", 503),
        ("connection", 502),
        ("service", 502),
        ("unusable-reply", 502),
        ("unexpected", 500),
    ],
)
def test_failures_have_expected_status_and_do_not_expose_sensitive_content(
    endpoint: Endpoint,
    failure: str,
    status: int,
    wire: Wire,
    keys: FakeKeys,
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    evaluation: AsyncMock,
):
    wire.failure = failure
    if failure == "auth-storage":
        keys.failure = APIKeyStorageError(PRIVATE)
    elif failure == "missing-key":
        monkeypatch.delenv("ANTHROPIC_API_KEY")
    elif failure == "unusable-reply":

        def unusable(prompt: str) -> str:
            return "not json"

        wire.replies = unusable
    elif failure == "unexpected":

        def broken(*args: object, **kwargs: object) -> Never:
            raise RuntimeError(PRIVATE)

        monkeypatch.setattr(endpoint.module, "_response", broken)
    # Exercise all application logging, with dependencies at normal verbosity.
    # Anthropic SDK DEBUG logging includes raw prompts by design.
    with (
        caplog.at_level(logging.INFO),
        caplog.at_level(logging.DEBUG, logger="groundline"),
    ):
        response = http.post(
            endpoint.path,
            json={"source": SOURCE + PRIVATE, "summary": SUMMARY + PRIVATE},
        )
    assert response.status_code == status
    assert "detail" in response.json()
    for secret in (SECRET, ANTHROPIC_KEY, PRIVATE):
        assert secret not in response.text + caplog.text
    if failure in {"auth-storage", "missing-key"}:
        assert wire.requests == []
    if failure == "auth-storage":
        evaluation.assert_not_called()
        assert response.json() == {"detail": "Authentication is unavailable"}
    elif failure == "unusable-reply":
        assert len(wire.requests) == 2  # First reply plus one parsing retry.
        assert "unusable reply after 1 retry" in response.json()["detail"]
    elif failure == "unexpected":
        assert response.json() == {"detail": "unexpected error; see the server log"}
    elif failure != "missing-key":
        assert len(wire.requests) == 1


def test_health_is_public_when_authentication_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(app.state, "api_keys", None, raising=False)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
