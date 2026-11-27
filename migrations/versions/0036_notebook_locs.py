"""Reserve immutable short locs for exact Markdown passages."""
from alembic import op
import sqlalchemy as sa

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notebook_locs",
        sa.Column("number", sa.Integer(), sa.Identity(always=True), primary_key=True),
        sa.Column("note_id", sa.String(108), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("passage", sa.String(90), nullable=False),
        sa.UniqueConstraint("note_id", "revision", "content_sha256", "passage", name="uq_notebook_loc_source"),
        sqlite_autoincrement=True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM notebook_locs")):
        raise RuntimeError("Passage locs cannot be forgotten or reassigned; use a reviewed recovery plan.")
    op.drop_table("notebook_locs")
