"""Permanent numeric notebook references, preserving all existing identities.

PostgreSQL operators must grant the backup role SELECT on the new identity
sequence. Runtime needs only its existing table DML grants. Do not reset this
sequence or reconstruct it from row count during backup/restore or transfer.
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    numbers = op.create_table("notebook_numbers",
        sa.Column("number", sa.Integer(), sa.Identity(always=True), primary_key=True),
        sa.Column("note_id", sa.String(108), nullable=False),
        sa.UniqueConstraint("note_id", name="uq_notebook_number_identity"),
        sqlite_autoincrement=True)
    notes = sa.table("notebook_notes", sa.column("id"), sa.column("created_at"))
    op.execute(numbers.insert().from_select(["note_id"],
        sa.select(notes.c.id).order_by(notes.c.created_at, notes.c.id)))


def downgrade():
    # Once allocated, these references must never silently acquire new targets.
    # A rollback of populated aliases requires an explicit preservation plan.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM notebook_numbers")):
        raise RuntimeError("Cannot discard allocated notebook numbers.")
    op.drop_table("notebook_numbers")
