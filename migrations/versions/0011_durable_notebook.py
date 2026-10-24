"""Durable private notebook revisions and exact-revision publication decisions.

Revision ID: 0011
Revises: 0010
No browser drafts, private proposals or illustrative examples are imported.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notebook_notes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("latest_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=True),
        sa.Column("source_metadata", sa.JSON(), nullable=True),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_create_key"),
        sa.UniqueConstraint("author_id", "source_id", name="uq_notebook_import_identity"))
    op.create_index("ix_notebook_author_created", "notebook_notes", ["author_id", "created_at", "id"])
    op.create_table("notebook_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("notebook_notes.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.JSON(), nullable=True),
        sa.UniqueConstraint("note_id", "revision", name="uq_notebook_revision"),
        sa.UniqueConstraint("note_id", "idempotency_key", name="uq_notebook_revision_key"))
    op.create_index("ix_notebook_revisions_note_id", "notebook_revisions", ["note_id"])
    op.create_index("ix_notebook_revision_created", "notebook_revisions", ["created_at", "id"])
    op.create_table("notebook_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision_id", sa.String(36), sa.ForeignKey("notebook_revisions.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_at", sa.Float(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(10000), nullable=True),
        sa.Column("review_key", sa.String(36), nullable=True),
        sa.Column("review_sha256", sa.String(64), nullable=True),
        sa.Column("test_only_self_review", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("revision_id", name="uq_notebook_publication_revision"),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_publication_key"),
        sa.UniqueConstraint("reviewer_id", "review_key", name="uq_notebook_publication_review_key"))
    op.create_index("ix_notebook_publications_revision_id", "notebook_publications", ["revision_id"])
    op.create_table("notebook_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("workspace", sa.JSON(), nullable=False),
        sa.Column("id_map", sa.JSON(), nullable=False),
        sa.Column("added_notes", sa.Integer(), nullable=False),
        sa.Column("added_revisions", sa.Integer(), nullable=False),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_import_key"),
        sa.UniqueConstraint("author_id", "sequence", name="uq_notebook_import_sequence"))
    op.create_index("ix_notebook_imports_author_id", "notebook_imports", ["author_id"])


def downgrade():
    op.drop_table("notebook_imports")
    op.drop_table("notebook_publications")
    op.drop_table("notebook_revisions")
    op.drop_table("notebook_notes")
