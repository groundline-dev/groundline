"""Integration contracts through real settings, pipelines and Anthropic SDK.

Only API-key storage and Anthropic's HTTP service are faked. Authentication,
validation and probe behavior live in their dedicated API test modules.
"""

import importlib
import json
import logging
import socket
from collections.abc import Callable
from typing import Any, Never

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient
from fastapi.testclient import TestClient

from groundline_core.clients import AnthropicClient
from tests.anthropic_helpers import api_error, message
from tests.api_integration_helpers import ANTHROPIC_KEY
from tests.api_integration_helpers import http as http
from tests.api_integration_helpers import isolated as isolated
from tests.api_integration_helpers import keys as keys
from tests.auth_helpers import BODY, ENDPOINTS, SECRET, Endpoint
from tests.auth_helpers import endpoint as endpoint
from tests.commissions_helpers import (
    DOSE,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    issues_reply,
)
from tests.commissions_helpers import SUMMARY as COMMISSION_SUMMARY
from tests.commissions_helpers import Script as CommissionScript
from tests.omission_helpers import (
    CLEAR_SUMMARY,
    SOURCE,
    SUMMARY,
    Script,
    clear_script,
    flagged_script,
    not_flagged_script,
)

PRIVATE = "private-source-summary-sentinel"

PARTIAL_INJURY = {
    "type": "omission",
    "severity": "supporting",
    "verdict": "partial",
    "confidence": 0.6,
    "statement": "Injured the finger skiing.",
    "source_evidence": "i was skiing over the weekend",
    "summary_evidence": "She was skiing and hyperextended her right index finger",
    "evidence_found": True,
    "explanation": "The summary only partly records this fact.",
}
MISSING_MOVEMENT = {
    "type": "omission",
    "severity": "supporting",
    "verdict": "absent",
    "confidence": 0.95,
    "statement": "Pain is worse with movement.",
    "source_evidence": "it hurts more when i move it",
    "summary_evidence": None,
    "evidence_found": True,
    "explanation": "The summary does not record this fact.",
}
MISSING_DOSE = {
    "type": "omission",
    "severity": "critical",
    "verdict": "absent",
    "confidence": 0.95,
    "statement": "Motrin 600 mg every 6 hours for a week.",
    "source_evidence": "motrin 600 mg every 6 hours",
    "summary_evidence": None,
    "evidence_found": True,
    "explanation": "The summary does not record this fact.",
}
DOSE_COMMISSION = {
    "type": "commission",
    "severity": "critical",
    "verdict": "contradicted",
    "confidence": 0.95,
    "statement": DOSE,
    "source_evidence": DOSE_SOURCE,
    "summary_evidence": None,
    "evidence_found": True,
    "explanation": "The source says something different.",
}
EXAM_COMMISSION = {
    "type": "commission",
    "severity": "supporting",
    "verdict": "unsupported",
    "confidence": 0.7,
    "statement": EXAM,
    "source_evidence": None,
    "summary_evidence": None,
    "evidence_found": None,
    "explanation": "The source does not mention this claim.",
}


