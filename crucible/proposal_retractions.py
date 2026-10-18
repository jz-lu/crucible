"""Author withdrawals, independent of review outcomes and live admission.

Callers authenticate a browser mutation and lock the target first. A withdrawal
is terminal for that proposal/request; it never edits its frozen evidence.
"""
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

SCHEME = "scheme_admission"
RESEARCH = "research_request"


class ProposalRetraction(Base):
    __tablename__ = "proposal_retractions"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_proposal_retraction_key"),
        CheckConstraint("target_kind IN ('scheme_admission', 'research_request')", name="ck_retraction_target_kind"),
    )
    target_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    target_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    supporting_note: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class SupportingNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=1, max_length=108)
    revision: int = Field(ge=1, le=100, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RetractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)
    supporting_note: SupportingNote | None = None
    idempotency_key: UUID

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        from .notebook import _inspect
        _inspect(value)
        return value


class SchemeRetractionInput(RetractionInput):
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResearchRetractionInput(RetractionInput):
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def get(session, kind, identifier):
    return session.get(ProposalRetraction, (kind, identifier))


def target_ids(kind):
    return select(ProposalRetraction.target_id).where(ProposalRetraction.target_kind == kind)


def ensure_active(session, kind, identifier):
    if get(session, kind, identifier) is not None:
        raise HTTPException(409, "This proposal was retracted by its author. Create a fresh proposal to reconsider it.")


def compact(created_at):
    return {"retracted": True, "retraction": {"created_at": created_at}} if created_at is not None else {}


def detail(session, record, user):
    if record is None:
        return {"retracted": False, "retraction": None}
    support = None
    if record.supporting_note is not None and user is not None:
        from .research_reviews import source_snapshot
        try:
            source_snapshot(session, user, SupportingNote.model_validate(record.supporting_note))
            support = record.supporting_note
        except (HTTPException, ValueError):
            # Inspecting a withdrawal never grants access to a linked private
            # note; even its identifier must remain hidden from other readers.
            pass
    return {"retracted": True, "retraction": {"created_at": record.created_at,
        "actor_id": record.actor_id, "target_revision": record.target_revision,
        "content_sha256": record.content_sha256, "reason": record.reason,
        "supporting_note": support}}


def withdraw(session, kind, target, user, now, body, *, revision, digest):
    from . import notebook, research_reviews
    if target.author_id != user.id:
        raise HTTPException(404, "Proposal not found.")
    commitment = notebook._hash({"target_kind": kind, "target_id": target.id,
        **body.model_dump(mode="json", exclude={"idempotency_key"})})
    replay = session.scalar(select(ProposalRetraction).where(ProposalRetraction.actor_id == user.id,
        ProposalRetraction.idempotency_key == str(body.idempotency_key)))
    if replay is not None:
        if replay.request_sha256 != commitment:
            raise HTTPException(409, "This withdrawal identifier was already used for a different request.")
        return replay
    ensure_active(session, kind, target.id)
    if kind == SCHEME:
        matches = body.expected_revision == revision and body.content_sha256 == digest
    else:
        matches = body.packet_sha256 == digest
    if not matches:
        raise HTTPException(409, "Read the exact current proposal or frozen evidence packet before retracting it.")
    if body.supporting_note is not None:
        research_reviews.source_snapshot(session, user, body.supporting_note)
    record = ProposalRetraction(target_kind=kind, target_id=target.id, actor_id=user.id,
        created_at=now, target_revision=revision, content_sha256=digest, reason=body.reason,
        supporting_note=body.supporting_note.model_dump() if body.supporting_note is not None else None,
        idempotency_key=str(body.idempotency_key), request_sha256=commitment)
    session.add(record)
    session.flush()
    return record
