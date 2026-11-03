"""The setup script's checks that need no database."""

import pytest
from sqlalchemy import make_url

from groundline.db import setup
from groundline.db.setup import (
    DatabaseSetupError,
    admin_env,
    psql_major,
    setup_database,
)


@pytest.fixture(autouse=True)
def no_psql(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests run by default, so they must never reach a real Postgres:
    a URL that passes every check would otherwise create its role."""

    def refuse(*args: object) -> str:
        raise AssertionError("a setup test reached psql")

    monkeypatch.setattr(setup, "_run_admin_sql", refuse)


def test_the_example_password_is_refused():
    with pytest.raises(DatabaseSetupError, match="CHANGE_ME"):
        setup_database("postgresql+psycopg://groundline:CHANGE_ME@localhost/groundline")


@pytest.mark.parametrize(
    "output,major",
    [
        ("psql (PostgreSQL) 18.3 (Homebrew)\n", 18),
        ("psql (PostgreSQL) 14.11 (Ubuntu 14.11-0ubuntu0.22.04.1)\n", 14),
        ("something else\n", None),
    ],
)
def test_reads_the_psql_version(output: str, major: int | None):
    assert psql_major(output) == major


def test_libpq_settings_in_the_shell_are_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PGHOST", "db.example.com")
    monkeypatch.setenv("PGPORT", "6543")
    monkeypatch.setenv("PGUSER", "someone")
    url = make_url("postgresql+psycopg://gl:pw@localhost:5432/gl")

    env = admin_env(url)

    assert not any(name.startswith("PG") for name in env)
    assert env["GROUNDLINE_DB_ROLE"] == "gl"


@pytest.mark.parametrize(
    "raw", ["postgresql://gl:pw-secret@:x/", "not a url pw-secret"]
)
def test_a_malformed_url_is_a_setup_error_without_the_password(raw: str):
    with pytest.raises(DatabaseSetupError, match="isn't a valid URL") as error:
        setup_database(raw)

    assert "pw-secret" not in str(error.value)


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+psycopg2://gl:pw-secret@localhost/gl",
        "postgresql+asyncpg://gl:pw-secret@localhost/gl",
        "mysql://gl:pw-secret@localhost/gl",
    ],
)
def test_only_the_psycopg_driver_is_accepted(raw: str):
    with pytest.raises(DatabaseSetupError, match="postgresql\\+psycopg://") as error:
        setup_database(raw)

    assert "pw-secret" not in str(error.value)
