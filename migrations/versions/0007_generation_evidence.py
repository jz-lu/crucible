"""Exact artifact pins and non-secret generation outcome accounting.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("adapter_versions", sa.Column("artifact_sha256", sa.String(64), nullable=True))
    op.create_table("generation_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("scheme_version", sa.String(32), nullable=False),
        sa.Column("profile_id", sa.String(100), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("round_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.Float(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=False),
        sa.Column("disposition", sa.String(40), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=True))
    op.create_index("ix_generation_profile", "generation_attempts", ["scheme_id", "scheme_version", "profile_id"])


def downgrade():
    op.drop_index("ix_generation_profile", table_name="generation_attempts")
    op.drop_table("generation_attempts")
    op.drop_column("adapter_versions", "artifact_sha256")
