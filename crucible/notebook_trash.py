"""Permanent draft deletion with content-free receipts and legacy recovery."""
import json
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint, delete, or_, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

NOTICE = "This private draft was previously removed."
PERMANENT_NOTICE = "This private note was permanently deleted."


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(strict=True, ge=1, le=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: UUID
    permanent: bool = Field(strict=True)


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
        from .notebook import NotebookNote
        note = session.get(NotebookNote, note_id)
        raise HTTPException(410, PERMANENT_NOTICE if note and note.latest_revision == 0 else NOTICE)


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


def purge_content(session, note):
    """Erase stored revisions and their import copies; keep identity/retry receipts.

    The caller holds the account and note write locks. All notebook imports and
    revisions serialize through the same account lock. Content from somebody
    else's authored citation, external exports and backup retention is outside
    this operation's scope; this is not a promise of physical media erasure.
    """
    from . import notebook, notebook_graph

    revision_ids = select(notebook.NotebookRevision.id).where(notebook.NotebookRevision.note_id == note.id)
    session.execute(delete(notebook_graph.NotebookReference).where(
        notebook_graph.NotebookReference.source_revision_id.in_(revision_ids)))
    session.execute(delete(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == note.id))
    for record in session.scalars(select(notebook.NotebookImport).where(
            notebook.NotebookImport.author_id == note.author_id)):
        identifiers = {key for key, value in record.id_map.items() if value == note.id} | {note.id}
        workspace = json.loads(json.dumps(record.workspace))
        workspace["notes"] = [item for item in workspace.get("notes", []) if item.get("id") not in identifiers]
        for view in workspace.get("views", []):
            if isinstance(view, dict):
                view["bindings"] = [item for item in view.get("bindings", [])
                                    if not isinstance(item, dict) or item.get("noteId") not in identifiers]
        if workspace != record.workspace:
            record.workspace = workspace
            record.byte_size = len(notebook._bytes(workspace))
    note.source_metadata = None
    # Zero is reserved for a permanent tombstone after an active deletion.
    # New import rows also start at zero, but acquire revisions in the same
    # transaction and have no deletion receipt. No note content survives here.
    note.latest_revision = 0


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
        note = session.get(notebook.NotebookNote, row.note_id)
        return {"deletion": data(row), "deleted": active(session, row.note_id) is not None,
                "permanent": note is not None and note.latest_revision == 0}

    @app.post("/api/notebook/notes/{note_id}/delete")
    def delete_note(note_id: UUID, body: DeleteInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        if not body.permanent:
            raise HTTPException(422, "Confirm permanent deletion explicitly.")
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, note_id)
            replay = session.scalar(select(NotebookDeletion).where(
                NotebookDeletion.author_id == user.id, NotebookDeletion.idempotency_key == str(body.idempotency_key)))
            if replay:
                if (replay.note_id, replay.revision, replay.content_sha256) != (
                        note.id, body.revision, body.content_sha256):
                    raise HTTPException(409, "This delete identifier was already used for another request.")
                if note.latest_revision != 0 or active(session, note.id).id != replay.id:
                    raise HTTPException(409, "This identifier belongs to a previous removal. Use a new confirmed deletion request.")
                return receipt(session, replay)
            if note.latest_revision == 0:
                raise HTTPException(410, PERMANENT_NOTICE)
            target = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == note.id,
                notebook.NotebookRevision.revision == note.latest_revision))
            if (target.revision, target.content_sha256) != (body.revision, body.content_sha256):
                raise HTTPException(409, "This note changed. Read its latest revision before deleting it.")
            if has_review_history(session, note.id):
                raise HTTPException(409, "Only unsubmitted private drafts can be deleted. Publication and review evidence remains in its recorded history.")
            now = clock()
            previous = active(session, note.id)
            if previous is not None:
                # Retire the former soft-removal receipt to satisfy its active
                # uniqueness constraint. This legacy column is a closure time
                # here, not a restoration: the same transaction purges content
                # and the permanent tombstone makes every restore fail.
                previous.restored_at = now
                session.flush()
            row = NotebookDeletion(id=str(uuid4()), note_id=note.id, author_id=user.id,
                revision=target.revision, content_sha256=target.content_sha256,
                deleted_at=now, idempotency_key=str(body.idempotency_key))
            session.add(row)
            session.flush()
            purge_content(session, note)
            session.flush()
            return receipt(session, row)

    @app.post("/api/notebook/notes/{note_id}/restore")
    def restore_note(note_id: UUID, body: RestoreInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, note_id)
            if note.latest_revision == 0:
                raise HTTPException(409, "Permanently deleted notes cannot be restored.")
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
