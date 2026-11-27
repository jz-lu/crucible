"""Separate author visibility from note content and publication screening."""
from alembic import op
import sqlalchemy as sa

revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notebook_visibility_changes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('note_id', sa.String(36), sa.ForeignKey('notebook_notes.id'), nullable=False),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('private', sa.Boolean(), nullable=False),
        sa.Column('revision_ids', sa.JSON(), nullable=False),
        sa.Column('previous_publications', sa.JSON(), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('author_id', 'idempotency_key', name='uq_note_visibility_key'))
    op.create_index('ix_notebook_visibility_changes_note_id', 'notebook_visibility_changes', ['note_id'])
    op.create_table('notebook_revision_visibility',
        sa.Column('revision_id', sa.String(36), sa.ForeignKey('notebook_revisions.id'), primary_key=True),
        sa.Column('private', sa.Boolean(), nullable=False),
        sa.Column('change_id', sa.String(36), sa.ForeignKey('notebook_visibility_changes.id'), nullable=False))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM notebook_visibility_changes')):
        raise RuntimeError('Visibility history is retained; downgrade needs a reviewed recovery plan.')
    op.drop_table('notebook_revision_visibility')
    op.drop_table('notebook_visibility_changes')
