"""Author cancellation of a pending exact revision, shared with draft erasure."""
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import select

from . import publication_screening_jobs as jobs


def lock_author_mutation(request, session, account):
    # Reject anonymous/ineligible callers before taking the global worker lock.
    # Full mutation authorization (including CSRF/revocation) is repeated after
    # it; otherwise account→control would deadlock finish's control→account path.
    account(request, session)
    control = jobs.lock_for_cancellation(session)
    return account(request, session, True), control


def cancel(session, publication, *, now, key, commitment):
    """Caller holds control→author→note→publication; this is not a review."""
    if publication.decision is not None:
        raise HTTPException(409, "This publication request already has a decision. Its recorded history is preserved.")
    publication.decision = 'cancelled'
    publication.decision_source = 'author'
    # Existing decision metadata records the author's terminal action. The
    # reviewer remains NULL: cancellation is never represented as human review.
    publication.reviewed_at = now
    publication.review_key, publication.review_sha256 = key, commitment
    publication.reason = None


def register_routes(app, db, clock, account, throttle):
    from . import notebook, notebook_trash

    @app.post('/api/notebook/notes/{note_id}/publication-cancellations')
    def cancel_publication(note_id: UUID, body: notebook.PublicationInput, request: Request):
        throttle(request, 'notebook-publication', 60, 3600)
        commitment = notebook._hash({'note_id': str(note_id), 'revision': body.revision,
            'content_sha256': body.content_sha256, 'action': 'cancel-publication'})
        with db.transaction(write=True) as session:
            user, control = lock_author_mutation(request, session, account)
            note = session.scalar(select(notebook.NotebookNote).where(
                notebook.NotebookNote.id == str(note_id), notebook.NotebookNote.author_id == user.id)
                .with_for_update())
            if note is None:
                raise HTTPException(404, 'Note not found.')
            target = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == note.id, notebook.NotebookRevision.revision == body.revision))
            publication = session.scalar(select(notebook.NotebookPublication).where(
                notebook.NotebookPublication.revision_id == target.id).with_for_update()) if target else None
            if publication is None or publication.author_id != user.id:
                raise HTTPException(404, 'Publication request not found.')
            if target.content_sha256 != body.content_sha256:
                raise HTTPException(409, 'Read the exact submitted revision before cancelling publication.')
            if publication.decision == 'cancelled':
                if publication.review_key != str(body.idempotency_key) or publication.review_sha256 != commitment:
                    raise HTTPException(409, 'This publication request was already cancelled.')
            else:
                notebook_trash.require_active(session, note.id)
                now = clock()
                cancel(session, publication, now=now, key=str(body.idempotency_key), commitment=commitment)
                jobs.cancel_publications(session, [publication.id], control=control, now=now)
                session.flush()
            return {'publication': {'id': publication.id, 'note_id': note.id,
                'revision': target.revision, 'content_sha256': target.content_sha256,
                'status': 'cancelled', 'cancelled_at': publication.reviewed_at}}
