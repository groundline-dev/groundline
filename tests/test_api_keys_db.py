"""API-key contracts against PostgreSQL, including transaction failures."""

import asyncio
import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Connection, Engine, event, func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from groundline.api_keys import APIKeyNotFoundError, APIKeys, APIKeyStorageError
from groundline.db import api_keys

pytestmark = pytest.mark.db


@asynccontextmanager
async def service(db_url: str) -> AsyncGenerator[APIKeys]:
    engine = create_async_engine(db_url, hide_parameters=True)
    try:
        yield APIKeys(engine)
    finally:
        await engine.dispose()


def test_creation_persists_only_hash_and_metadata(db_url: str, db_engine: Engine):
    async def run():
        engine = create_async_engine(db_url)
        sent: list[str] = []

        def capture(
            _connection: Connection,
            _cursor: object,
            statement: str,
            parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            sent.append(statement + repr(parameters))

        event.listen(engine.sync_engine, "before_cursor_execute", capture)
        try:
            created = await APIKeys(engine).create(name="  laptop  ")
            credential = created.secret.get_secret_value()
            # A separate connection sees the committed row before create returns.
            with db_engine.connect() as connection:
                row = connection.execute(select(api_keys)).mappings().one()
            assert row["key_hash"] == hashlib.sha256(credential.encode()).hexdigest()
            assert row["prefix"] == credential[:11]
            assert created.info.id.version == 7
            assert created.info.name == "laptop"
            assert created.info.status == "active"
            assert created.info.revoked_at is None
            assert created.info.expires_at is None
            assert row["last_used_at"] is None
            assert credential not in repr(created)
            assert credential not in repr(dict(row))
            assert credential not in " ".join(sent)
            assert "key_hash" not in created.info.model_dump()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_custom_names_can_repeat(db_url: str, db_engine: Engine):
    async def run():
        async with service(db_url) as keys:
            first = await keys.create(name="laptop")
            second = await keys.create(name="laptop")
            assert first.info.id != second.info.id
            assert first.info.name == second.info.name

    asyncio.run(run())


def test_revocation_is_immediate_and_idempotent(db_url: str, db_engine: Engine):
    async def run():
        async with service(db_url) as keys:
            created = await keys.create()
            credential = created.secret.get_secret_value()
            assert await keys.authenticate(credential) == created.info
            first = await keys.revoke(created.info.id)
            assert first.changed
            assert first.info.status == "revoked"
            assert first.info.revoked_at is not None
            assert await keys.authenticate(credential) is None
            again = await keys.revoke(created.info.id)
            assert not again.changed
            assert again.info.revoked_at == first.info.revoked_at
            with db_engine.connect() as connection:
                assert connection.scalar(select(api_keys.c.last_used_at)) is None

    asyncio.run(run())


def test_concurrent_revocations_preserve_the_first_timestamp(
    db_url: str,
    db_engine: Engine,
):
    async def run():
        async with service(db_url) as keys:
            created = await keys.create()
            outcomes = await asyncio.gather(
                keys.revoke(created.info.id), keys.revoke(created.info.id)
            )
            assert sum(outcome.changed for outcome in outcomes) == 1
            assert outcomes[0].info.revoked_at == outcomes[1].info.revoked_at

    asyncio.run(run())


def test_unknown_identifier_and_unknown_credential(db_url: str, db_engine: Engine):
    async def run():
        async with service(db_url) as keys:
            with pytest.raises(APIKeyNotFoundError):
                await keys.revoke(uuid4())
            assert await keys.authenticate("gl_" + "z" * 43) is None

    asyncio.run(run())


def test_expiry_uses_creation_time_and_is_checked_on_every_request(
    db_url: str,
    db_engine: Engine,
):
    async def run():
        async with service(db_url) as keys:
            created = await keys.create(expires_in=timedelta(days=90))
            assert created.info.expires_at == created.info.created_at + timedelta(
                days=90
            )
            credential = created.secret.get_secret_value()
            assert await keys.authenticate(credential) is not None
            # No sleep or clock approximation: set an exact boundary in the DB.
            with db_engine.begin() as connection:
                connection.execute(update(api_keys).values(expires_at=func.now()))
            assert await keys.authenticate(credential) is None
            assert (await keys.revoke(created.info.id)).changed

    asyncio.run(run())


def test_unrepresentable_expiry_leaves_no_row(db_url: str, db_engine: Engine):
    async def run():
        async with service(db_url) as keys:
            with pytest.raises(ValueError, match="out of range"):
                await keys.create(expires_in=timedelta.max)
        with db_engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(api_keys)) == 0

    asyncio.run(run())


def test_commit_failure_does_not_return_a_secret_or_leave_a_row(
    db_url: str,
    db_engine: Engine,
):
    # A deferred trigger fails at COMMIT, after INSERT RETURNING has succeeded.
    with db_engine.begin() as connection:
        connection.execute(
            text("""
            CREATE FUNCTION reject_created_key() RETURNS trigger LANGUAGE plpgsql
            AS $$ BEGIN RAISE EXCEPTION 'private driver details'; END $$
        """)
        )
        connection.execute(
            text("""
            CREATE CONSTRAINT TRIGGER reject_created_key
            AFTER INSERT ON api_keys DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION reject_created_key()
        """)
        )

    async def run():
        async with service(db_url) as keys:
            with pytest.raises(APIKeyStorageError) as error:
                await keys.create()
            assert str(error.value) == "API-key storage is unavailable"
        with db_engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(api_keys)) == 0

    try:
        asyncio.run(run())
    finally:
        with db_engine.begin() as connection:
            connection.execute(text("DROP TRIGGER reject_created_key ON api_keys"))
            connection.execute(text("DROP FUNCTION reject_created_key()"))
