"""Schema-only registration for the 0029-compatible old-application fallback.

Imported only by Alembic. This module has no routes, award calculation or write
helpers. The normal release defines its model in scoring_awards instead; full
source-directory replacement removes this fallback-only module on deployment.
"""
from sqlalchemy import CheckConstraint, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class SchemeAdmissionRetirement(Base):
    __tablename__ = "scheme_admission_retirements"
    __table_args__ = (CheckConstraint("created_at >= 0", name="ck_admission_retirement_time"),)
    family_id: Mapped[str] = mapped_column(ForeignKey("scheme_admission_credits.family_id"), primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
