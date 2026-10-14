"""Scoped agent credentials and browser-approved device connections.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("machine_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("token_hash"))
    op.create_index("ix_machine_tokens_user_id", "machine_tokens", ["user_id"])
    op.create_index("ix_machine_tokens_expires_at", "machine_tokens", ["expires_at"])
    op.create_table("device_connections",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("device_hash", sa.String(64), nullable=False),
        sa.Column("user_code", sa.String(9), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("last_polled_at", sa.Float(), nullable=True),
        sa.Column("approved_user_id", sa.String(36), nullable=True),
        sa.Column("approved_at", sa.Float(), nullable=True),
        sa.Column("denied_at", sa.Float(), nullable=True),
        sa.Column("token_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["approved_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["token_id"], ["machine_tokens.id"]),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("device_hash"),
        sa.UniqueConstraint("user_code"), sa.UniqueConstraint("token_id"))
    op.create_index("ix_device_connections_expires_at", "device_connections", ["expires_at"])


def downgrade():
    op.drop_index("ix_device_connections_expires_at", table_name="device_connections")
    op.drop_table("device_connections")
    op.drop_index("ix_machine_tokens_expires_at", table_name="machine_tokens")
    op.drop_index("ix_machine_tokens_user_id", table_name="machine_tokens")
    op.drop_table("machine_tokens")
