"""The Anthropic client, tested against a fake HTTP transport: no network calls."""

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from groundline.clients.anthropic import AnthropicClient
from groundline.core import APIKeyRejectedError, ModelError, ModelRefusal, ModelReply
from tests.anthropic_helpers import api_error, message


def complete(
    reply: dict[str, Any], status: int = 200
) -> tuple[ModelReply, httpx2.Request]:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(status, json=reply)

    async def go() -> ModelReply:
        client = AnthropicClient(
            "sk-test",
            key_source="/repo/.env",
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )
        try:
            return await client.complete(
                "Find the facts.",
                model="claude-opus-5-5",
                effort="medium",
                max_tokens=16000,
            )
        finally:
            await client.close()

    return asyncio.run(go()), sent[0]


def test_sends_the_prompt_with_model_effort_and_max_tokens():
    _, request = complete(message())

    body = json.loads(request.content)
    assert request.url.path == "/v1/messages"
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 16000
    assert body["output_config"] == {"effort": "medium"}
    assert body["messages"] == [{"role": "user", "content": "Find the facts."}]


def test_does_not_use_the_refusal_fallback_beta():
    # The fallback beta isn't covered by the BAA (see the plan's decisions).
    _, request = complete(message())

    assert "fallbacks" not in json.loads(request.content)
    assert "anthropic-beta" not in request.headers


def test_returns_the_text_tokens_and_answering_model():
    reply, _ = complete(message())

    assert reply == ModelReply(
        text='{"facts": []}',
        model="claude-opus-5-5",
        input_tokens=1200,
        output_tokens=340,
    )


def test_a_refusal_is_an_error_naming_its_category():
    refusal = message(
        content=[],
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": "bio", "explanation": None},
    )

    with pytest.raises(ModelRefusal, match="declined to answer.*bio") as error:
        complete(refusal)

    # Billed, so it's kept for the cost report.
    assert error.value.reply is not None
    assert error.value.reply.output_tokens == 340


def test_a_reply_cut_off_at_max_tokens_is_an_error():
    with pytest.raises(ModelError, match="max_tokens") as error:
        complete(message(stop_reason="max_tokens"))

    assert error.value.reply is not None
    assert error.value.reply.input_tokens == 1200


def test_a_rejected_key_gets_a_clear_message():
    with pytest.raises(APIKeyRejectedError) as error:
        complete(api_error("authentication_error", "invalid x-api-key"), status=401)

    message = str(error.value)
    assert message.startswith("ANTHROPIC_API_KEY was rejected by the Anthropic API")
    assert "invalid x-api-key" in message
    assert "Key from: /repo/.env" in message


def test_a_key_without_access_to_the_model_gets_a_clear_message():
    with pytest.raises(
        APIKeyRejectedError, match="doesn't have access to claude-opus-5-5"
    ):
        complete(api_error("permission_error", "not allowed"), status=403)


@pytest.fixture(autouse=True)
def outage_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    for name, value in {
        "GROUNDLINE_MODEL_MAX_ATTEMPTS": "3",
        "GROUNDLINE_MODEL_RETRY_TIMEOUT_SECONDS": "120",
        "GROUNDLINE_MODEL_TIMEOUT_SECONDS": "60",
        "GROUNDLINE_MODEL_CONNECT_TIMEOUT_SECONDS": "5",
        "GROUNDLINE_MODEL_RETRY_BASE_SECONDS": "0.001",
        "GROUNDLINE_MODEL_RETRY_CAP_SECONDS": "0.002",
        "GROUNDLINE_CHECK_TIMEOUT_SECONDS": "120",
    }.items():
        monkeypatch.setenv(name, value)


def run_outage_transport(handler: Callable[[httpx2.Request], httpx2.Response]):
    async def run():
        client = AnthropicClient(
            "dummy-key",
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )
        try:
            return await client.complete(
                "private-prompt",
                model="claude-opus-5-5",
                effort="medium",
                max_tokens=100,
            )
        finally:
            await client.close()

    return asyncio.run(run())


@pytest.mark.parametrize("status", [408, 409, 429, 500, 503, 529])
def test_temporary_failures_recover_without_stacked_sdk_retries(status: int):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        if len(calls) < 3:
            return httpx2.Response(status, json=api_error("api_error", "private-body"))
        return httpx2.Response(200, json=message())

    assert run_outage_transport(handler).input_tokens == 1200
    assert len(calls) == 3
    assert all(
        request.headers.get("x-stainless-retry-count", "0") == "0" for request in calls
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_permanent_errors_ignore_retry_true(status: int):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        return httpx2.Response(
            status,
            headers={"x-should-retry": "true"},
            json=api_error("api_error", "private-body"),
        )

    expected = APIKeyRejectedError if status in (401, 403) else anthropic.APIStatusError
    with pytest.raises(expected):
        run_outage_transport(handler)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [408, 409, 429, 529])
def test_provider_retry_false_vetoes_transient_status(status: int):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        return httpx2.Response(
            status,
            headers={"x-should-retry": "false"},
            json=api_error("api_error", "private-body"),
        )

    with pytest.raises(Exception):
        run_outage_transport(handler)
    assert len(calls) == 1


