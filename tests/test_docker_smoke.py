"""Exercise the Compose model double over HTTP without needing Docker."""

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from groundline.app import app
from tests import docker_smoke as smoke
from tests.auth_helpers import HEADERS, FakeKeys


@pytest.fixture
def model_service() -> Iterator[smoke.MockAnthropic]:
    with smoke.MockAnthropic(("127.0.0.1", 0)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_smoke_scripts_work_through_real_routes_settings_and_sdk(
    model_service: smoke.MockAnthropic,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "dependency_overrides", {})
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", smoke.ANTHROPIC_KEY)
    monkeypatch.setenv(
        "ANTHROPIC_BASE_URL", f"http://127.0.0.1:{model_service.server_port}"
    )
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    client = TestClient(app, headers=HEADERS)

    for endpoint in ("omissions", "commissions"):
        response = client.post(
            f"/v1/{endpoint}",
            json={"source": smoke.SOURCE, "summary": smoke.SUMMARY},
        )
        assert response.status_code == 200, response.text
        smoke.verify_evaluation(endpoint, response.json())

    status, body = smoke.request(
        "GET", f"http://127.0.0.1:{model_service.server_port}/requests", {}, None
    )
    assert status == 200
    assert body == {"calls": smoke.EXPECTED_CALLS, "errors": []}


@pytest.mark.parametrize("failure", ["credential", "prompt", "extra-call"])
def test_mock_rejects_unexpected_requests_instead_of_returning_success(
    model_service: smoke.MockAnthropic, failure: str
):
    headers = {"Authorization": f"Bearer {smoke.ANTHROPIC_KEY}"}
    prompt = f"<transcript>{smoke.SOURCE}</transcript>"
    if failure == "credential":
        headers = {}
    elif failure == "prompt":
        prompt = "unknown stage"
    else:
        model_service.calls["extract"] = 1
    status, body = smoke.request(
        "POST",
        f"http://127.0.0.1:{model_service.server_port}/v1/messages",
        headers,
        json.dumps({"model": "claude-opus-5-5", "messages": [{"content": prompt}]}),
    )
    assert status == 400
    assert body["type"] == "error"
    assert len(model_service.errors) == 1
