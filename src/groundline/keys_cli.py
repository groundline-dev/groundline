"""The API-key management subgroup; database work stays in one event loop."""

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, timedelta
from typing import Annotated
from uuid import UUID

import typer

from groundline.api_keys import (
    APIKeyInputError,
    APIKeyNotFoundError,
    APIKeys,
    APIKeyStorageError,
    api_key_name,
)
from groundline.core.errors import SetupError
from groundline.db.connection import make_engine
from groundline.settings import Settings

app = typer.Typer(
    help="Create and revoke groundline API keys.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _lifetime(value: str | None) -> timedelta | None:
    if value is None:
        return None
    match = re.fullmatch(r"([1-9][0-9]*)([smhd])", value)
    message = "--expires-in needs a positive duration such as 30m, 12h, or 90d"
    if match is None:
        raise APIKeyInputError(message)
    try:
        seconds = int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
        return timedelta(seconds=seconds)
    except (ValueError, OverflowError):
        raise APIKeyInputError("--expires-in is out of range") from None


async def _run[T](operation: Callable[[APIKeys], Awaitable[T]]) -> T:
    engine = make_engine(Settings().require_database_url())
    try:
        return await operation(APIKeys(engine))
    finally:
        await engine.dispose()


def _finish(action: Callable[[], None]) -> None:
    try:
        action()
    except (
        SetupError,
        APIKeyStorageError,
        APIKeyNotFoundError,
        APIKeyInputError,
    ) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from None
    except Exception:
        # Do not reuse the evaluator's wrapper: arbitrary exception text can
        # contain credentials, database passwords, or SQL parameters.
        typer.echo("error: API-key operation failed", err=True)
        raise typer.Exit(2) from None


@app.command()
def create(
    name: Annotated[
        str | None, typer.Option(help="A label; generated if omitted.")
    ] = None,
    expires_in: Annotated[
        str | None, typer.Option(help="Lifetime such as 90d; never expires if omitted.")
    ] = None,
) -> None:
    """Create an API key and show its secret once, after saving its hash."""
    _finish(lambda: _create(name, expires_in))


def _create(name: str | None, expires_in: str | None) -> None:
    # Validate arguments before requiring a URL or opening a connection.
    label = api_key_name(name)
    lifetime = _lifetime(expires_in)
    created = asyncio.run(
        _run(lambda keys: keys.create(name=label, expires_in=lifetime))
    )
    info = created.info
    expiry = info.expires_at.astimezone(UTC).isoformat() if info.expires_at else "never"
    try:
        typer.echo(
            f"Name: {info.name}\nID: {info.id}\nExpires: {expiry}\n"
            "Copy the API key now; it cannot be retrieved later.\n"
            f"API key: {created.secret.get_secret_value()}"
        )
    except OSError:
        raise SetupError(
            f"API key {info.id} was created, but its output could not be written. "
            "Revoke it and create a replacement."
        ) from None


@app.command()
def revoke(
    identifier: Annotated[str, typer.Argument(help="The full API-key UUID.")],
) -> None:
    """Permanently revoke an API key; repeating this command is safe."""
    _finish(lambda: _revoke(identifier))


def _revoke(identifier: str) -> None:
    try:
        parsed = UUID(identifier)
    except ValueError:
        raise APIKeyInputError("API-key identifier must be a UUID") from None
    result = asyncio.run(_run(lambda keys: keys.revoke(parsed)))
    action = "Revoked" if result.changed else "Already revoked"
    typer.echo(f"{action}: {result.info.id} ({result.info.name})")
