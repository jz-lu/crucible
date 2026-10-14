"""Explicit once-per-family scheme admission credits.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scheme_admission_credits",
        sa.Column("family_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("scheme_version", sa.String(32), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("specification_sha256", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("reviewer_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["protocol_proposals.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["protocol_proposal_reviews.id"]),
        sa.PrimaryKeyConstraint("family_id"),
        sa.UniqueConstraint("scheme_id", name="uq_scheme_credit_scheme"),
        sa.UniqueConstraint("proposal_id", name="uq_scheme_credit_proposal"),
        sa.UniqueConstraint("review_id", name="uq_scheme_credit_review"),
        sa.UniqueConstraint("source_sha256", name="uq_scheme_credit_source"))
    op.create_index("ix_scheme_admission_credits_user_id", "scheme_admission_credits", ["user_id"])


def downgrade():
    op.drop_index("ix_scheme_admission_credits_user_id", table_name="scheme_admission_credits")
    op.drop_table("scheme_admission_credits")
