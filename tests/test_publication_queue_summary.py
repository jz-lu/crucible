"""Compact reviewer navigation never substitutes for an authorized exact-note read."""
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from crucible import beta_access, dh_v4, notebook, registry
from test_notebook import (system, approve, content, create, import_workspace,
                           publication_body, request_publication, revise, workspace)
from test_notebook_access import notebook_access, token_for


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    # Real portable adapter and ordinary artifact checks; no native claim.
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


def queue(actor, **params):
    response = actor.client.get('/api/notebook/review-queue', params={'summary': 'true', **params})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response


def test_summary_preserves_exact_queued_revision_and_default_full_response(system):
    _, owner, reviewer, _, now = system
    first = create(owner, content(title='Earlier published revision'))
    request_publication(owner, first); approve(reviewer, first)
    now[0] += 1
    submitted = revise(owner, first, content(title='Submitted revision', kind='observation',
        body='SUBMITTED_FULL_BODY', conditions='SUBMITTED_CONDITIONS', extensions={'large': 'PRIVATE_EXTENSION'}))
    request_publication(owner, submitted)
    revise(owner, submitted, content(title='UNSUBMITTED_LATER_DRAFT'))

    default = reviewer.client.get('/api/notebook/review-queue').json()
    assert default == reviewer.client.get('/api/notebook/review-queue?summary=false').json()
    full, = default['notes']
    small, = queue(reviewer).json()['notes']
    for field in ('id', 'owner', 'can_edit', 'can_delete', 'can_restore', 'deletion', 'can_review',
                  'can_self_review', 'can_moderate', 'created_at', 'revision', 'content_sha256',
                  'publication_status', 'revision_count', 'shared_revision', 'proposal_revision', 'imported'):
        assert small[field] == full[field], field
    assert (small['id'], small['revision'], small['content_sha256']) == (
        submitted['id'], 2, submitted['content_sha256'])
    selected, = small['revisions']
    assert selected['summary'] is True and selected['imported'] is False
    assert selected['content'] == {key: full['revisions'][0]['content'][key]
                                  for key in ('title', 'claim', 'tags', 'kind')}
    assert selected['publication']['requested_at'] == full['revisions'][0]['publication']['requested_at']
    assert full['revisions'][0]['content']['body'] == 'SUBMITTED_FULL_BODY'
    assert 'UNSUBMITTED_LATER_DRAFT' not in queue(reviewer).text
    assert 'SUBMITTED_FULL_BODY' not in queue(reviewer).text
    assert 'PRIVATE_EXTENSION' not in queue(reviewer).text
    opened = reviewer.client.get(f'/api/notebook/notes/{submitted["id"]}?revision=2').json()['note']
    assert opened['revisions'][-1]['content']['body'] == 'SUBMITTED_FULL_BODY'


def test_summary_keeps_oldest_order_cursor_and_own_visibility_without_self_approval(system):
    app, owner, reviewer, reader, now = system
    own = create(reviewer, content(title='Own submitted request'))
    request_publication(reviewer, own)
    now[0] += 1
    other = create(owner, content(title='Other submitted request'))
    request_publication(owner, other)
    create(owner, content(title='UNSUBMITTED_PRIVATE_NOTE'))
    full = reviewer.client.get('/api/notebook/review-queue?limit=1').json()
    first = queue(reviewer, limit=1).json()
    assert first['next_after'] == full['next_after']
    assert [note['id'] for note in first['notes']] == [own['id']]
    assert first['notes'][0]['can_review'] is False
    assert first['notes'][0]['can_self_review'] is False
    second = queue(reviewer, limit=1, after=first['next_after']).json()
    assert [note['id'] for note in second['notes']] == [other['id']]
    assert second['notes'][0]['can_review'] is True and second['next_after'] is None
    assert 'UNSUBMITTED_PRIVATE_NOTE' not in queue(reviewer).text
    response = reviewer.post(f'/api/notebook/notes/{own["id"]}/publication-reviews',
        publication_body(own, decision='approved', reason='No self-approval permission.'))
    assert response.status_code == 403
    with app.state.db.transaction() as session:
        assert session.scalar(select(notebook.NotebookPublication.decision)
            .where(notebook.NotebookPublication.author_id == reviewer.identifier)) is None
    for actor in (owner, reader):
        assert actor.client.get('/api/notebook/review-queue?summary=true').status_code == 403
    assert TestClient(app).get('/api/notebook/review-queue?summary=true').status_code == 401


def test_two_pending_revisions_of_one_note_are_distinct_exact_queue_entries(system):
    _, owner, reviewer, _, now = system
    first = create(owner, content(title='First submitted version'))
    request_publication(owner, first)
    now[0] += 1
    second = revise(owner, first, content(title='Second submitted version'))
    request_publication(owner, second)
    one = queue(reviewer, limit=1).json()
    two = queue(reviewer, limit=1, after=one['next_after']).json()
    assert [(page['notes'][0]['revision'], page['notes'][0]['content_sha256']) for page in (one, two)] == [
        (first['revision'], first['content_sha256']), (second['revision'], second['content_sha256'])]
    assert one['notes'][0]['id'] == two['notes'][0]['id'] == first['id']
    assert two['next_after'] is None


