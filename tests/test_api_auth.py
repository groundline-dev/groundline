"""Authenticate the production check routes without PostgreSQL or model calls."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from groundline.api import common
from groundline.api_keys import APIKeyStorageError
from groundline.app import app
from tests.auth_helpers import BODY, HEADERS, SECRET, Endpoint, FakeKeys
from tests.auth_helpers import endpoint as endpoint
from tests.auth_helpers import evaluation as evaluation
from tests.fakes import FakeClient

http = TestClient(app)
PRIVATE = "private-validation-content"


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeKeys:
    monkeypatch.chdir(tmp_path)
    service = FakeKeys()
    monkeypatch.setattr(app.state, "api_keys", service, raising=False)
    return service


@pytest.fixture
def model(endpoint: Endpoint, monkeypatch: pytest.MonkeyPatch) -> Mock:
    dependency = Mock(return_value=FakeClient(endpoint.not_flagged()))

    # Use a typed, parameterless dependency: Mock's *args are request parameters.
    def provide() -> FakeClient:
        return dependency()

    monkeypatch.setitem(app.dependency_overrides, common.model_client, provide)
    return dependency


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic abc",
        "Bearer",
        "Bearer malformed",
        "Bearer " + SECRET + " extra",
    ],
)
def test_rejected_headers_cannot_start_work(
    endpoint: Endpoint,
    header: str | None,
    keys: FakeKeys,
    model: Mock,
    evaluation: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    response = http.post(
        endpoint.path,
        json=BODY,
        headers={"Authorization": header} if header is not None else {},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert keys.calls == []
    model.assert_not_called()
    evaluation.assert_not_called()
    assert SECRET not in response.text + caplog.text


def test_unknown_credentials_are_rejected_after_lookup(
    endpoint: Endpoint, keys: FakeKeys, model: Mock, evaluation: AsyncMock
):
    unknown = "gl_" + "z" * 43
    response = http.post(
        endpoint.path, json=BODY, headers={"Authorization": f"Bearer {unknown}"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert keys.calls == [unknown]
    model.assert_not_called()
    evaluation.assert_not_called()


def test_authorized_requests_run_and_authentication_is_rechecked(
    endpoint: Endpoint, keys: FakeKeys, model: Mock, evaluation: AsyncMock
):
    for _ in range(2):
        response = http.post(endpoint.path, json=BODY, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["flagged"] is False
        assert body["usage"]["calls"] == endpoint.calls
        assert SECRET not in response.text
    keys.info = None  # Simulate storage rejecting a previously accepted API key.
    rejected = http.post(endpoint.path, json=BODY, headers=HEADERS)
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid or missing API key"}
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert keys.calls == [SECRET] * 3
    assert model.call_count == evaluation.await_count == 2
    assert len(model.return_value.requests) == 2 * endpoint.calls


@pytest.mark.parametrize(
    "failure", [None, APIKeyStorageError(SECRET), RuntimeError(SECRET)]
)
def test_unavailable_authentication_cannot_start_work(
    endpoint: Endpoint,
    failure: Exception | None,
    keys: FakeKeys,
    model: Mock,
    evaluation: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    if failure is None:
        app.state.api_keys = None
    else:
        keys.failure = failure
    with (
        caplog.at_level(logging.INFO),
        caplog.at_level(logging.DEBUG, logger="groundline"),
    ):
        response = http.post(
            endpoint.path,
            json={
                "source": BODY["source"] + PRIVATE,
                "summary": BODY["summary"] + PRIVATE,
            },
            headers=HEADERS,
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is unavailable"}
    model.assert_not_called()
    evaluation.assert_not_called()
    for secret in (SECRET, PRIVATE):
        assert secret not in response.text + caplog.text


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
    model: Mock,
    evaluation: AsyncMock,
):
    body: dict[str, object] = dict(BODY)
    max_chars = (
        common.LIMITS.max_source_chars
        if field == "source"
        else common.LIMITS.max_summary_chars
    )
    values: dict[str, object] = {
        "empty": "",
        "blank": " \n ",
        "type": {"text": PRIVATE},
        "too-long": PRIVATE + "x" * max_chars,
    }
    if invalid == "missing":
        del body[field]
    else:
        body[field] = values[invalid]
    response = http.post(
        endpoint.path,
        json=body,
        headers=HEADERS if authenticated else {},
    )
    assert response.status_code == (422 if authenticated else 401)
    if authenticated:
        assert response.json()["detail"][0]["loc"] == ["body", field]
    else:
        model.assert_not_called()
    assert PRIVATE not in response.text
    evaluation.assert_not_called()
    assert len(model.return_value.requests) == 0


@pytest.mark.parametrize(
    "authenticated", [True, False], ids=["active-key", "missing-key"]
)
def test_a_json_array_is_authenticated_before_validation(
    endpoint: Endpoint, authenticated: bool, model: Mock, evaluation: AsyncMock
):
    response = http.post(
        endpoint.path, json=[], headers=HEADERS if authenticated else {}
    )
    assert response.status_code == (422 if authenticated else 401)
    assert "detail" in response.json()
    evaluation.assert_not_called()
    assert model.return_value.requests == []


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("body,status", [(b'{"source":', 422), (b"\xff", 400)])
def test_parsing_errors_precede_authentication(
    endpoint: Endpoint,
    authenticated: bool,
    body: bytes,
    status: int,
    keys: FakeKeys,
    model: Mock,
    evaluation: AsyncMock,
):
    response = http.post(
        endpoint.path,
        content=body,
        headers={
            "Content-Type": "application/json",
            **(HEADERS if authenticated else {}),
        },
    )
    assert response.status_code == status
    assert "detail" in response.json()
    assert keys.calls == []
    model.assert_not_called()
    evaluation.assert_not_called()


def test_health_stays_public_when_authentication_is_unavailable(keys: FakeKeys):
    app.state.api_keys = None
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert keys.calls == []


def test_openapi_documents_authentication_errors(endpoint: Endpoint):
    operation = app.openapi()["paths"][endpoint.path]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "401" in operation["responses"]
    assert "Authentication" in operation["responses"]["503"]["description"]


# Probes only (ADR 0012). A public route that takes or returns data needs a
# new ADR, not an entry here.
PUBLIC_ROUTES = {"/health", "/ready"}


def test_openapi_requires_an_api_key_on_every_route_but_public_probes():
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if path in PUBLIC_ROUTES:
                assert "security" not in operation
            else:
                assert operation.get("security") == [{"HTTPBearer": []}], (
                    f"{method.upper()} {path} doesn't require an API key"
                )
