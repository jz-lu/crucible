"""Inert, immutable attack source snapshots.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # Bound the evidence-visibility and sealing checks by method, not all history.
    op.create_index("ix_submissions_method_id", "submissions", ["method_id"])
    op.create_table("source_artifacts",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("bundle", sa.JSON(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False))
    op.create_table("method_sources",
        sa.Column("method_id", sa.String(36), sa.ForeignKey("methods.id"), primary_key=True),
        sa.Column("sha256", sa.String(64), sa.ForeignKey("source_artifacts.sha256"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_index("ix_method_sources_sha256", "method_sources", ["sha256"])


def downgrade():
    op.drop_index("ix_method_sources_sha256", table_name="method_sources")
    op.drop_table("method_sources")
    op.drop_table("source_artifacts")
    op.drop_index("ix_submissions_method_id", table_name="submissions")
