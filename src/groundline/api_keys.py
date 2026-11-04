"""Issue, revoke, and authenticate groundline API keys against PostgreSQL.

The caller owns the async engine. Every operation uses a fresh transaction;
authentication is never cached (ADR 0011). No plaintext credential enters SQL.
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

from groundline.db import api_keys

_ADJECTIVES = ("bright", "calm", "gentle", "nimble", "quiet", "swift", "warm", "wise")
_NOUNS = ("badger", "cedar", "finch", "maple", "otter", "panda", "robin", "willow")
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


class APIKeyInfo(BaseModel):
    """Public metadata: no credential or hash, including on serialization."""

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
    info: APIKeyInfo
    secret: SecretStr


@dataclass(frozen=True)
class RevokedAPIKey:
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
        raise ValueError("API-key name must be non-empty, printable, and on one line")
    return name.strip()


def valid_api_key_format(credential: str) -> bool:
    return _CREDENTIAL.fullmatch(credential) is not None


class APIKeys:
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
        label = api_key_name(name)
        if expires_in is not None and expires_in <= timedelta(0):
            raise ValueError("API-key lifetime must be positive")
        secret = generate_api_key()
        credential = secret.get_secret_value()
        digest = hashlib.sha256(credential.encode()).hexdigest()
        async with self._transaction() as connection:
            expires_at = None
            if expires_in is not None:
                # now() also supplies created_at: one clock for both timestamps.
                created_at = await connection.scalar(select(func.now()))
                assert isinstance(created_at, datetime)
                try:
                    expires_at = created_at + expires_in
                except OverflowError:
                    raise ValueError("API-key expiry is out of range") from None
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
        if not valid_api_key_format(credential):
            return None
        digest = hashlib.sha256(credential.encode()).hexdigest()
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
