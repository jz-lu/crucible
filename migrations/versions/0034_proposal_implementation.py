"""Append-only implementation progress; no changes to existing research.

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("proposal_implementation_events",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("scheme_review_id", sa.String(36), sa.ForeignKey("protocol_proposal_reviews.id"), nullable=True),
        sa.Column("research_request_id", sa.String(36), sa.ForeignKey("research_assessments.request_id"), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("decision_sha256", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("(scheme_review_id IS NOT NULL AND research_request_id IS NULL) OR "
                           "(scheme_review_id IS NULL AND research_request_id IS NOT NULL)", name="ck_implementation_one_decision"),
        sa.CheckConstraint("status IN ('in_progress', 'completed')", name="ck_implementation_status"),
        sa.CheckConstraint("sequence > 0", name="ck_implementation_sequence"),
        sa.UniqueConstraint("scheme_review_id", "sequence", name="uq_implementation_scheme_sequence"),
        sa.UniqueConstraint("research_request_id", "sequence", name="uq_implementation_research_sequence"),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_implementation_actor_key"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM proposal_implementation_events")):
        raise RuntimeError("Implementation history is retained; downgrade requires a reviewed recovery plan.")
    op.drop_table("proposal_implementation_events")
