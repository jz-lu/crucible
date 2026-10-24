"""Reversible removal of owned drafts; submitted evidence is never erased."""
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint, or_, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

NOTICE = "This private note is in Trash. Its author can restore it."


class NotebookDeletion(Base):
    __tablename__ = "notebook_deletions"
    __table_args__ = (
        UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_delete_key"),
        UniqueConstraint("author_id", "restore_key", name="uq_notebook_restore_key"),
        Index("uq_notebook_active_deletion", "note_id", unique=True,
              sqlite_where=text("restored_at IS NULL"), postgresql_where=text("restored_at IS NULL")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("notebook_notes.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    deleted_at: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    restored_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    restore_key: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deletion_id: UUID
    idempotency_key: UUID


def deleted_ids():
    return select(NotebookDeletion.note_id).where(NotebookDeletion.restored_at.is_(None))


def active(session, note_id):
    return session.scalar(select(NotebookDeletion).where(
        NotebookDeletion.note_id == note_id, NotebookDeletion.restored_at.is_(None)))


def require_active(session, note_id):
    if active(session, note_id):
        raise HTTPException(410, NOTICE)


def records(session, note_ids):
    if not note_ids:
        return {}
    return {row.note_id: row for row in session.scalars(select(NotebookDeletion).where(
        NotebookDeletion.note_id.in_(note_ids), NotebookDeletion.restored_at.is_(None)))}


def data(row):
    return {"id": row.id, "note_id": row.note_id, "revision": row.revision,
            "content_sha256": row.content_sha256, "deleted_at": row.deleted_at,
            "restored_at": row.restored_at}


def has_review_history(session, note_id):
    from . import notebook, proposal_notes, research_reviews

    publication = select(notebook.NotebookPublication.id).join(
        notebook.NotebookRevision, notebook.NotebookRevision.id == notebook.NotebookPublication.revision_id
        ).where(notebook.NotebookRevision.note_id == note_id).exists()
    proposal = select(proposal_notes.ProposalNoteLink.note_id).where(
        proposal_notes.ProposalNoteLink.note_id == note_id).exists()
    # The request contract bounds its frozen sources to eight exact references.
    request = select(research_reviews.ResearchRequest.id).where(or_(*(
        research_reviews.ResearchRequest.sources[index]["note_id"].as_string() == note_id
        for index in range(8)))).exists()
    return bool(session.scalar(select(or_(publication, proposal, request))))


def register_routes(app, db, clock, account, throttle):
    from . import notebook

    def owned(session, user, identifier):
        note = session.scalar(select(notebook.NotebookNote).where(
            notebook.NotebookNote.id == str(identifier),
            notebook.NotebookNote.author_id == user.id).with_for_update())
        if note is None:
            raise HTTPException(404, "Note not found.")
        return note

    def receipt(session, row):
        return {"deletion": data(row), "deleted": active(session, row.note_id) is not None}

    @app.post("/api/notebook/notes/{note_id}/delete")
    def delete_note(note_id: UUID, body: notebook.PublicationInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, note_id)
            replay = session.scalar(select(NotebookDeletion).where(
                NotebookDeletion.author_id == user.id, NotebookDeletion.idempotency_key == str(body.idempotency_key)))
            if replay:
                if (replay.note_id, replay.revision, replay.content_sha256) != (
                        note.id, body.revision, body.content_sha256):
                    raise HTTPException(409, "This delete identifier was already used for another request.")
                return receipt(session, replay)
            target = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == note.id,
                notebook.NotebookRevision.revision == note.latest_revision))
            if (target.revision, target.content_sha256) != (body.revision, body.content_sha256):
                raise HTTPException(409, "This note changed. Read its latest revision before deleting it.")
            if active(session, note.id):
                raise HTTPException(409, "This note is already in Trash. Use its existing deletion receipt.")
            if has_review_history(session, note.id):
                raise HTTPException(409, "Only unsubmitted private drafts can be deleted. Publication and review evidence remains in its recorded history.")
            row = NotebookDeletion(id=str(uuid4()), note_id=note.id, author_id=user.id,
                revision=target.revision, content_sha256=target.content_sha256,
                deleted_at=clock(), idempotency_key=str(body.idempotency_key))
            session.add(row)
            session.flush()
            return receipt(session, row)

    @app.post("/api/notebook/notes/{note_id}/restore")
    def restore_note(note_id: UUID, body: RestoreInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, note_id)
            row = session.scalar(select(NotebookDeletion).where(
                NotebookDeletion.id == str(body.deletion_id),
                NotebookDeletion.note_id == note.id, NotebookDeletion.author_id == user.id).with_for_update())
            if row is None:
                raise HTTPException(404, "Deletion not found.")
            replay = session.scalar(select(NotebookDeletion).where(
                NotebookDeletion.author_id == user.id, NotebookDeletion.restore_key == str(body.idempotency_key)))
            if replay:
                if replay.id != row.id:
                    raise HTTPException(409, "This restore identifier was already used for another deletion.")
                return receipt(session, row)
            if row.restored_at is not None:
                raise HTTPException(409, "This deletion was already restored. Read the note's current state.")
            row.restored_at, row.restore_key = clock(), str(body.idempotency_key)
            session.flush()
            return receipt(session, row)
