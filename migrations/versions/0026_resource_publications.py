"""Durable resource publications from the approved measurement repository."""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("resource_publications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("publication", sa.JSON(), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("bundle", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint("repository_id", "run_id", "run_attempt", "scheme_id", "version", name="uq_resource_publication_run"))
    op.create_index("ix_resource_publications_scheme_id", "resource_publications", ["scheme_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM resource_publications")):
        raise RuntimeError("Preserve resource evidence before downgrading schema 0026")
    op.drop_table("resource_publications")
