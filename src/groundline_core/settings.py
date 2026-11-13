"""Secrets and environment settings, read from the environment or a `.env` file.

Values already in the environment win over `.env`. The library reads a `.env`
file only when its caller names one; the CLI and the server name `.env` in the
current folder. Keys are held as `SecretStr` so they never appear when settings
are printed or logged.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from groundline_core.core.errors import SetupError

ENV_FILE = ".env"
"""The file the CLI and the server read settings from, in the current folder."""

ENV_NAME = "ANTHROPIC_API_KEY"
ARGUMENT = "anthropic_api_key"

type EnvFile = str | Path | None
"""A `.env` file to read settings from, or None to read only the environment."""


class MissingAPIKeyError(SetupError):
    def __init__(self, needed_by: str | None, env_file: EnvFile) -> None:
        if env_file is None:
            message = _missing_argument_message(needed_by)
            super().__init__(message, message)
        else:
            super().__init__(
                _missing_key_message(needed_by, Path(env_file)),
                _missing_key_message(needed_by, Path(env_file), show_path=False),
            )


class UnreadableSettingsError(SetupError):
    def __init__(self, error_type: str, env_file: EnvFile) -> None:
        if env_file is None:
            message = (
                f"can't read the settings ({error_type}).\n"
                "  Fix: check the environment variables groundline reads."
            )
            super().__init__(message, message)
        else:
            super().__init__(
                _unreadable_message(error_type, Path(env_file)),
                _unreadable_message(error_type, Path(env_file), show_path=False),
            )


@dataclass(frozen=True)
class AnthropicKey:
    """The Anthropic API key to use, with what errors should call it and where
    it came from. The key is a `SecretStr`, so printing this never shows it."""

    value: SecretStr
    name: str
    source: str


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
            message = (
                "DATABASE_URL is not set. Add it to the environment or .env; "
                "see DEVELOPMENT.md for database setup."
            )
            raise SetupError(message, message)
        return self.database_url.get_secret_value()


def read_settings(env_file: EnvFile) -> Settings:
    """The settings from the environment and `env_file`, if given, or an error
    naming only the problem's type and the file to check: a settings error can
    quote the value it rejected, such as a key."""
    try:
        # pydantic-settings takes `_env_file` at runtime, but pyright builds the
        # constructor from the fields alone, so it doesn't know the argument.
        return Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    except Exception as error:
        raise UnreadableSettingsError(type(error).__name__, env_file) from error


def anthropic_key(
    argument: str | SecretStr | None,
    *,
    env_file: EnvFile,
    needed_by: str | None = None,
) -> AnthropicKey:
    """The Anthropic API key: `argument` if given, otherwise ANTHROPIC_API_KEY
    from the environment, otherwise from `env_file`. A blank argument counts as
    missing rather than falling back, so a caller's bug isn't hidden.

    `needed_by` says what needs the key (e.g. which stages and model), to
    include in the error. A missing key is reported here, when it's needed,
    rather than when settings are loaded.
    """
    if argument is not None:
        value = argument if isinstance(argument, SecretStr) else SecretStr(argument)
        if not value.get_secret_value().strip():
            raise MissingAPIKeyError(needed_by, env_file)
        return AnthropicKey(value, "The Anthropic API key", f"the {ARGUMENT} argument")
    found = read_settings(env_file).anthropic_api_key
    if found is None:
        raise MissingAPIKeyError(needed_by, env_file)
    if os.environ.get(ENV_NAME) or env_file is None:
        source = "the environment"
    else:
        source = str(Path(env_file).absolute())
    return AnthropicKey(found, ENV_NAME, source)


def _unreadable_message(
    error_type: str, env_file: Path, *, show_path: bool = True
) -> str:
    """Why the settings can't be read and how to fix it. An HTTP response leaves
    the path out (`show_path=False`), since it is a detail of the server."""
    file = env_file.absolute() if show_path else f"the server's {env_file.name}"
    return (
        f"can't read the settings ({error_type}).\n"
        f"  Fix: check that {file} is a readable UTF-8 text file."
    )


def _missing_argument_message(needed_by: str | None) -> str:
    """Where the library looked for the key, when its caller named no `.env`."""
    lines = ["The Anthropic API key is not set."]
    if needed_by:
        lines.append(f"  Needed by: {needed_by}")
    lines.append(
        f"  Looked in: the {ARGUMENT} argument, and {ENV_NAME} in the environment"
    )
    lines.append(f"  Fix: pass {ARGUMENT}=..., or set {ENV_NAME}.")
    return "\n".join(lines)


def _missing_key_message(
    needed_by: str | None, env_file: Path, *, show_path: bool = True
) -> str:
    """Where the key was looked for and how to fix it. An HTTP response leaves
    the path out (`show_path=False`), since it is a detail of the server."""
    lines = [f"{ENV_NAME} is not set."]
    if needed_by:
        lines.append(f"  Needed by: {needed_by}")
    if show_path:
        path = env_file.absolute()
        if not path.is_file():
            status = "not found"
        else:
            status = f"found, but it has no {ENV_NAME}, or it's blank"
        lines.append(f"  Looked in: the environment, and {path} ({status})")
    lines.append(
        f"  Fix: export {ENV_NAME}=..., or copy .env.example to .env and add it there."
    )
    return "\n".join(lines)
