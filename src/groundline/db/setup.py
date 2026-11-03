"""Set up a local database: `scripts/db-setup`, or `python -m groundline.db.setup`.

Creates the role and database DATABASE_URL names, if they don't exist, sets the
role's password to the one in the URL, then applies the migrations. Safe to
rerun. It connects to Postgres through `psql` as the current OS user, who must be
a Postgres superuser (true by default for Homebrew; see DEVELOPMENT.md for Linux).
"""

import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url
from sqlalchemy.exc import ArgumentError

from groundline.core.errors import SetupError
from groundline.settings import Settings

LOCAL_HOSTS = {None, "localhost", "127.0.0.1", "::1"}
EXAMPLE_PASSWORD = "CHANGE_ME"
MIN_PSQL = 15  # for \getenv

IS_SUPERUSER = r"""
\getenv role GROUNDLINE_DB_ROLE
SELECT rolsuper FROM pg_roles WHERE rolname = :'role'
"""

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
    if _run_admin_sql(IS_SUPERUSER, url).strip() == "t":
        # Setting its password would change a superuser's login, and the API
        # would then run with every privilege.
        raise DatabaseSetupError(
            f"{url.username} is a Postgres superuser; DATABASE_URL needs a role of "
            "its own,\n  such as groundline, which db-setup creates"
        )
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
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        # Not `from error`: the parser's messages can quote the URL's password.
        raise DatabaseSetupError("DATABASE_URL isn't a valid URL") from None
    if url.host not in LOCAL_HOSTS:
        raise DatabaseSetupError(
            f"db-setup only sets up a database on this machine, not on {url.host}"
        )
    if not (url.username and url.password and url.database):
        raise DatabaseSetupError(
            "DATABASE_URL needs a user, a password and a database name, such as\n"
            "  postgresql+psycopg://groundline:PASSWORD@localhost:5432/groundline"
        )
    if url.password == EXAMPLE_PASSWORD:
        raise DatabaseSetupError(
            f"DATABASE_URL still has the example password, {EXAMPLE_PASSWORD}; "
            "choose your own in .env"
        )
    return url


def psql_major(version: str) -> int | None:
    """The major version in `psql --version` output, such as 18."""
    match = re.search(r"\(PostgreSQL\) (\d+)", version)
    return int(match.group(1)) if match else None


def admin_env(url: URL) -> dict[str, str]:
    """psql's environment: the values the setup SQL reads, and no PG* settings
    from the shell, which could point psql at another server or user."""
    env = {
        name: value for name, value in os.environ.items() if not name.startswith("PG")
    }
    return env | {
        "GROUNDLINE_DB_ROLE": str(url.username),
        "GROUNDLINE_DB_PASSWORD": str(url.password),
        "GROUNDLINE_DB_NAME": str(url.database),
    }


def _psql() -> str:
    psql = shutil.which("psql")
    if psql is None:
        raise DatabaseSetupError(
            "psql not found: install PostgreSQL 18, and on macOS link it "
            "(see DEVELOPMENT.md)"
        )
    output = subprocess.run([psql, "--version"], capture_output=True, text=True).stdout
    major = psql_major(output)
    if major is None or major < MIN_PSQL:
        raise DatabaseSetupError(
            f"{psql} is {output.strip() or 'an unknown version'}; db-setup needs "
            f"psql {MIN_PSQL} or later. Install PostgreSQL 18 and put its psql "
            "first on your PATH (see DEVELOPMENT.md)"
        )
    return psql


def _run_admin_sql(script: str, url: URL) -> str:
    """Run `script` through the local socket as the current OS user; its output."""
    args = [_psql(), "--no-psqlrc", "--quiet", "--tuples-only", "--no-align"]
    args.append("--set=ON_ERROR_STOP=1")
    args.append("--dbname=postgres")
    if url.port:
        args.append(f"--port={url.port}")
    result = subprocess.run(
        args, input=script, env=admin_env(url), capture_output=True, text=True
    )
    if result.returncode != 0:
        detail = result.stderr.strip().replace(str(url.password), "***")
        raise DatabaseSetupError(f"psql failed:\n  {detail}")
    return result.stdout


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