def test_summary_has_constant_scalar_queries_and_omits_large_bodies_and_source_metadata(system):
    app, owner, reviewer, _, _ = system
    for number in range(25):
        value = content(title=f'Request {number:02}', body='BODY_MARKER' + 'b' * 39900,
                        conditions='CONDITIONS_MARKER' + 'c' * 19900)
        request_publication(owner, create(owner, value))
    with app.state.db.transaction(write=True) as session:
        for note in session.scalars(select(notebook.NotebookNote)):
            note.source_metadata = {'not_for_a_queue': 'SOURCE_METADATA_MARKER'}

    def capture(limit, **params):
        queries, expressions = [], []
        def sql(_, __, statement, ___, ____, _____):
            if statement.lstrip().startswith('SELECT'):
                queries.append(statement)
        def expr(_, statement, *args): expressions.append(statement)
        event.listen(app.state.db.engine, 'before_cursor_execute', sql)
        event.listen(app.state.db.engine, 'before_execute', expr)
        try:
            response = queue(reviewer, limit=limit, **params)
        finally:
            event.remove(app.state.db.engine, 'before_cursor_execute', sql)
            event.remove(app.state.db.engine, 'before_execute', expr)
        return response, queries, expressions

    one, small, _ = capture(1)
    many, large, expressions = capture(25)
    assert len(many.json()['notes']) == 25
    assert len(small) == len(large)  # Status hydration is batched, never per note.
    later, cursor_queries, _ = capture(1, after=one.json()['next_after'])
    assert len(later.json()['notes']) == 1
    assert len(cursor_queries) == len(large) + 1  # One stable cursor lookup.
    for statement in large:
        projection = statement.split('FROM', 1)[0]
        assert 'notebook_revisions.content AS ' not in projection
        assert 'notebook_revisions.source_revision AS ' not in projection
        assert 'notebook_notes.source_metadata' not in projection
        assert 'protocol_proposals.specification' not in projection
        assert 'protocol_proposal_revisions.specification' not in projection
    main = [statement for statement in expressions if
            'ORDER BY notebook_publications.requested_at, notebook_publications.id' in str(statement)]
    assert len(main) == 1 and main[0]._limit_clause.value == 26 and main[0]._offset_clause is None
    compiled = str(main[0].compile(dialect=postgresql.dialect()))
    assert '->>' in compiled and 'JSON_EXTRACT' not in compiled  # Compilation only, not a PG execution claim.
    assert len(many.content) < 60000
    assert all(marker not in many.text for marker in ('BODY_MARKER', 'CONDITIONS_MARKER', 'SOURCE_METADATA_MARKER'))
    full = reviewer.client.get('/api/notebook/review-queue?limit=25')
    assert len(full.content) > 1500000 and 'BODY_MARKER' in full.text
    print(f'Queue fixture: {len(large)} SELECTs for both 1 and 25 summaries; '
          f'{len(many.content)} summary bytes vs {len(full.content)} full bytes at25 notes.')


def test_imported_revision_summary_keeps_provenance_flag_without_imported_body(system):
    _, owner, reviewer, _, _ = system
    imported = import_workspace(owner, workspace())
    assert imported.status_code == 200
    note_id = next(iter(imported.json()['id_map'].values()))
    note = owner.client.get('/api/notebook/notes/' + note_id).json()['note']
    request_publication(owner, note)
    selected, = queue(reviewer).json()['notes']
    assert selected['imported'] is True
    assert selected['revisions'][0]['imported'] is True
    assert selected['revisions'][0]['summary'] is True
    assert 'body' not in selected['revisions'][0]['content']


def test_summary_never_authorizes_a_stale_or_wrong_hash_decision(system):
    _, owner, reviewer, _, _ = system
    note = create(owner); request_publication(owner, note)
    selected, = queue(reviewer).json()['notes']
    path = f'/api/notebook/notes/{note["id"]}/publication-reviews'
    bad = publication_body(selected, content_sha256='0' * 64, decision='approved', reason='Wrong hash.')
    assert reviewer.post(path, bad).status_code == 409
    assert len(queue(reviewer).json()['notes']) == 1
    valid = publication_body(selected, decision='approved', reason='Exact reviewed revision.')
    assert reviewer.post(path, valid).status_code == 200
    assert reviewer.post(path, valid).status_code == 200  # Existing exact replay remains valid.
    assert reviewer.post(path, {**valid, 'idempotency_key': str(uuid4())}).status_code == 409
    assert queue(reviewer).json() == {'notes': [], 'next_after': None}


def test_summary_rechecks_membership_and_rejects_agents_before_queue_reads(notebook_access):
    app, now, identifiers, actors = notebook_access
    owner, reviewer = actors['author'], actors['reviewer']
    note = create(owner, content(title='MEMBER_ONLY_QUEUED_TITLE')); request_publication(owner, note)
    assert len(queue(reviewer).json()['notes']) == 1
    token = token_for(app, reviewer)
    response = reviewer.client.get('/api/notebook/review-queue?summary=true', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 403 and 'MEMBER_ONLY_QUEUED_TITLE' not in response.text
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, identifiers['reviewer'], 'revoked', now[0])
    queries = []
    def sql(_, __, statement, ___, ____, _____): queries.append(statement)
    event.listen(app.state.db.engine, 'before_cursor_execute', sql)
    try:
        response = reviewer.client.get('/api/notebook/review-queue?summary=true')
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', sql)
    assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'
    assert 'MEMBER_ONLY_QUEUED_TITLE' not in response.text
    assert not any('notebook_publications' in statement for statement in queries)


@pytest.mark.parametrize('query', ['after=' + str(uuid4()), 'limit=0', 'limit=101', 'summary=not-a-bool'])
def test_summary_rejects_invalid_bounds_and_cursors(system, query):
    _, _, reviewer, _, _ = system
    response = reviewer.client.get('/api/notebook/review-queue?summary=true&' + query)
    assert response.status_code == 422
