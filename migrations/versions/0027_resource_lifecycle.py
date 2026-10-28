"""Desired resource activations and authenticated lifecycle evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("resource_activations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("producer_sha256", sa.String(64), nullable=False),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_index("ix_resource_activations_scheme_id", "resource_activations", ["scheme_id"])
    op.create_table("resource_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("repository_id", sa.String(32), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("scheme_id", sa.String(100), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("activation_id", sa.String(64), nullable=False),
        sa.Column("received_at", sa.Float(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("repository_id", "run_id", "run_attempt", "kind", "scheme_id", "version", name="uq_resource_event_run"))
    op.create_index("ix_resource_events_scheme_id", "resource_events", ["scheme_id"])
    op.create_index("ix_resource_events_activation_id", "resource_events", ["activation_id"])


def downgrade():
    for table in ("resource_events", "resource_activations"):
        if op.get_bind().scalar(sa.text("SELECT count(*) FROM " + table)):
            raise RuntimeError("Preserve resource lifecycle evidence before downgrading schema 0027")
    op.drop_table("resource_events")
    op.drop_table("resource_activations")
