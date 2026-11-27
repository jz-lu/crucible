"""Author-controlled visibility, independent of immutable content and review.

The note lock serializes intents. Moderator withdrawals always take precedence.
The current projection keeps ordinary reads small; the journal retains retries.
"""
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class VisibilityChange(Base):
    __tablename__ = 'notebook_visibility_changes'
    __table_args__ = (UniqueConstraint('author_id', 'idempotency_key', name='uq_note_visibility_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey('notebook_notes.id'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Float)
    private: Mapped[bool] = mapped_column(Boolean)
    revision_ids: Mapped[list] = mapped_column(JSON)
    previous_publications: Mapped[list] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class RevisionVisibility(Base):
    __tablename__ = 'notebook_revision_visibility'
    revision_id: Mapped[str] = mapped_column(ForeignKey('notebook_revisions.id'), primary_key=True)
    private: Mapped[bool] = mapped_column(Boolean)
    change_id: Mapped[str] = mapped_column(ForeignKey('notebook_visibility_changes.id'))


def hidden_ids():
    return select(RevisionVisibility.revision_id).where(RevisionVisibility.private.is_(True))


def hidden(session, revision_id):
    state = session.get(RevisionVisibility, revision_id)
    return bool(state and state.private)


def hidden_set(session, identifiers):
    return set(session.scalars(hidden_ids().where(RevisionVisibility.revision_id.in_(identifiers)))) if identifiers else set()


def replay(session, author_id, key, commitment):
    row = session.scalar(select(VisibilityChange).where(VisibilityChange.author_id == author_id,
        VisibilityChange.idempotency_key == str(key)))
    if row and row.request_sha256 != commitment:
        raise HTTPException(409, 'This identifier was used for another visibility change.')
    return row


def record(session, note, publications, *, private, key, commitment, now):
    # Called with the author and note locked, after access and exact pins pass.
    identifiers = [p.revision_id for p in publications]
    row = VisibilityChange(id=str(uuid4()), note_id=note.id, author_id=note.author_id,
        created_at=now, private=private, revision_ids=identifiers,
        previous_publications=[{field:getattr(p, field) for field in
            ('id', 'revision_id', 'decision', 'decision_source', 'reviewed_at', 'review_key', 'review_sha256')}
            for p in publications], idempotency_key=str(key), request_sha256=commitment)
    session.add(row); session.flush()
    for identifier in identifiers:
        state = session.get(RevisionVisibility, identifier)
        if state is None:
            state = RevisionVisibility(revision_id=identifier, private=private, change_id=row.id)
            session.add(state)
        else:
            state.private, state.change_id = private, row.id
    session.flush()
    return row


def cancellation_receipt(session, author_id, note_id, key):
    """An old cancellation retry cannot cancel a subsequently resumed request."""
    rows = session.scalars(select(VisibilityChange).where(VisibilityChange.author_id == author_id,
        VisibilityChange.note_id == note_id))
    return next((p for row in rows for p in row.previous_publications
                 if p['decision'] == 'cancelled' and p['review_key'] == str(key)), None)
