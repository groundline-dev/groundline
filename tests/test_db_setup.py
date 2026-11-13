"""Test how the setup script rejects bad database URLs.

Before it creates anything, setup_database checks both URLs it's given: each
must be a valid postgresql+psycopg:// URL without the example password
CHANGE_ME. These tests pass it bad URLs and check the error it raises.

Unlike the `db` tests, these run on every `uv run pytest`, even on a computer
with Postgres running. If a URL passed every check, setup would log in to that
Postgres as admin and create a role. To prevent that, every admin URL here
points at 127.0.0.1:1. Nothing listens on port 1, so setup can't connect.
"""

from pathlib import Path

import pytest

from groundline_core.db import setup
from groundline_core.db.setup import DatabaseSetupError, setup_database

APP = "postgresql+psycopg://groundline:app-secret@localhost:5433/groundline"
ADMIN = "postgresql+psycopg://postgres:admin-secret@127.0.0.1:1/postgres"


@pytest.mark.parametrize(
    "app_url,admin_url,named",
    [
        (
            "postgresql+psycopg://groundline:CHANGE_ME@localhost/groundline",
            ADMIN,
            "DATABASE_URL",
        ),
        (
            APP,
            "postgresql+psycopg://postgres:CHANGE_ME@127.0.0.1:1/postgres",
            "DATABASE_ADMIN_URL",
        ),
    ],
)
def test_the_example_password_is_refused_in_either_url(
    app_url: str, admin_url: str, named: str
):
    with pytest.raises(DatabaseSetupError, match="CHANGE_ME") as error:
        setup_database(app_url, admin_url)

    assert named in str(error.value)


@pytest.mark.parametrize(
    "raw", ["postgresql://gl:pw-secret@:x/", "not a url pw-secret"]
)
@pytest.mark.parametrize("which", ["DATABASE_URL", "DATABASE_ADMIN_URL"])
def test_a_malformed_url_is_a_setup_error_without_the_password(raw: str, which: str):
    urls = (raw, ADMIN) if which == "DATABASE_URL" else (APP, raw)

    with pytest.raises(DatabaseSetupError, match="isn't a valid URL") as error:
        setup_database(*urls)

    assert which in str(error.value)
    assert "pw-secret" not in str(error.value)


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+psycopg2://gl:pw-secret@localhost/gl",
        "postgresql+asyncpg://gl:pw-secret@localhost/gl",
        "mysql://gl:pw-secret@localhost/gl",  # betterleaks:allow (fixture)
    ],
)
@pytest.mark.parametrize("which", ["DATABASE_URL", "DATABASE_ADMIN_URL"])
def test_only_the_psycopg_driver_is_accepted(raw: str, which: str):
    urls = (raw, ADMIN) if which == "DATABASE_URL" else (APP, raw)

    with pytest.raises(DatabaseSetupError, match="postgresql\\+psycopg://") as error:
        setup_database(*urls)

    assert "pw-secret" not in str(error.value)


def test_a_database_on_another_host_is_allowed():
    """Inside Compose, setup reaches Postgres at postgres:5432, so the app URL's
    host can't be limited to localhost."""
    app_url = "postgresql+psycopg://groundline:app-secret@postgres:5432/groundline"

    # The URL passes every check, so setup goes on to connect to the admin
    # server and fails there, since nothing listens on 127.0.0.1:1.
    with pytest.raises(DatabaseSetupError, match="can't connect") as error:
        setup_database(app_url, ADMIN)

    assert "this machine" not in str(error.value)


def test_an_unreachable_admin_server_is_a_setup_error_without_passwords():
    with pytest.raises(DatabaseSetupError, match="can't connect") as error:
        setup_database(APP, ADMIN)

    message = str(error.value)
    assert "DATABASE_ADMIN_URL" in message
    assert "admin-secret" not in message and "app-secret" not in message


def test_main_explains_a_missing_admin_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)  # no .env
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    # Nothing answers at postgres:1, in case main gets past its checks.
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://groundline:app-secret@postgres:1/gl"
    )

    with pytest.raises(SystemExit) as exited:
        setup.main()

    assert "DATABASE_ADMIN_URL is not set" in str(exited.value.code)
