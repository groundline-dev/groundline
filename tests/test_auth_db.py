"""Revocation through the CLI must affect the already-running API process."""

import logging
import re
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from psycopg import OperationalError
from sqlalchemy import Engine, event, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool
from typer.testing import CliRunner

from groundline import app as app_module
from groundline.cli import app as cli
from groundline.db import api_keys
from groundline.db.connection import make_engine
from groundline.settings import Settings
from tests.async_helpers import finish_before_watchdog
from tests.auth_helpers import protected_app

pytestmark = pytest.mark.db
runner = CliRunner()


def test_startup_probe_uses_auth_engine_and_recovers_after_connection_refusal(
    db_url: str,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    created = runner.invoke(cli, ["keys", "create"])
    assert created.exit_code == 0
    match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
    assert match is not None
    headers = {"Authorization": f"Bearer {match[1]}"}
    refusing = True
    attempts: list[bool] = []
    disposed: list[bool] = []

    def factory(raw: str) -> AsyncEngine:
        engine = make_engine(raw)

        def connect(
            _dialect: object,
            _record: object,
            _args: list[object],
            params: dict[str, object],
        ) -> None:
            assert params["target_session_attrs"] == "read-write"
            attempts.append(refusing)
            if refusing:
                # Simulate a refused read-only target; do not require a replica.
                raise OperationalError("session is read-only")

        def on_disposed(_engine: object) -> None:
            disposed.append(True)

        event.listen(engine.sync_engine, "do_connect", connect)
        event.listen(engine.sync_engine, "engine_disposed", on_disposed)
        return engine

    monkeypatch.setattr(app_module, "make_engine", factory)
    with (
        caplog.at_level(logging.INFO, logger="groundline"),
        TestClient(protected_app()) as client,
    ):
        assert attempts == [True]  # Startup must probe this engine.
        assert "session is read-only" in caplog.text
        assert "connected to the database" not in caplog.text
        assert disposed == []
        assert client.get("/health").status_code == 200
        assert client.get("/protected", headers=headers).status_code == 503
        refusing = False
        response = client.get("/protected", headers=headers)
        assert response.status_code == 200
        with db_engine.connect() as connection:
            identifier = connection.scalar(select(api_keys.c.id))
        assert response.json() == {"api_key_id": str(identifier)}
        assert attempts == [True, True, False]
        assert disposed == []
    assert disposed == [True]


def test_cli_revocation_and_expiry_are_seen_by_running_api_instances(
    db_url: str,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", db_url)
    created = runner.invoke(cli, ["keys", "create"])
    assert created.exit_code == 0, created.stderr
    match = re.search(r"API key: (gl_[A-Za-z0-9_-]{43})", created.stdout)
    assert match is not None
    headers = {"Authorization": f"Bearer {match[1]}"}
    with db_engine.connect() as connection:
        identifier = connection.scalar(select(api_keys.c.id))
    # Separate application lifespans/engines model workers reading the same DB.
    with TestClient(protected_app()) as first, TestClient(protected_app()) as second:
        for client in (first, second):
            assert client.get("/protected", headers=headers).status_code == 200
        with db_engine.begin() as connection:
            connection.execute(update(api_keys).values(expires_at=func.now()))
        for client in (first, second):
            response = client.get("/protected", headers=headers)
            assert response.status_code == 401
            assert response.json() == {"detail": "Invalid or missing API key"}
        with db_engine.begin() as connection:
            connection.execute(update(api_keys).values(expires_at=None))
        assert first.get("/protected", headers=headers).status_code == 200
        revoked = runner.invoke(cli, ["keys", "revoke", str(identifier)])
        assert revoked.exit_code == 0, revoked.stderr
        for client in (first, second):
            response = client.get("/protected", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert client.get("/health").status_code == 200
        unknown = first.get(
            "/protected", headers={"Authorization": "Bearer gl_" + "z" * 43}
        )
        assert unknown.status_code == 401
        assert unknown.json() == {"detail": "Invalid or missing API key"}
        assert unknown.headers["www-authenticate"] == "Bearer"
    with db_engine.connect() as connection:
        assert connection.scalar(select(api_keys.c.last_used_at)) is None


def test_exhausted_pool_is_bounded_and_recovers(
    db_url: str, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    from groundline.api_keys import APIKeys, APIKeyStorageError
    from groundline.db import connection as connection_module

    monkeypatch.setattr(Settings, "db_pool_timeout_seconds", 0.3)

    def factory(*args: Any, **kwargs: Any):
        return create_async_engine(*args, **kwargs, pool_size=1, max_overflow=0)

    monkeypatch.setattr(connection_module, "create_async_engine", factory)

    async def run():
        engine = make_engine(db_url)
        try:
            keys = APIKeys(engine)
            created = await keys.create(name="pool-test")
            async with engine.connect():
                with pytest.raises(APIKeyStorageError):
                    await finish_before_watchdog(
                        keys.authenticate(created.secret.get_secret_value())
                    )
            assert (
                await keys.authenticate(created.secret.get_secret_value()) is not None
            )
            assert cast(AsyncAdaptedQueuePool, engine.pool).checkedout() == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_timed_out_query_releases_connection_for_next_authentication(
    db_url: str, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    import asyncio

    from groundline.api_keys import APIKeys, APIKeyStorageError

    monkeypatch.setattr(Settings, "db_query_timeout_seconds", 0.3)

    async def run():
        engine = make_engine(db_url)
        keys = APIKeys(engine)
        try:
            created = await keys.create(name="timeout-test")
            statements: list[str] = []

            def slow_query(
                connection: Any,
                cursor: Any,
                statement: str,
                parameters: Any,
                context: Any,
                many: bool,
            ):
                statements.append(statement)
                return "SELECT pg_sleep(10)", ()

            event.listen(
                engine.sync_engine, "before_cursor_execute", slow_query, retval=True
            )
            try:
                with pytest.raises(APIKeyStorageError):
                    await finish_before_watchdog(
                        keys.authenticate(created.secret.get_secret_value())
                    )
                assert len(statements) == 1
            finally:
                event.remove(engine.sync_engine, "before_cursor_execute", slow_query)
            assert (
                await keys.authenticate(created.secret.get_secret_value()) is not None
            )
            assert cast(AsyncAdaptedQueuePool, engine.pool).checkedout() == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_write_lock_wait_times_out_once_and_pool_recovers(
    db_url: str, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    import asyncio

    from sqlalchemy import text

    from groundline.api_keys import APIKeys, APIKeyStorageError

    monkeypatch.setattr(Settings, "db_query_timeout_seconds", 0.3)

    async def run():
        engine = make_engine(db_url)
        keys = APIKeys(engine)
        try:
            created = await keys.create(name="lock-test")
            updates: list[str] = []

            def capture(
                connection: Any,
                cursor: Any,
                statement: str,
                parameters: Any,
                context: Any,
                many: bool,
            ):
                if statement.lstrip().upper().startswith("UPDATE"):
                    updates.append(statement)

            event.listen(engine.sync_engine, "before_cursor_execute", capture)
            with db_engine.begin() as blocker:
                blocker.execute(text("SELECT id FROM api_keys FOR UPDATE"))
                with pytest.raises(APIKeyStorageError):
                    await finish_before_watchdog(keys.revoke(created.info.id))
                assert len(updates) == 1
            info = await keys.authenticate(created.secret.get_secret_value())
            assert info is not None and info.status == "active"
            assert cast(AsyncAdaptedQueuePool, engine.pool).checkedout() == 0
        finally:
            await engine.dispose()

    asyncio.run(run())
