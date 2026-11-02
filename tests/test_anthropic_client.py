"""The Anthropic client, tested against a fake HTTP transport: no network calls."""

import asyncio
import json
from typing import Any

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from groundline.clients.anthropic import AnthropicClient
from groundline.core import ModelError, ModelRefusal, ModelReply


def message(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": '{"facts": []}'},
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 340},
        **overrides,
    }


def complete(reply: dict[str, Any]) -> tuple[ModelReply, httpx2.Request]:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=reply)

    async def go() -> ModelReply:
        client = AnthropicClient(
            "sk-test",
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

    with pytest.raises(ModelRefusal, match="declined to answer.*bio"):
        complete(refusal)


def test_a_reply_cut_off_at_max_tokens_is_an_error():
    with pytest.raises(ModelError, match="max_tokens"):
        complete(message(stop_reason="max_tokens"))