class Wire:
    """Local Anthropic HTTP service with recorded requests and scripted replies."""

    def __init__(self, replies: Callable[[str], str]):
        self.replies = replies
        self.requests: list[httpx2.Request] = []
        self.reply: Callable[[httpx2.Request], httpx2.Response] = self.success

    def respond(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {ANTHROPIC_KEY}"
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        return self.reply(request)

    def success(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        text = self.replies(body["messages"][0]["content"])
        return httpx2.Response(
            200,
            json=message(
                model=body["model"],
                content=[{"type": "text", "text": text}],
                usage={"input_tokens": 100, "output_tokens": 20},
            ),
        )


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

    # The package exports an `evaluate` function that hides its namesake module.
    monkeypatch.setattr(
        importlib.import_module("groundline_core.evaluate"), "AnthropicClient", client
    )
    return wire


def assert_success(
    response: httpx2.Response, wire: Wire, expected: dict[str, Any], calls: int
) -> None:
    assert response.status_code == 200
    body = response.json()
    usage = body["metadata"].pop("usage")
    assert body == expected
    assert len(wire.requests) == usage["calls"] == calls
    assert usage["input_tokens"] == 100 * calls
    assert usage["output_tokens"] == 20 * calls
    assert usage["cost_usd"] > 0


@pytest.mark.parametrize("endpoint", ENDPOINTS[:1], ids=lambda e: e.path, indirect=True)
@pytest.mark.parametrize(
    "script,summary,flagged,score,findings,counts,calls",
    [
        pytest.param(
            flagged_script,
            SUMMARY,
            True,
            0.95,
            [MISSING_DOSE, PARTIAL_INJURY, MISSING_MOVEMENT],
            {"total": 3, "present": 0, "partial": 1, "absent": 2},
            4,
            id="flagged",
        ),
        pytest.param(
            not_flagged_script,
            SUMMARY,
            False,
            0.0,
            [PARTIAL_INJURY, MISSING_MOVEMENT],
            {"total": 3, "present": 1, "partial": 1, "absent": 1},
            4,
            id="findings-below-threshold",
        ),
        pytest.param(
            clear_script,
            CLEAR_SUMMARY,
            False,
            0.0,
            [],
            {"total": 3, "present": 3, "partial": 0, "absent": 0},
            3,
            id="clear",
        ),
    ],
)
def test_omission_success_uses_environment_key_and_returns_expected_values(
    endpoint: Endpoint,
    script: Callable[[], Script],
    summary: str,
    flagged: bool,
    score: float,
    findings: list[dict[str, Any]],
    counts: dict[str, int],
    calls: int,
    wire: Wire,
    http: TestClient,
):
    wire.replies = script()
    response = http.post(endpoint.path, json={**BODY, "summary": summary})
    assert_success(
        response,
        wire,
        {
            "evaluation_type": "omissions",
            "flagged": flagged,
            "score": 0.25 if flagged else 1.0,
            "findings": findings,
            "counts": counts,
            "metadata": {
                "pipeline": "omission_all_opus",
                "alarm": {"score": score, "threshold": 0.9, "level": "strict"},
            },
        },
        calls,
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS[1:], ids=lambda e: e.path, indirect=True)
@pytest.mark.parametrize(
    "issues,flagged,score,findings",
    [
        pytest.param(
            (DOSE_ISSUE, EXAM_ISSUE),
            True,
            0.95,
            [DOSE_COMMISSION, EXAM_COMMISSION],
            id="flagged",
        ),
        pytest.param(
            (EXAM_ISSUE,), False, 0.0, [EXAM_COMMISSION], id="findings-below-threshold"
        ),
        pytest.param((), False, 0.0, [], id="clear"),
    ],
)
def test_commission_success_uses_environment_key_and_returns_expected_values(
    endpoint: Endpoint,
    issues: tuple[dict[str, Any], ...],
    flagged: bool,
    score: float,
    findings: list[dict[str, Any]],
    wire: Wire,
    http: TestClient,
):
    wire.replies = CommissionScript(commissions_check=issues_reply(*issues))
    response = http.post(endpoint.path, json={**BODY, "summary": COMMISSION_SUMMARY})
    assert_success(
        response,
        wire,
        {
            "evaluation_type": "commissions",
            "flagged": flagged,
            "score": 0.1667 if flagged else 1.0,
            "findings": findings,
            "counts": None,
            "metadata": {
                "pipeline": "commission_quick",
                "alarm": {"score": score, "threshold": 0.85, "level": "default"},
            },
        },
        1,
    )


def failure_response(
    http: TestClient, endpoint: Endpoint, caplog: pytest.LogCaptureFixture, status: int
) -> httpx2.Response:
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
    return response


def disconnected(request: httpx2.Request) -> Never:
    raise httpx2.ConnectError(PRIVATE, request=request)


def error_reply(
    status: int, kind: str, detail: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    def reply(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json=api_error(kind, detail), request=request)

    return reply


@pytest.mark.parametrize(
    "reply,status",
    [
        pytest.param(
            error_reply(401, "authentication_error", "invalid x-api-key"),
            503,
            id="rejected-anthropic-key",
        ),
        pytest.param(disconnected, 502, id="connection"),
        pytest.param(
            error_reply(500, "api_error", PRIVATE),
            502,
            id="service",
        ),
    ],
)
def test_sdk_failures_have_expected_status_and_no_sensitive_content(
    endpoint: Endpoint,
    reply: Callable[[httpx2.Request], httpx2.Response],
    status: int,
    wire: Wire,
    http: TestClient,
    caplog: pytest.LogCaptureFixture,
):
    wire.reply = reply
    failure_response(http, endpoint, caplog, status)
    assert len(wire.requests) == 1


def test_missing_environment_key_fails_before_any_model_call(
    endpoint: Endpoint,
    wire: Wire,
    http: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    response = failure_response(http, endpoint, caplog, 503)
    assert response.json()["detail"].startswith("ANTHROPIC_API_KEY is not set.")
    assert wire.requests == []


def test_unusable_reply_retries_once_without_exposing_sensitive_content(
    endpoint: Endpoint,
    wire: Wire,
    http: TestClient,
    caplog: pytest.LogCaptureFixture,
):
    def unusable(prompt: str) -> str:
        return "not json"

    wire.replies = unusable
    response = failure_response(http, endpoint, caplog, 502)
    assert len(wire.requests) == 2
    assert "unusable reply after 1 retry" in response.json()["detail"]


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_intercepted_contracts_block_even_loopback_sockets(method: str):
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="blocked network connection"):
            getattr(sock, method)(("127.0.0.1", 1))
