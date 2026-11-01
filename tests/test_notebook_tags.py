"""Whole-notebook tag discovery respects the reader's exact visibility."""
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from test_notebook import system, create, content, revise, request_publication, approve
from test_notebook_browse import curated, seed_notes
from test_notebook_agents import agent
from test_notebook_trash import delete


def tags(actor, query=''):
    response = actor.client.get('/api/notebook/tags', params={'q':query})
    assert response.status_code == 200, response.text
    return response.json()['tags']


def test_tag_suggestions_use_latest_visible_revisions_and_curated_research(system, curated):
    _, owner, reviewer, reader, _ = system
    saved = create(owner, content(tags=['private-only']))
    shared = revise(owner, saved, content(tags=['shared-topic', 'a,b', '100%_literal']))
    request_publication(owner, shared); approve(reviewer, shared)
    revise(owner, shared, content(tags=['PRIVATE_LATER_SECRET']))
    assert tags(reader, 'shared') == ['shared-topic']
    assert tags(reader, 'private') == []
    assert tags(owner, 'PRIVATE_LATER') == ['PRIVATE_LATER_SECRET']
    assert tags(owner, 'shared') == []
    assert tags(reader, 'curated') == ['curated-topic']
    assert tags(reader, 'a,b') == ['a,b']
    assert tags(reader, '%_') == ['100%_literal']
    assert tags(reader, '\\') == []


def test_tag_search_covers_notes_beyond_any_page_and_deduplicates(system, curated):
    _, owner, _, reader, _ = system
    seed_notes(owner, 80, values=lambda i: {'tags':['needle' if i == 1 else 'other']})
    create(owner, content(tags=['Needle', 'needle-point', 'a-needle']))
    assert tags(owner, 'needle') == ['Needle', 'needle-point', 'a-needle']
    assert tags(reader, 'needle') == []
    for i in range(3):
        create(owner, content(tags=[f'subject-{j + i * 40:03}' for j in range(40)]))
    assert len(tags(owner, 'subject-')) == 50
    assert tags(owner, 'subject-119') == ['subject-119']


def test_tag_endpoint_requires_browser_access_and_omits_trash(system, curated):
    app, owner, _, _, _ = system
    saved = create(owner, content(tags=['discarded-tag']))
    delete(owner, saved)
    assert tags(owner, 'discarded') == []
    assert TestClient(app).get('/api/notebook/tags').status_code == 401
    assert agent(app, owner).get('/api/notebook/tags').status_code == 403
    for query in ('\x00', 'x' * 101):
        assert owner.client.get('/api/notebook/tags', params={'q':query}).status_code == 422


def test_tag_query_is_bounded_scalar_and_compiles_for_postgresql(system, curated):
    app, owner, *_ = system
    seed_notes(owner, 70)
    statements = []
    def capture(_, statement, *args): statements.append(statement)
    event.listen(app.state.db.engine, 'before_execute', capture)
    try:
        assert tags(owner, 'finite') == ['finite groups']
    finally:
        event.remove(app.state.db.engine, 'before_execute', capture)
    search = [statement for statement in statements if 'tag_sources' in str(statement)]
    assert len(search) == 1 and search[0]._limit_clause.value == 50
    sql = str(search[0].compile(dialect=postgresql.dialect()))
    assert '->>' in sql and 'ILIKE' in sql and 'JSON_EXTRACT' not in sql
    assert 'notebook_revisions.content AS ' not in sql
