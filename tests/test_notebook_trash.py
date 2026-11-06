"""Permanent draft erasure, stable identities and explicit legacy recovery."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from crucible import machine_auth, notebook, notebook_numbers, notebook_trash
from test_notebook import (system, content, create, revise, revision_body,
                           publication_body, request_publication, approve,
                           workspace, import_workspace, count)
from test_notebook_agents import agent
from test_notebook_graph import graph, reference
from test_proposal_notes import scheme_content, submit as submit_scheme
from test_research_reviews import submit as submit_research, request_body


def delete(actor, note, body=None):
    return actor.post(f'/api/notebook/notes/{note["id"]}/delete',
                      body if body is not None else publication_body(note, permanent=True))


def legacy_delete(actor, note):
    """Seed a historical soft deletion without restoring a retired write API."""
    with actor.app.state.db.transaction(write=True) as session:
        row = notebook_trash.NotebookDeletion(id=str(uuid4()), note_id=note['id'],
            author_id=actor.identifier, revision=note['revision'], content_sha256=note['content_sha256'],
            deleted_at=actor.app.state.clock(), idempotency_key=str(uuid4()))
        session.add(row); session.flush()
        return notebook_trash.data(row)


def restore(actor, deletion, body=None):
    return actor.post(f'/api/notebook/notes/{deletion["note_id"]}/restore', body or {
        "deletion_id": deletion["id"], "idempotency_key": str(uuid4())})


def test_permanent_delete_erases_all_revisions_preserves_number_and_cannot_restore(system):
    app, owner, _, reader, _ = system
    first = create(owner, content(title="Disposable draft", body="PRIVATE_OLD_BODY"))
    latest = revise(owner, first, content(title="Disposable revised draft", body="PRIVATE_NEW_BODY"))
    citing = create(owner, content(references=[reference(first)]))
    path = "/api/notebook/notes/" + first["id"]
    assert graph(owner, citing).json()["links"]
    assert delete(owner, first).status_code == 409
    result = delete(owner, latest)
    assert result.status_code == 200, result.text
    assert result.json()["deleted"] is result.json()["permanent"] is True
    assert restore(owner, result.json()['deletion']).status_code == 409
    assert owner.client.get(path).status_code == 410
    assert owner.client.get(path + '?include_deleted=true').status_code == 404
    assert reader.client.get(path).status_code == 404
    for query in ('', '?scope=mine', '?scope=trash', '?q=Disposable', '?projection=index'):
        assert first['id'] not in owner.client.get('/api/notebook/notes' + query).text
    assert first['id'] not in {n['id'] for n in owner.client.get('/api/notebook/export').json()['notes']}
    assert graph(owner, first).status_code == 404
    assert graph(owner, citing).json()['links'] == []
    with app.state.db.transaction() as session:
        assert session.scalars(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == first['id'])).all() == []
        tombstone = session.get(notebook.NotebookNote, first['id'])
        assert tombstone.latest_revision == 0 and tombstone.source_metadata is None
        assert session.scalar(select(notebook_numbers.NotebookNumber.number).where(
            notebook_numbers.NotebookNumber.note_id == first['id'])) == first['number']
    assert create(owner)['number'] > first['number']


@pytest.mark.parametrize('permanent', [None, False, 'true', 1])
def test_irreversible_deletion_requires_explicit_boolean_confirmation(system, permanent):
    _, owner, *_ = system
    note = create(owner)
    payload = publication_body(note)
    if permanent is not None:
        payload['permanent'] = permanent
    assert delete(owner, note, payload).status_code == 422
    assert owner.client.get('/api/notebook/notes/' + note['id']).status_code == 200


def test_retries_are_idempotent_and_do_not_recreate_content(system):
    app, owner, *_ = system
    note = create(owner)
    payload = publication_body(note, permanent=True)
    first = delete(owner, note, payload)
    assert first.status_code == 200
    assert delete(owner, note, payload).json() == first.json()
    assert delete(owner, note).status_code == 410
    assert delete(owner, note, {**payload, 'content_sha256': 'f' * 64}).status_code == 409
    other = create(owner)
    assert delete(owner, other, {**payload, 'revision': other['revision'],
                                'content_sha256': other['content_sha256']}).status_code == 409
    assert count(app, notebook_trash.NotebookDeletion) == 1
    assert count(app, notebook.NotebookRevision) == 1


def test_only_owner_with_write_authority_can_permanently_delete(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner)
    path = '/api/notebook/notes/' + note['id']
    payload = publication_body(note, permanent=True)
    for actor in (reviewer, reader):
        assert delete(actor, note).status_code == 404
    assert owner.client.post(path + '/delete', json=payload).status_code == 403
    for scopes in (machine_auth.DEFAULT_SCOPES, ('notebook:read',), ('notebook:publish',)):
        assert agent(app, owner, scopes).post(path + '/delete', json=payload).status_code == 403
    writer = agent(app, owner, ('notebook:write',))
    assert writer.post(path + '/delete', json=payload).json()['permanent'] is True
    assert reader.client.get(path + '?include_deleted=true').status_code == 404


@pytest.mark.parametrize('history', ['approved', 'changes_requested', 'scheme', 'research'])
def test_submitted_evidence_cannot_be_deleted_even_after_new_private_revision(system, history):
    _, owner, reviewer, *_ = system
    note = create(owner, scheme_content() if history == 'scheme' else content())
    if history == 'scheme':
        assert submit_scheme(owner, note).status_code == 200
    elif history == 'research':
        submit_research(owner, note, purpose='procedure')
    else:
        request_publication(owner, note)
        if history == 'approved': approve(reviewer, note)
        elif history == 'changes_requested':
            assert reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
                publication_body(note, decision='changes_requested', reason='Needs clearer attribution.')).status_code == 200
    revised = revise(owner, note)
    assert not revised['can_delete']
    response = delete(owner, revised)
    assert response.status_code == 409 and 'evidence' in response.text
    assert owner.client.get('/api/notebook/notes/' + note['id']).status_code == 200


def test_import_archive_target_and_bindings_are_erased_without_harming_other_notes(system):
    app, owner, *_ = system
    value = workspace(revisions=2)
    value['notes'][0]['revisions'][0]['body'] = 'ERASE_THIS_PRIVATE_COPY'
    value['notes'][0]['private_metadata'] = 'ERASE_THIS_METADATA'
    value['notes'].extend(workspace('keep-note')['notes'])
    value['views'] = [{'id':'view', 'kind':'argument', 'title':'View', 'question':'Question', 'scope':'Scope', 'bindings':[
        {'id':'binding', 'noteId':'local-note', 'noteRevision':1, 'excerpt':'ERASE_THIS_EXCERPT', 'scope':'Scope', 'assessment':'unreviewed', 'assessmentReason':'', 'from':'A', 'to':'B', 'relation':'supports'},
        {'id':'keep', 'noteId':'keep-note', 'noteRevision':1, 'excerpt':'Keep this', 'scope':'Scope', 'assessment':'unreviewed', 'assessmentReason':'', 'from':'A', 'to':'B', 'relation':'supports'}]}]
    imported = import_workspace(owner, value)
    assert imported.status_code == 200, imported.text
    pin = imported.json()['id_map']['local-note']
    note = owner.client.get('/api/notebook/notes/' + pin).json()['note']
    assert delete(owner, note).status_code == 200
    archived = owner.client.get('/api/notebook/imports/' + imported.json()['import_id']).json()
    assert 'ERASE_THIS' not in str(archived)
    assert archived['redacted'] is True and archived['deleted_note_ids'] == [pin]
    assert archived['original_request_sha256'] == notebook._hash(value)
    assert [item['id'] for item in archived['workspace']['notes']] == ['keep-note']
    assert [item['id'] for item in archived['workspace']['views'][0]['bindings']] == ['keep']
    assert import_workspace(owner, value).status_code == 410
    assert count(app, notebook.NotebookRevision) == 1
    for suffix, body in (('/revisions', revision_body(note)),
                         ('/publication-requests', publication_body(note)),
                         ('/proposal-submissions', publication_body(note))):
        assert owner.post('/api/notebook/notes/' + pin + suffix, body).status_code == 410
    assert owner.post('/api/notebook/research-requests', request_body(note, purpose='procedure')).status_code == 404


def test_legacy_removed_drafts_remain_visible_only_to_owner_in_my_notes(system):
    _, owner, _, reader, _ = system
    note = create(owner)
    old = legacy_delete(owner, note)
    assert owner.client.get('/api/notebook/notes').json()['notes'] == []
    for query in ('scope=mine', 'scope=mine&projection=index', 'projection=index'):
        row = owner.client.get('/api/notebook/notes?' + query).json()['notes'][0]
        assert row['id'] == note['id'] and row['can_delete'] and not row['can_edit']
        assert row['deletion'] == old
    assert reader.client.get('/api/notebook/notes?projection=index').json()['notes'] == []
    assert owner.client.get('/api/notebook/notes/' + note['id'] + '?include_deleted=true').status_code == 200
    result = delete(owner, note)
    assert result.status_code == 200 and result.json()['permanent'] is True
    assert restore(owner, old).status_code == 409
    assert owner.client.get('/api/notebook/notes?scope=mine').json()['notes'] == []


def test_legacy_restore_api_preserves_existing_drafts_and_old_keys_cannot_purge(system):
    _, owner, *_ = system
    note = create(owner)
    old = legacy_delete(owner, note)
    with owner.app.state.db.transaction() as session:
        key = session.get(notebook_trash.NotebookDeletion, old['id']).idempotency_key
    assert delete(owner, note, publication_body(note, permanent=True, idempotency_key=key)).status_code == 409
    assert restore(owner, old).status_code == 200
    assert owner.client.get('/api/notebook/notes/' + note['id']).status_code == 200


def test_erasure_releases_capacity_but_old_create_retry_cannot_resurrect(system, monkeypatch):
    app, owner, *_ = system
    key = str(uuid4())
    note = create(owner, key=key)
    assert delete(owner, note).status_code == 200
    assert owner.post('/api/notebook/notes', {'content':content(), 'idempotency_key':key}).status_code == 410
    monkeypatch.setattr(notebook, 'MAX_NOTES', 1)
    assert create(owner)['id'] != note['id']


def test_import_redaction_uses_saved_identity_not_reused_source_name(system):
    app, owner, *_ = system
    removed = import_workspace(owner, workspace('source-name'))
    note = owner.client.get('/api/notebook/notes/' + removed.json()['id_map']['source-name']).json()['note']
    unrelated = create(owner, content(title='Independent note'))
    # Historical archives may come from tools with independently reused local
    # names. Their durable mapping, not the label alone, determines ownership.
    with app.state.db.transaction(write=True) as session:
        row = notebook.NotebookImport(id=str(uuid4()), author_id=owner.identifier,
            created_at=app.state.clock(), sequence=2, byte_size=100,
            idempotency_key=str(uuid4()), request_sha256='a' * 64,
            workspace=workspace('source-name'), id_map={'source-name':unrelated['id']},
            added_notes=1, added_revisions=1)
        session.add(row); session.flush(); identifier=row.id
    assert delete(owner, note).status_code == 200
    archived = owner.client.get('/api/notebook/imports/' + identifier).json()
    assert archived['workspace'] == workspace('source-name')
    assert archived['redacted'] is False and archived['deleted_note_ids'] == []
    assert owner.client.get('/api/notebook/notes/' + unrelated['id']).status_code == 200


def test_old_import_retry_returns_receipt_without_restoring_erased_content(system):
    app, owner, *_ = system
    payload = {'workspace':workspace(), 'idempotency_key':str(uuid4())}
    first = owner.post('/api/notebook/import', payload)
    identifier = first.json()['id_map']['local-note']
    note = owner.client.get('/api/notebook/notes/' + identifier).json()['note']
    assert delete(owner, note).status_code == 200
    retried = owner.post('/api/notebook/import', payload)
    assert retried.status_code == 200 and retried.json()['replayed'] is True
    assert retried.json()['id_map'] == first.json()['id_map']
    assert count(app, notebook.NotebookRevision) == 0
    assert owner.client.get('/api/notebook/notes/' + identifier).status_code == 410
