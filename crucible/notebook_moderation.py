"""Withdraw community access without rewriting evidence or scientific decisions."""
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Float, ForeignKey, String, UniqueConstraint, select, tuple_
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

NOTICE = "This revision was withdrawn from community visibility by a moderator. Its audit record is retained privately."


class PublicationWithdrawal(Base):
    __tablename__ = "notebook_publication_withdrawals"
    __table_args__ = (UniqueConstraint("moderator_id", "idempotency_key", name="uq_notebook_withdrawal_key"),)
    revision_id: Mapped[str] = mapped_column(ForeignKey("notebook_revisions.id"), primary_key=True)
    moderator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(10000))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class WithdrawalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, le=100, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=10000)
    idempotency_key: UUID

    @field_validator("reason")
    @classmethod
    def explanation(cls, value):
        from . import notebook
        notebook._inspect(value)
        if not value.strip():
            raise ValueError("Record the reason for withdrawing community access.")
        return value


def withdrawn_ids():
    return select(PublicationWithdrawal.revision_id)


def records(session, identifiers):
    if not identifiers:
        return {}
    return {row.revision_id: row for row in session.scalars(select(PublicationWithdrawal)
        .where(PublicationWithdrawal.revision_id.in_(identifiers)))}


def audit_data(record):
    # This detail is for the author and configured reviewers only. The public
    # notice deliberately omits a reason that could repeat private information.
    return {"created_at": record.created_at, "reason": record.reason, "scope": NOTICE}


def packet_is_withheld(session, record, user, settings):
    """Frozen copies retain their hashes but cannot bypass a source withdrawal."""
    from . import notebook, protocol_proposals
    if protocol_proposals.is_reviewer(settings, user):
        return False
    refs = [(source["note_id"], source["revision"], source["content_sha256"])
            for source in record.sources if source.get("author_id") != user.id
            and not source["note_id"].startswith("curated:")]
    if not refs:
        return False
    revision = notebook.NotebookRevision
    return session.scalar(select(PublicationWithdrawal.revision_id)
        .join(revision, revision.id == PublicationWithdrawal.revision_id)
        .where(tuple_(revision.note_id, revision.revision, revision.content_sha256).in_(refs)).limit(1)) is not None


def register_routes(app, db, settings, clock, account, throttle):
    from . import notebook, protocol_proposals

    @app.post("/api/notebook/notes/{note_id}/publication-withdrawals")
    def withdraw_publication(note_id: UUID, body: WithdrawalInput, request: Request):
        throttle(request, "notebook-moderation", 60, 3600)
        commitment = notebook._hash({"note_id": str(note_id),
            **body.model_dump(mode="json", exclude={"idempotency_key"})})
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            if not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, "A configured moderator is required.")
            # Account-before-note follows the existing notebook mutation order.
            note = session.scalar(select(notebook.NotebookNote)
                .where(notebook.NotebookNote.id == str(note_id)).with_for_update())
            revision = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == str(note_id), notebook.NotebookRevision.revision == body.revision))
            publication = session.scalar(select(notebook.NotebookPublication).where(
                notebook.NotebookPublication.revision_id == revision.id)) if revision else None
            if note is None or publication is None or publication.decision != "approved":
                raise HTTPException(404, "Shared note revision not found.")
            replay = session.scalar(select(PublicationWithdrawal).where(
                PublicationWithdrawal.moderator_id == user.id,
                PublicationWithdrawal.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This identifier was used for another withdrawal.")
                return {"withdrawal": audit_data(replay)}
            if revision.content_sha256 != body.content_sha256:
                raise HTTPException(409, "Read the exact shared revision before withdrawing it.")
            if session.get(PublicationWithdrawal, revision.id):
                raise HTTPException(409, "This revision has already been withdrawn. Its history is preserved.")
            record = PublicationWithdrawal(revision_id=revision.id, moderator_id=user.id, created_at=clock(),
                reason=body.reason, idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            session.add(record)
            session.flush()
            return {"withdrawal": audit_data(record)}
