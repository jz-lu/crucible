"""Owner-only compact progress; polling never loads content or model findings."""
from uuid import uuid4

import pytest
from sqlalchemy import event

from test_notebook import system, create, request_publication, approve, content
from test_notebook_agents import agent
from test_notebook_moderation import withdraw


def request_id(owner):
    note = request_publication(owner, create(owner, content(body="PRIVATE_BODY_SENTINEL")))
    return note, note["revisions"][0]["publication"]["id"]


def test_owner_projection_is_bounded_and_has_no_content_reads(system):
    app, owner, reviewer, reader, _ = system
    note, identifier = request_id(owner)
    summary = owner.client.get('/api/notebook/notes?scope=mine&projection=index').json()
    assert summary['notes'][0]['revisions'][0]['publication'] == {'id': identifier}
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(app.state.db.engine, 'before_cursor_execute', capture)
    try:
        response = owner.client.get('/api/notebook/publication-status', params={'publication_id': identifier})
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', capture)
    assert response.status_code == 200
    assert response.json() == {'publications': [{'id': identifier, 'note_id': note['id'],
        'revision': 1, 'content_sha256': note['content_sha256'], 'status': 'submitted',
        'screening_status': None}]}
    assert not any('notebook_revisions.content ' in query or 'notebook_revisions.content,' in query
                   or 'publication_screening_attempts' in query for query in statements)
    approve(reviewer, note)
    assert owner.client.get('/api/notebook/publication-status', params={'publication_id': identifier}).json()['publications'][0]['status'] == 'approved'
    # Even a published record does not grant another account owner-status access.
    for actor in (reviewer, reader):
        assert actor.client.get('/api/notebook/publication-status', params={'publication_id': identifier}).status_code == 404


@pytest.mark.parametrize('identifiers', [[], ['bad'], [str(uuid4())] * 26])
def test_status_rejects_unbounded_or_malformed_queries(system, identifiers):
    _, owner, *_ = system
    response = owner.client.get('/api/notebook/publication-status', params=[('publication_id', value) for value in identifiers])
    assert response.status_code == 422


def test_status_rejects_duplicates_and_mixed_ownership_without_partial_disclosure(system):
    _, owner, reviewer, *_ = system
    _, own = request_id(owner)
    _, foreign = request_id(reviewer)
    assert owner.client.get('/api/notebook/publication-status', params=[('publication_id', own)] * 2).status_code == 422
    for other in (foreign, str(uuid4())):
        result = owner.client.get('/api/notebook/publication-status', params=[('publication_id', own), ('publication_id', other)])
        assert result.status_code == 404 and 'publications' not in result.json()


def test_status_agent_scope_and_owner_are_checked(system):
    app, owner, reviewer, *_ = system
    _, identifier = request_id(owner)
    path = '/api/notebook/publication-status?publication_id=' + identifier
    assert agent(app, owner, ['notebook:read']).get(path).status_code == 200
    assert agent(app, reviewer, ['notebook:read']).get(path).status_code == 404
    assert agent(app, owner, ['notebook:write']).get(path).status_code == 403
