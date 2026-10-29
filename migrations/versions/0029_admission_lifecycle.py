"""Durable terminal admission retirement; preserves original credit evidence.

Revision ID: 0029
Revises: 0028
"""
from alembic import op
import sqlalchemy as sa

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scheme_admission_retirements",
        sa.Column("family_id", sa.String(100), nullable=False),
        sa.Column("reviewer_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["scheme_admission_credits.family_id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.CheckConstraint("created_at >= 0", name="ck_admission_retirement_time"),
        sa.PrimaryKeyConstraint("family_id"))


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM scheme_admission_retirements")):
        raise RuntimeError("Preserve admission retirement evidence before downgrading schema0029")
    op.drop_table("scheme_admission_retirements")
