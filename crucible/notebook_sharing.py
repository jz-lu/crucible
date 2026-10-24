"""Retired named-recipient sharing: retain immutable history, grant no access.

The ORM definitions remain for existing databases and migration compatibility.
Note access is limited to authors, approved community revisions, and explicit reviewer requests.
Old endpoints fail explicitly after the usual authentication and ownership checks.
"""
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PrivateNoteShare(Base):
    __tablename__ = 'notebook_private_shares'
    __table_args__ = (
        Index('uq_notebook_active_private_share', 'note_id', 'recipient_id', unique=True,
              sqlite_where=text('revoked_at IS NULL'), postgresql_where=text('revoked_at IS NULL')),
        Index('ix_notebook_private_share_recipient', 'recipient_id', 'revoked_at', 'note_id'),
        Index('ix_notebook_private_share_inbox', 'recipient_id', 'state', 'revoked_at', 'shared_at', 'id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey('notebook_notes.id'))
    recipient_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    from_revision: Mapped[int] = mapped_column(Integer)
    shared_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Historical metadata only. Grants and recipient responses no longer authorize access.
    state: Mapped[str] = mapped_column(String(16), default='pending', server_default='pending')
    response_version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')


class PrivateNoteShareResponse(Base):
    """Preserved receipts from the retired recipient acceptance workflow."""
    __tablename__ = 'notebook_private_share_responses'
    __table_args__ = (
        UniqueConstraint('recipient_id', 'idempotency_key', name='uq_notebook_share_response_key'),
        UniqueConstraint('share_id', 'response_version', name='uq_notebook_share_response_version'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    share_id: Mapped[str] = mapped_column(ForeignKey('notebook_private_shares.id'))
    recipient_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Float)
    before_state: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    response_version: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class PrivateNoteShareChange(Base):
    __tablename__ = 'notebook_private_share_changes'
    __table_args__ = (
        UniqueConstraint('note_id', 'sequence', name='uq_notebook_share_change_sequence'),
        UniqueConstraint('author_id', 'idempotency_key', name='uq_notebook_share_change_key'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey('notebook_notes.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    sequence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    before_recipients: Mapped[list] = mapped_column(JSON)
    after_recipients: Mapped[list] = mapped_column(JSON)
    before_sharing_sha256: Mapped[str] = mapped_column(String(64))
    after_sharing_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


RETIRED = "Private participant sharing has been removed. Authors can submit a saved revision for community publication."


def register_routes(app, db, settings, clock, account, throttle):
    from . import notebook

    def owned(session, user, note_id):
        identifier = session.scalar(select(notebook.NotebookNote.id).where(
            notebook.NotebookNote.id == str(note_id), notebook.NotebookNote.author_id == user.id))
        if identifier is None:
            raise HTTPException(404, "Note not found.")

    @app.get('/api/notebook/private-shares')
    def list_received_shares(request: Request):
        with db.transaction() as session:
            account(request, session)
            raise HTTPException(410, RETIRED)

    @app.get('/api/notebook/notes/{note_id}/private-shares')
    def get_shares(note_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session)
            owned(session, user, note_id)
            raise HTTPException(410, RETIRED)

    @app.put('/api/notebook/notes/{note_id}/private-shares')
    def replace_shares(note_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session, True)
            owned(session, user, note_id)
            raise HTTPException(410, RETIRED)

    @app.post('/api/notebook/private-shares/{share_id}/response')
    def respond_to_share(share_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session, True)
            identifier = session.scalar(select(PrivateNoteShare.id).where(
                PrivateNoteShare.id == str(share_id), PrivateNoteShare.recipient_id == user.id,
                PrivateNoteShare.revoked_at.is_(None)))
            if identifier is None:
                raise HTTPException(404, "Private share not found.")
            raise HTTPException(410, RETIRED)
