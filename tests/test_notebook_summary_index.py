"""A bounded all-or-nothing summary index preserves the paginated notebook."""
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from crucible import beta_access, notebook
from test_beta_boundary import private_app
from test_notebook import system, content, create, revise, request_publication, approve
from test_notebook_agents import agent
from test_notebook_browse import curated, seed_notes, browse, page
from test_notebook_moderation import withdraw
from test_notebook_trash import delete
from test_research_reviews import submit


def index(actor, **params):
    response = actor.client.get('/api/notebook/notes', params={'projection': 'index', **params})
    assert response.status_code == 200, response.text
    data = response.json()
    assert response.headers['cache-control'] == 'no-store'
    assert data['format'] == 'crucible-notebook-summary-index-v1'
    assert data['projection'] == 'index' and data['viewer_id'] == actor.identifier
    assert data['next_after'] is None
    return data


def test_complete_index_matches_all_pages_with_curated_summaries_and_no_bodies(system, curated):
    app, owner, _, _, _ = system
    seed_notes(owner, 121, values=lambda n: {'title': f'Note {n}', 'claim': 'Visible summary',
        'tags': ['human-written', 'topic'] if n % 2 else ['literature'],
        'body': 'ACCOUNT_BODY_NOT_IN_INDEX', 'extensions': {'private': 'EXTENSION_NOT_IN_INDEX'}})
    with app.state.db.transaction(write=True) as session:
        note = session.get(notebook.NotebookNote, browse(owner)[0][0]['id'])
        note.source_metadata = {'private': 'IMPORTED_METADATA_NOT_IN_INDEX'}
    response = index(owner, include_curated=True)
    assert response['complete'] is True and response['overflow_reason'] is None
    assert response['scope'] == 'all' and response['include_curated'] is True
    assert response['notes'] == browse(owner, include_curated=True)[0]
    assert len(response['notes']) == 128
    encoded = json.dumps(response)
    for private in ('ACCOUNT_BODY_NOT_IN_INDEX', 'EXTENSION_NOT_IN_INDEX',
                    'IMPORTED_METADATA_NOT_IN_INDEX', 'CURATED FULL BODY MUST NOT BE RETURNED'):
        assert private not in encoded
    # These exact metadata filters are derivable from the complete set. Search
    # still uses the original server endpoint, including literal wildcard rules.
    for scope, tag in [('human', 'human-written'), ('literature', 'literature')]:
        expected = [row for row in response['notes'] if tag in row['revisions'][-1]['content']['tags']]
        assert expected == browse(owner, scope=scope, include_curated=True)[0]
    assert [row for row in response['notes'] if 'topic' in row['revisions'][-1]['content']['tags']] == browse(owner, tag='topic', include_curated=True)[0]


def test_index_keeps_latest_visible_revision_and_exact_proposal_status(system, curated):
    _, owner, reviewer, reader, _ = system
    public = create(owner, content(title='Approved summary', tags=['literature']))
    request_publication(owner, public); approve(reviewer, public)
    private = revise(owner, public, content(title='PRIVATE_LATEST_SUMMARY', tags=['human-written']))
    proposal = submit(owner, private, purpose='procedure')
    create(owner, content(title='PRIVATE_UNPUBLISHED_SUMMARY'))
    for actor in (reviewer, reader):
        result = index(actor, include_curated=True)
        assert result['notes'] == browse(actor, include_curated=True)[0]
        assert 'PRIVATE_' not in json.dumps(result)
        observed = next(row for row in result['notes'] if row['id'] == public['id'])
        assert observed['revision'] == 1 and observed['revision_count'] == 1
        assert observed['proposal_revision'] is None
    owned = index(owner)['notes']
    observed = next(row for row in owned if row['id'] == public['id'])
    assert observed['revision'] == 2 and observed['shared_revision']['revision'] == 1
    assert observed['proposal_revision']['requests'][0]['id'] == proposal['id']
    assert [row for row in owned if row['proposal_revision'] is not None] == browse(owner, proposals_only=True)[0]


def test_index_rechecks_withdrawal_and_trash_without_cross_request_cache(system):
    _, owner, reviewer, reader, _ = system
    note = create(owner)
    request_publication(owner, note); approve(reviewer, note)
    assert len(index(reader)['notes']) == 1
    assert withdraw(reviewer, note).status_code == 200
    assert index(reader)['notes'] == []
    assert index(owner)['notes'][0]['publication_status'] == 'withdrawn'
    draft = create(owner, content(title='Private draft'))
    assert delete(owner, draft).status_code == 200
    assert draft['id'] not in json.dumps(index(owner))
    assert page(owner, scope='trash')['notes'][0]['id'] == draft['id']


@pytest.mark.parametrize('params', [
    {'q': ''}, {'q': 'title'}, {'after': 'cursor'}, {'limit': 50}, {'tag': 'topic'},
    {'proposals_only': False}, {'scope': 'human'}, {'scope': 'literature'}, {'scope': 'trash'},
    {'scope': 'shared'}, {'projection': 'unknown'},
])
def test_index_rejects_partial_query_domains(system, params):
    _, owner, *_ = system
    response = owner.client.get('/api/notebook/notes', params={'projection': 'index', **params})
    assert response.status_code == 422 and 'notes' not in response.json()


