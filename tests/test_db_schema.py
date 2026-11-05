"""Test the setup script and the api_keys table against a real Postgres.

These tests are marked `db`, so a plain `uv run pytest` skips them. To run them,
start the Compose Postgres with `make db`, set DATABASE_ADMIN_URL as
DEVELOPMENT.md describes, and run `uv run pytest -m db`. Each run uses a
throwaway role and database.
"""

import uuid
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from groundline.db import api_keys, metadata
from groundline.db.setup import (
    DatabaseSetupError,
    migrate,
    setup_database,
)

pytestmark = pytest.mark.db


def add_key(db_engine: Engine, **values: object) -> dict[str, object]:
    row = {"key_hash": uuid.uuid4().hex, "prefix": "gl_abcde", "name": "test"}
    with db_engine.begin() as connection:
        result = connection.execute(
            insert(api_keys).values({**row, **values}).returning(api_keys)
        )
        return dict(result.mappings().one())


def test_setup_is_safe_to_rerun(db_url: str, admin_url: str):
    setup_database(db_url, admin_url)


def test_setup_refuses_a_superuser_role(admin_url: str):
    # The admin login, postgres, is a superuser: it can't be the app's login.
    with pytest.raises(DatabaseSetupError, match="superuser"):
        setup_database(admin_url, admin_url)


@pytest.mark.parametrize("kind", ["DATABASE", "ROLE"])
def test_the_apps_login_cant_create_databases_or_logins(db_engine: Engine, kind: str):
    # ADR 0013. Autocommit, because CREATE DATABASE can't run in a transaction.
    with (
        db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn,
        pytest.raises(ProgrammingError, match="permission denied"),
    ):
        conn.execute(text(f"CREATE {kind} groundline_forbidden"))


def test_the_migrations_match_the_table_definition(db_engine: Engine):
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, metadata) == []


def test_a_new_key_is_active_with_a_time_ordered_id(db_engine: Engine):
    key = add_key(db_engine)

    assert isinstance(key["id"], uuid.UUID) and key["id"].version == 7
    assert key["status"] == "active"
    assert isinstance(key["created_at"], datetime)
    assert key["revoked_at"] is None
    assert key["last_used_at"] is None
    assert key["expires_at"] is None


def test_key_hashes_are_unique(db_engine: Engine):
    add_key(db_engine, key_hash="same")

    with pytest.raises(IntegrityError, match="key_hash"):
        add_key(db_engine, key_hash="same")


def test_status_is_active_or_revoked(db_engine: Engine):
    with pytest.raises(IntegrityError, match="status"):
        add_key(db_engine, status="expired")


@pytest.mark.parametrize(
    "status,revoked_at",
    [("revoked", None), ("active", datetime(2026, 1, 1, tzinfo=UTC))],
)
def test_revoked_at_is_set_exactly_when_revoked(
    db_engine: Engine, status: str, revoked_at: datetime | None
):
    with pytest.raises(IntegrityError, match="revoked_at"):
        add_key(db_engine, status=status, revoked_at=revoked_at)


def test_a_revoked_key_records_when(db_engine: Engine):
    when = datetime(2026, 1, 1, tzinfo=UTC)
    key = add_key(db_engine, status="revoked", revoked_at=when)

    assert key["revoked_at"] == when


def test_name_and_prefix_are_required(db_engine: Engine):
    with pytest.raises(IntegrityError):
        add_key(db_engine, name=None)
    with pytest.raises(IntegrityError):
        add_key(db_engine, prefix=None)


def test_the_migrations_downgrade_and_upgrade_cleanly(db_url: str, db_engine: Engine):
    migrate(db_url, "base")
    with db_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() is None
    migrate(db_url, "head")
    with db_engine.connect() as connection:
        assert connection.execute(select(api_keys)).all() == []
