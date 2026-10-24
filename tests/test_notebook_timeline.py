"""Creation-date counts cover the current readable corpus, never private history."""
from datetime import datetime, timezone
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, update
from sqlalchemy.dialects import postgresql

from crucible import beta_access, notebook, notebook_sharing
from test_notebook import system, create, revise, content, workspace, import_workspace, request_publication, approve
from test_notebook_access import notebook_access, token_for
from test_notebook_browse import seed_notes
from test_notebook_sharing import seed_legacy_shares
from test_proposal_notes import scheme_content, submit as submit_scheme
from test_research_reviews import submit as submit_research

PATH = '/api/notebook/timeline'


@pytest.fixture
def curated(tmp_path, monkeypatch):
    path = tmp_path / 'timeline-curated.json'
    def write(notes):
        path.write_text(json.dumps({'format': notebook.FORMAT, 'notes': notes, 'views': []}))
        notebook._curated_collection.cache_clear()
    write([])
    monkeypatch.setattr(notebook, 'CURATED_NOTES_PATH', path)
    yield write, path
    notebook._curated_collection.cache_clear()


def timeline(actor):
    response = actor.client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return response.json()


def utc_day(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()


def test_empty_timeline_has_no_synthetic_points(system, curated):
    _, owner, _, _, now = system
    assert timeline(owner) == {'format': 'crucible-notebook-timeline-v1',
        'date_basis': 'recorded_creation_utc', 'audience': 'currently_visible_notes',
        'as_of': utc_day(now[0]), 'total': 0, 'undated_count': 0, 'points': [], 'server_time': now[0]}


def test_counts_each_note_once_and_uses_server_import_date_not_authored_history(system, curated):
    app, owner, reviewer, _, now = system
    today = now[0]; midnight = int(today // 86400) * 86400
    now[0] = midnight - 1
    first = create(owner, content(title='PRIVATE_TITLE_NEVER_RETURNED', body='PRIVATE_BODY_NEVER_RETURNED'))
    request_publication(owner, first); approve(reviewer, first)
    now[0] = midnight
    second = create(owner)
    first2 = revise(owner, first)
    request_publication(owner, first2); approve(reviewer, first2)
    now[0] = today
    # Authored dates, including future ones, survive only as import provenance.
    imported = workspace('imported-on-server', revisions=3)
    imported['notes'][0]['revisions'][0]['createdAt'] = 9007199254740991
    assert import_workspace(owner, imported).status_code == 200
    result = timeline(owner)
    assert result['total'] == 3 and result['undated_count'] == 0
    assert result['points'] == [
        {'date': utc_day(midnight-1), 'created': 1, 'total': 1},
        {'date': utc_day(midnight), 'created': 2, 'total': 3}]
    assert not any(marker in json.dumps(result) for marker in (first['id'], second['id'], 'PRIVATE_', 'content_sha256', 'revisions'))
    assert timeline(reviewer)['total'] == 1  # Two public revisions still make one note.


def test_current_visibility_matches_all_notes_and_ignores_retired_grants(system, curated):
    _, owner, reviewer, reader, _ = system
    public = create(owner); request_publication(owner, public); approve(reviewer, public)
    private = create(owner)
    submitted = create(owner); request_publication(owner, submitted)
    scheme = create(owner, scheme_content()); assert submit_scheme(owner, scheme).status_code == 200
    packet = create(owner); submit_research(owner, packet, purpose='procedure')
    revise(owner, public, content(title='Private later revision'))
    assert timeline(owner)['total'] == 5
    assert timeline(reader)['total'] == timeline(reviewer)['total'] == 1
    seed_legacy_shares(owner, private, ['reader'])
    assert timeline(reader)['total'] == timeline(reviewer)['total'] == 1
    for actor in (owner, reviewer, reader):
        notes = actor.client.get('/api/notebook/notes?include_curated=true&limit=100').json()['notes']
        assert timeline(actor)['total'] == len(notes)


def test_timeline_excludes_retired_grants_regardless_of_historical_response(system, curated):
    app, owner, reviewer, reader, _ = system
    private = create(owner, content(title='Privately shared note'))
    public = create(owner, content(title='Independently published revision'))
    request_publication(owner, public); approve(reviewer, public)
    later = revise(owner, public, content(title='Later private revision'))
    for original in (private, later):
        seed_legacy_shares(owner, original, ['reader'])

    for legacy_state in ('pending', 'accepted', 'dismissed'):
        with app.state.db.transaction(write=True) as session:
            session.execute(update(notebook_sharing.PrivateNoteShare).values(state=legacy_state))
        listed = reader.client.get('/api/notebook/notes').json()['notes']
        result = timeline(reader)
        assert result['total'] == len(listed) == 1
        assert sum(point['created'] for point in result['points']) == 1
        assert timeline(owner)['total'] == 2 and timeline(reviewer)['total'] == 1
        assert {item['id']: item['revision'] for item in listed} == {public['id']: 1}
    listed = reader.client.get('/api/notebook/notes').json()['notes']
    assert timeline(reader)['total'] == 1
    assert [(item['id'], item['revision']) for item in listed] == [(public['id'], 1)]


def test_curated_notes_use_first_date_once_and_quarantine_unusable_dates(system, curated):
    _, owner, _, _, now = system
    old = int((now[0] - 86400) * 1000)
    def note(identifier, timestamp, revisions=1):
        return {'id': identifier, 'revisions': [dict(content(title=identifier), revision=i+1,
            createdAt=timestamp if i == 0 else int(now[0]*1000)) for i in range(revisions)]}
    curated[0]([note('earlier-curated', old, 4), note('same-date', old),
        note('timestamp-zero', 0), note('future', int((now[0]+1)*1000)), note('huge', 9007199254740991)])
    result = timeline(owner)
    assert result['total'] == 5 and result['undated_count'] == 3
    assert result['points'] == [{'date': utc_day(old/1000), 'created': 2, 'total': 2}]
    assert all(n['id'] not in json.dumps(result) for n in notebook.curated_collection())
    curated[1].unlink()
    response = owner.client.get(PATH)
    assert response.status_code == 503 and 'points' not in response.json()


def test_account_dates_aggregate_without_loading_note_bodies_or_one_query_per_note(system, curated):
    app, owner, _, _, _ = system
    seed_notes(owner, 150, values=lambda n: {'title': f'Large note {n}', 'body': 'PRIVATE_BODY'*500})
    compiled, statements = [], []
    def sql(connection, cursor, statement, parameters, context, executemany):
        if 'notebook_notes' in statement:
            statements.append(statement)
            compiled.append(context.compiled.statement.compile(dialect=postgresql.dialect()))
    event.listen(app.state.db.engine, 'before_cursor_execute', sql)
    try:
        result = timeline(owner)
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', sql)
    assert result['total'] == 150 and result['points'] == [{'date': '1970-01-01', 'created': 150, 'total': 150}]
    assert len(statements) == 1
    assert 'count(*)' in statements[0].lower() and 'GROUP BY' in statements[0]
    assert 'content' not in statements[0] and 'source_revision' not in statements[0]
    assert 'floor(' in str(compiled[0]) and 'GROUP BY' in str(compiled[0])


def test_unusable_account_dates_do_not_overflow_or_create_future_points(system, curated):
    app, owner, _, _, now = system
    records = seed_notes(owner, 5)
    with app.state.db.transaction(write=True) as session:
        for record, stamp in zip(records, (0, -1, now[0]+1, float('inf'), 9007199254740991)):
            session.get(notebook.NotebookNote, record['id']).created_at = stamp
    result = timeline(owner)
    assert result['total'] == result['undated_count'] == 5 and result['points'] == []


def test_retracted_proposal_still_counts_as_a_note(system, curated):
    _, owner, _, _, _ = system
    note = create(owner)
    record = submit_research(owner, note, purpose='procedure')
    before = timeline(owner)
    response = owner.post('/api/notebook/research-requests/' + record['id'] + '/retraction', {
        'packet_sha256': record['packet_sha256'], 'reason': 'Withdraw this proposal, keeping its research note.',
        'idempotency_key': str(uuid4())})
    assert response.status_code == 200, response.text
    assert timeline(owner) == before


def test_timeline_keeps_beta_browser_boundary_and_no_account_cache(notebook_access, curated):
    app, now, ids, actors = notebook_access
    owner = actors['author']
    assert timeline(owner)['total'] == 0
    with TestClient(app) as anonymous:
        assert anonymous.get(PATH).status_code == 401
        token = token_for(app, owner)
        assert anonymous.get(PATH, headers={'Authorization': 'Bearer '+token}).status_code == 403
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, ids['author'], 'revoked', now[0])
    assert owner.client.get(PATH).status_code in (401, 403)
    assert actors['reviewer'].client.get(PATH).status_code == 200
