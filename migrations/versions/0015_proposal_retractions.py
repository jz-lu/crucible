"""Explicit author withdrawals preserve proposal, note and review history."""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("proposal_retractions",
        sa.Column("target_kind", sa.String(32), primary_key=True),
        sa.Column("target_id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("supporting_note", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_proposal_retraction_key"),
        sa.CheckConstraint("target_kind IN ('scheme_admission', 'research_request')", name="ck_retraction_target_kind"))
    op.create_index("ix_proposal_retractions_actor_id", "proposal_retractions", ["actor_id"])


def downgrade():
    op.drop_table("proposal_retractions")
