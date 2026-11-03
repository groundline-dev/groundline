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
    postgresql+psycopg://groundline:...@localhost:5432/groundline."""

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
