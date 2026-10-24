"""Whole-notebook filters, merged cursors and bounded curated summary reads."""
import base64
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, update
from sqlalchemy.dialects import postgresql

from crucible import notebook, notebook_sharing
from test_notebook import system, content, create, revise, request_publication, approve, import_workspace, workspace
from test_research_reviews import submit
from test_notebook_sharing import seed_legacy_shares


def seed_notes(actor, count, *, start=1, prefix=1, values=None):
    notes, revisions, records = [], [], []
    for number in range(start, start + count):
        identifier = str(UUID(int=(prefix << 124) + number))
        value = content(**(values(number) if values else {'title': f'Entry {number}'}))
        digest = notebook._hash(value)
        notes.append(notebook.NotebookNote(id=identifier, author_id=actor.identifier, created_at=float(number),
            latest_revision=1, idempotency_key=str(uuid4()), request_sha256='a'*64))
        revisions.append(notebook.NotebookRevision(id=str(uuid4()), note_id=identifier, revision=1,
            created_at=float(number), content=value, content_sha256=digest,
            idempotency_key=str(uuid4()), request_sha256='b'*64))
        records.append({'id': identifier, 'revision': 1, 'content_sha256': digest, 'created_at': float(number)})
    with actor.app.state.db.transaction(write=True) as session:
        session.add_all(notes); session.flush(); session.add_all(revisions)
    return records


@pytest.fixture
def curated(tmp_path, monkeypatch):
    path = tmp_path / 'curated.json'
    value = {'format': notebook.FORMAT, 'notes': [
        {'id': f'curated-example-{number:03d}', 'revisions': [dict(content(title=f'Curated match {number}',
            tags=['literature', 'curated-topic'], body='CURATED FULL BODY MUST NOT BE RETURNED'),
            revision=1, createdAt=(number * 4 + 1) * 1000)]} for number in range(7)], 'views': []}
    path.write_text(json.dumps(value))
    monkeypatch.setattr(notebook, 'CURATED_NOTES_PATH', path)
    notebook._curated_collection.cache_clear()
    yield path, value
    notebook._curated_collection.cache_clear()


