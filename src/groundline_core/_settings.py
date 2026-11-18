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
from typing import ClassVar

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from groundline_core._core.errors import SetupError

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


class APILimitsSettings(BaseSettings):
    """API-only limits, read from the environment or .env at API import.

    CLI and direct Python evaluation do not load or enforce these limits.
    """

    model_config = SettingsConfigDict(
        env_prefix="GROUNDLINE_",
        env_file=ENV_FILE,
        env_ignore_empty=True,
        extra="ignore",
    )

    max_body_bytes: int = Field(default=5_242_880, gt=0)
    max_source_chars: int = Field(default=200_000, gt=0)
    max_summary_chars: int = Field(default=200_000, gt=0)

    @field_validator(
        "max_body_bytes", "max_source_chars", "max_summary_chars", mode="before"
    )
    @classmethod
    def _positive_decimal(cls, value: object) -> object:
        if isinstance(value, str) and not (value.isascii() and value.isdecimal()):
            raise ValueError("must be a positive ASCII decimal integer")
        return value


class _EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_ignore_empty=True,  # a blank `ANTHROPIC_API_KEY=` counts as unset
        extra="ignore",
    )


class CheckSettings(_EnvironmentSettings):
    """Only the timing needed to start a standalone evaluation."""

    check_timeout_seconds: float = Field(
        default=120,
        gt=0,
        allow_inf_nan=False,
        validation_alias="GROUNDLINE_CHECK_TIMEOUT_SECONDS",
    )
    """Seconds allowed for a check: authentication, model calls and retries."""


class ModelSettings(_EnvironmentSettings):
    """Only the retry and timeout policy needed by an explicit model client."""

    model_max_attempts: int = Field(
        default=3, gt=0, validation_alias="GROUNDLINE_MODEL_MAX_ATTEMPTS"
    )
    """Attempts per model call, including the first request; 1 disables outage
    retries. If the model answers in the wrong format, groundline can still
    ask it to answer again in the required format."""

    model_timeout_seconds: ClassVar[float] = 60
    """Time for one model attempt."""
    model_connect_timeout_seconds: ClassVar[float] = 5
    model_retry_timeout_seconds: ClassVar[float] = 120
    """Total time for one model call, including attempts and retry waits."""
    model_retry_base_seconds: ClassVar[float] = 1
    model_retry_cap_seconds: ClassVar[float] = 8


class Settings(CheckSettings, ModelSettings):
    """The application's complete configuration, including credentials."""

    anthropic_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    """Where the database is, password included, such as
    postgresql+psycopg://groundline:...@localhost:5433/groundline."""
    database_admin_url: SecretStr | None = None
    """The Postgres admin login, which only setup and the database tests use to
    create the app's login and database. The app itself never gets it (ADR 0013)."""

    # Seconds allowed per operation, capped by any enclosing check deadline.
    db_auth_timeout_seconds: ClassVar[float] = 5
    db_connect_timeout_seconds: ClassVar[float] = 5
    db_pool_timeout_seconds: ClassVar[float] = 5
    db_query_timeout_seconds: ClassVar[float] = 5
    db_check_timeout_seconds: ClassVar[float] = 5
    """Time to acquire a database connection and check that it works."""
    db_readiness_timeout_seconds: ClassVar[float] = 5
    """Time for the entire database startup or readiness probe."""

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
    return _read_settings(Settings, env_file)


def read_check_settings(env_file: EnvFile = None) -> CheckSettings:
    return _read_settings(CheckSettings, env_file)


def read_model_settings(env_file: EnvFile = None) -> ModelSettings:
    return _read_settings(ModelSettings, env_file)


def _read_settings[T: BaseSettings](settings_type: type[T], env_file: EnvFile) -> T:
    """Read only the requested settings, without exposing rejected values."""
    try:
        return settings_type(_env_file=env_file)  # pyright: ignore[reportCallIssue]
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
    """The error for an Anthropic API key found nowhere.

    The message says where the key was looked for and how to fix it. Without an
    `env_file`, it names no path. With one, the HTTP response still leaves the
    path out, since the path is a detail of the server.
    """
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
