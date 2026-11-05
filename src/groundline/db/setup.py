"""Set up the app's database: `scripts/db-setup`, or `python -m groundline.db.setup`.

Logs in with DATABASE_ADMIN_URL and creates the login and database that
DATABASE_URL names, if they don't exist. It sets that login's password to the
one in DATABASE_URL, then applies the migrations as the app's login. Safe to
rerun. The app's login must not be a superuser (ADR 0012).
"""

import sys
from collections.abc import Generator
from contextlib import contextmanager
from importlib.resources import files

import psycopg
from alembic import command
from alembic.config import Config
from alembic.util import CommandError
from psycopg import sql
from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from groundline.core.errors import SetupError
from groundline.db.config import DRIVERS
from groundline.db.connection import scrub
from groundline.settings import Settings

EXAMPLE_PASSWORD = "CHANGE_ME"
CONNECT_TIMEOUT_SECONDS = 5


class DatabaseSetupError(SetupError):
    pass


def setup_database(app_raw: str, admin_raw: str) -> None:
    """Create the app's login and database through the admin login, then migrate."""
    app_url = _checked_url(app_raw, "DATABASE_URL")
    admin_url = _checked_url(admin_raw, "DATABASE_ADMIN_URL")
    role = sql.Identifier(str(app_url.username))
    database = sql.Identifier(str(app_url.database))
    with _admin_connection(admin_url, app_url) as admin:
        row = admin.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = %s", (app_url.username,)
        ).fetchone()
        is_superuser = row is not None and row[0]
        if is_superuser:
            # Setting its password would change a superuser's login, and the API
            # would then run with every privilege.
            raise DatabaseSetupError(
                f"{app_url.username} is a Postgres superuser; DATABASE_URL needs a "
                "login of its own,\n  such as groundline, which db-setup creates"
            )
        if not _exists(admin, "pg_roles", "rolname", app_url.username):
            admin.execute(sql.SQL("CREATE ROLE {}").format(role))
        admin.execute(
            # NOCREATEDB and NOCREATEROLE also take those rights from a login
            # that existed before setup ran (ADR 0012).
            sql.SQL(
                "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
            ).format(role, sql.Literal(app_url.password))
        )
        if not _exists(admin, "pg_database", "datname", app_url.database):
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(database, role))
    try:
        migrate(app_raw, "head")
    except (SQLAlchemyError, CommandError) as error:
        # The app's login runs the migrations, so its password could be in a
        # driver message.
        reason = scrub(str(error), app_url.password)
        raise DatabaseSetupError(f"the migrations failed:\n  {reason}") from None


def drop_database(app_raw: str, admin_raw: str) -> None:
    """Drop the database and login DATABASE_URL names. For tests' throwaway ones."""
    app_url = _checked_url(app_raw, "DATABASE_URL")
    admin_url = _checked_url(admin_raw, "DATABASE_ADMIN_URL")
    with _admin_connection(admin_url, app_url) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(str(app_url.database))
            )
        )
        admin.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(
                sql.Identifier(str(app_url.username))
            )
        )


def migrate(raw: str, revision: str) -> None:
    """Upgrade or downgrade the database to `revision`, such as "head"."""
    config = Config()
    config.set_main_option(
        "script_location", str(files("groundline.db") / "migrations")
    )
    config.attributes["database_url"] = make_url(raw)
    if revision == "base" or revision.startswith("-"):
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def _checked_url(raw: str, name: str) -> URL:
    """The URL in setting `name`, or an error that never quotes its password."""
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        # Not `from error`: the parser's messages can quote the URL's password.
        raise DatabaseSetupError(f"{name} isn't a valid URL") from None
    if url.drivername not in DRIVERS:
        # Checked before connecting, so a bad URL never leaves a half-made login.
        raise DatabaseSetupError(
            f"{name} uses {url.drivername}://, but groundline needs "
            "postgresql+psycopg:// (or postgresql://), the driver it installs"
        )
    if not (url.username and url.password and url.database):
        raise DatabaseSetupError(
            f"{name} needs a user, a password and a database name, such as\n"
            "  postgresql+psycopg://USER:PASSWORD@localhost:5433/DATABASE"
        )
    if url.password == EXAMPLE_PASSWORD:
        raise DatabaseSetupError(
            f"{name} still has the example password, {EXAMPLE_PASSWORD}; "
            "choose your own in .env"
        )
    return url


@contextmanager
def _admin_connection(admin_url: URL, app_url: URL) -> Generator[psycopg.Connection]:
    """A connection as the admin login, in autocommit: CREATE DATABASE can't run
    inside a transaction. Its errors become setup errors that never quote either
    URL's password."""
    host = admin_url.host or "localhost"
    port = admin_url.port or 5432
    try:
        connection = psycopg.connect(
            host=host,
            port=port,
            user=admin_url.username,
            password=admin_url.password,
            dbname=admin_url.database,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            autocommit=True,
        )
    except psycopg.Error as error:
        reason = scrub(str(error), admin_url.password, app_url.password)
        raise DatabaseSetupError(
            f"can't connect to the Postgres server in DATABASE_ADMIN_URL "
            f"({host}:{port}):\n  {reason}"
        ) from None
    try:
        with connection:
            yield connection
    except psycopg.Error as error:
        reason = scrub(str(error), admin_url.password, app_url.password)
        raise DatabaseSetupError(f"Postgres refused setup:\n  {reason}") from None


def _exists(
    admin: psycopg.Connection, catalog: str, column: str, value: object
) -> bool:
    query = sql.SQL("SELECT 1 FROM {} WHERE {} = %s").format(
        sql.Identifier(catalog), sql.Identifier(column)
    )
    return admin.execute(query, (value,)).fetchone() is not None


def main() -> None:
    settings = Settings()
    if settings.database_url is None:
        sys.exit(
            "error: DATABASE_URL is not set.\n"
            "  Fix: copy .env.example to .env and fill in DATABASE_URL."
        )
    if settings.database_admin_url is None:
        sys.exit(
            "error: DATABASE_ADMIN_URL is not set; setup logs in with it to create "
            "the app's login.\n"
            "  Fix: copy .env.example to .env and fill in DATABASE_ADMIN_URL."
        )
    try:
        setup_database(
            settings.database_url.get_secret_value(),
            settings.database_admin_url.get_secret_value(),
        )
    except DatabaseSetupError as error:
        sys.exit(f"error: {error}")
    print("database ready")


if __name__ == "__main__":
    main()