def page(actor, **params):
    response = actor.client.get('/api/notebook/notes', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def browse(actor, **params):
    found, sizes, after = [], [], None
    for _ in range(30):
        result = page(actor, **params, **({'after': after} if after else {}))
        found.extend(result['notes']); sizes.append(len(result['notes']))
        after = result['next_after']
        if after is None:
            return found, sizes
    raise AssertionError('Unbounded or repeated notebook cursor')


def test_latest_summary_search_and_scopes_work_beyond_fifty_matches(system):
    _, owner, _, _, _ = system
    records = seed_notes(owner, 93, values=lambda number: {
        'title': f'Unrelated {number}' if number <= 30 else f'Needle summary {number}',
        'claim': 'A current result.', 'tags': ['literature', 'exact-topic'] if number > 30 else ['other'],
        'body': 'BODY_ONLY_TOKEN'})
    matches, sizes = browse(owner, q='needle', scope='literature', tag='exact-topic', limit=25)
    assert sizes == [25, 25, 13]
    assert [note['id'] for note in matches] == [note['id'] for note in reversed(records[30:])]
    assert len({note['id'] for note in matches}) == 63
    assert page(owner, q='BODY_ONLY_TOKEN')['notes'] == []
    assert len(page(owner, q='exact-topic', limit=100)['notes']) == 63
    assert page(owner, tag='exact')['notes'] == []
    changed = records[-1]
    revise(owner, changed, content(title='Current unrelated title', claim='No match here.', tags=[]))
    assert changed['id'] not in {note['id'] for note in browse(owner, q='needle', limit=25)[0]}
    assert len(browse(owner, scope='mine', limit=25)[0]) == 93


def test_search_uses_literal_wildcards_and_the_same_exact_tag_domain(system):
    _, owner, _, _, _ = system
    literal = create(owner, content(title='100%_ literal', tags=['a,b', 'exact-tag']))
    create(owner, content(title='100XX alternative', tags=['a', 'b', 'exact-tag-extra']))
    assert [note['id'] for note in page(owner, q='%_')['notes']] == [literal['id']]
    assert [note['id'] for note in page(owner, tag='exact-tag')['notes']] == [literal['id']]
    assert [note['id'] for note in page(owner, q='a,b')['notes']] == [literal['id']]
    for params in ({'q': '\x00'}, {'tag': '\x00'}, {'after': '\x00'}, {'scope': 'private'}):
        assert owner.client.get('/api/notebook/notes', params=params).status_code == 422


def test_human_written_filter_uses_latest_visible_tags_before_pagination(system, curated):
    _, owner, reviewer, reader, _ = system
    records = seed_notes(owner, 73, values=lambda number: {'title': f'Human subject {number}',
        'tags': ['human-written', 'topic'] if number <= 61 else ['topic']})
    found, sizes = browse(owner, scope='human', q='subject', tag='topic', include_curated=True, limit=25)
    assert sizes == [25, 25, 11]
    assert [item['id'] for item in found] == [item['id'] for item in reversed(records[:61])]
    assert page(reader, scope='human', include_curated=True)['notes'] == []
    saved = records[0]
    request_publication(owner, saved); approve(reviewer, saved)
    revised = revise(owner, saved, content(title='An agent revision', tags=['topic']))
    assert saved['id'] not in {item['id'] for item in browse(owner, scope='human')[0]}
    visible = page(reader, scope='human')['notes']
    assert [item['id'] for item in visible] == [saved['id']]
    assert visible[0]['revision'] == 1
    request_publication(owner, revised); approve(reviewer, revised)
    assert page(reader, scope='human')['notes'] == []


def test_merged_cursor_has_no_duplicates_and_default_mode_stays_account_only(system, curated):
    _, owner, _, _, _ = system
    before = seed_notes(owner, 29, prefix=1)
    after = seed_notes(owner, 29, prefix=15)
    records, sizes = browse(owner, include_curated='true', limit=25)
    expected = [identifier for _, identifier in sorted(
        [(-note['created_at'], note['id']) for note in before + after] +
        [(-note['revisions'][-1]['createdAt'] / 1000, 'curated:' + note['id']) for note in curated[1]['notes']])]
    assert [note['id'] for note in records] == expected
    assert sizes == [25, 25, 15] and len(set(expected)) == 65
    assert len(browse(owner, limit=25)[0]) == 58
    assert len(browse(owner, include_curated='true', scope='mine', limit=25)[0]) == 58
    literature = page(owner, include_curated='true', scope='literature', limit=25)['notes']
    assert len(literature) == 7
    for note in literature:
        assert note['source_kind'] == 'curated-research' and note['owner'] is None
        assert note['can_edit'] is False and note['publication_status'] == 'curated'
        assert set(note['revisions'][0]['content']) <= {'title', 'claim', 'tags', 'kind'}
        assert 'CURATED FULL BODY' not in json.dumps(note)


def test_saved_revision_time_reorders_owner_and_ignores_import_provenance(system):
    _, owner, _, _, now = system
    first = create(owner, content(title='First note'))
    now[0] += 10
    second = create(owner, content(title='Second note'))
    assert [item['id'] for item in page(owner)['notes']] == [second['id'], first['id']]
    now[0] += 10
    revised = revise(owner, first)
    listed = page(owner)['notes']
    assert [item['id'] for item in listed] == [first['id'], second['id']]
    assert listed[0]['revisions'][0]['created_at'] == now[0]
    assert listed[0]['created_at'] == first['created_at']
    # Imported dates are provenance. Their server-side save time controls order.
    now[0] += 10
    imported = workspace()
    imported['notes'][0]['revisions'][0]['createdAt'] = 0
    response = import_workspace(owner, imported)
    assert response.status_code == 200, response.text
    listed = page(owner)['notes']
    assert listed[0]['imported'] is True
    assert listed[0]['revisions'][0]['created_at'] == now[0]
    assert [item['id'] for item in listed[1:]] == [revised['id'], second['id']]


def test_equal_time_order_is_stable_across_multiple_pages(system):
    app, owner, _, _, _ = system
    records = seed_notes(owner, 73)
    with app.state.db.transaction(write=True) as session:
        session.execute(update(notebook.NotebookRevision).values(created_at=123.25))
    found, sizes = browse(owner, limit=25)
    assert sizes == [25, 25, 23]
    assert [item['id'] for item in found] == [item['id'] for item in records]
    first = page(owner, limit=25)
    assert first['next_after'] != first['notes'][-1]['id']
    assert notebook._read_browse_cursor(first['next_after']) == (123.25, records[24]['id'])


def test_private_edits_do_not_change_readers_order_timestamps_or_cursor(system):
    _, owner, reviewer, reader, now = system
    older = create(owner, content(title='Visible older'))
    request_publication(owner, older); approve(reviewer, older)
    now[0] += 10
    newer = create(owner, content(title='Visible newer'))
    request_publication(owner, newer); approve(reviewer, newer)
    first = page(reader, limit=1)
    assert first['notes'][0]['id'] == newer['id']
    before = page(reader)['notes']
    now[0] += 1000
    revise(owner, older, content(title='PRIVATE_LATER_TOKEN'))
    create(owner, content(title='PRIVATE_NEW_TOKEN'))
    for actor in (reader, reviewer):
        listed = page(actor)['notes']
        assert [item['id'] for item in listed] == [newer['id'], older['id']]
        assert [item['revisions'][0]['created_at'] for item in listed] == [
            item['revisions'][0]['created_at'] for item in before]
        assert all(item['revision'] == 1 and item['revision_count'] == 1 for item in listed)
        assert page(actor, limit=1)['next_after'] == first['next_after']
        next_page = page(actor, limit=1, after=first['next_after'])
        assert [item['id'] for item in next_page['notes']] == [older['id']]
        assert next_page['next_after'] is None
        assert 'PRIVATE_' not in json.dumps(listed)
    assert page(owner, limit=1, q='PRIVATE_LATER_TOKEN')['notes'][0]['id'] == older['id']


def test_curated_recent_order_uses_latest_revision_and_unicode_cursor(system, curated):
    _, owner, _, _, _ = system
    path, value = curated
    value['notes'][0]['id'] = 'α' * 100
    value['notes'][0]['revisions'][0]['createdAt'] = 100000
    value['notes'][0]['revisions'].append(dict(content(title='Current curated summary'),
        revision=2, createdAt=23000))
    path.write_text(json.dumps(value))
    records, sizes = browse(owner, include_curated='true', limit=1)
    expected = sorted(value['notes'], key=lambda item: (-item['revisions'][-1]['createdAt'], item['id']))
    assert [item['id'] for item in records] == ['curated:' + item['id'] for item in expected]
    assert sizes == [1] * 7
    assert records[1]['id'] == 'curated:' + 'α' * 100
    assert records[1]['revisions'][0]['created_at'] == 23


@pytest.mark.parametrize('cursor', [
    '', 'not-a-cursor', str(UUID(int=1)), 'curated:legacy-id', '%%%%', '\x00',
    'x' * 769,
    *[base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=') for payload in (
        [], [2, 1, str(UUID(int=1))], [True, 1, str(UUID(int=1))],
        [1, True, str(UUID(int=1))], [1, -1, str(UUID(int=1))],
        [1, float('nan'), str(UUID(int=1))], [1, float('inf'), str(UUID(int=1))],
        [1, 9007199254740992, str(UUID(int=1))], [1, 1, 'not-an-id'],
        [1, 1, 'curated:'], [1, 1, 'curated:' + 'x' * 101], [1, 1, 'curated:\x00'],
    )],
])
def test_invalid_and_legacy_cursors_fail_clearly(system, cursor):
    _, owner, _, _, _ = system
    response = owner.client.get('/api/notebook/notes', params={'after': cursor})
    assert response.status_code == 422
    if len(cursor) <= 768:
        assert response.json()['detail'] == 'Invalid notebook page cursor. Restart the notebook list.'