@pytest.mark.parametrize("attempts", [1, 2, 4])
def test_attempt_setting_counts_original_attempt(
    monkeypatch: pytest.MonkeyPatch, attempts: int
):
    monkeypatch.setenv("GROUNDLINE_MODEL_MAX_ATTEMPTS", str(attempts))
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        return httpx2.Response(529, json=api_error("overloaded_error", "private-body"))

    with pytest.raises(Exception):
        run_outage_transport(handler)
    assert len(calls) == attempts


@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ReadTimeout])
def test_connection_and_timeout_failures_can_recover(
    failure: type[httpx2.RequestError],
):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        if len(calls) == 1:
            raise failure("private-transport-message", request=request)
        return httpx2.Response(200, json=message())

    assert run_outage_transport(handler).output_tokens == 340
    assert len(calls) == 2
    assert calls[-1].headers.get("x-stainless-retry-count", "0") == "0"


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "3600"},
        {"retry-after-ms": "3600000"},
        {"retry-after-ms": "invalid", "Retry-After": "3600"},
    ],
)
def test_retry_after_that_cannot_fit_stops_immediately(headers: dict[str, str]):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        return httpx2.Response(
            529, headers=headers, json=api_error("overloaded_error", "private-body")
        )

    async def run():
        # A watchdog makes a regression fail without waiting for the header.
        client = AnthropicClient(
            "dummy",
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )
        try:
            task = asyncio.create_task(
                client.complete(
                    "private-prompt",
                    model="claude-opus-5-5",
                    effort="medium",
                    max_tokens=100,
                )
            )
            done, _ = await asyncio.wait({task}, timeout=3)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert done, "Retry-After cannot fit: must stop without sleeping"
            assert task.exception() is not None
            assert len(calls) == 1
        finally:
            await client.close()

    asyncio.run(run())


def test_retry_logs_have_metadata_without_private_content(
    caplog: pytest.LogCaptureFixture,
):
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        calls.append(request)
        return (
            httpx2.Response(529, json=api_error("overloaded_error", "private-body"))
            if len(calls) == 1
            else httpx2.Response(200, json=message())
        )

    with caplog.at_level(logging.DEBUG, logger="groundline"):
        run_outage_transport(handler)
    records = [r for r in caplog.records if r.name.startswith("groundline")]
    assert records, "groundline must record safe retry metadata"
    text = " ".join(r.getMessage() for r in records)
    assert "529" in text
    assert "attempt" in text.lower()
    assert "private-prompt" not in text
    assert "private-body" not in text
    assert "dummy-key" not in text


@pytest.mark.parametrize("budget,expected_attempts", [(0.5, 2), (2, 3)])
def test_actual_hung_transport_is_cancelled_within_attempt_and_retry_budgets(
    budget: float, expected_attempts: int, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GROUNDLINE_MODEL_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setenv("GROUNDLINE_MODEL_RETRY_TIMEOUT_SECONDS", str(budget))
    monkeypatch.setenv("GROUNDLINE_MODEL_RETRY_BASE_SECONDS", "0.0001")
    monkeypatch.setenv("GROUNDLINE_MODEL_RETRY_CAP_SECONDS", "0.0001")

    async def run():
        calls: list[httpx2.Request] = []
        cancelled: list[bool] = []

        async def handler(request: httpx2.Request):
            calls.append(request)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)
            raise AssertionError("hung transport returned")

        client = AnthropicClient(
            "dummy",
            http_client=DefaultAsyncHttpxClient(
                transport=httpx2.MockTransport(handler)
            ),
        )
        try:
            task = asyncio.create_task(
                client.complete(
                    "a", model="claude-opus-5-5", effort="medium", max_tokens=100
                )
            )
            done, _ = await asyncio.wait({task}, timeout=4)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert done, "transport inactivity timeout alone cannot bound a hung call"
            assert task.exception() is not None
            assert len(calls) == len(cancelled) == expected_attempts
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "headers,minimum",
    [
        ({"Retry-After": "3"}, 3),
        ({"retry-after-ms": "250", "Retry-After": "3"}, 0.25),
        ({"retry-after-ms": "bad", "Retry-After": "3"}, 3),
        ({"retry-after-ms": "-1", "Retry-After": "3"}, 3),
        ({"retry-after-ms": "nan", "Retry-After": "3"}, 3),
        ({"Retry-After": "Thu, 01 Jan 1970 00:16:43 GMT"}, 3),
        ({"Retry-After": "nan"}, None),
        ({"Retry-After": "-1"}, None),
    ],
)
def test_header_translation_uses_explicit_wall_clock(
    headers: dict[str, str], minimum: float | None
):
    from groundline.clients import anthropic as adapter

    decide = getattr(adapter, "retry_decision", None)
    assert callable(decide), (
        "adapter must translate headers independently of scheduling"
    )
    error = anthropic.APIStatusError(
        "private",
        response=httpx2.Response(
            529, headers=headers, request=httpx2.Request("POST", "https://test")
        ),
        body=None,
    )
    retryable, wait = cast(tuple[bool, float | None], decide(error, now=1000))
    assert retryable is True
    assert wait == minimum


def test_configured_connection_timeout_reaches_transport(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GROUNDLINE_MODEL_CONNECT_TIMEOUT_SECONDS", "0.7")
    monkeypatch.setenv("GROUNDLINE_MODEL_TIMEOUT_SECONDS", "2")
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request):
        requests.append(request)
        return httpx2.Response(200, json=message())

    run_outage_transport(handler)
    assert requests[0].extensions["timeout"]["connect"] == 0.7
    assert requests[0].extensions["timeout"]["read"] <= 2
