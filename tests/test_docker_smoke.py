"""Exercise the Compose model double over HTTP without needing Docker."""

import json
import socket
from collections.abc import Iterator
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from tests import docker_smoke as smoke
from tests.api_integration_helpers import http as http
from tests.api_integration_helpers import isolated as isolated
from tests.api_integration_helpers import keys as keys


@pytest.fixture
def model_service(
    isolated: set[tuple[str, int]], monkeypatch: pytest.MonkeyPatch
) -> Iterator[smoke.MockAnthropic]:
    with smoke.MockAnthropic(("127.0.0.1", 0)) as server:
        isolated.add(("127.0.0.1", server.server_port))
        monkeypatch.setenv(
            "ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_port}"
        )
        thread = Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_smoke_scripts_work_through_real_routes_settings_and_sdk(
    model_service: smoke.MockAnthropic,
    http: TestClient,
):
    for endpoint in ("omissions", "commissions"):
        response = http.post(
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
        json.dumps(
            {
                "model": smoke.MODEL_SETTINGS["omissions"]["extract"]["model"],
                "messages": [{"content": prompt}],
            }
        ),
    )
    assert status == 400
    assert body["type"] == "error"
    assert len(model_service.errors) == 1


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
@pytest.mark.parametrize("host", ["127.0.0.1", "192.0.2.1"])
def test_http_smoke_blocks_connections_outside_the_mock(
    model_service: smoke.MockAnthropic, method: str, host: str
):
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="blocked network connection"):
            getattr(sock, method)((host, 1))


def test_non_json_error_reports_status_and_body(
    model_service: smoke.MockAnthropic, monkeypatch: pytest.MonkeyPatch
):
    def unavailable(handler: smoke.Handler) -> None:
        handler.send_response(503)
        handler.end_headers()
        handler.wfile.write(b"upstream temporarily unavailable")

    monkeypatch.setattr(smoke.Handler, "do_GET", unavailable)
    url = f"http://127.0.0.1:{model_service.server_port}/health"
    with pytest.raises(AssertionError) as error:
        smoke.request("GET", url, {}, None)
    message = str(error.value)
    assert f"GET {url}" in message
    assert "HTTP 503" in message
    assert "upstream temporarily unavailable" in message
