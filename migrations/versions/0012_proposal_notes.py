"""Pin scheme review to exact durable notebook revisions; no automatic sharing."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("proposal_note_links",
        sa.Column("proposal_revision_id", sa.String(36), sa.ForeignKey("protocol_proposal_revisions.id"), primary_key=True),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("protocol_proposals.id"), nullable=False),
        sa.Column("note_revision_id", sa.String(36), sa.ForeignKey("notebook_revisions.id"), nullable=False),
        sa.Column("note_id", sa.String(36), sa.ForeignKey("notebook_notes.id"), nullable=False),
        sa.Column("author_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(36), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("note_revision_id", name="uq_proposal_note_revision"),
        sa.UniqueConstraint("author_id", "idempotency_key", name="uq_proposal_note_request"))
    op.create_index("ix_proposal_note_links_proposal_id", "proposal_note_links", ["proposal_id"])
    op.create_index("ix_proposal_note_links_note_id", "proposal_note_links", ["note_id"])


def downgrade():
    op.drop_table("proposal_note_links")
