"""Durable, opt-in local statement workflow rehearsal.

Revision ID: 0032
Revises: 0031
Only adds empty tables. No existing note, publication, challenge, score or
scientific selection changes. The application keeps this trial local-only.
"""
from alembic import op
import sqlalchemy as sa

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('local_statement_updates',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('packet', sa.JSON(), nullable=False),
        sa.Column('packet_sha256', sa.String(64), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('assessment', sa.JSON(), nullable=True),
        sa.UniqueConstraint('author_id', 'idempotency_key', name='uq_local_statement_update_key'))
    op.create_table('local_statement_current',
        sa.Column('target_id', sa.String(80), primary_key=True, nullable=False),
        sa.Column('adoption_id', sa.String(36), nullable=True))
    op.create_table('local_statement_adoptions',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('update_id', sa.String(36), sa.ForeignKey('local_statement_updates.id'), nullable=False),
        sa.Column('target_id', sa.String(80), nullable=False),
        sa.Column('reviewer_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('previous_id', sa.String(36), nullable=True),
        sa.UniqueConstraint('update_id'))
    op.create_index('ix_local_statement_adoptions_target_id', 'local_statement_adoptions', ['target_id'])


def downgrade():
    connection = op.get_bind()
    names = ('local_statement_adoptions', 'local_statement_updates', 'local_statement_current')
    for name in names:
        if connection.scalar(sa.text('SELECT count(*) FROM ' + name)):
            raise RuntimeError('Statement workflow history is retained; downgrade requires a reviewed recovery plan.')
    op.drop_index('ix_local_statement_adoptions_target_id', table_name='local_statement_adoptions')
    for name in names:
        op.drop_table(name)
