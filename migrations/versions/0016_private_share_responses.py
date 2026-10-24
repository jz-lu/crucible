"""Recipient notebook organization, independent of author reading grants."""
from alembic import op
import sqlalchemy as sa

revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notebook_private_shares', sa.Column('state', sa.String(16), nullable=False,
        server_default='pending'))
    op.add_column('notebook_private_shares', sa.Column('response_version', sa.Integer(), nullable=False,
        server_default='0'))
    # Preserve the notebook visibility of every existing reading grant. New
    # grants (including regrants after revocation) require recipient acceptance.
    op.execute("UPDATE notebook_private_shares SET state = 'accepted'")
    op.create_index('ix_notebook_private_share_inbox', 'notebook_private_shares',
        ['recipient_id', 'state', 'revoked_at', 'shared_at', 'id'])
    op.create_table('notebook_private_share_responses',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('share_id', sa.String(36), sa.ForeignKey('notebook_private_shares.id'), nullable=False),
        sa.Column('recipient_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('before_state', sa.String(16), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('response_version', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('recipient_id', 'idempotency_key', name='uq_notebook_share_response_key'),
        sa.UniqueConstraint('share_id', 'response_version', name='uq_notebook_share_response_version'))


def downgrade():
    op.drop_table('notebook_private_share_responses')
    op.drop_index('ix_notebook_private_share_inbox', table_name='notebook_private_shares')
    op.drop_column('notebook_private_shares', 'response_version')
    op.drop_column('notebook_private_shares', 'state')
