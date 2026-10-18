"""Research requests and scoped assessments, separate from scheme activation."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_review_requests',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('title', sa.String(240), nullable=False),
        sa.Column('question', sa.String(10000), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('author_id', 'idempotency_key', name='uq_research_request_key'))
    op.create_index('ix_research_review_requests_author_id', 'research_review_requests', ['author_id'])
    op.create_table('research_assessments',
        sa.Column('request_id', sa.String(36), sa.ForeignKey('research_review_requests.id'), primary_key=True),
        sa.Column('reviewer_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('outcome', sa.String(32), nullable=False),
        sa.Column('checks', sa.JSON(), nullable=False),
        sa.Column('findings', sa.String(20000), nullable=False),
        sa.Column('limitations', sa.String(10000), nullable=False),
        sa.Column('test_only_self_review', sa.Boolean(), nullable=False),
        sa.Column('idempotency_key', sa.String(36), nullable=False),
        sa.Column('request_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('reviewer_id', 'idempotency_key', name='uq_research_assessment_key'))


def downgrade():
    op.drop_table('research_assessments')
    op.drop_table('research_review_requests')
