"""Authenticate the production omissions route without PostgreSQL or model calls."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from groundline.api import common
from groundline.api import omissions as api
from groundline.api_keys import APIKeyStorageError
from groundline.app import app
from tests.auth_helpers import SECRET, FakeKeys
from tests.fakes import FakeClient
from tests.omission_helpers import SOURCE, SUMMARY, not_flagged_script

http = TestClient(app)
HEADERS = {"Authorization": f"Bearer {SECRET}"}
BODY = {"source": SOURCE, "summary": SUMMARY}


@pytest.fixture(autouse=True)
def keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeKeys:
    monkeypatch.chdir(tmp_path)
    service = FakeKeys()
    monkeypatch.setattr(app.state, "api_keys", service, raising=False)
    return service


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Mock:
    dependency = Mock(return_value=FakeClient(not_flagged_script()))

    # Use a typed, parameterless dependency: Mock's *args are request parameters.
    def provide() -> FakeClient:
        return dependency()

    monkeypatch.setitem(app.dependency_overrides, common.model_client, provide)
    return dependency


@pytest.fixture
def evaluation(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    spy = AsyncMock(wraps=api.evaluate)
    monkeypatch.setattr(api, "evaluate", spy)
    return spy


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
    header: str | None,
    keys: FakeKeys,
    model: Mock,
    evaluation: AsyncMock,
    caplog: pytest.LogCaptureFixture,
):
    response = http.post(
        "/v1/omissions",
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
    keys: FakeKeys, model: Mock, evaluation: AsyncMock
):
    unknown = "gl_" + "z" * 43
    response = http.post(
        "/v1/omissions", json=BODY, headers={"Authorization": f"Bearer {unknown}"}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert keys.calls == [unknown]
    model.assert_not_called()
    evaluation.assert_not_called()


def test_authorized_requests_run_and_authentication_is_rechecked(
    keys: FakeKeys, model: Mock, evaluation: AsyncMock
):
    for _ in range(2):
        response = http.post("/v1/omissions", json=BODY, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["flagged"] is False
        assert body["usage"]["calls"] == 4
        assert body["alarm"] == {"score": 0.0, "threshold": 0.9, "level": "strict"}
        assert SECRET not in response.text
    keys.info = None  # Simulate storage rejecting a previously accepted API key.
    rejected = http.post("/v1/omissions", json=BODY, headers=HEADERS)
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "Invalid or missing API key"}
    assert rejected.headers["www-authenticate"] == "Bearer"
    assert keys.calls == [SECRET] * 3
    assert model.call_count == evaluation.await_count == 2
    assert len(model.return_value.requests) == 8


@pytest.mark.parametrize(
    "failure", [None, APIKeyStorageError(SECRET), RuntimeError(SECRET)]
)
def test_unavailable_authentication_cannot_start_work(
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
    response = http.post("/v1/omissions", json=BODY, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is unavailable"}
    model.assert_not_called()
    evaluation.assert_not_called()
    assert SECRET not in response.text + caplog.text


@pytest.mark.parametrize("authenticated", [False, True])
def test_authentication_precedes_field_validation(
    authenticated: bool, model: Mock, evaluation: AsyncMock
):
    response = http.post(
        "/v1/omissions",
        json={"source": SOURCE},
        headers=HEADERS if authenticated else {},
    )
    assert response.status_code == (422 if authenticated else 401)
    evaluation.assert_not_called()
    assert len(model.return_value.requests) == 0


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("body,status", [(b'{"source":', 422), (b"\xff", 400)])
def test_parsing_errors_precede_authentication(
    authenticated: bool,
    body: bytes,
    status: int,
    keys: FakeKeys,
    model: Mock,
    evaluation: AsyncMock,
):
    response = http.post(
        "/v1/omissions",
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


def test_openapi_protects_omissions_and_documents_authentication_errors():
    schema = app.openapi()
    operation = schema["paths"]["/v1/omissions"]["post"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert "401" in operation["responses"]
    assert "Authentication" in operation["responses"]["503"]["description"]
    assert "security" not in schema["paths"]["/health"]["get"]
    commissions = schema["paths"]["/v1/commissions"]["post"]
    assert "security" not in commissions
    assert "401" not in commissions["responses"]
    assert "Authentication" not in commissions["responses"]["503"]["description"]
