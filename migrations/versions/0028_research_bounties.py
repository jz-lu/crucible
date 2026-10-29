"""Separate durable research bounties and expiring monthly action allowance."""
from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('bounty_policies',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('effective_month', sa.String(length=7), nullable=False),
        sa.Column('configuration', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('effective_month'))

    op.create_table('bounty_wallets',
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), primary_key=True, nullable=False),
        sa.Column('available_units', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('last_month', sa.String(length=7), nullable=False),
        sa.CheckConstraint('available_units >= 0', name='ck_bounty_wallet_nonnegative'))

    op.create_table('bounty_tasks',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('owner_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('terms_sha256', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=160), nullable=False),
        sa.Column('summary', sa.String(length=1200), nullable=False),
        sa.Column('background_tags', sa.JSON(), nullable=False),
        sa.Column('scope', sa.Integer(), nullable=False),
        sa.Column('reward_points', sa.Integer(), nullable=False),
        sa.Column('base_reward_points', sa.Integer(), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('policy_id', sa.String(length=64), sa.ForeignKey('bounty_policies.id'), nullable=True),
        sa.Column('reservation_id', sa.String(length=36), nullable=True),
        sa.Column('qualified_submission_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.Float(), nullable=False))
    op.create_index('ix_bounty_task_board', 'bounty_tasks', ['status', 'scope', 'created_at', 'id'], unique=False)
    op.create_index('ix_bounty_tasks_owner_id', 'bounty_tasks', ['owner_id'], unique=False)

    op.create_table('bounty_terms',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('terms', sa.JSON(), nullable=False),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('created_by', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('task_id', 'revision', name='uq_bounty_terms_revision'))
    op.create_index('ix_bounty_terms_task_id', 'bounty_terms', ['task_id'], unique=False)

    op.create_table('bounty_allowance_events',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('policy_id', sa.String(length=64), sa.ForeignKey('bounty_policies.id'), nullable=False),
        sa.Column('month', sa.String(length=7), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('event_key', sa.String(length=120), nullable=False),
        sa.Column('wallet_version', sa.Integer(), nullable=False),
        sa.Column('available_delta', sa.Integer(), nullable=False),
        sa.Column('units', sa.Integer(), nullable=False),
        sa.Column('reward_points', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), nullable=True),
        sa.Column('task_sequence', sa.Integer(), nullable=True),
        sa.Column('terms_sha256', sa.String(length=64), nullable=True),
        sa.Column('original_event_id', sa.String(length=36), sa.ForeignKey('bounty_allowance_events.id'), nullable=True),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('original_event_id', 'kind', name='uq_bounty_allowance_compensation'),
        sa.UniqueConstraint('user_id', 'event_key', name='uq_bounty_allowance_event'),
        sa.UniqueConstraint('user_id', 'wallet_version', name='uq_bounty_wallet_event_version'))
    op.create_index('ix_bounty_allowance_events_user_id', 'bounty_allowance_events', ['user_id'], unique=False)
    op.create_index('ix_bounty_allowance_task_sequence', 'bounty_allowance_events', ['task_id', 'task_sequence'], unique=False)

    op.create_table('bounty_submissions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), nullable=False),
        sa.Column('terms_id', sa.String(length=36), sa.ForeignKey('bounty_terms.id'), nullable=False),
        sa.Column('author_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('note_id', sa.String(length=36), sa.ForeignKey('notebook_notes.id'), nullable=False),
        sa.Column('note_revision', sa.Integer(), nullable=False),
        sa.Column('note_sha256', sa.String(length=64), nullable=False),
        sa.Column('claim', sa.String(length=16), nullable=False),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('receipt_order', sa.Integer(), nullable=False),
        sa.Column('reward_boundary', sa.Integer(), nullable=False),
        sa.Column('offered_reward_points', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('task_id', 'author_id', 'note_id', 'note_revision', name='uq_bounty_submission_evidence'),
        sa.UniqueConstraint('task_id', 'receipt_order', name='uq_bounty_submission_order'))
    op.create_index('ix_bounty_submissions_author_id', 'bounty_submissions', ['author_id'], unique=False)
    op.create_index('ix_bounty_submissions_task_id', 'bounty_submissions', ['task_id'], unique=False)
    op.create_index('uq_bounty_active_claim', 'bounty_submissions', ['task_id', 'author_id'], unique=True, sqlite_where=sa.text("claim = 'solution' AND status IN ('pending', 'qualified')"), postgresql_where=sa.text("claim = 'solution' AND status IN ('pending', 'qualified')"))

    op.create_table('bounty_decisions',
        sa.Column('id', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), nullable=False),
        sa.Column('submission_id', sa.String(length=36), sa.ForeignKey('bounty_submissions.id'), nullable=True),
        sa.Column('terms_id', sa.String(length=36), sa.ForeignKey('bounty_terms.id'), nullable=False),
        sa.Column('evidence_sha256', sa.String(length=64), nullable=True),
        sa.Column('actor_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('decision', sa.String(length=24), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('checks', sa.JSON(), nullable=False),
        sa.Column('task_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))
    op.create_index('ix_bounty_decisions_submission_id', 'bounty_decisions', ['submission_id'], unique=False)
    op.create_index('ix_bounty_decisions_task_id', 'bounty_decisions', ['task_id'], unique=False)

    op.create_table('bounty_awards',
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), primary_key=True, nullable=False),
        sa.Column('submission_id', sa.String(length=36), sa.ForeignKey('bounty_submissions.id'), nullable=False),
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('policy_id', sa.String(length=64), sa.ForeignKey('bounty_policies.id'), nullable=False),
        sa.Column('terms_id', sa.String(length=36), sa.ForeignKey('bounty_terms.id'), nullable=False),
        sa.Column('poster_decision_id', sa.String(length=36), sa.ForeignKey('bounty_decisions.id'), nullable=False),
        sa.Column('pipeline_decision_id', sa.String(length=36), sa.ForeignKey('bounty_decisions.id'), nullable=False),
        sa.Column('reward_boundary', sa.Integer(), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False),
        sa.UniqueConstraint('submission_id'))
    op.create_index('ix_bounty_awards_user_id', 'bounty_awards', ['user_id'], unique=False)

    op.create_table('bounty_write_receipts',
        sa.Column('user_id', sa.String(length=36), sa.ForeignKey('users.id'), primary_key=True, nullable=False),
        sa.Column('idempotency_key', sa.String(length=36), primary_key=True, nullable=False),
        sa.Column('request_sha256', sa.String(length=64), nullable=False),
        sa.Column('task_id', sa.String(length=36), sa.ForeignKey('bounty_tasks.id'), nullable=False),
        sa.Column('effect', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.Float(), nullable=False))

    # Policy only: there are no example tasks, accounts, grants or awards.
    policy = sa.table('bounty_policies', sa.column('id', sa.String),
                      sa.column('effective_month', sa.String), sa.column('configuration', sa.JSON),
                      sa.column('created_by', sa.String), sa.column('created_at', sa.Float))
    op.bulk_insert(policy, [{
        'id': 'c392e762f76b1dd9e1db2e3db06e33ef238e0eda4977618ae53592ef6753a0b7',
        'effective_month': '2026-10',
        'configuration': {'monthly_units': 10, 'proposal_cost_units': 1, 'boost_cost_units': 1, 'points_per_boost': 100, 'proposal_reward_points': 100, 'carryover': 'expires', 'refund_rule': 'original_month', 'claim_order': 'earliest_qualifying'},
        'created_by': None, 'created_at': 1793232000.0
    }])


def downgrade():
    tables = ('bounty_write_receipts', 'bounty_awards', 'bounty_decisions', 'bounty_submissions', 'bounty_allowance_events', 'bounty_terms', 'bounty_tasks', 'bounty_wallets', 'bounty_policies')
    for name in tables:
        if name == 'bounty_policies':
            count = op.get_bind().scalar(sa.text("SELECT count(*) FROM bounty_policies WHERE id != 'c392e762f76b1dd9e1db2e3db06e33ef238e0eda4977618ae53592ef6753a0b7'"))
        else:
            count = op.get_bind().scalar(sa.text("SELECT count(*) FROM " + name))
        if count:
            raise RuntimeError("Preserve bounty research and ledger evidence before downgrading schema 0028")
    for name in tables:
        op.drop_table(name)
