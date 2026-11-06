"""Author retraction, exact retry identity and irreversible content erasure."""
import pytest
from sqlalchemy import select

from crucible import machine_auth, notebook
from crucible.publication_screening_models import ScreeningAttempt, ScreeningJob
from test_notebook import system, create, revise, content, publication_body, request_publication, approve
from test_notebook_agents import agent
from test_notebook_trash import delete, restore
from test_publication_screening_integration import screened, request, configure, run, SyntheticProvider


def cancel(actor, note, payload=None):
    return actor.post('/api/notebook/notes/' + note['id'] + '/publication-cancellations',
                      payload or publication_body(note))


def test_owner_cancels_exact_pending_revision_with_safe_retries(system):
    app, owner, reviewer, reader, _ = system
    first = create(owner)
    request_publication(owner, first)
    latest = revise(owner, first)
    payload = publication_body(first)
    assert cancel(owner, latest).status_code == 404
    assert cancel(owner, first, {**payload, 'content_sha256': 'f' * 64}).status_code == 409
    response = cancel(owner, first, payload)
    assert response.status_code == 200, response.text
    receipt = response.json()['publication']
    assert receipt['status'] == 'cancelled' and receipt['revision'] == 1
    assert cancel(owner, first, payload).json() == response.json()
    assert cancel(owner, first).status_code == 409
    assert owner.client.get('/api/notebook/publication-status',
        params={'publication_id':receipt['id']}).json()['publications'][0]['status'] == 'cancelled'
    assert reader.client.get('/api/notebook/notes/' + first['id']).status_code == 404
    assert reviewer.post('/api/notebook/notes/' + first['id'] + '/publication-reviews',
        publication_body(first, decision='approved', reason='Reviewed.')).status_code == 409
    # A new saved revision can be submitted; the old immutable request stays cancelled.
    request_publication(owner, latest)
    with app.state.db.transaction() as session:
        row = session.get(notebook.NotebookPublication, receipt['id'])
        assert row.decision_source == 'author' and row.reviewer_id is None
    assert owner.client.get('/api/notebook/notes/' + first['id']).json()['note']['can_delete']


def test_cancel_requires_ownership_csrf_and_publish_scope(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner)
    request_publication(owner, note)
    path = '/api/notebook/notes/' + note['id'] + '/publication-cancellations'
    for actor in (reader, reviewer):
        assert cancel(actor, note).status_code == 404
    assert owner.client.post(path, json=publication_body(note)).status_code == 403
    for scopes in (machine_auth.DEFAULT_SCOPES, ('notebook:read',), ('notebook:write',)):
        assert agent(app, owner, scopes).post(path, json=publication_body(note)).status_code == 403
    assert agent(app, owner, ('notebook:publish',)).post(path, json=publication_body(note)).status_code == 200


@pytest.mark.parametrize('decision', ['approved', 'changes_requested'])
def test_real_review_decisions_cannot_be_retracted_or_deleted(system, decision):
    _, owner, reviewer, *_ = system
    note = create(owner)
    request_publication(owner, note)
    assert reviewer.post('/api/notebook/notes/' + note['id'] + '/publication-reviews',
        publication_body(note, decision=decision, reason='Review evidence.')).status_code == 200
    assert cancel(owner, note).status_code == 409
    assert delete(owner, note).status_code == 409


def test_pending_deletion_erases_content_but_preserves_content_free_commitments(screened):
    owner = screened.owner
    note = create(owner, content(title='ERASE_TITLE', body='ERASE_BODY'))
    assert request(owner, note).status_code == 200
    configure(screened)
    # A completed flag can contain excerpts; it is not a scientific or human review.
    assert run(screened, SyntheticProvider(flagged=True))['status'] == 'human_review'
    with screened.app.state.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.result is not None
        result_commitment = attempt.result_sha256
        usage_commitment = (attempt.charged_micro_usd, attempt.usage_known)
        attempt.result = {**attempt.result, 'synthetic_private_excerpt':'ERASE_BODY'}
    latest = revise(owner, note, content(title='ERASE_LATER', body='ERASE_LATER_BODY'))
    payload = publication_body(latest, permanent=True)
    removed = delete(owner, latest, payload)
    assert removed.status_code == 200, removed.text
    assert delete(owner, latest, payload).json() == removed.json()
    assert restore(owner, removed.json()['deletion']).status_code == 409
    path = '/api/notebook/notes/' + note['id']
    assert owner.client.get(path).status_code == 410
    for actor in (owner, screened.reviewer, screened.reader):
        assert actor.client.get(path + '?include_deleted=true&revision=1').status_code == 404
        assert note['id'] not in actor.client.get('/api/notebook/notes?projection=index').text
    assert note['id'] not in owner.client.get('/api/notebook/export').text
    with screened.app.state.db.transaction() as session:
        revisions = list(session.scalars(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == note['id'])))
        assert len(revisions) == 1
        assert revisions[0].content == {} and revisions[0].source_revision is None
        assert revisions[0].content_sha256 == note['content_sha256']
        pub = session.scalar(select(notebook.NotebookPublication))
        assert pub.decision == 'cancelled' and pub.reason is None
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.result is None and attempt.charged_micro_usd > 0
        assert attempt.result_sha256 == result_commitment
        assert (attempt.charged_micro_usd, attempt.usage_known) == usage_commitment
        assert session.scalar(select(ScreeningJob)).status == 'cancelled'


def test_cancel_then_delete_stays_idempotent_and_does_not_restore_content(screened):
    owner = screened.owner
    note = create(owner)
    assert request(owner, note).status_code == 200
    payload = publication_body(note)
    cancelled = cancel(owner, note, payload)
    assert cancelled.status_code == 200
    original_cancellation_time = screened.now[0]
    screened.now[0] += 10
    assert delete(owner, note).status_code == 200
    assert cancel(owner, note, payload).json() == cancelled.json()
    with screened.app.state.db.transaction() as session:
        assert session.scalar(select(ScreeningJob)).finished_at == original_cancellation_time


def test_old_published_revision_protects_history_when_new_request_is_cancelled(system):
    _, owner, reviewer, reader, _ = system
    note = create(owner)
    request_publication(owner, note)
    approve(reviewer, note)
    latest = revise(owner, note)
    request_publication(owner, latest)
    assert cancel(owner, latest).status_code == 200
    assert delete(owner, latest).status_code == 409
    visible = reader.client.get('/api/notebook/notes/' + note['id']).json()['note']
    assert visible['revision'] == 1 and visible['content_sha256'] == note['content_sha256']
