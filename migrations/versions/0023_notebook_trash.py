"""Reversible deletion of unsubmitted notebook drafts."""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notebook_deletions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("notebook_notes.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("deleted_at", sa.Float(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("restored_at", sa.Float(), nullable=True),
        sa.Column("restore_key", sa.String(36), nullable=True),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_delete_key"),
        sa.UniqueConstraint("author_id", "restore_key", name="uq_notebook_restore_key"))
    op.create_index("ix_notebook_deletions_note_id", "notebook_deletions", ["note_id"])
    op.create_index("uq_notebook_active_deletion", "notebook_deletions", ["note_id"], unique=True,
                    sqlite_where=sa.text("restored_at IS NULL"), postgresql_where=sa.text("restored_at IS NULL"))


def downgrade():
    op.drop_table("notebook_deletions")
