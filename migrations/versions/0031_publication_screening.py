"""Durable publication screening and explicit automated provenance.

Revision ID: 0031
Revises: 0030
No existing request is queued and no provider is enabled by this migration.
"""
from alembic import op
import sqlalchemy as sa

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notebook_publications', sa.Column('decision_source', sa.String(16), nullable=True))
    op.execute(sa.text("UPDATE notebook_publications SET decision_source = 'human' "
                       "WHERE decision IS NOT NULL AND reviewer_id IS NOT NULL"))
    op.create_table('publication_screening_jobs',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('publication_id', sa.String(36), sa.ForeignKey('notebook_publications.id'), nullable=False),
        sa.Column('revision_id', sa.String(36), sa.ForeignKey('notebook_revisions.id'), nullable=False),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('policy_id', sa.String(64), nullable=False),
        sa.Column('policy', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.Float(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('last_started_at', sa.Float(), nullable=True),
        sa.Column('finished_at', sa.Float(), nullable=True),
        sa.Column('error_code', sa.String(48), nullable=True),
        sa.UniqueConstraint('publication_id', 'policy_id', name='uq_screening_publication_policy'),
        sa.CheckConstraint('attempt_count >= 0', name='ck_screening_attempt_count'))
    op.create_index('ix_screening_due', 'publication_screening_jobs', ['status', 'available_at', 'created_at'])
    op.create_index('ix_screening_author_started', 'publication_screening_jobs', ['author_id', 'last_started_at'])
    op.create_table('publication_screening_budgets',
        sa.Column('month', sa.String(7), primary_key=True, nullable=False),
        sa.Column('spent_micro_usd', sa.BigInteger(), nullable=False),
        sa.Column('reserved_micro_usd', sa.BigInteger(), nullable=False),
        sa.CheckConstraint('spent_micro_usd >= 0', name='ck_screening_budget_spent'),
        sa.CheckConstraint('reserved_micro_usd >= 0', name='ck_screening_budget_reserved'))
    op.create_table('publication_screening_attempts',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('publication_screening_jobs.id'), nullable=False),
        sa.Column('number', sa.Integer(), nullable=False),
        sa.Column('lease_token', sa.String(36), nullable=False),
        sa.Column('budget_month', sa.String(7), sa.ForeignKey('publication_screening_budgets.month'), nullable=False),
        sa.Column('reserved_micro_usd', sa.BigInteger(), nullable=False),
        sa.Column('charged_micro_usd', sa.BigInteger(), nullable=True),
        sa.Column('usage_known', sa.Boolean(), nullable=True),
        sa.Column('started_at', sa.Float(), nullable=False),
        sa.Column('lease_expires_at', sa.Float(), nullable=False),
        sa.Column('finished_at', sa.Float(), nullable=True),
        sa.Column('outcome', sa.String(32), nullable=True),
        sa.Column('error_code', sa.String(48), nullable=True),
        sa.Column('model_id', sa.String(160), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('result_sha256', sa.String(64), nullable=True),
        sa.UniqueConstraint('job_id', 'number', name='uq_screening_attempt_number'),
        sa.UniqueConstraint('lease_token', name='uq_screening_attempt_lease'),
        sa.CheckConstraint('reserved_micro_usd >= 0', name='ck_screening_attempt_reserved'),
        sa.CheckConstraint('charged_micro_usd IS NULL OR charged_micro_usd >= 0', name='ck_screening_attempt_charged'))
    op.create_index('ix_publication_screening_attempts_job_id', 'publication_screening_attempts', ['job_id'])
    op.create_table('publication_screening_control',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=False, nullable=False),
        sa.Column('mode', sa.String(16), nullable=False),
        sa.Column('policy_id', sa.String(64), nullable=True),
        sa.Column('monthly_budget_micro_usd', sa.BigInteger(), nullable=False),
        sa.Column('active_job_id', sa.String(36), sa.ForeignKey('publication_screening_jobs.id'), nullable=True),
        sa.Column('lease_token', sa.String(36), nullable=True),
        sa.Column('lease_expires_at', sa.Float(), nullable=True),
        sa.Column('updated_at', sa.Float(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_screening_single_control'),
        sa.CheckConstraint('monthly_budget_micro_usd >= 0', name='ck_screening_control_budget'),
        sa.CheckConstraint("mode IN ('disabled','shadow','automatic')", name='ck_screening_control_mode'))
    op.create_table('notebook_automatic_publications',
        sa.Column('publication_id', sa.String(36), sa.ForeignKey('notebook_publications.id'), primary_key=True, nullable=False),
        sa.Column('job_id', sa.String(36), sa.ForeignKey('publication_screening_jobs.id'), nullable=False),
        sa.Column('attempt_id', sa.String(36), sa.ForeignKey('publication_screening_attempts.id'), nullable=False),
        sa.Column('revision_id', sa.String(36), sa.ForeignKey('notebook_revisions.id'), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('policy_id', sa.String(64), nullable=False),
        sa.Column('model_id', sa.String(160), nullable=False),
        sa.Column('actor', sa.String(80), nullable=False),
        sa.Column('result_sha256', sa.String(64), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('job_id', name='uq_automatic_publication_job'),
        sa.UniqueConstraint('attempt_id', name='uq_automatic_publication_attempt'))
    control = sa.table('publication_screening_control', sa.column('id', sa.Integer),
        sa.column('mode', sa.String), sa.column('monthly_budget_micro_usd', sa.BigInteger),
        sa.column('updated_at', sa.Float))
    op.bulk_insert(control, [{'id': 1, 'mode': 'disabled', 'monthly_budget_micro_usd': 0, 'updated_at': 0.0}])


def downgrade():
    connection = op.get_bind()
    durable = ('notebook_automatic_publications', 'publication_screening_attempts', 'publication_screening_jobs')
    for table in durable:
        if connection.scalar(sa.text('SELECT count(*) FROM ' + table)):
            raise RuntimeError('Preserve screening work and publication provenance before downgrading schema0031')
    if connection.scalar(sa.text('SELECT count(*) FROM publication_screening_budgets '
                                 'WHERE spent_micro_usd <> 0 OR reserved_micro_usd <> 0')):
        raise RuntimeError('Preserve screening cost evidence before downgrading schema0031')
    if connection.scalar(sa.text("SELECT count(*) FROM notebook_publications WHERE decision_source = 'automated'")):
        raise RuntimeError('Preserve automatic publication provenance before downgrading schema0031')
    for table in ('notebook_automatic_publications', 'publication_screening_control',
                  'publication_screening_attempts', 'publication_screening_budgets', 'publication_screening_jobs'):
        op.drop_table(table)
    with op.batch_alter_table('notebook_publications') as batch:
        batch.drop_column('decision_source')
