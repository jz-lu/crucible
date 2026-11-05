"""Durable screening work, cost accounting and non-human publication provenance."""
from sqlalchemy import BigInteger, CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ScreeningJob(Base):
    __tablename__ = 'publication_screening_jobs'
    __table_args__ = (
        UniqueConstraint('publication_id', 'policy_id', name='uq_screening_publication_policy'),
        Index('ix_screening_due', 'status', 'available_at', 'created_at'),
        Index('ix_screening_author_started', 'author_id', 'last_started_at'),
        CheckConstraint('attempt_count >= 0', name='ck_screening_attempt_count'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    publication_id: Mapped[str] = mapped_column(ForeignKey('notebook_publications.id'))
    revision_id: Mapped[str] = mapped_column(ForeignKey('notebook_revisions.id'))
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    content_sha256: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    policy: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float)
    last_started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)


class ScreeningAttempt(Base):
    __tablename__ = 'publication_screening_attempts'
    __table_args__ = (
        UniqueConstraint('job_id', 'number', name='uq_screening_attempt_number'),
        UniqueConstraint('lease_token', name='uq_screening_attempt_lease'),
        CheckConstraint('reserved_micro_usd >= 0', name='ck_screening_attempt_reserved'),
        CheckConstraint('charged_micro_usd IS NULL OR charged_micro_usd >= 0', name='ck_screening_attempt_charged'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('publication_screening_jobs.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[str] = mapped_column(String(36))
    budget_month: Mapped[str] = mapped_column(ForeignKey('publication_screening_budgets.month'))
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger)
    charged_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_known: Mapped[bool | None] = mapped_column(nullable=True)
    started_at: Mapped[float] = mapped_column(Float)
    lease_expires_at: Mapped[float] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ScreeningBudget(Base):
    __tablename__ = 'publication_screening_budgets'
    __table_args__ = (
        CheckConstraint('spent_micro_usd >= 0', name='ck_screening_budget_spent'),
        CheckConstraint('reserved_micro_usd >= 0', name='ck_screening_budget_reserved'),
    )
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    spent_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)


class ScreeningControl(Base):
    __tablename__ = 'publication_screening_control'
    __table_args__ = (
        CheckConstraint('id = 1', name='ck_screening_single_control'),
        CheckConstraint('monthly_budget_micro_usd >= 0', name='ck_screening_control_budget'),
        CheckConstraint("mode IN ('disabled','shadow','automatic')", name='ck_screening_control_mode'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    mode: Mapped[str] = mapped_column(String(16), default='disabled')
    policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    monthly_budget_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
    active_job_id: Mapped[str | None] = mapped_column(ForeignKey('publication_screening_jobs.id'), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[float] = mapped_column(Float)


class AutomaticPublication(Base):
    __tablename__ = 'notebook_automatic_publications'
    __table_args__ = (
        UniqueConstraint('job_id', name='uq_automatic_publication_job'),
        UniqueConstraint('attempt_id', name='uq_automatic_publication_attempt'),
    )
    publication_id: Mapped[str] = mapped_column(ForeignKey('notebook_publications.id'), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('publication_screening_jobs.id'))
    attempt_id: Mapped[str] = mapped_column(ForeignKey('publication_screening_attempts.id'))
    revision_id: Mapped[str] = mapped_column(ForeignKey('notebook_revisions.id'))
    content_sha256: Mapped[str] = mapped_column(String(64))
    policy_id: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(String(160))
    actor: Mapped[str] = mapped_column(String(80))
    result_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