def test_search_does_not_reveal_private_later_versions_or_other_accounts(system, curated):
    _, owner, reviewer, reader, _ = system
    first = create(owner, content(title='Shared match', tags=['literature']))
    request_publication(owner, first); approve(reviewer, first)
    revise(owner, first, content(title='PRIVATE_LATER_TOKEN', tags=['private-only']))
    create(owner, content(title='PRIVATE_NEW_TOKEN'))
    for actor in (reader, reviewer):
        assert page(actor, include_curated='true', q='PRIVATE_')['notes'] == []
        assert page(actor, include_curated='true', tag='private-only')['notes'] == []
        shared = page(actor, q='Shared match')['notes']
        assert len(shared) == 1 and shared[0]['revision'] == 1
        assert shared[0]['revision_count'] == 1
        assert page(actor, include_curated='true', scope='mine')['notes'] == []
    assert page(owner, q='Shared match')['notes'] == []
    assert page(owner, q='PRIVATE_LATER_TOKEN')['notes'][0]['revision'] == 2


def test_retired_shared_scope_does_not_disclose_notes_or_curated_matches(system, curated):
    _, owner, reviewer, reader, _ = system
    first = create(owner, content(title='EARLIER_PRIVATE_TOKEN', tags=['earlier-private']))
    public = revise(owner, first, content(title='Public snapshot'))
    request_publication(owner, public); approve(reviewer, public)
    latest = revise(owner, public, content(title='PRIVATE_LATEST_TOKEN', tags=['granted']))
    seed_legacy_shares(owner, latest, ['reader'])
    for actor in (owner, reviewer, reader):
        response = actor.client.get('/api/notebook/notes', params={'scope':'shared', 'include_curated':'true'})
        assert response.status_code == 410 and 'notes' not in response.json()
    for params in ({'q':'EARLIER_PRIVATE_TOKEN'}, {'tag':'earlier-private'}, {'q':'PRIVATE_LATEST_TOKEN'}, {'tag':'granted'}):
        assert page(reader, **params)['notes'] == []
    visible = page(reader, q='Public snapshot')['notes']
    assert [(row['id'], row['revision'], row['revision_count']) for row in visible] == [(first['id'], 2, 1)]


