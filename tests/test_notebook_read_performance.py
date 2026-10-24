"""Notebook history stays complete without querying each revision's reviewer."""
from uuid import uuid4

from sqlalchemy import select, update

from crucible import notebook, notebook_trash
from test_browser_admission import selects
from test_notebook import system, create, content, revise, request_publication, approve


def reviewed_history(app, owner, reviewer, count):
    note = create(owner)
    for number in range(2, count + 1):
        note = revise(owner, note, content(title=f'Revision {number}', body=f'Complete body {number}'))
    with app.state.db.transaction(write=True) as session:
        revisions = session.scalars(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == note['id'])).all()
        for revision in revisions:
            session.add(notebook.NotebookPublication(id=str(uuid4()), revision_id=revision.id,
                author_id=owner.identifier, requested_at=app.state.clock(), idempotency_key=str(uuid4()),
                request_sha256='0' * 64, decision='approved', reviewer_id=reviewer.identifier,
                reviewed_at=app.state.clock(), reason='Synthetic publication fixture.',
                review_key=str(uuid4()), review_sha256='1' * 64, test_only_self_review=False))
    return note


def test_many_reviewed_revisions_use_same_query_budget_as_one(system):
    app, owner, reviewer, reader, _ = system
    one = reviewed_history(app, owner, reviewer, 1)
    many = reviewed_history(app, owner, reviewer, 8)
    for actor in (owner, reader):
        with selects(app) as first_queries:
            first = actor.client.get(f'/api/notebook/notes/{one["id"]}')
        with selects(app) as many_queries:
            response = actor.client.get(f'/api/notebook/notes/{many["id"]}?revision=3')
        assert first.status_code == response.status_code == 200
        assert len(first_queries) == len(many_queries)
        result = response.json()['note']
        assert result['revision'] == 3 and result['revision_count'] == 8
        assert [row['revision'] for row in result['revisions']] == list(range(1, 9))
        assert result['revisions'][2]['content']['body'] == 'Complete body 3'
        assert all(row['publication']['reviewer'] == {'handle': 'reviewer'} for row in result['revisions'])
        assert result['can_delete'] is False
        publication_reads = [q for q in many_queries if 'FROM notebook_publications LEFT OUTER JOIN users' in q]
        assert len(publication_reads) == 1


def test_unsubmitted_review_history_check_is_one_query_and_still_allows_delete(system):
    app, owner, _, _, _ = system
    note = create(owner)
    with app.state.db.transaction() as session, selects(app) as queries:
        assert notebook_trash.has_review_history(session, note['id']) is False
    assert len(queries) == 1 and queries[0].count('EXISTS') == 3
    result = owner.client.get(f'/api/notebook/notes/{note["id"]}').json()['note']
    assert result['can_delete'] is True and result['publication_status'] == 'private'


def test_list_reads_current_status_with_summary_and_never_hydrates_import_metadata(system):
    app, owner, reviewer, reader, _ = system
    private = create(owner, content(title='Private summary', body='PRIVATE_BODY_SENTINEL'))
    pending = create(owner, content(title='Pending summary'))
    request_publication(owner, pending)
    public = create(owner, content(title='Public summary'))
    request_publication(owner, public); approve(reviewer, public)
    withdrawn = create(owner, content(title='Withdrawn summary'))
    request_publication(owner, withdrawn); approve(reviewer, withdrawn)
    from test_notebook_moderation import withdraw
    assert withdraw(reviewer, withdrawn).status_code == 200
    with app.state.db.transaction(write=True) as session:
        session.execute(update(notebook.NotebookNote).where(notebook.NotebookNote.id == private['id'])
            .values(source_id='synthetic-import', source_metadata={'parts': ['unused provenance' * 2000] * 6}))

    with selects(app) as queries:
        response = owner.client.get('/api/notebook/notes')
    assert response.status_code == 200
    result = {row['id']: row for row in response.json()['notes']}
    assert {key: row['publication_status'] for key, row in result.items()} == {
        private['id']: 'private', pending['id']: 'submitted', public['id']: 'approved', withdrawn['id']: 'withdrawn'}
    assert result[private['id']]['imported'] is True
    assert len(queries) == 5  # Fresh account + summary/status + shared summary + two request kinds.
    assert not any('notebook_notes.source_metadata' in query for query in queries)
    assert not any('notebook_publications.reason' in query or 'notebook_publication_withdrawals.reason' in query for query in queries)
    assert 'PRIVATE_BODY_SENTINEL' not in response.text and 'unused provenance' not in response.text
    visible = reader.client.get('/api/notebook/notes').json()['notes']
    assert [row['id'] for row in visible] == [public['id']]
    # Query projection never removes durable provenance from storage/export.
    exported = owner.client.get('/api/notebook/export').json()['notes']
    assert next(row for row in exported if row['id'] == private['id'])['parts'][0].startswith('unused provenance')
    with app.state.db.transaction() as session:
        assert session.get(notebook.NotebookNote, private['id']).source_metadata['parts'][0].startswith('unused provenance')


def test_summary_status_join_keeps_one_row_across_deletion_and_restore_history(system):
    app, owner, _, reader, _ = system
    from test_notebook_trash import delete, restore
    saved = create(owner)
    first = delete(owner, saved).json()['deletion']
    assert restore(owner, first).status_code == 200
    second = delete(owner, saved).json()['deletion']
    with selects(app) as queries:
        response = owner.client.get('/api/notebook/notes?scope=trash&limit=1')
    assert response.status_code == 200
    result = response.json()
    assert len(result['notes']) == 1 and result['next_after'] is None
    row = result['notes'][0]
    assert row['deletion'] == second and row['can_restore'] and not row['can_edit']
    assert len(queries) == 5
    assert owner.client.get('/api/notebook/notes').json()['notes'] == []
    assert reader.client.get('/api/notebook/notes?scope=trash').json()['notes'] == []
    assert restore(owner, second).status_code == 200
    restored = owner.client.get('/api/notebook/notes?limit=1').json()
    assert len(restored['notes']) == 1 and restored['next_after'] is None
    assert restored['notes'][0]['deletion'] is None
    assert restored['notes'][0]['can_edit'] and not restored['notes'][0]['can_restore']
