"""Create api_keys.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuidv7()"),
        ),
        sa.Column("key_hash", sa.Text, nullable=False, comment="SHA-256, hex"),
        sa.Column(
            "prefix", sa.Text, nullable=False, comment="the key's first characters"
        ),
        sa.Column(
            "name", sa.Text, nullable=False, comment="a label, such as YY's laptop"
        ),
        sa.Column(
            "status", sa.Text, nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", TIMESTAMP(timezone=True)),
        sa.Column("last_used_at", TIMESTAMP(timezone=True)),
        sa.Column(
            "expires_at", TIMESTAMP(timezone=True), comment="null: never expires"
        ),
        sa.UniqueConstraint("key_hash", name="api_keys_key_hash_key"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="api_keys_status"),
        sa.CheckConstraint(
            "(status = 'revoked') = (revoked_at IS NOT NULL)",
            name="api_keys_revoked_at",
        ),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
