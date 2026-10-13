"""Private immutable protocol proposal intake.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("protocol_proposals",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("author_id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_protocol_proposal_idempotency"))
    op.create_index("ix_protocol_proposal_author_time", "protocol_proposals", ["author_id", "created_at", "id"])
    op.create_table("protocol_proposal_revisions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["protocol_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "revision", name="uq_protocol_proposal_revision"),
        sa.UniqueConstraint("proposal_id", "idempotency_key", name="uq_protocol_revision_idempotency"))
    op.create_table("protocol_proposal_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("implementation_sha256", sa.String(64), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["protocol_proposals.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", "revision", name="uq_protocol_proposal_review"),
        sa.UniqueConstraint("reviewer_id", "idempotency_key", name="uq_protocol_review_idempotency"))


def downgrade():
    op.drop_table("protocol_proposal_reviews")
    op.drop_table("protocol_proposal_revisions")
    op.drop_index("ix_protocol_proposal_author_time", table_name="protocol_proposals")
    op.drop_table("protocol_proposals")
