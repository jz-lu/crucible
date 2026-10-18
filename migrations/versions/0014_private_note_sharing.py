"""Author-controlled private notebook audiences and immutable sharing receipts."""
from alembic import op
import sqlalchemy as sa

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notebook_private_shares',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('note_id', sa.String(36), sa.ForeignKey('notebook_notes.id'), nullable=False),
        sa.Column('recipient_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('from_revision', sa.Integer(), nullable=False),
        sa.Column('shared_at', sa.Float(), nullable=False),
        sa.Column('revoked_at', sa.Float(), nullable=True))
    op.create_index('uq_notebook_active_private_share', 'notebook_private_shares', ['note_id', 'recipient_id'],
        unique=True, sqlite_where=sa.text('revoked_at IS NULL'), postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('ix_notebook_private_share_recipient', 'notebook_private_shares', ['recipient_id', 'revoked_at', 'note_id'])
    op.create_table('notebook_private_share_changes',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('note_id', sa.String(36), sa.ForeignKey('notebook_notes.id'), nullable=False),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('before_recipients', sa.JSON(), nullable=False),
        sa.Column('after_recipients', sa.JSON(), nullable=False),
        sa.Column('before_sharing_sha256', sa.String(64), nullable=False),
        sa.Column('after_sharing_sha256', sa.String(64), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('note_id', 'sequence', name='uq_notebook_share_change_sequence'),
        sa.UniqueConstraint('author_id', 'idempotency_key', name='uq_notebook_share_change_key'))
    op.create_index('ix_notebook_private_share_changes_note_id', 'notebook_private_share_changes', ['note_id'])


def downgrade():
    op.drop_table('notebook_private_share_changes')
    op.drop_table('notebook_private_shares')
