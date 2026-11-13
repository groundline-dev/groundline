"""Secrets and environment settings, read from the environment or a `.env` file.

Values already in the environment win over `.env`. Nothing reads a `.env` file
unless its caller names one: the CLI, the server and database setup name `.env`
in the current folder, and library callers of `evaluate` name one only if they
want it. Keys are held as `SecretStr` so they never appear when settings are
printed or logged.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from groundline_core.core.errors import SetupError

ENV_FILE = ".env"
"""The file the CLI, the server and database setup read settings from, in the
current folder."""

ANTHROPIC_KEY_VARIABLE = "ANTHROPIC_API_KEY"
ANTHROPIC_KEY_PARAMETER = "anthropic_api_key"

type EnvFile = str | Path | None
"""A `.env` file to read settings from, or None to read only the environment."""


class MissingAPIKeyError(SetupError):
    """No usable Anthropic API key: none was found, or the caller passed a blank
    one. `missing_key_error` builds the usual one."""


class UnreadableSettingsError(SetupError):
    def __init__(self, error_type: str, env_file: EnvFile) -> None:
        if env_file is None:
            fix = api_fix = "check the environment variables groundline reads."
        else:
            path = Path(env_file)
            fix = f"check that {path.absolute()} is a readable UTF-8 text file."
            api_fix = (
                f"check that the server's {path.name} is a readable UTF-8 text file."
            )
        super().__init__(
            _unreadable_message(error_type, fix),
            _unreadable_message(error_type, api_fix),
        )


@dataclass(frozen=True)
class AnthropicKey:
    """The Anthropic API key to use, with what errors should call it and where
    it came from. The key is a `SecretStr`, so printing this never shows it."""

    value: SecretStr
    name: str
    source: str


class Settings(BaseSettings):
    """The settings from the environment alone. `read_settings` adds a `.env`
    file when its caller names one."""

    model_config = SettingsConfigDict(
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
            message = _key_message(
                f"The {ANTHROPIC_KEY_PARAMETER} argument is blank.",
                needed_by,
                fix="pass the Anthropic API key, or leave "
                f"{ANTHROPIC_KEY_PARAMETER} out to use {ANTHROPIC_KEY_VARIABLE}.",
            )
            raise MissingAPIKeyError(message, message)
        return AnthropicKey(
            value, "The Anthropic API key", f"the {ANTHROPIC_KEY_PARAMETER} argument"
        )
    from_settings = read_settings(env_file).anthropic_api_key
    if from_settings is None:
        raise missing_key_error(needed_by, env_file)
    if os.environ.get(ANTHROPIC_KEY_VARIABLE) or env_file is None:
        source = "the environment"
    else:
        source = str(Path(env_file).absolute())
    return AnthropicKey(from_settings, ANTHROPIC_KEY_VARIABLE, source)


def missing_key_error(needed_by: str | None, env_file: EnvFile) -> MissingAPIKeyError:
    """The error for a key found nowhere: where it was looked for, and how to fix
    it. Without an `env_file`, the library's message names no path. With one, an
    HTTP response still leaves the path out, since it is a detail of the server."""
    if env_file is None:
        message = _key_message(
            "The Anthropic API key is not set.",
            needed_by,
            looked_in=f"the {ANTHROPIC_KEY_PARAMETER} argument, "
            f"and {ANTHROPIC_KEY_VARIABLE} in the environment",
            fix=f"pass {ANTHROPIC_KEY_PARAMETER}=..., or set {ANTHROPIC_KEY_VARIABLE}.",
        )
        return MissingAPIKeyError(message, message)
    path = Path(env_file).absolute()
    if not path.is_file():
        status = "not found"
    else:
        status = f"found, but it has no {ANTHROPIC_KEY_VARIABLE}, or it's blank"
    headline = f"{ANTHROPIC_KEY_VARIABLE} is not set."
    fix = (
        f"export {ANTHROPIC_KEY_VARIABLE}=..., or copy .env.example to "
        f"{path.name} and add it there."
    )
    return MissingAPIKeyError(
        _key_message(
            headline,
            needed_by,
            looked_in=f"the environment, and {path} ({status})",
            fix=fix,
        ),
        _key_message(headline, needed_by, fix=fix),
    )


def _key_message(
    headline: str, needed_by: str | None, *, fix: str, looked_in: str | None = None
) -> str:
    """A missing-key message: what's wrong, what needs the key, where it was
    looked for, and how to fix it."""
    lines = [headline]
    if needed_by:
        lines.append(f"  Needed by: {needed_by}")
    if looked_in:
        lines.append(f"  Looked in: {looked_in}")
    lines.append(f"  Fix: {fix}")
    return "\n".join(lines)


def _unreadable_message(error_type: str, fix: str) -> str:
    return f"can't read the settings ({error_type}).\n  Fix: {fix}"
