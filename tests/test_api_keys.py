import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast, get_type_hints
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from groundline_core import api_keys
from groundline_core._core.deadline import check_deadline
from groundline_core._settings import Settings
from groundline_core.api_keys import APIKeys, APIKeyStorageError
from tests.async_helpers import finish_before_watchdog


@pytest.fixture
def transaction_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    for name in ("connect", "check", "pool", "query"):
        monkeypatch.setattr(Settings, f"db_{name}_timeout_seconds", 0.5)


def test_query_limit_is_per_statement_and_commit(
    transaction_limits: None,
):
    """Synthetic waits exercise the client boundaries, not PostgreSQL rollback."""
    created_at = datetime(2026, 11, 4, tzinfo=UTC)
    row = {
        "id": UUID("00000000-0000-7000-8000-000000000001"),
        "name": "test",
        "prefix": "gl_test",
        "status": "active",
        "created_at": created_at,
        "expires_at": created_at + timedelta(days=1),
        "revoked_at": None,
    }

    async def run():
        steps: list[str] = []

        class Connection:
            async def scalar(self, statement: object):
                await asyncio.sleep(0.2)
                steps.append("scalar")
                return created_at

            async def execute(self, statement: object):
                await asyncio.sleep(0.2)
                steps.append("insert")
                result = MagicMock()
                result.mappings.return_value.one.return_value = row
                return result

        class Engine:
            @asynccontextmanager
            async def begin(self):
                yield Connection()
                await asyncio.sleep(0.2)
                steps.append("commit")

        created = await finish_before_watchdog(
            APIKeys(cast(AsyncEngine, Engine())).create(
                name="test", expires_in=timedelta(days=1)
            )
        )
        assert created.info.id == row["id"]
        assert steps == ["scalar", "insert", "commit"]

    asyncio.run(run())


@pytest.mark.parametrize("hang_at", ["acquire", "query", "commit", "deadline"])
def test_transaction_timeouts_are_identified_without_replay(
    hang_at: str, transaction_limits: None, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(Settings, "db_query_timeout_seconds", 0.3)

    async def run():
        attempts = 0
        cancelled: list[str] = []
        cleanups = 0

        async def hang(where: str):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(where)

        class Connection:
            async def execute(self, statement: object):
                if hang_at in ("query", "deadline"):
                    await hang(hang_at)
                result = MagicMock()
                result.mappings.return_value.__iter__.return_value = []
                return result

        class Engine:
            @asynccontextmanager
            async def begin(self):
                nonlocal attempts, cleanups
                attempts += 1
                try:
                    if hang_at == "acquire":
                        await hang(hang_at)
                    yield Connection()
                    if hang_at == "commit":
                        await hang(hang_at)
                finally:
                    cleanups += 1

        async def operation():
            with check_deadline(0.1 if hang_at == "deadline" else 120):
                return await APIKeys(cast(AsyncEngine, Engine())).list_all()

        with pytest.raises(APIKeyStorageError) as error:
            await finish_before_watchdog(operation())
        assert str(error.value) == "API-key operation timed out"
        assert error.value.__suppress_context__
        assert attempts == cleanups == 1
        assert cancelled == [hang_at]

    asyncio.run(run())


def test_listing_return_annotation_can_be_evaluated():
    assert get_type_hints(APIKeys.list_all)["return"] == list[api_keys.APIKeyInfo]


def test_generation_requests_256_random_bits_and_hides_the_secret(
    monkeypatch: pytest.MonkeyPatch,
):
    requested: list[int | None] = []

    def token_urlsafe(nbytes: int | None = None) -> str:
        requested.append(nbytes)
        return "a" * 43

    monkeypatch.setattr(api_keys.secrets, "token_urlsafe", token_urlsafe)
    secret = api_keys.generate_api_key()
    assert requested == [32]
    assert secret.get_secret_value() == "gl_" + "a" * 43
    assert secret.get_secret_value() not in repr(secret)


def test_real_generated_credentials_match_the_public_format():
    secret = api_keys.generate_api_key().get_secret_value()
    assert len(secret) >= 32
    assert api_keys.valid_api_key_format(secret)


def test_generated_names_and_custom_names():
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9a-f]{8}", api_keys.api_key_name(None))
    assert api_keys.api_key_name("  Avi's laptop  ") == "Avi's laptop"


def test_name_length_is_checked_after_trimming():
    assert api_keys.api_key_name("  " + "é" * 128 + "  ") == "é" * 128
    with pytest.raises(ValueError, match="at most 128"):
        api_keys.api_key_name("  " + "é" * 129 + "  ")


@pytest.mark.parametrize("name", ["", "  ", "a\nb", "a\r", "\tlabel", "a\x1bb"])
def test_invalid_names_are_rejected(name: str):
    with pytest.raises(ValueError, match="name"):
        api_keys.api_key_name(name)


def test_invalid_inputs_do_not_need_storage():
    async def run():
        engine = create_async_engine("postgresql+psycopg://user:secret@127.0.0.1:1/db")
        try:
            service = APIKeys(engine)
            assert await service.authenticate("malformed-secret") is None
            for lifetime in (timedelta(0), timedelta(seconds=-1)):
                with pytest.raises(ValueError, match="positive"):
                    await service.create(expires_in=lifetime)
            with pytest.raises(ValueError, match="name"):
                await service.create(name="\n")
            with pytest.raises(ValueError, match="at most 128"):
                await service.create(name="x" * 129)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_connection_failure_is_sanitized():
    async def run():
        engine = create_async_engine(
            "postgresql+psycopg://user:password-secret@127.0.0.1:1/db",
            connect_args={"connect_timeout": 1},
        )
        try:
            with pytest.raises(APIKeyStorageError) as error:
                await APIKeys(engine).create()
            assert str(error.value) == "API-key storage is unavailable"
            assert error.value.__suppress_context__
        finally:
            await engine.dispose()

    asyncio.run(run())
