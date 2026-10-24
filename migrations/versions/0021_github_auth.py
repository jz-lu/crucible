"""Explicit GitHub identities and short-lived bound browser authorization."""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=True)
    op.create_table("github_identities",
        sa.Column("github_user_id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("github_login", sa.String(39), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("last_login_at", sa.Float(), nullable=False))
    op.create_table("github_flows",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("original_session_hash", sa.String(64), nullable=False),
        sa.Column("original_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("github_user_id", sa.String(32), nullable=True),
        sa.Column("github_login", sa.String(39), nullable=True),
        sa.Column("invitation_id", sa.String(36), sa.ForeignKey("beta_invitations.id"), nullable=True),
        sa.Column("invitation_encrypted", sa.Text(), nullable=True),
        sa.Column("return_route", sa.String(2048), nullable=False),
        sa.Column("result_session_hash", sa.String(64), nullable=True))
    op.create_index("ix_github_flows_original_session_hash", "github_flows", ["original_session_hash"])
    op.create_index("ix_github_flows_expires_at", "github_flows", ["expires_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM users WHERE password_hash IS NULL")):
        raise RuntimeError("Cannot remove GitHub sign-in while provider-only accounts exist; preserve their access first.")
    op.drop_index("ix_github_flows_expires_at", table_name="github_flows")
    op.drop_index("ix_github_flows_original_session_hash", table_name="github_flows")
    op.drop_table("github_flows")
    op.drop_table("github_identities")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.Text(), nullable=False)
