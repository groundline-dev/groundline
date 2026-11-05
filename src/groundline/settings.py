"""Secrets and environment settings, read from the environment or a `.env` file.

Values already in the environment win over `.env`. The `.env` file is read from
the current folder. Keys are held as `SecretStr` so they never appear when
settings are printed or logged.
"""

import os
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from groundline.core.errors import SetupError

ENV_FILE = ".env"


class MissingAPIKeyError(SetupError):
    pass


class UnreadableSettingsError(SetupError):
    pass


def dotenv_path() -> Path:
    """The `.env` file settings are read from: in the current folder."""
    return Path.cwd() / ENV_FILE


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_ignore_empty=True,  # a blank `ANTHROPIC_API_KEY=` counts as unset
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    """Where the database is, password included, such as
    postgresql+psycopg://groundline:...@localhost:5433/groundline."""
    database_admin_url: SecretStr | None = None
    """The Postgres admin login, which only setup and the database tests use to
    create the app's login and database. The app itself never gets it (ADR 0013)."""

    def require_database_url(self) -> str:
        """Require storage only when an operation actually uses it."""
        if self.database_url is None:
            raise SetupError(
                "DATABASE_URL is not set. Add it to the environment or .env; "
                "see DEVELOPMENT.md for database setup."
            )
        return self.database_url.get_secret_value()

    def anthropic_key(self, needed_by: str | None = None) -> str:
        """The Anthropic API key, or an error explaining how to provide it.

        `needed_by` says what needs the key (e.g. which stages and model), to
        include in the error. A missing key is reported here, when it's needed,
        rather than when settings are loaded.
        """
        if self.anthropic_api_key is None:
            raise MissingAPIKeyError(
                _missing_key_message("ANTHROPIC_API_KEY", needed_by)
            )
        return self.anthropic_api_key.get_secret_value()

    def anthropic_key_source(self) -> str:
        """Where the key came from, for error messages."""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "the environment"
        return str(dotenv_path())


def read_settings() -> Settings:
    """The settings, or an error naming only the problem's type and the file to
    check: a settings error can quote the value it rejected, such as a key."""
    try:
        return Settings()
    except Exception as error:
        raise UnreadableSettingsError(
            f"can't read the settings ({type(error).__name__}).\n"
            f"  Fix: check that {dotenv_path()} is a readable UTF-8 text file."
        ) from error


def _missing_key_message(name: str, needed_by: str | None) -> str:
    path = dotenv_path()
    if not path.is_file():
        status = "not found"
    else:
        status = f"found, but it has no {name}, or it's blank"
    lines = [f"{name} is not set."]
    if needed_by:
        lines.append(f"  Needed by: {needed_by}")
    lines += [
        f"  Looked in: the environment, and {path} ({status})",
        f"  Fix: export {name}=..., or copy .env.example to .env and add it there.",
    ]
    return "\n".join(lines)
