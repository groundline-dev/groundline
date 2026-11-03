"""The setup script and the api_keys table, against a real local Postgres.

Marked `db`, so excluded by default. Run with `uv run pytest -m db`: they need
PostgreSQL 18 running locally, and an OS user that's a Postgres superuser, as
DEVELOPMENT.md describes. Each run uses a throwaway role and database.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from groundline.db import api_keys, metadata
from groundline.db.setup import drop_database, migrate, setup_database

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    name = f"groundline_test_{uuid.uuid4().hex[:8]}"
    url = f"postgresql+psycopg://{name}:pw-{name}@localhost:5432/{name}"
    setup_database(url)
    yield url
    drop_database(url)


@pytest.fixture
def engine(url: str) -> Iterator[Engine]:
    engine = create_engine(url)
    yield engine
    with engine.begin() as connection:
        connection.execute(api_keys.delete())
    engine.dispose()


def add_key(engine: Engine, **values: object) -> dict[str, object]:
    row = {"key_hash": uuid.uuid4().hex, "prefix": "gl_abcde", "name": "test"}
    with engine.begin() as connection:
        result = connection.execute(
            insert(api_keys).values({**row, **values}).returning(api_keys)
        )
        return dict(result.mappings().one())


def test_setup_is_safe_to_rerun(url: str):
    setup_database(url)


def test_the_migrations_match_the_table_definition(engine: Engine):
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, metadata) == []


def test_a_new_key_is_active_with_a_time_ordered_id(engine: Engine):
    key = add_key(engine)

    assert isinstance(key["id"], uuid.UUID) and key["id"].version == 7
    assert key["status"] == "active"
    assert isinstance(key["created_at"], datetime)
    assert key["revoked_at"] is None
    assert key["last_used_at"] is None
    assert key["expires_at"] is None


def test_key_hashes_are_unique(engine: Engine):
    add_key(engine, key_hash="same")

    with pytest.raises(IntegrityError, match="key_hash"):
        add_key(engine, key_hash="same")


def test_status_is_active_or_revoked(engine: Engine):
    with pytest.raises(IntegrityError, match="status"):
        add_key(engine, status="expired")


@pytest.mark.parametrize(
    "status,revoked_at",
    [("revoked", None), ("active", datetime(2026, 1, 1, tzinfo=UTC))],
)
def test_revoked_at_is_set_exactly_when_revoked(
    engine: Engine, status: str, revoked_at: datetime | None
):
    with pytest.raises(IntegrityError, match="revoked_at"):
        add_key(engine, status=status, revoked_at=revoked_at)


def test_a_revoked_key_records_when(engine: Engine):
    when = datetime(2026, 1, 1, tzinfo=UTC)
    key = add_key(engine, status="revoked", revoked_at=when)

    assert key["revoked_at"] == when


def test_name_and_prefix_are_required(engine: Engine):
    with pytest.raises(IntegrityError):
        add_key(engine, name=None)
    with pytest.raises(IntegrityError):
        add_key(engine, prefix=None)


def test_the_migrations_downgrade_and_upgrade_cleanly(url: str, engine: Engine):
    migrate(url, "base")
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() is None
    migrate(url, "head")
    with engine.connect() as connection:
        assert connection.execute(select(api_keys)).all() == []
