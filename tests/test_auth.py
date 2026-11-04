import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from groundline import app as app_module
from groundline.api_keys import APIKeyInfo, APIKeys, APIKeyStorageError
from groundline.db.connection import make_engine
from tests.auth_helpers import protected_app

SECRET = "gl_" + "a" * 43


class FakeKeys(APIKeys):
    def __init__(self):
        self.calls: list[str] = []
        self.failure: Exception | None = None
        self.info: APIKeyInfo | None = APIKeyInfo(
            id=UUID("00000000-0000-7000-8000-000000000001"),
            name="test",
            prefix=SECRET[:11],
            status="active",
            created_at=datetime(2026, 11, 4, tzinfo=UTC),
            expires_at=None,
            revoked_at=None,
        )

    async def authenticate(self, credential: str) -> APIKeyInfo | None:
        self.calls.append(credential)
        if self.failure:
            raise self.failure
        return self.info


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)


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
def test_missing_or_malformed_credentials_never_touch_storage(header: str | None):
    keys = FakeKeys()
    app = protected_app(with_lifespan=False)
    app.state.api_keys = keys
    with TestClient(app) as client:
        response = client.get(
            "/protected", headers={"Authorization": header} if header else {}
        )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Invalid or missing API key"}
    assert keys.calls == []


def test_success_returns_only_metadata_and_rechecks_every_request():
    keys = FakeKeys()
    app = protected_app(with_lifespan=False)
    app.state.api_keys = keys
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get(
                "/protected", headers={"Authorization": f"Bearer {SECRET}"}
            )
            assert response.status_code == 200
            assert keys.info is not None
            assert response.json() == {"api_key_id": str(keys.info.id)}
            assert SECRET not in response.text
        keys.info = None
        response = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid or missing API key"}
    assert keys.calls == [SECRET] * 3


@pytest.mark.parametrize(
    "failure", [APIKeyStorageError("driver-private"), RuntimeError(SECRET)]
)
def test_storage_and_unexpected_errors_fail_closed_without_details(
    failure: Exception,
    caplog: pytest.LogCaptureFixture,
):
    keys = FakeKeys()
    keys.failure = failure
    app = protected_app(with_lifespan=False)
    app.state.api_keys = keys
    with TestClient(app) as client:
        response = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is unavailable"}
    assert SECRET not in response.text + caplog.text
    assert "driver-private" not in response.text + caplog.text


def test_missing_database_keeps_health_public_and_protected_routes_unavailable():
    with TestClient(protected_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/protected").status_code == 401
        response = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
        assert response.status_code == 503


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://gl:pw-secret@:x/db",
        "postgresql+wrong://gl:pw-secret@localhost/db",
        "postgresql+psycopg://gl:pw-secret@127.0.0.1:1/db",
    ],
)
def test_unusable_database_never_breaks_public_health_or_leaks_passwords(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv("DATABASE_URL", url)
    with (
        caplog.at_level(logging.INFO, logger="groundline"),
        TestClient(protected_app()) as client,
    ):
        assert client.get("/health").status_code == 200
        response = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
        assert response.status_code == 503
    assert "pw-secret" not in caplog.text + response.text


def test_lifespan_disposes_the_engine_and_clears_the_service(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://gl:pw@127.0.0.1:1/db")
    disposed: list[bool] = []

    def factory(raw: str) -> AsyncEngine:
        engine = make_engine(raw, timeout=1)

        def on_disposed(_engine: object) -> None:
            disposed.append(True)

        event.listen(engine.sync_engine, "engine_disposed", on_disposed)
        return engine

    monkeypatch.setattr(app_module, "make_engine", factory)
    app = protected_app()
    with TestClient(app):
        assert isinstance(app.state.api_keys, APIKeys)
    assert app.state.api_keys is None
    assert disposed == [True]


def test_openapi_advertises_bearer_security():
    schema = protected_app().openapi()
    assert schema["paths"]["/protected"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health"]["get"]