def test_count_bound_returns_no_partial_index_and_leaves_pagination_available(system, curated, monkeypatch):
    _, owner, _, reader, _ = system
    assert notebook.SUMMARY_INDEX_MAX_NOTES == 500
    assert index(reader)['complete'] is True and index(reader)['notes'] == []
    monkeypatch.setattr(notebook, 'SUMMARY_INDEX_MAX_NOTES', 3)
    seed_notes(owner, 3)
    assert index(owner)['complete'] is True and len(index(owner)['notes']) == 3
    seed_notes(owner, 1, start=4)
    result = index(owner)
    assert result['complete'] is False and result['notes'] == [] and result['overflow_reason'] == 'count_limit'
    assert len(browse(owner, limit=2)[0]) == 4
    # Curated and account rows share the same total cap.
    assert index(reader, include_curated=True)['overflow_reason'] == 'count_limit'


def test_byte_bound_counts_utf8_and_never_returns_a_truncated_complete_index(system, monkeypatch):
    _, owner, *_ = system
    assert notebook.SUMMARY_INDEX_MAX_BYTES == 512 * 1024
    create(owner, content(claim='界' * 500))
    full = index(owner)
    exact_bytes = len(notebook._bytes(full))
    assert exact_bytes > len(json.dumps(full, ensure_ascii=False, separators=(',', ':')))
    monkeypatch.setattr(notebook, 'SUMMARY_INDEX_MAX_BYTES', exact_bytes)
    assert index(owner)['complete'] is True
    monkeypatch.setattr(notebook, 'SUMMARY_INDEX_MAX_BYTES', exact_bytes - 1)
    overflow = index(owner)
    assert overflow['complete'] is False and overflow['notes'] == [] and overflow['overflow_reason'] == 'byte_limit'
    assert len(page(owner)['notes']) == 1


def test_scoped_agent_index_stays_owned_and_does_not_inherit_browser_reviewer_rights(system):
    app, owner, reviewer, _, _ = system
    public = create(owner)
    request_publication(owner, public); approve(reviewer, public)
    private = create(reviewer)
    client = agent(app, reviewer, ['notebook:read'])
    client.cookies.update(owner.client.cookies)
    result = client.get('/api/notebook/notes?projection=index').json()
    assert result['viewer_id'] == reviewer.identifier and result['scope'] == 'mine'
    assert result['complete'] is True and [row['id'] for row in result['notes']] == [private['id']]
    assert not any(result['capabilities'].values()) and not result['notes'][0]['can_review']
    assert client.get('/api/notebook/notes?projection=index&include_curated=true').status_code == 422
    denied = agent(app, reviewer, ['notebook:write'])
    assert denied.get('/api/notebook/notes?projection=index').status_code == 403
    assert TestClient(app).get('/api/notebook/notes?projection=index').status_code == 401


def test_index_requires_fresh_beta_access_on_every_request(private_app):
    path = '/api/notebook/notes?projection=index&include_curated=true'
    assert TestClient(private_app).get(path).status_code == 401
    member = private_app.test_login('participant')
    note = create(member, content(title='MEMBER_ONLY_SUMMARY'))
    visible = member.client.get(path)
    assert visible.status_code == 200 and note['id'] in visible.text
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, 'revoked', private_app.test_now[0])
    denied = member.client.get(path)
    assert denied.status_code == 401 and 'MEMBER_ONLY_SUMMARY' not in denied.text
    assert 'notes' not in denied.json() and denied.headers['cache-control'] == 'no-store'


def test_index_uses_bounded_scalar_batched_queries_and_portable_json_projection(system, curated):
    app, owner, *_ = system
    seed_notes(owner, 1)
    def capture():
        queries, expressions = [], []
        def sql(_, __, statement, ___, ____, _____):
            if statement.lstrip().startswith('SELECT'): queries.append(statement)
        def expression(_, statement, *args): expressions.append(statement)
        event.listen(app.state.db.engine, 'before_cursor_execute', sql)
        event.listen(app.state.db.engine, 'before_execute', expression)
        try:
            result = index(owner, include_curated=True)
        finally:
            event.remove(app.state.db.engine, 'before_cursor_execute', sql)
            event.remove(app.state.db.engine, 'before_execute', expression)
        return queries, expressions, result
    small, _, _ = capture()
    seed_notes(owner, 119, start=2)
    large, expressions, result = capture()
    assert len(result['notes']) == 127 and len(small) == len(large)
    main = [statement for statement in expressions if 'ORDER BY notebook_revisions.created_at DESC, notebook_notes.id' in str(statement)]
    assert len(main) == 1 and main[0]._limit_clause.value == 501 and main[0]._offset_clause is None
    for statement in large:
        projection = statement.split('FROM', 1)[0]
        assert 'notebook_revisions.content AS ' not in projection and 'source_metadata' not in projection
        assert 'research_review_requests.question' not in projection and 'research_assessments.findings' not in projection
    compiled = str(main[0].compile(dialect=postgresql.dialect()))
    assert '->>' in compiled and 'JSON_EXTRACT' not in compiled
