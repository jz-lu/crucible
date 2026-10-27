"""Optional researcher profiles without changing account or receipt identity."""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("researcher_profiles",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("affiliation", sa.String(200), nullable=False),
        sa.Column("position", sa.String(150), nullable=False),
        sa.Column("website", sa.String(2048), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("last_idempotency_key", sa.String(36), nullable=False),
        sa.Column("last_request_sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_researcher_profile_revision"))


def downgrade():
    op.drop_table("researcher_profiles")
