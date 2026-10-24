"""Retain original release pins and append explicit platform-build reviews."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("implementation_builds",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=True),
        sa.Column("baseline_source_sha256", sa.String(64), nullable=False),
        sa.Column("baseline_artifact_sha256", sa.String(64), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("manifest_text", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(200), nullable=False),
        sa.Column("review_basis", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.Float(), nullable=False),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["scheme_id", "version"],
            ["adapter_versions.scheme_id", "adapter_versions.version"], name="fk_implementation_build_release"))


def downgrade():
    op.drop_table("implementation_builds")
