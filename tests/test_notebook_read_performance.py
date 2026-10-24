"""Notebook history stays complete without querying each revision's reviewer."""
from uuid import uuid4

from sqlalchemy import select

from crucible import notebook, notebook_trash
from test_browser_admission import selects
from test_notebook import system, create, content, revise


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
