"""Durable task terms and separate allowance/award journals for research bounties."""
from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class BountyPolicy(Base):
    __tablename__ = 'bounty_policies'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    effective_month: Mapped[str] = mapped_column(String(7), unique=True)
    configuration: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class BountyWallet(Base):
    __tablename__ = 'bounty_wallets'
    __table_args__ = (CheckConstraint('available_units >= 0', name='ck_bounty_wallet_nonnegative'),)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    available_units: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_month: Mapped[str] = mapped_column(String(7))


class BountyTask(Base):
    __tablename__ = 'bounty_tasks'
    __table_args__ = (Index('ix_bounty_task_board', 'status', 'scope', 'created_at', 'id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    status: Mapped[str] = mapped_column(String(24))
    version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    terms_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(String(1200))
    background_tags: Mapped[list] = mapped_column(JSON)
    scope: Mapped[int] = mapped_column(Integer)
    reward_points: Mapped[int] = mapped_column(Integer, default=0)
    base_reward_points: Mapped[int] = mapped_column(Integer, default=0)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey('bounty_policies.id'), nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    qualified_submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class BountyTerms(Base):
    __tablename__ = 'bounty_terms'
    __table_args__ = (UniqueConstraint('task_id', 'revision', name='uq_bounty_terms_revision'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('bounty_tasks.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    terms: Mapped[dict] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Float)


class BountyAllowanceEvent(Base):
    __tablename__ = 'bounty_allowance_events'
    __table_args__ = (
        UniqueConstraint('user_id', 'event_key', name='uq_bounty_allowance_event'),
        UniqueConstraint('user_id', 'wallet_version', name='uq_bounty_wallet_event_version'),
        UniqueConstraint('original_event_id', 'kind', name='uq_bounty_allowance_compensation'),
        Index('ix_bounty_allowance_task_sequence', 'task_id', 'task_sequence'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey('bounty_policies.id'))
    month: Mapped[str] = mapped_column(String(7))
    kind: Mapped[str] = mapped_column(String(24))
    event_key: Mapped[str] = mapped_column(String(120))
    wallet_version: Mapped[int] = mapped_column(Integer)
    available_delta: Mapped[int] = mapped_column(Integer)
    units: Mapped[int] = mapped_column(Integer)
    reward_points: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(ForeignKey('bounty_tasks.id'), nullable=True)
    task_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_event_id: Mapped[str | None] = mapped_column(ForeignKey('bounty_allowance_events.id'), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class BountySubmission(Base):
    __tablename__ = 'bounty_submissions'
    __table_args__ = (
        UniqueConstraint('task_id', 'receipt_order', name='uq_bounty_submission_order'),
        UniqueConstraint('task_id', 'author_id', 'note_id', 'note_revision', name='uq_bounty_submission_evidence'),
        Index('uq_bounty_active_claim', 'task_id', 'author_id', unique=True,
              sqlite_where=text("claim = 'solution' AND status IN ('pending', 'qualified')"),
              postgresql_where=text("claim = 'solution' AND status IN ('pending', 'qualified')")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('bounty_tasks.id'), index=True)
    terms_id: Mapped[str] = mapped_column(ForeignKey('bounty_terms.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    note_id: Mapped[str] = mapped_column(ForeignKey('notebook_notes.id'))
    note_revision: Mapped[int] = mapped_column(Integer)
    note_sha256: Mapped[str] = mapped_column(String(64))
    claim: Mapped[str] = mapped_column(String(16))
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    receipt_order: Mapped[int] = mapped_column(Integer)
    reward_boundary: Mapped[int] = mapped_column(Integer)
    offered_reward_points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)


class BountyDecision(Base):
    __tablename__ = 'bounty_decisions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('bounty_tasks.id'), index=True)
    submission_id: Mapped[str | None] = mapped_column(ForeignKey('bounty_submissions.id'), nullable=True, index=True)
    terms_id: Mapped[str] = mapped_column(ForeignKey('bounty_terms.id'))
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(24))
    decision: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    checks: Mapped[dict] = mapped_column(JSON)
    task_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)


class BountyAward(Base):
    __tablename__ = 'bounty_awards'
    task_id: Mapped[str] = mapped_column(ForeignKey('bounty_tasks.id'), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey('bounty_submissions.id'), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey('bounty_policies.id'))
    terms_id: Mapped[str] = mapped_column(ForeignKey('bounty_terms.id'))
    poster_decision_id: Mapped[str] = mapped_column(ForeignKey('bounty_decisions.id'))
    pipeline_decision_id: Mapped[str] = mapped_column(ForeignKey('bounty_decisions.id'))
    reward_boundary: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)


class BountyWriteReceipt(Base):
    __tablename__ = 'bounty_write_receipts'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(ForeignKey('bounty_tasks.id'))
    effect: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)