def test_legacy_grants_do_not_affect_search_or_newest_first_pagination(system, curated):
    app, owner, _, reader, _ = system
    records = seed_notes(owner, 93, values=lambda number: {
        'title': f'Needle private {number}', 'claim': 'Current result.',
        'tags': ['shared-topic'], 'body':'PRIVATE_BODY_ONLY_TOKEN'})
    with app.state.db.transaction(write=True) as session:
        session.add_all([notebook_sharing.PrivateNoteShare(id=str(uuid4()), note_id=record['id'],
            recipient_id=reader.identifier, from_revision=1, shared_at=float(index), revoked_at=None,
            state=('pending','accepted','dismissed')[index % 3]) for index, record in enumerate(records)])
    owned = seed_notes(reader, 53, prefix=2, values=lambda number: {'title':f'Needle owned {number}', 'tags':['shared-topic']})
    found, sizes = browse(reader, q='needle', tag='shared-topic', limit=25)
    assert sizes == [25, 25, 3]
    assert [row['id'] for row in found] == [record['id'] for record in reversed(owned)]
    assert all(row['can_edit'] and row['revision_count'] == 1 for row in found)
    assert page(reader, q='PRIVATE_BODY_ONLY_TOKEN')['notes'] == []
    revise(owner, records[30], content(title='Needle newest private', tags=['shared-topic']))
    assert page(reader, q='needle', tag='shared-topic', limit=1)['notes'][0]['id'] == owned[-1]['id']


