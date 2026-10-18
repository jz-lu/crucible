"""Exact-revision request labels cannot be forged or leak private review packets."""
from dataclasses import replace
import json
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from crucible import notebook
from crucible.app import create_app
from crucible.db import User
from test_notebook import system, Actor, create, revise, content, request_publication, approve
from test_proposal_notes import scheme_content, submit as submit_scheme
from test_protocol_proposals import review_body
from test_research_reviews import submit as submit_research, decision, legacy_technical_request


def detail(actor, note):
    response = actor.client.get('/api/notebook/notes/' + note['id'])
    assert response.status_code == 200, response.text
    return response.json()['note']


def listed(actor, identifier):
    return next(note for note in actor.client.get('/api/notebook/notes').json()['notes'] if note['id'] == identifier)


def test_request_labels_ignore_forged_metadata(system):
    _, owner, _, _, _ = system
    note = create(owner, content(tags=['approved', 'scored', 'scheme_admission'], extensions={
        'requests': [{'kind': 'scheme_admission', 'status': 'approved'}],
        'research_request': {'status': 'recommend'}, 'scheme_review': {'status': 'approved'}}))
    assert detail(owner, note)['revisions'][0]['requests'] == []
    assert listed(owner, note['id'])['revisions'][0]['requests'] == []


def test_scheme_request_status_is_exact_revision_and_never_means_active(system):
    _, owner, reviewer, _, _ = system
    first = create(owner, scheme_content())
    record = submit_scheme(owner, first).json()['proposal']
    result = reviewer.post('/api/protocol/proposals/' + record['id'] + '/reviews', review_body(record))
    assert result.status_code == 200, result.text
    old = detail(owner, first)['revisions'][0]['requests']
    assert old == [{'id': record['id'], 'kind': 'scheme_admission', 'status': 'approved',
                    'url': '/#propose?proposal=' + record['id'], 'proposal_revision': 1,
                    'test_only_self_review': False, 'effective': True}]
    second = revise(owner, first, {**scheme_content(), 'body': 'A new unreviewed explanation.'})
    assert listed(owner, first['id'])['revisions'][0]['requests'] == []
    assert listed(owner, first['id'])['proposal_revision'] == {
        'revision': 1, 'content_sha256': first['content_sha256'], 'requests': old}
    next_record = submit_scheme(owner, second).json()['proposal']
    assert next_record['revision'] == 2
    revisions = detail(owner, first)['revisions']
    assert revisions[0]['requests'] == old
    assert revisions[1]['requests'][0]['status'] == 'submitted'
    assert revisions[1]['requests'][0]['proposal_revision'] == 2
    assert listed(owner, first['id'])['proposal_revision']['revision'] == 2
    assert listed(owner, first['id'])['proposal_revision']['content_sha256'] == second['content_sha256']
    assert all(item['status'] not in ('active', 'admitted', 'scored') for r in revisions for item in r['requests'])


def test_research_requests_follow_exact_revision_and_hash(system):
    app, owner, reviewer, _, _ = system
    first = create(owner)
    record = submit_research(owner, first, purpose='scoring_nomination')
    result = reviewer.post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
    assert result.status_code == 200
    expected = {'id': record['id'], 'kind': 'scoring_nomination', 'status': 'recommend',
                'url': '/#notebook?research_request=' + record['id'],
                'test_only_self_review': False, 'effective': True}
    second = revise(owner, first)
    revisions = detail(owner, first)['revisions']
    assert revisions[0]['requests'] == [expected]
    assert revisions[1]['requests'] == []
    assert listed(owner, first['id'])['revisions'][0]['revision'] == second['revision']
    assert listed(owner, first['id'])['revisions'][0]['requests'] == []
    assert listed(owner, first['id'])['proposal_revision'] == {
        'revision': 1, 'content_sha256': first['content_sha256'], 'requests': [expected]}
    with app.state.db.transaction() as session:
        user = session.get(User, owner.identifier)
        false_ref = (first['id'], 1, '0' * 64)
        assert notebook.request_summaries(session, app.state.settings, user, [false_ref]) == {false_ref: []}


