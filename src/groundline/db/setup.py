"""Set up a local database: `scripts/db-setup`, or `python -m groundline.db.setup`.

Creates the role and database DATABASE_URL names, if they don't exist, sets the
role's password to the one in the URL, then applies the migrations. Safe to
rerun. It connects to Postgres through `psql` as the current OS user, who must be
a Postgres superuser (true by default for Homebrew; see DEVELOPMENT.md for Linux).
"""

import os
import shutil
import subprocess
import sys
from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url

from groundline.core.errors import SetupError
from groundline.settings import Settings

LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1"}

CREATE = r"""
\getenv role GROUNDLINE_DB_ROLE
\getenv password GROUNDLINE_DB_PASSWORD
\getenv database GROUNDLINE_DB_NAME
SELECT format('CREATE ROLE %I', :'role')
  WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'role', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'database', :'role')
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database') \gexec
"""

DROP = r"""
\getenv role GROUNDLINE_DB_ROLE
\getenv database GROUNDLINE_DB_NAME
SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', :'database') \gexec
SELECT format('DROP ROLE IF EXISTS %I', :'role') \gexec
"""


class DatabaseSetupError(SetupError):
    pass


def setup_database(raw: str) -> None:
    url = _local_url(raw)
    _run_admin_sql(CREATE, url)
    migrate(raw, "head")


def drop_database(raw: str) -> None:
    """Drop the database and role DATABASE_URL names. For tests' throwaway ones."""
    _run_admin_sql(DROP, _local_url(raw))


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


def _local_url(raw: str) -> URL:
    url = make_url(raw)
    if url.host not in LOCAL_HOSTS:
        raise DatabaseSetupError(
            f"db-setup only sets up a database on this machine, not on {url.host}"
        )
    if not (url.username and url.password and url.database):
        raise DatabaseSetupError(
            "DATABASE_URL needs a user, a password and a database name, such as\n"
            "  postgresql+psycopg://groundline:PASSWORD@localhost:5432/groundline"
        )
    return url


def _run_admin_sql(script: str, url: URL) -> None:
    psql = shutil.which("psql")
    if psql is None:
        raise DatabaseSetupError(
            "psql not found: install PostgreSQL 18 (see DEVELOPMENT.md)"
        )
    env = {
        **os.environ,
        "GROUNDLINE_DB_ROLE": str(url.username),
        "GROUNDLINE_DB_PASSWORD": str(url.password),
        "GROUNDLINE_DB_NAME": str(url.database),
    }
    # No host: connect through the local socket, as the current OS user.
    args = [psql, "--no-psqlrc", "--quiet", "--set=ON_ERROR_STOP=1"]
    args.append("--dbname=postgres")
    if url.port:
        args.append(f"--port={url.port}")
    result = subprocess.run(args, input=script, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip().replace(str(url.password), "***")
        raise DatabaseSetupError(f"psql failed:\n  {detail}")


def main() -> None:
    url = Settings().database_url
    if url is None:
        sys.exit(
            "error: DATABASE_URL is not set.\n"
            "  Fix: copy .env.example to .env and fill in DATABASE_URL."
        )
    try:
        setup_database(url.get_secret_value())
    except DatabaseSetupError as error:
        sys.exit(f"error: {error}")
    print("database ready")


if __name__ == "__main__":
    main()
