"""One revision-pinned agent rating per account and note."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("note_ratings",
        sa.Column("note_id", sa.String(108), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.CheckConstraint("value IN (0, 1, 2)", name="ck_note_rating_value"),
        sa.CheckConstraint("version >= 1", name="ck_note_rating_version"))


def downgrade():
    op.drop_table("note_ratings")