def test_shared_readers_cannot_see_others_requests_and_citations_are_not_targets(system):
    _, owner, reviewer, requester, _ = system
    primary, supporting = create(owner), create(owner, content(title='Supporting lemma'))
    for note in (primary, supporting):
        request_publication(owner, note); approve(reviewer, note)
    sources = [{'note_id': note['id'], 'revision': 1, 'content_sha256': note['content_sha256']}
               for note in (primary, supporting)]
    record = submit_research(requester, primary, sources=sources, question='PRIVATE REQUEST QUESTION')
    # Authorship/sharing of a source does not make someone the packet requester.
    for read in (detail(owner, primary), listed(owner, primary['id'])):
        assert read['revisions'][0]['requests'] == []
        assert read['shared_revision']['requests'] == []
        assert record['id'] not in json.dumps(read)
    for actor in (requester, reviewer):
        for read in (detail(actor, primary), listed(actor, primary['id'])):
            assert read['revisions'][0]['requests'][0]['id'] == record['id']
            assert read['shared_revision']['requests'][0]['id'] == record['id']
            assert 'PRIVATE REQUEST QUESTION' not in json.dumps(read)
        assert detail(actor, supporting)['revisions'][0]['requests'] == []
        assert listed(actor, supporting['id'])['revisions'][0]['requests'] == []


def test_shared_revision_has_its_own_requests_when_latest_is_private(system):
    _, owner, reviewer, _, _ = system
    first = create(owner)
    request_publication(owner, first); approve(reviewer, first)
    record = submit_research(owner, first)
    revise(owner, first)
    for read in (detail(owner, first), listed(owner, first['id'])):
        assert read['revisions'][-1]['requests'] == []
        assert read['shared_revision']['revision'] == 1
        assert read['shared_revision']['requests'][0]['id'] == record['id']


def test_test_only_assessments_remain_ineffective_after_development_switch(system):
    app, _, reviewer, _, now = system
    note = create(reviewer)
    record = submit_research(reviewer, note)
    proposal_note = create(reviewer, scheme_content())
    proposal = submit_scheme(reviewer, proposal_note).json()['proposal']
    dev = create_app(replace(app.state.settings, allow_self_review=True), clock=lambda: now[0])
    try:
        actor = Actor(dev, reviewer.identifier)
        result = actor.post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
        assert result.status_code == 200, result.text
        result = actor.post('/api/protocol/proposals/' + proposal['id'] + '/reviews', review_body(proposal))
        assert result.status_code == 200, result.text
        for selected in (note, proposal_note):
            current = detail(actor, selected)['revisions'][0]['requests'][0]
            assert current['test_only_self_review'] is True and current['effective'] is True
            restored = detail(reviewer, selected)['revisions'][0]['requests'][0]
            assert restored['test_only_self_review'] is True and restored['effective'] is False
        # The older scheme panel must carry the same qualifications as the
        # newer requests panel, including after the development flag is off.
        for reader, effective in ((actor, True), (reviewer, False)):
            scheme = detail(reader, proposal_note)['revisions'][0]['scheme_review']
            assert scheme['test_only_self_review'] is True
            assert scheme['effective'] is effective
    finally:
        dev.state.db.engine.dispose()


def test_list_batches_scalar_metadata_without_packet_or_proposal_bodies(system):
    app, owner, _, _, _ = system
    for number in range(8):
        note = create(owner, content(title=f'Claim {number}', body='SECRET NOTE BODY'))
        submit_research(owner, note, question='SECRET PACKET QUESTION')
    proposal_note = create(owner, scheme_content()); submit_scheme(owner, proposal_note)
    def read(limit):
        statements = []
        def capture(_, __, statement, ___, ____, _____):
            if statement.lstrip().lower().startswith('select'):
                statements.append(statement)
        event.listen(app.state.db.engine, 'before_cursor_execute', capture)
        try:
            response = owner.client.get('/api/notebook/notes?limit=' + str(limit))
            assert response.status_code == 200
            assert 'SECRET NOTE BODY' not in response.text and 'SECRET PACKET QUESTION' not in response.text
            return statements
        finally:
            event.remove(app.state.db.engine, 'before_cursor_execute', capture)
    small, large = read(1), read(50)
    assert len(small) == len(large)
    # The first query uses compact SQL membership sets for proposal labels. The two
    # batched metadata queries remain independent of the page size.
    assert sum('FROM research_review_requests LEFT OUTER JOIN research_assessments' in sql for sql in large) == 1
    assert sum(sql.lstrip().startswith('SELECT notebook_revisions.note_id, notebook_revisions.revision, notebook_revisions.content_sha256, proposal_note_links.proposal_id') for sql in large) == 1
    for sql in large:
        projection = sql.split('FROM', 1)[0]
        assert 'research_review_requests.question' not in projection
        assert 'research_assessments.findings' not in projection
        assert 'protocol_proposal_revisions.specification' not in projection
        assert 'notebook_revisions.content AS ' not in projection
    # The same structured JSON/index expressions compile for PostgreSQL too;
    # no SQLite-specific JSON function is embedded in our query construction.
    captured = []
    def expressions(_, statement, *args):
        captured.append(statement)
    event.listen(app.state.db.engine, 'before_execute', expressions)
    try:
        owner.client.get('/api/notebook/notes')
    finally:
        event.remove(app.state.db.engine, 'before_execute', expressions)
    rendered = [str(statement.compile(dialect=postgresql.dialect())) for statement in captured]
    assert any('research_review_requests' in sql and '->>' in sql for sql in rendered)
    assert all('JSON_EXTRACT' not in sql for sql in rendered)