def test_merged_curated_proposals_remain_exact_and_private_across_old_revisions(system, curated):
    _, owner, reviewer, reader, _ = system
    path, value = curated
    source = value['notes'][0]
    old = {key: val for key, val in source['revisions'][0].items() if key in notebook.CONTENT_FIELDS}
    ref = {'id': 'curated:' + source['id'], 'revision': 1, 'content_sha256': notebook._hash(old)}
    record = submit(owner, ref, purpose='visualization_nomination')
    source['revisions'].append(dict(content(title='Current curated summary', tags=['literature']), revision=2, createdAt=2000))
    path.write_text(json.dumps(value))
    for actor in (owner, reviewer):
        records = page(actor, include_curated='true', proposals_only='true')['notes']
        assert len(records) == 1 and records[0]['revision'] == 2
        assert records[0]['proposal_revision']['revision'] == 1
        assert records[0]['proposal_revision']['requests'][0]['id'] == record['id']
        assert records[0]['revisions'][0]['requests'] == []
    assert page(reader, include_curated='true', proposals_only='true')['notes'] == []
    assert page(owner, include_curated='true', proposals_only='true', q='Curated match')['notes'] == []
    assert page(owner, include_curated='true', proposals_only='true', q='Current curated')['notes'][0]['id'] == ref['id']


def test_curated_cache_is_validated_refreshed_and_never_caches_account_requests(system, curated, monkeypatch):
    _, owner, _, reader, _ = system
    path, value = curated
    calls, original = [], notebook._workspace
    def validate(document):
        calls.append(True)
        return original(document)
    monkeypatch.setattr(notebook, '_workspace', validate)
    for actor in (owner, reader, owner):
        assert len(page(actor, include_curated='true', scope='literature')['notes']) == 7
    assert len(calls) == 1
    value['notes'][0]['revisions'][0]['title'] = 'Changed bundled title'
    path.write_text(json.dumps(value))
    assert page(owner, include_curated='true', q='Changed bundled')['notes'][0]['id'].startswith('curated:')
    assert len(calls) == 2
    path.write_text('{invalid')
    assert owner.client.get('/api/notebook/notes?include_curated=true').status_code == 503
    assert page(owner)['notes'] == []


def test_browse_queries_remain_scalar_batched_and_portable(system, curated):
    app, owner, _, _, _ = system
    seed_notes(owner, 35, values=lambda number: {'title': f'Searchable entry {number}', 'tags': ['literature']})
    def capture(limit, after=None):
        queries, expressions = [], []
        def sql(_, __, statement, ___, ____, _____):
            if statement.lstrip().startswith('SELECT'):
                queries.append(statement)
        def expr(_, statement, *args): expressions.append(statement)
        event.listen(app.state.db.engine, 'before_cursor_execute', sql)
        event.listen(app.state.db.engine, 'before_execute', expr)
        try:
            result = page(owner, include_curated='true', q='entry', scope='literature', limit=limit,
                          **({'after': after} if after else {}))
            assert len(result['notes']) == limit
        finally:
            event.remove(app.state.db.engine, 'before_cursor_execute', sql)
            event.remove(app.state.db.engine, 'before_execute', expr)
        main = [statement for statement in expressions if
                'ORDER BY notebook_revisions.created_at DESC, notebook_notes.id' in str(statement)]
        assert len(main) == 1
        assert main[0]._limit_clause.value == limit + 1
        assert main[0]._offset_clause is None
        if after:
            assert 'notebook_revisions.created_at <' in str(main[0])
            assert 'notebook_notes.id >' in str(main[0])
        return queries, expressions
    small, _ = capture(1)
    large, expressions = capture(25)
    assert len(small) == len(large)
    next_page, _ = capture(25, page(owner, include_curated='true', q='entry', scope='literature', limit=1)['next_after'])
    assert len(next_page) == len(large)
    for sql in large:
        projection = sql.split('FROM', 1)[0]
        assert 'notebook_revisions.content AS ' not in projection
        assert 'research_review_requests.question' not in projection
        assert 'research_assessments.findings' not in projection
    compiled = [str(statement.compile(dialect=postgresql.dialect())) for statement in expressions]
    assert any('ILIKE' in sql and '->>' in sql for sql in compiled)
    assert all('JSON_EXTRACT' not in sql for sql in compiled)
