"""The database's tables. A change here needs a migration to match:
`uv run alembic revision -m "..."`, then fill it in."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    MetaData,
    Table,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP

metadata = MetaData()

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", Uuid(as_uuid=True), primary_key=True, server_default=text("uuidv7()")),
    Column("key_hash", Text, nullable=False, unique=True, comment="SHA-256, hex"),
    Column("prefix", Text, nullable=False, comment="the key's first characters"),
    Column("name", Text, nullable=False, comment="a label, such as YY's laptop"),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
    Column("revoked_at", TIMESTAMP(timezone=True)),
    Column("last_used_at", TIMESTAMP(timezone=True)),
    Column("expires_at", TIMESTAMP(timezone=True), comment="null: never expires"),
    CheckConstraint("status IN ('active', 'revoked')", name="api_keys_status"),
    CheckConstraint(
        "(status = 'revoked') = (revoked_at IS NOT NULL)",
        name="api_keys_revoked_at",
    ),
)
