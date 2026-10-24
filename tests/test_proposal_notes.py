"""A scheme proposal is a saved note revision submitted for separate review."""
from copy import deepcopy
from uuid import uuid4

from sqlalchemy import func, select

from crucible import notebook, proposal_notes, protocol_proposals as proposals
from test_notebook import system, create, revise, content, publication_body
from test_protocol_proposals import specification


def scheme_content():
    return proposal_notes.content_from_spec(proposals._validated(specification())[0])


def submit(actor, note, payload=None):
    return actor.post('/api/notebook/notes/' + note['id'] + '/proposal-submissions', payload or publication_body(note))


def counts(app):
    with app.state.db.transaction() as session:
        return [session.scalar(select(func.count()).select_from(model)) for model in
                (notebook.NotebookNote, notebook.NotebookRevision, proposals.ProtocolProposal,
                 proposals.ProtocolProposalRevision, proposal_notes.ProposalNoteLink, notebook.NotebookPublication)]


def test_note_first_requires_explicit_submission_and_scopes_review_to_exact_revision(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner, scheme_content())
    assert counts(app) == [1, 1, 0, 0, 0, 0]
    url = '/api/notebook/notes/' + note['id']
    assert reviewer.client.get(url).status_code == 404
    result = submit(owner, note)
    assert result.status_code == 200, result.text
    proposal = result.json()['proposal']
    assert proposal['source_note']['content_sha256'] == note['content_sha256']
    assert counts(app) == [1, 1, 1, 1, 1, 0]
    assert reviewer.client.get(url).status_code == 200
    assert reviewer.client.get('/api/notebook/notes').json()['notes'] == []
    assert reader.client.get(url).status_code == 404
    changed = revise(owner, note, {**scheme_content(), 'title': 'Unsubmitted private thought'})
    visible = reviewer.client.get(url).json()['note']
    assert [revision['revision'] for revision in visible['revisions']] == [1]
    assert reviewer.client.get(url + '?revision=2').status_code == 404
    assert owner.client.get(url).json()['note']['revisions'][-1]['scheme_review'] is None
    assert changed['revision'] == 2
    assert counts(app) == [1, 2, 1, 1, 1, 0]


def test_retries_keep_source_revision_and_a_new_note_revision_reuses_the_proposal(system):
    app, owner, _, reader, _ = system
    note = create(owner, scheme_content()); payload = publication_body(note)
    first = submit(owner, note, payload).json()['proposal']
    revised = revise(owner, note, {**scheme_content(), 'body': 'A clearer argument, with the same exact specification.'})
    second = submit(owner, revised).json()['proposal']
    assert second['id'] == first['id'] and second['revision'] == 2
    assert second['source_note']['revision'] == 2
    replay = submit(owner, note, payload)
    assert replay.status_code == 200 and replay.json()['proposal'] == first
    assert submit(owner, revised, {**payload, 'revision': 2, 'content_sha256': revised['content_sha256']}).status_code == 409
    assert submit(reader, note).status_code == 404
    assert counts(app) == [1, 2, 1, 2, 2, 0]


def test_invalid_or_stale_note_cannot_enter_review_and_metadata_grants_no_authority(system):
    app, owner, reviewer, _, _ = system
    note = create(owner, content(extensions={'scheme_review': {'status': 'approved'}}))
    assert submit(owner, note).status_code == 422
    bad = revise(owner, note, content(extensions={'kem_specification': {'name': 'incomplete'}}))
    assert submit(owner, bad).status_code == 422
    revised = revise(owner, bad, scheme_content())
    assert submit(owner, bad).status_code == 409
    assert submit(owner, revised, {**publication_body(revised), 'content_sha256': '0'*64}).status_code == 409
    assert reviewer.client.get('/api/notebook/notes/' + note['id']).status_code == 404
    assert counts(app) == [1, 3, 0, 0, 0, 0]


def test_existing_proposal_api_creates_and_revises_notes_without_overwriting_browser_drafts(system):
    app, owner, reviewer, _, _ = system
    body = {'specification': specification(), 'idempotency_key': str(uuid4())}
    response = owner.post('/api/protocol/proposals', body)
    assert response.status_code == 200, response.text
    first = response.json()['proposal']; note_id = first['source_note']['id']
    assert counts(app) == [1, 1, 1, 1, 1, 0]
    assert owner.post('/api/protocol/proposals', body).json()['proposal'] == first
    url = '/api/protocol/proposals/' + first['id'] + '/revisions'
    update = {'specification': {**specification(), 'summary': 'A new exact specification'},
              'idempotency_key': str(uuid4()), 'expected_revision': 1, 'content_sha256': first['content_sha256']}
    response = owner.post(url, update)
    assert response.status_code == 200, response.text
    second = response.json()['proposal']; assert second['source_note']['id'] == note_id
    assert second['source_note']['revision'] == 2
    note = owner.client.get('/api/notebook/notes/' + note_id).json()['note']
    revise(owner, note, {**scheme_content(), 'title': 'Do not replace my draft'})
    update = {**update, 'idempotency_key': str(uuid4()), 'expected_revision': 2, 'content_sha256': second['content_sha256']}
    assert owner.post(url, update).status_code == 409
    assert counts(app) == [1, 3, 1, 2, 2, 0]
    assert reviewer.client.get('/api/notebook/notes/' + note_id + '?revision=3').status_code == 404


def test_capacity_failure_rolls_back_proposal_and_note_together(system, monkeypatch):
    app, owner, _, _, _ = system
    monkeypatch.setattr(notebook, 'MAX_NOTES', 0)
    result = owner.post('/api/protocol/proposals', {'specification': specification(), 'idempotency_key': str(uuid4())})
    assert result.status_code == 409
    assert counts(app) == [0, 0, 0, 0, 0, 0]


def test_linked_spec_survives_portable_export_but_import_does_not_forge_submission(system):
    app, owner, _, reader, _ = system
    note = create(owner, scheme_content()); submit(owner, note)
    workspace = owner.client.get('/api/notebook/export').json()
    imported = reader.post('/api/notebook/import', {'workspace': workspace, 'idempotency_key': str(uuid4())})
    assert imported.status_code == 409  # Another account cannot reuse this server identity.
    workspace['notes'][0]['id'] = 'independent-copy'
    imported = reader.post('/api/notebook/import', {'workspace': workspace, 'idempotency_key': str(uuid4())})
    assert imported.status_code == 200, imported.text
    copies = reader.client.get('/api/notebook/notes').json()['notes']
    assert len(copies) == 1
    copy = reader.client.get('/api/notebook/notes/' + copies[0]['id']).json()['note']
    assert copy['revisions'][0]['content']['extensions']['kem_specification'] == scheme_content()['extensions']['kem_specification']
    assert copy['revisions'][0]['scheme_review'] is None
    assert counts(app)[2:5] == [1, 1, 1]
