"""Issue, revoke, and authenticate groundline API keys against PostgreSQL.

The module doesn't depend on the CLI or FastAPI. The caller creates the async
engine with `groundline_core.db.connection.make_engine`, which connects to the
primary database and hides SQL parameters from errors, and closes it when done:

    engine = make_engine(Settings().require_database_url())
    try:
        created = await APIKeys(engine).create(
            name="integration", expires_in=timedelta(days=90)
        )
    finally:
        await engine.dispose()

Use an engine only in the event loop that created it. The CLI creates and
closes one inside each command's `asyncio.run`; the API keeps one for its
whole lifespan.

Every operation uses a fresh transaction, and authentication is never cached
(ADR 0011). No plaintext credential enters SQL. No method returns the stored
hash. Bad input, unknown identifiers and storage failures each raise their own
exception type, with a message that is safe to show.
"""

import hashlib
import re
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from groundline_core.db import api_keys

_ADJECTIVES = ("bright", "calm", "gentle", "nimble", "quiet", "swift", "warm", "wise")
_NOUNS = (
    "badger",
    "cedar",
    "finch",
    "maple",
    "otter",
    "groundhog",
    "panda",
    "robin",
    "rock",
    "willow",
)
_CREDENTIAL = re.compile(r"gl_[A-Za-z0-9_-]{43}")
_PUBLIC_COLUMNS = tuple(
    api_keys.c[name]
    for name in (
        "id",
        "name",
        "prefix",
        "status",
        "created_at",
        "revoked_at",
        "expires_at",
    )
)


class APIKeyStorageError(Exception):
    """A sanitized storage failure, safe to show to a caller."""


class APIKeyNotFoundError(Exception):
    """No API key has the requested identifier."""


class APIKeyInputError(ValueError):
    """Invalid input, described without echoing its potentially secret value."""


