"""Historical table declarations for the retired Findings and investigations UI.

Retain these models and the original migrations so existing databases and
backups keep their records. This module defines no routes or active workflow.
Current research uses the account-backed notebook and its scoped reviews.
"""
from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Contribution(Base):
    __tablename__ = "contributions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[float] = mapped_column(Float)


class ContributionRevision(Base):
    __tablename__ = "contribution_revisions"
    __table_args__ = (
        UniqueConstraint("contribution_id", "revision", name="uq_contribution_revision"),
        Index("ix_contribution_target_time", "target_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contribution_id: Mapped[str] = mapped_column(ForeignKey("contributions.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    target_id: Mapped[str] = mapped_column(String(100))
    target_version: Mapped[str] = mapped_column(String(32))
    profile_ids: Mapped[list] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(200))
    claim: Mapped[str] = mapped_column(Text)
    conditions: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(32))
    evidence_kind: Mapped[str] = mapped_column(String(32))
    time_cost: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_cost: Mapped[str | None] = mapped_column(Text, nullable=True)
    success_probability: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_contribution_id: Mapped[str | None] = mapped_column(ForeignKey("contributions.id"), nullable=True, index=True)
    verified_submission_id: Mapped[str | None] = mapped_column(ForeignKey("submissions.id"), nullable=True)