def test_request_guidance_is_served_as_read_only_json(system):
    _, owner, *_ = system
    response = owner.client.get('/notebook/request-types.json')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('application/json')
    guidance = response.json()
    assert guidance['format'] == 'crucible-note-request-types-v1'
    actions = {item['id']: item for item in guidance['actions']}
    assert actions['visualization_nomination']['api']['purpose'] == 'visualization_nomination'
    assert 'contribution_nomination' not in actions


def test_proposals_filter_uses_visible_history_and_privacy_before_pagination(system):
    app, owner, reviewer, other, now = system
    notes = sorted([create(owner, scheme_content()) for _ in range(7)], key=lambda note: note['id'])
    forged, technical, private_packet, old_revision, scheme, assessed, supporting = notes
    revise(owner, forged, content(tags=['proposal', 'scheme_admission'], extensions={
        'proposal_revision': {'revision': 1}, 'requests': [{'kind': 'scheme_admission'}]}))
    legacy_technical_request(app, owner, technical, now[0])
    request_publication(owner, private_packet); approve(reviewer, private_packet)
    other_request = submit_research(other, private_packet, purpose='procedure', question='PRIVATE TO OTHER')
    old_request = submit_research(owner, old_revision, purpose='visualization_nomination', sources=[
        {'note_id': note['id'], 'revision': 1, 'content_sha256': note['content_sha256']}
        for note in (old_revision, supporting)])
    newest = revise(owner, old_revision, content(title='Later draft, not yet proposed'))
    submit_scheme(owner, scheme)
    record = submit_research(owner, assessed, purpose='procedure')
    assert reviewer.post('/api/notebook/research-requests/' + record['id'] + '/assessments',
        decision(record, outcome='decline')).status_code == 200
    expected = [old_revision['id'], scheme['id'], assessed['id']]
    found, cursor = [], None
    for _ in range(4):
        response = owner.client.get('/api/notebook/notes', params={
            'proposals_only': 'true', 'limit': 1, **({'after': cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        found.extend(note['id'] for note in page['notes'])
        assert 'PRIVATE TO OTHER' not in response.text and other_request['id'] not in response.text
        cursor = page['next_after']
        if cursor is None:
            break
    assert found == expected
    old = listed(owner, old_revision['id'])
    assert old['revision'] == newest['revision'] == 2
    assert old['revisions'][0]['requests'] == []
    assert old['proposal_revision']['revision'] == 1
    assert old['proposal_revision']['content_sha256'] == old_revision['content_sha256']
    assert old['proposal_revision']['requests'][0]['id'] == old_request['id']
    assert detail(owner, old_revision)['proposal_revision'] == old['proposal_revision']
    assert listed(owner, supporting['id'])['proposal_revision'] is None
    assert listed(owner, private_packet['id'])['proposal_revision'] is None
    for actor in (other, reviewer):
        page = actor.client.get('/api/notebook/notes?proposals_only=true&limit=1').json()
        assert [note['id'] for note in page['notes']] == [private_packet['id']]
        assert page['notes'][0]['proposal_revision']['requests'][0]['id'] == other_request['id']


def test_private_later_proposal_does_not_label_shared_earlier_revision(system):
    _, owner, reviewer, reader, _ = system
    first = create(owner, scheme_content())
    request_publication(owner, first); approve(reviewer, first)
    second = revise(owner, first, scheme_content())
    record = submit_scheme(owner, second).json()['proposal']
    assert listed(owner, first['id'])['proposal_revision']['revision'] == 2
    for actor in (reader, reviewer):
        visible = listed(actor, first['id'])
        assert visible['revision'] == 1 and visible['proposal_revision'] is None
        assert record['id'] not in json.dumps(visible)
        assert actor.client.get('/api/notebook/notes?proposals_only=true').json()['notes'] == []


def curated_ref():
    collection = json.loads((Path(__file__).resolve().parents[1] / 'web/map/notes.json').read_text())
    source = next(note for note in collection['notes'] if note['id'] == 'literature-high-corruption-csp')
    revision = source['revisions'][0]
    value = {key: item for key, item in revision.items() if key in notebook.CONTENT_FIELDS}
    return {'id': 'curated:' + source['id'], 'revision': revision['revision'], 'content_sha256': notebook._hash(value)}


def test_curated_proposal_index_is_compact_private_paginated_and_includes_assessed(system):
    app, owner, reviewer, other, now = system
    source = curated_ref()
    first = submit_research(owner, source, purpose='visualization_nomination', question='PRIVATE PACKET CONTENT')
    assert reviewer.post('/api/notebook/research-requests/' + first['id'] + '/assessments', decision(first)).status_code == 200
    second = submit_research(owner, source, purpose='procedure')
    foreign = submit_research(other, source, purpose='map_nomination')
    excluded = [legacy_technical_request(app, owner, source, now[0]),
        submit_research(owner, source, purpose='source_check')]
    local = create(owner)
    excluded.append(submit_research(owner, local, purpose='scoring_nomination', sources=[
        {'note_id': local['id'], 'revision': 1, 'content_sha256': local['content_sha256']},
        {'note_id': source['id'], 'revision': source['revision'], 'content_sha256': source['content_sha256']}]))
    statements = []
    def capture(_, __, sql, ___, ____, _____):
        if sql.lstrip().startswith('SELECT') and 'research_review_requests' in sql:
            statements.append(sql)
    event.listen(app.state.db.engine, 'before_cursor_execute', capture)
    try:
        page = owner.client.get('/api/notebook/research-requests?curated_proposals=true&limit=1').json()
        assert len(page['requests']) == 1 and page['next_after']
        page2 = owner.client.get('/api/notebook/research-requests', params={
            'curated_proposals': 'true', 'limit': 1, 'after': page['next_after']}).json()
        assert len(page2['requests']) == 1 and page2['next_after'] is None
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', capture)
    records = page['requests'] + page2['requests']
    assert [r['id'] for r in records] == sorted([first['id'], second['id']])
    for record in records:
        assert set(record) == {'id', 'purpose', 'primary_source', 'assessment'}
        assert record['primary_source'] == {'note_id': source['id'], 'revision': source['revision'], 'content_sha256': source['content_sha256']}
    assert next(r for r in records if r['id'] == first['id'])['assessment'] == {
        'outcome': 'recommend', 'test_only_self_review': False, 'effective': True}
    assert 'PRIVATE PACKET CONTENT' not in json.dumps(records)
    assert len(statements) == 2
    for sql in statements:
        projection = sql.split('FROM', 1)[0]
        assert 'research_review_requests.question' not in projection
        assert ', research_review_requests.sources' not in projection
        assert 'research_assessments.findings' not in projection
    reviewed = reviewer.client.get('/api/notebook/research-requests?curated_proposals=true&limit=100').json()['requests']
    assert {r['id'] for r in reviewed} == {first['id'], second['id'], foreign['id']}
    own = other.client.get('/api/notebook/research-requests?curated_proposals=true').json()['requests']
    assert [r['id'] for r in own] == [foreign['id']]
    for suffix in ('&review=true', '&source_note_id=x&source_revision=1&source_sha256=' + 'a'*64):
        assert owner.client.get('/api/notebook/research-requests?curated_proposals=true' + suffix).status_code == 422
