"""The Anthropic client, tested against a fake HTTP transport: no network calls."""

import asyncio
import json
from typing import Any

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from groundline_core.clients.anthropic import AnthropicClient
from groundline_core.core import (
    APIKeyRejectedError,
    ModelError,
    ModelRefusal,
    ModelReply,
)
from tests.anthropic_helpers import api_error, message


def complete(
    reply: dict[str, Any], status: int = 200, **options: Any
) -> tuple[ModelReply, httpx2.Request]:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(status, json=reply)

    async def go() -> ModelReply:
        client = AnthropicClient(
            "sk-test",
            key_source="/repo/.env",
            **options,
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


def test_a_rejected_key_is_named_as_the_caller_says():
    with pytest.raises(APIKeyRejectedError) as error:
        complete(
            api_error("authentication_error", "invalid x-api-key"),
            status=401,
            key_name="The Anthropic API key",
        )

    assert str(error.value).startswith(
        "The Anthropic API key was rejected by the Anthropic API"
    )


def test_a_key_without_access_to_the_model_gets_a_clear_message():
    with pytest.raises(
        APIKeyRejectedError, match="doesn't have access to claude-opus-5-5"
    ):
        complete(api_error("permission_error", "not allowed"), status=403)
