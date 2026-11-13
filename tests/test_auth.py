import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from groundline import app as app_module
from groundline.api_keys import APIKeys, APIKeyStorageError
from groundline.db.connection import make_engine
from tests.auth_helpers import SECRET, FakeKeys, protected_app


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
    records = [record for record in caplog.records if record.name == "groundline.auth"]
    assert len(records) == 1
    assert records[0].getMessage() == (
        f"API-key authentication is unavailable: {type(failure).__name__}"
    )
    assert records[0].exc_info is None


def test_missing_database_keeps_health_public_and_protected_routes_unavailable():
    with TestClient(protected_app()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/protected").status_code == 401
        response = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
        assert response.status_code == 503


def test_unreadable_settings_keep_health_public_and_auth_unavailable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    (tmp_path / ".env").write_bytes(b"DATABASE_URL=\xff\xfe" + SECRET.encode())
    app = protected_app()
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        missing = client.get("/protected")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        unavailable = client.get(
            "/protected", headers={"Authorization": f"Bearer {SECRET}"}
        )
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "Authentication is unavailable"}
        assert app.state.api_keys is None
    assert "can't read the settings (UnicodeDecodeError)" in caplog.text
    assert SECRET not in caplog.text + unavailable.text + missing.text


@pytest.mark.parametrize(
    ("url", "diagnostic"),
    [
        (
            "postgresql://gl:pw-secret@:x/db",
            "DATABASE_URL is not a usable PostgreSQL URL",
        ),
        (
            "postgresql+wrong://gl:pw-secret@localhost/db",
            "DATABASE_URL must use postgresql+psycopg://",
        ),
        (
            "postgresql+psycopg://gl:pw-secret@127.0.0.1:1/db",
            "can't connect to the database at 127.0.0.1:1/db",
        ),
    ],
)
def test_unusable_database_never_breaks_public_health_or_leaks_passwords(
    url: str,
    diagnostic: str,
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
    assert diagnostic in caplog.text


@pytest.mark.parametrize("probe_crashes", [False, True])
def test_lifespan_disposes_the_engine_and_clears_the_service(
    monkeypatch: pytest.MonkeyPatch,
    probe_crashes: bool,
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
    if probe_crashes:
        monkeypatch.setattr(
            app_module, "check_connection", AsyncMock(side_effect=RuntimeError("probe"))
        )
        with pytest.raises(RuntimeError, match="probe"), TestClient(app):
            pytest.fail("A crashed probe must not complete startup")
    else:
        with TestClient(app):
            assert isinstance(app.state.api_keys, APIKeys)
            assert disposed == []
    assert app.state.api_keys is None
    assert disposed == [True]


def test_openapi_advertises_bearer_security():
    schema = protected_app().openapi()
    assert schema["paths"]["/protected"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in schema["paths"]["/health"]["get"]


@pytest.mark.parametrize("boundary", ["transaction", "authentication"])
def test_cleanup_error_does_not_replace_external_cancellation(
    boundary: str, monkeypatch: pytest.MonkeyPatch
):
    """Cleanup failures are synthetic; this does not simulate a lost commit."""
    import asyncio
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager
    from typing import cast

    from fastapi import Request
    from fastapi.security import HTTPAuthorizationCredentials
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import AsyncConnection

    from groundline.api_keys import APIKeyInfo
    from groundline.auth import require_api_key

    for name in ("AUTH", "CONNECT", "CHECK", "POOL", "QUERY"):
        monkeypatch.setenv(f"GROUNDLINE_DB_{name}_TIMEOUT_SECONDS", "5")

    async def run():
        entered = asyncio.Event()
        attempts = 0

        async def hang():
            nonlocal attempts
            attempts += 1
            entered.set()
            await asyncio.Event().wait()

        class Connection:
            async def execute(self, statement: object):
                await hang()

        class Engine:
            @asynccontextmanager
            async def begin(self) -> AsyncGenerator[AsyncConnection]:
                try:
                    yield cast(AsyncConnection, Connection())
                finally:
                    raise SQLAlchemyError("private rollback failure")

        class NoisyKeys(FakeKeys):
            async def authenticate(self, credential: str) -> APIKeyInfo | None:
                try:
                    await hang()
                finally:
                    raise RuntimeError("private cleanup failure")

        if boundary == "transaction":
            keys = APIKeys(cast(AsyncEngine, Engine()))
            task = asyncio.create_task(keys.authenticate(SECRET))
        else:
            app = protected_app(with_lifespan=False)
            app.state.api_keys = NoisyKeys()
            task = asyncio.create_task(
                require_api_key(
                    Request({"type": "http", "app": app}),
                    HTTPAuthorizationCredentials(scheme="Bearer", credentials=SECRET),
                )
            )
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()
            assert attempts == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
