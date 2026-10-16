"""Explicit beta membership, invitation mailbox proof and access audit.

Revision ID: 0009
Revises: 0008
Existing accounts and credentials are preserved; no membership is inferred.
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("beta_memberships",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False))
    op.create_table("account_states",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("suspended_at", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False))
    op.create_table("beta_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("redeemed_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("redeemed_at", sa.Float(), nullable=True),
        sa.Column("send_count", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("last_sent_at", sa.Float(), nullable=True))
    op.create_index("ix_beta_invitations_expires_at", "beta_invitations", ["expires_at"])
    op.create_table("mailbox_verifications",
        sa.Column("invitation_id", sa.String(36), sa.ForeignKey("beta_invitations.id"), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_mac", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float(), nullable=True))
    op.create_table("verified_mailboxes",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("verified_at", sa.Float(), nullable=False))
    op.create_table("access_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False))
    op.create_index("ix_access_events_user_id", "access_events", ["user_id"])


def downgrade():
    op.drop_table("access_events")
    op.drop_table("verified_mailboxes")
    op.drop_table("mailbox_verifications")
    op.drop_table("beta_invitations")
    op.drop_table("account_states")
    op.drop_table("beta_memberships")
