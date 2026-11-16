"""Anthropic HTTP envelopes shared by transport-level tests."""

import importlib
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient

from groundline_core.clients.anthropic import AnthropicClient


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


def api_error(kind: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": kind, "message": message}}


def fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, replies: Callable[[str], str | httpx2.Response]
) -> list[httpx2.Request]:
    """Make `evaluate` build its real Anthropic client on a fake transport that
    answers each prompt with `replies(prompt)`: a reply's text, or a whole HTTP
    response such as an error. Returns the requests sent, so a test can see
    which key was used: nothing reaches the real API."""
    sent: list[httpx2.Request] = []
    evaluate_module = importlib.import_module("groundline_core.evaluate")
    real = evaluate_module.AnthropicClient

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        prompt = json.loads(request.content)["messages"][0]["content"]
        reply = replies(prompt)
        if isinstance(reply, httpx2.Response):
            return reply
        return httpx2.Response(
            200, json=message(content=[{"type": "text", "text": reply}])
        )

    def make(api_key: str, **options: Any) -> AnthropicClient:
        transport = httpx2.MockTransport(handler)
        http_client = DefaultAsyncHttpxClient(transport=transport)
        return real(api_key, http_client=http_client, **options)

    monkeypatch.setattr(evaluate_module, "AnthropicClient", make)
    return sent


def keys_sent(sent: list[httpx2.Request]) -> set[str]:
    """The Anthropic API keys the requests carried. The SDK sends the key as a
    Bearer token."""
    return {
        request.headers["authorization"].removeprefix("Bearer ") for request in sent
    }
