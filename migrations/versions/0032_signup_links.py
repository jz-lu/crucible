"""Expiring signup links that issue existing recipient-bound invitations.

Revision ID: 0032
Revises: 0031
Additive tables only. No link is created and no existing access is changed.
"""
from alembic import op
import sqlalchemy as sa

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('beta_signup_links',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('token_hash', sa.String(64), unique=True, nullable=False),
        sa.Column('label', sa.String(100), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('expires_at', sa.Float(), nullable=False),
        sa.Column('revoked_at', sa.Float(), nullable=True))
    op.create_index('ix_beta_signup_links_expires_at', 'beta_signup_links', ['expires_at'])
    op.create_table('beta_signup_invitations',
        sa.Column('invitation_id', sa.String(36), sa.ForeignKey('beta_invitations.id'), primary_key=True, nullable=False),
        sa.Column('signup_link_id', sa.String(36), sa.ForeignKey('beta_signup_links.id'), nullable=False),
        sa.Column('email', sa.String(254), nullable=False),
        sa.UniqueConstraint('signup_link_id', 'email', name='uq_signup_link_email'))
    op.create_index('ix_beta_signup_invitations_signup_link_id', 'beta_signup_invitations', ['signup_link_id'])


def downgrade():
    connection = op.get_bind()
    for table in ('beta_signup_invitations', 'beta_signup_links'):
        if connection.scalar(sa.text('SELECT count(*) FROM ' + table)):
            raise RuntimeError('Preserve signup links and admission provenance before downgrading schema0032')
    op.drop_table('beta_signup_invitations')
    op.drop_table('beta_signup_links')