class APIKeyInfo(BaseModel):
    """Public metadata: no credential or hash, including on serialization.

    `status` is only ever `active` or `revoked`. An expired key keeps its
    stored status; the CLI shows it as `expired` by comparing `expires_at`
    with the current time.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    prefix: str
    status: Literal["active", "revoked"]
    created_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None


@dataclass(frozen=True)
class CreatedAPIKey:
    """A new key's metadata and its plaintext, returned only after commit.

    `secret` is redacted in reprs and logs. Read the plaintext explicitly with
    `secret.get_secret_value()`; it can't be retrieved again later.
    """

    info: APIKeyInfo
    secret: SecretStr


@dataclass(frozen=True)
class RevokedAPIKey:
    """A revoked key's metadata, and whether this call revoked it.

    `changed` is False when the key was already revoked; `revoked_at` then
    keeps the time of the first revocation.
    """

    info: APIKeyInfo
    changed: bool


def generate_api_key() -> SecretStr:
    """A recognizable prefix and 256 random bits, with a redacted repr."""
    return SecretStr("gl_" + secrets.token_urlsafe(32))


def api_key_name(name: str | None) -> str:
    """Validate a custom label or generate a friendly, non-unique one."""
    if name is None:
        return (
            f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_NOUNS)}-"
            f"{secrets.token_hex(4)}"
        )
    if not name.isprintable() or not name.strip():
        raise APIKeyInputError(
            "API-key name must be non-empty, printable, and on one line"
        )
    label = name.strip()
    if len(label) > 128:
        raise APIKeyInputError(
            "API-key name must be at most 128 characters after trimming"
        )
    return label


def valid_api_key_format(credential: str) -> bool:
    return _CREDENTIAL.fullmatch(credential) is not None


def _digest(credential: str) -> str:
    """The stored form of a key: its SHA-256 hash in hex (ADR 0010)."""
    return hashlib.sha256(credential.encode()).hexdigest()


class APIKeys:
    """API-key storage on an async engine the caller creates and closes."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[AsyncConnection]:
        try:
            async with self._engine.begin() as connection:
                yield connection
        except (SQLAlchemyError, OSError):
            # Driver messages can contain SQL parameters or database passwords.
            raise APIKeyStorageError("API-key storage is unavailable") from None

    async def create(
        self, *, name: str | None = None, expires_in: timedelta | None = None
    ) -> CreatedAPIKey:
        """Store a new key and return it with its plaintext secret.

        A key holds 256 random bits and is stored as its SHA-256 hash
        (ADR 0010). `name` is a label, not a unique identifier: without one,
        a friendly name is generated. Raises `APIKeyInputError` for a name
        that is blank, not printable, on more than one line or over 128
        characters, and for a lifetime that isn't positive or whose expiry
        date is out of range.
        """
        label = api_key_name(name)
        if expires_in is not None and expires_in <= timedelta(0):
            raise APIKeyInputError("API-key lifetime must be positive")
        secret = generate_api_key()
        credential = secret.get_secret_value()
        digest = _digest(credential)
        async with self._transaction() as connection:
            expires_at = None
            if expires_in is not None:
                # now() also supplies created_at: one clock for both timestamps.
                created_at = await connection.scalar(select(func.now()))
                assert isinstance(created_at, datetime)
                try:
                    expires_at = created_at + expires_in
                except OverflowError:
                    raise APIKeyInputError("API-key expiry is out of range") from None
            result = await connection.execute(
                insert(api_keys)
                .values(
                    key_hash=digest,
                    prefix=credential[:11],
                    name=label,
                    expires_at=expires_at,
                )
                .returning(*_PUBLIC_COLUMNS)
            )
            info = APIKeyInfo.model_validate(result.mappings().one())
        # Leaving the transaction successfully commits before exposing the secret.
        return CreatedAPIKey(info, secret)

    async def revoke(self, identifier: UUID) -> RevokedAPIKey:
        """Revoke the key with this ID; raise `APIKeyNotFoundError` if none."""
        async with self._transaction() as connection:
            result = await connection.execute(
                update(api_keys)
                .where(api_keys.c.id == identifier, api_keys.c.status == "active")
                .values(status="revoked", revoked_at=func.now())
                .returning(*_PUBLIC_COLUMNS)
            )
            row = result.mappings().one_or_none()
            changed = row is not None
            if row is None:
                # A concurrent revoke may have won. Under READ COMMITTED this
                # fresh statement sees it, without changing its original time.
                result = await connection.execute(
                    select(*_PUBLIC_COLUMNS).where(api_keys.c.id == identifier)
                )
                row = result.mappings().one_or_none()
            if row is None:
                raise APIKeyNotFoundError("API-key identifier was not found")
            info = APIKeyInfo.model_validate(row)
        return RevokedAPIKey(info, changed)

    async def authenticate(self, credential: str) -> APIKeyInfo | None:
        """Return the metadata of an active, unexpired key, otherwise None.

        Expired keys fail even though their stored status is still `active`.
        Authenticating doesn't update `last_used_at`.
        """
        if not valid_api_key_format(credential):
            return None
        digest = _digest(credential)
        async with self._transaction() as connection:
            result = await connection.execute(
                select(*_PUBLIC_COLUMNS).where(
                    api_keys.c.key_hash == digest,
                    api_keys.c.status == "active",
                    or_(
                        api_keys.c.expires_at.is_(None),
                        api_keys.c.expires_at > func.now(),
                    ),
                )
            )
            row = result.mappings().one_or_none()
            info = APIKeyInfo.model_validate(row) if row is not None else None
        return info

    async def list_all(self) -> list[APIKeyInfo]:
        """List all keys newest first, returning metadata only, never hashes.

        Keys created at the same time are ordered by ID, descending. The list
        is empty when there are no keys. Names longer than the 128-character
        limit for new keys are still returned in full.
        """
        async with self._transaction() as connection:
            result = await connection.execute(
                select(*_PUBLIC_COLUMNS).order_by(
                    api_keys.c.created_at.desc(), api_keys.c.id.desc()
                )
            )
            return [APIKeyInfo.model_validate(row) for row in result.mappings()]
