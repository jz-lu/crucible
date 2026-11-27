"""Exact decisions, private evidence, durable retries and bounded follow-through."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import notebook, proposal_implementation as progress, protocol_proposals as schemes, registry, research_reviews
from crucible.proposal_notes import link_revision
from test_notebook import system, create, content, revise, request_publication, approve
from test_research_reviews import submit, decision


def recommended(owner, reviewer, *, purpose="procedure"):
    note = create(owner, content(title="Synthetic implementation evidence"))
    record = submit(owner, note, purpose=purpose)
    result = reviewer.post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
    assert result.status_code == 200, result.text
    return note, result.json()['request']


def approved_scheme(app, owner, reviewer):
    from test_protocol_proposals import specification
    note = create(owner, content(title="Synthetic scheme source"))
    spec = specification()
    spec["name"] = "Synthetic scheme: lifecycle demonstration"
    digest = notebook._hash(spec)
    identifier, review_id = str(uuid4()), str(uuid4())
    with app.state.db.transaction(write=True) as session:
        record = schemes.ProtocolProposal(id=identifier, author_id=owner.identifier, created_at=1,
            status="approved", revision=1, content_sha256=digest, specification=spec, idempotency_key=str(uuid4()))
        session.add(record); session.flush()
        revision = schemes.ProtocolProposalRevision(id=str(uuid4()), proposal_id=identifier, revision=1,
            created_at=1, idempotency_key=str(uuid4()), request_sha256=digest, content_sha256=digest, specification=spec)
        session.add(revision); session.flush()
        session.add(schemes.ProtocolProposalReview(id=review_id, proposal_id=identifier, revision=1,
            reviewer_id=reviewer.identifier, created_at=2, decision='approved', content_sha256=digest,
            implementation_sha256=None, checks={}, notes='Synthetic approval fixture, not admission evidence.',
            evidence='Synthetic lifecycle evidence only.', idempotency_key=str(uuid4()), request_sha256=digest))
        saved = session.get(notebook.NotebookNote, note['id'])
        saved_revision = session.scalar(select(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == saved.id))
        link_revision(session, record, revision, saved, saved_revision, 2, origin='notebook')
    return identifier, review_id, note


def endpoint(kind, identifier):
    return f'/api/proposal-implementations/{kind}/{identifier}'


def read(actor, kind, identifier, **params):
    response = actor.client.get(endpoint(kind, identifier), params=params)
    assert response.status_code == 200, response.text
    return response.json()['implementation']


def body(state, **changes):
    return {'decision_id':state['decision_id'], 'expected_event_id':state['head_event_id'],
            'status':'in_progress', 'summary':'Implementing the accepted change in a test branch.',
            'evidence':[], 'idempotency_key':str(uuid4()), **changes}


def post(actor, state, **changes):
    response = actor.post(endpoint(state['kind'], state['target_id']) + '/events', body(state, **changes))
    assert response.status_code == 200, response.text
    return response.json()['implementation']


def evidence(note):
    return [{'label':'Exact supporting note', 'url':f'/#notebook?note={note["id"]}&revision={note["revision"]}&sha256={note["content_sha256"]}'}]


def test_research_lifecycle_retains_decision_and_does_not_change_science(system):
    app, owner, reviewer, _, _ = system
    note, record = recommended(owner, reviewer)
    before_registry = deepcopy(registry.CURRENT)
    before = owner.client.get('/api/notebook/research-requests/' + record['id']).json()['request']
    state = read(reviewer, 'research', record['id'])
    assert state['status'] == 'awaiting_implementation' and state['can_update']
    assert not read(owner, 'research', record['id'])['can_update']
    state = post(reviewer, state)
    state = post(reviewer, state, status='completed', summary='The accepted process is documented and validated.', evidence=evidence(note))
    state = post(reviewer, state, summary='Reopened: a supported scenario needs another check.')
    assert [event['status'] for event in state['events']] == ['in_progress', 'completed', 'in_progress']
    after = owner.client.get('/api/notebook/research-requests/' + record['id']).json()['request']
    assert after['assessment'] == before['assessment'] and after['sources'] == before['sources']
    assert after['implementation']['status'] == 'in_progress'
    assert registry.CURRENT == before_registry
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication)) == 0
        assert session.scalar(select(func.count()).select_from(progress.ImplementationEvent)) == 3
    annotated = owner.client.get('/api/notebook/notes/' + note['id']).json()['note']['revisions'][0]['requests']
    assert annotated[0]['implementation']['status'] == 'in_progress'


def test_scheme_approval_and_lifecycle_are_separate_and_history_is_exact(system):
    app, owner, reviewer, _, _ = system
    identifier, decision_id, note = approved_scheme(app, owner, reviewer)
    state = read(reviewer, 'scheme', identifier)
    assert state['decision_id'] == decision_id and state['source_revision'] == 1
    state = post(reviewer, state)
    assert reviewer.client.get('/api/protocol/proposals/' + identifier).json()['proposal']['implementation']['status'] == 'in_progress'
    assert owner.client.get('/api/notebook/notes/' + note['id']).json()['note']['revisions'][0]['requests'][0]['implementation']['status'] == 'in_progress'
    with app.state.db.transaction(write=True) as session:
        record = session.get(schemes.ProtocolProposal, identifier)
        record.revision = 2; record.status = 'submitted'; record.content_sha256 = 'b' * 64
        record.specification = {**record.specification, 'name':'Renamed replacement proposal'}
        session.add(schemes.ProtocolProposalRevision(id=str(uuid4()), proposal_id=identifier, revision=2,
            created_at=3, idempotency_key=str(uuid4()), request_sha256='b'*64, content_sha256='b'*64, specification=record.specification))
    current = read(reviewer, 'scheme', identifier)
    assert current['status'] == 'awaiting_review' and not current['eligible'] and current['events'] == []
    historical = read(owner, 'scheme', identifier, decision_id=decision_id)
    assert historical['superseded'] and historical['status'] == 'in_progress' and not historical['can_update']
    assert historical['title'] == state['title'] and historical['title'] != current['title']
    assert historical['history_url'].endswith('?decision_id=' + decision_id)
    assert reviewer.post(endpoint('scheme', identifier) + '/events', body(state, status='completed', evidence=evidence(note))).status_code == 409


def test_owner_outsider_bearer_and_missing_csrf_cannot_mutate(system):
    _, owner, reviewer, reader, _ = system
    _, record = recommended(owner, reviewer)
    state = read(owner, 'research', record['id']); url = endpoint('research', record['id'])
    assert owner.post(url + '/events', body(state)).status_code == 403
    assert reader.client.get(url).status_code == 404
    assert reader.client.get('/api/proposal-implementations').status_code == 403
    assert reader.client.get('/api/proposal-implementations?scope=mine').json()['implementations'] == []
    assert reviewer.client.get(url, headers={'Authorization':'Bearer synthetic'}).status_code == 401
    assert reviewer.client.post(url + '/events', json=body(state)).status_code == 403


def test_review_required_and_source_check_is_not_implementation(system):
    _, owner, reviewer, _, _ = system
    pending = submit(owner, create(owner), purpose='procedure')
    state = read(reviewer, 'research', pending['id'])
    assert state['status'] == 'awaiting_review' and not state['eligible']
    _, check = recommended(owner, reviewer, purpose='source_check')
    state = read(reviewer, 'research', check['id'])
    assert not state['eligible'] and not state['can_update']
    assert reviewer.post(endpoint('research', check['id']) + '/events', body(state)).status_code == 409
    assert reviewer.client.get('/api/proposal-implementations').json()['implementations'] == []


def test_progress_requires_real_transition_and_completion_evidence(system):
    _, owner, reviewer, _, _ = system
    _, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id']); url = endpoint('research', record['id']) + '/events'
    assert reviewer.post(url, body(state, status='completed', evidence=[{'label':'PR','url':'https://example.org/pull/1'}])).status_code == 422
    state = post(reviewer, state)
    assert reviewer.post(url, body(state)).status_code == 409
    assert reviewer.post(url, body(state, status='completed')).status_code == 422
    assert reviewer.post(url, body(state, status='completed', summary='  ')).status_code == 422
    state = post(reviewer, state, status='completed', evidence=[{'label':'Reviewed change','url':'https://example.org/commit/123'}])
    assert state['status'] == 'completed'


def test_in_progress_accepts_further_work_without_a_false_completion(system):
    app, owner, reviewer, _, _ = system
    note, record = recommended(owner, reviewer)
    initial = post(reviewer, read(reviewer, 'research', record['id']))
    url = endpoint('research', record['id']) + '/events'
    payload = body(initial, summary='The implementation is complete; compatibility checks are underway.')
    result = reviewer.post(url, payload)
    assert result.status_code == 200, result.text
    updated = result.json()['implementation']
    assert updated['status'] == 'in_progress'
    assert [event['status'] for event in updated['events']] == ['in_progress', 'in_progress']
    assert updated['events'][0] == initial['events'][0]
    assert reviewer.post(url, payload).json()['event'] == result.json()['event']
    assert reviewer.post(url, body(initial, summary='A conflicting stale update')).status_code == 409
    assert reviewer.post(url, body(updated, summary=payload['summary'])).status_code == 409
    with_evidence = post(reviewer, updated, summary=payload['summary'], evidence=evidence(note))
    assert len(with_evidence['events']) == 3
    completed = post(reviewer, with_evidence, status='completed', summary='Compatibility checks passed.', evidence=evidence(note))
    response = reviewer.post(url, body(completed, status='completed', summary='Another completion', evidence=evidence(note)))
    assert response.status_code == 409 and 'Reopen' in response.json()['detail']
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(progress.ImplementationEvent)) == 4


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'http://example.org', 'https://secret@example.org/x',
    'https://example.org:444/x', 'https://example.org\\@evil.test', '/#notebook?note=a',
    '/#notebook?note=a&revision=1&sha256=' + 'a'*64 + '&note=b'])
def test_unsafe_or_ambiguous_evidence_is_rejected(system, url):
    _, owner, reviewer, _, _ = system
    _, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id'])
    assert reviewer.post(endpoint('research', record['id']) + '/events', body(state, evidence=[{'label':'evidence','url':url}])).status_code == 422


def test_exact_evidence_cannot_read_unsubmitted_private_notes(system):
    _, owner, reviewer, reader, _ = system
    note, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id']); url = endpoint('research', record['id']) + '/events'
    unrelated = create(reader, content(title='Private bystander note'))
    assert reviewer.post(url, body(state, evidence=evidence(unrelated))).status_code == 404
    owner_private = create(owner)
    assert reviewer.post(url, body(state, evidence=evidence(owner_private))).status_code == 422
    revised = revise(owner, note)
    assert reviewer.post(url, body(state, evidence=evidence(revised))).status_code == 422
    assert post(reviewer, state, evidence=evidence(note))['status'] == 'in_progress'


def test_exact_retry_identifies_original_event_after_other_updates(system):
    app, owner, reviewer, _, _ = system
    note, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id']); url = endpoint('research', record['id']) + '/events'
    payload = body(state)
    first = reviewer.post(url, payload).json()
    complete = post(reviewer, first['implementation'], status='completed', evidence=evidence(note))
    replay = reviewer.post(url, payload)
    assert replay.status_code == 200
    assert replay.json()['event'] == first['event']
    assert replay.json()['implementation']['head_event_id'] == complete['head_event_id']
    assert reviewer.post(url, {**payload, 'summary':'Different request'}).status_code == 409
    assert reviewer.post(url, body(state)).status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(progress.ImplementationEvent)) == 2


def test_concurrent_updates_have_one_winner(system):
    _, owner, reviewer, _, _ = system
    _, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id']); url = endpoint('research', record['id']) + '/events'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reviewer.post(url, body(state)).status_code, range(2)))
    assert sorted(results) == [200, 409]


def test_retracted_target_blocks_new_updates_preserves_history_and_safe_retry(system):
    _, owner, reviewer, _, _ = system
    _, record = recommended(owner, reviewer)
    state = read(reviewer, 'research', record['id']); url = endpoint('research', record['id']) + '/events'
    payload = body(state); started = reviewer.post(url, payload).json()['implementation']
    result = owner.post('/api/notebook/research-requests/' + record['id'] + '/retraction',
        {'packet_sha256':record['packet_sha256'], 'reason':'Withdraw this proposal.', 'idempotency_key':str(uuid4())})
    assert result.status_code == 200, result.text
    state = read(reviewer, 'research', record['id'])
    assert state['status'] == 'retracted' and len(state['events']) == 1 and not state['can_update']
    assert reviewer.post(url, body(started, status='completed', evidence=[{'label':'PR','url':'https://example.org/1'}])).status_code == 409
    assert reviewer.post(url, payload).status_code == 200
    assert reviewer.client.get('/api/proposal-implementations').json()['implementations'] == []


def test_queue_is_bounded_private_and_keeps_completed_work_discoverable(system):
    app, owner, reviewer, reader, _ = system
    identifier, _, _ = approved_scheme(app, owner, reviewer)
    note, record = recommended(owner, reviewer)
    state = post(reviewer, read(reviewer, 'research', record['id']))
    post(reviewer, state, status='completed', evidence=evidence(note))
    first = reviewer.client.get('/api/proposal-implementations?limit=1').json()
    second = reviewer.client.get('/api/proposal-implementations', params={'limit':1,'after':first['next_after']}).json()
    assert {first['implementations'][0]['target_id'], second['implementations'][0]['target_id']} == {identifier, record['id']}
    assert second['next_after'] is None
    assert 'sources' not in str(first) and 'events' not in first['implementations'][0]
    assert owner.client.get('/api/proposal-implementations?scope=mine').json()['implementations']
    assert reader.client.get('/api/proposal-implementations?scope=mine').json()['implementations'] == []
    for cursor in ('garbage', 'WzEsIm5vdGtpbmQiLCJpZCJd'):
        assert reviewer.client.get('/api/proposal-implementations', params={'after':cursor}).status_code == 422


def test_self_review_does_not_enable_implementation(system):
    app, owner, reviewer, _, _ = system
    identifier, review_id, _ = approved_scheme(app, owner, reviewer)
    with app.state.db.transaction(write=True) as session:
        review = session.get(schemes.ProtocolProposalReview, review_id)
        review.checks = {'test_only_self_review':True}
    state = read(reviewer, 'scheme', identifier)
    assert not state['eligible'] and not state['can_update']
    assert reviewer.post(endpoint('scheme', identifier) + '/events', body(state)).status_code == 409


def test_canonical_note_location_urls_remain_supported():
    base = '/#notebook?note=abc&revision=1&sha256=' + 'a'*64
    for suffix in ('', '&location=L2', '&note_section=summary', '&statement=claim-a', '&argument=proof-a'):
        assert progress.evidence_url(base + suffix) == base + suffix
    with pytest.raises(ValueError):
        progress.evidence_url(base + '&statement=claim-a&argument=proof-a')


@pytest.mark.parametrize('invalid', ['A\x00B', 'A\ud800B'])
def test_invalid_database_text_is_rejected_before_mutation(invalid):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        progress.Evidence(label=invalid, url='https://example.org/evidence')
    with pytest.raises(ValidationError):
        progress.EventInput(decision_id=str(uuid4()), expected_event_id=None, status='in_progress',
            summary=invalid, idempotency_key=str(uuid4()))
    with pytest.raises(ValueError):
        progress.evidence_url('https://example.org/' + invalid)


def test_shared_note_never_exposes_its_private_proposal_progress(system):
    _, owner, reviewer, reader, _ = system
    note, record = recommended(owner, reviewer)
    post(reviewer, read(reviewer, 'research', record['id']))
    request_publication(owner, note); approve(reviewer, note)
    visible = reader.client.get('/api/notebook/notes/' + note['id']).json()['note']
    assert visible['revisions'][0]['requests'] == []
    assert reader.client.get(endpoint('research', record['id'])).status_code == 404


def test_withdrawn_other_author_source_hides_private_packet_from_own_queue(system):
    _, owner, reviewer, reader, _ = system
    note = create(reader)
    request_publication(reader, note); approve(reviewer, note)
    record = submit(owner, note, purpose='procedure')
    result = reviewer.post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
    assert result.status_code == 200
    assert owner.client.get('/api/proposal-implementations?scope=mine').json()['implementations']
    response = reviewer.post('/api/notebook/notes/' + note['id'] + '/publication-withdrawals',
        {'revision':note['revision'], 'content_sha256':note['content_sha256'],
         'reason':'Synthetic privacy withdrawal', 'idempotency_key':str(uuid4())})
    assert response.status_code == 200, response.text
    assert owner.client.get('/api/proposal-implementations?scope=mine').json()['implementations'] == []
    assert owner.client.get(endpoint('research', record['id'])).status_code == 404
    assert read(reviewer, 'research', record['id'])['eligible']
