"""Author withdrawal is explicit, exact, private and independent of review truth."""
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import notebook, proposal_retractions as withdrawals, protocol_proposals, registry
from crucible.db import User
from crucible.machine_auth import MachineToken, SCOPES
from crucible.protocol_admission import check_admission
from crucible.scoring_awards import AdmissionCreditError, SchemeAdmissionCredit, award_scheme_credit
from crucible.security import token_hash
from test_notebook import system, Actor, create, revise, content, publication_body, request_publication, approve, ORIGIN
from test_note_requests import detail, listed
from test_proposal_notes import scheme_content, submit as submit_scheme, counts
from test_protocol_proposals import review_body, revision_body
from test_research_reviews import submit as submit_research, decision, request_body, legacy_technical_request
from test_scoring_awards import admission, CURRENT_RSA


def scheme_body(record, **changes):
    return {'expected_revision': record['revision'], 'content_sha256': record['content_sha256'],
            'reason': 'The author found an unsupported premise and withdraws this proposal.',
            'idempotency_key': str(uuid4()), **changes}


def research_body(record, **changes):
    return {'packet_sha256': record['packet_sha256'], 'reason': 'This nomination is premature.',
            'idempotency_key': str(uuid4()), **changes}


def source_ref(note):
    return {'note_id': note['id'], 'revision': note['revision'], 'content_sha256': note['content_sha256']}


@pytest.mark.parametrize('kind', ['scheme', 'research'])
def test_only_author_browser_can_withdraw_exact_current_record(system, kind):
    app, owner, reviewer, reader, now = system
    note = create(owner, scheme_content() if kind == 'scheme' else content())
    record = submit_scheme(owner, note).json()['proposal'] if kind == 'scheme' else submit_research(owner, note, purpose='visualization_nomination')
    prefix = '/api/protocol/proposals/' if kind == 'scheme' else '/api/notebook/research-requests/'
    endpoint = prefix + record['id'] + '/retraction'
    body = scheme_body(record) if kind == 'scheme' else research_body(record)
    for actor in (reviewer, reader):
        assert actor.post(endpoint, body).status_code == 404
    assert owner.client.post(endpoint, json=body).status_code == 403
    assert TestClient(app).post(endpoint, json=body, headers={'Origin': ORIGIN}).status_code == 401
    raw = 'cru_' + 'r' * 43
    with app.state.db.transaction(write=True) as session:
        session.add(MachineToken(id=str(uuid4()), user_id=owner.identifier, token_hash=token_hash(raw),
            label='All existing scopes are insufficient', scopes=list(SCOPES), created_at=now[0], expires_at=now[0]+1000))
    assert owner.client.post(endpoint, json=body, headers={'Origin': ORIGIN, 'X-CSRF-Token': owner.csrf,
        'Authorization': 'Bearer ' + raw}).status_code == 403
    field = 'content_sha256' if kind == 'scheme' else 'packet_sha256'
    assert owner.post(endpoint, {**body, field: '0'*64}).status_code == 409
    assert owner.post(endpoint, {**body, 'reason': '   '}).status_code == 422
    if kind == 'scheme':
        assert owner.post(endpoint, {**body, 'expected_revision': 2}).status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(withdrawals.ProposalRetraction)) == 0
    response = owner.post(endpoint, body)
    assert response.status_code == 200, response.text
    result = response.json()['proposal' if kind == 'scheme' else 'request']
    assert result['retracted'] and not result['can_retract']
    assert result['retraction']['content_sha256'] == record[field]
    assert owner.post(endpoint, body).json() == response.json()
    assert owner.post(endpoint, {**body, 'reason': 'A different reason'}).status_code == 409
    assert owner.post(endpoint, {**body, 'idempotency_key': str(uuid4())}).status_code == 409


def test_scheme_withdrawal_preserves_history_and_prevents_revival(system):
    app, owner, reviewer, reader, _ = system
    first = create(owner, scheme_content()); original_submit = publication_body(first)
    original = submit_scheme(owner, first, original_submit).json()['proposal']
    approved_body = review_body(original)
    approval = reviewer.post('/api/protocol/proposals/' + original['id'] + '/reviews', approved_body)
    assert approval.status_code == 200, approval.text
    second = revise(owner, first, {**scheme_content(), 'body': 'A new explanation that still needs review.'})
    current = submit_scheme(owner, second).json()['proposal']
    frozen_counts = counts(app); catalogue_before = tuple(registry.CURRENT.items())
    path = '/api/protocol/proposals/' + current['id']
    stale = owner.post(path + '/retraction', scheme_body(original))
    assert stale.status_code == 409
    result = owner.post(path + '/retraction', scheme_body(current))
    assert result.status_code == 200, result.text
    assert result.json()['proposal']['status'] == 'submitted'
    assert reviewer.client.get('/api/protocol/review-queue').json()['proposals'] == []
    assert reviewer.post(path + '/reviews', review_body(current)).status_code == 409
    assert owner.post(path + '/revisions', revision_body(current)).status_code == 409
    replay = submit_scheme(owner, first, original_submit).json()['proposal']
    assert replay['retracted'] and replay['revision'] == 1 and replay['status'] == 'approved'
    review_replay = reviewer.post(path + '/reviews', approved_body)
    assert review_replay.status_code == 200 and review_replay.json()['proposal']['retracted']
    assert review_replay.json()['review'] == approval.json()['review']
    assert counts(app) == frozen_counts and tuple(registry.CURRENT.items()) == catalogue_before
    third = revise(owner, second, {**scheme_content(), 'body': 'Further research is still allowed.'})
    assert submit_scheme(owner, third).status_code == 409
    for revision in detail(owner, first)['revisions'][:2]:
        assert revision['requests'][0]['retracted'] is True
        assert revision['scheme_review']['retracted'] is True
    summary = listed(owner, first['id'])
    assert summary['proposal_revision']['revision'] == 2
    assert summary['proposal_revision']['requests'][0]['retracted'] is True
    assert summary['revisions'][0]['requests'] == []  # A new note is not an implicit proposal.
    fresh = submit_scheme(owner, create(owner, scheme_content()))
    assert fresh.status_code == 200 and fresh.json()['proposal']['id'] != current['id']
    with app.state.db.transaction() as session:
        with pytest.raises(ValueError, match='retracted'):
            check_admission(session, current['id'], 'rsa-kem', CURRENT_RSA.SCHEME_VERSION)


def test_research_withdrawal_preserves_assessment_and_retries_without_publishing(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner); body = request_body(note, purpose='visualization_nomination')
    record = owner.post('/api/notebook/research-requests', body).json()['request']
    path = '/api/notebook/research-requests/' + record['id']
    reviewed_body = decision(record)
    assessment = reviewer.post(path + '/assessments', reviewed_body).json()['request']['assessment']
    response = owner.post(path + '/retraction', research_body(record))
    assert response.status_code == 200, response.text
    result = response.json()['request']
    assert result['assessment'] == assessment and not result['can_review']
    assert reviewer.post(path + '/assessments', decision(record)).status_code == 409
    assert reviewer.post(path + '/assessments', reviewed_body).json()['request']['retracted']
    assert owner.post('/api/notebook/research-requests', body).json()['request']['retracted']
    assert detail(owner, note)['revisions'][0]['publication_status'] == 'private'
    assert detail(owner, note)['revisions'][0]['requests'][0]['retracted']
    assert reader.client.get(path).status_code == 404
    pending = submit_research(owner, note, purpose='procedure')
    assert owner.post('/api/notebook/research-requests/' + pending['id'] + '/retraction', research_body(pending)).status_code == 200
    assert reviewer.client.get('/api/notebook/research-requests?review=true').json()['requests'] == []
    for purpose in ('technical_review', 'source_check'):
        informational = (legacy_technical_request(app, owner, note, app.state.clock()) if purpose == 'technical_review'
                         else submit_research(owner, note, purpose=purpose))
        assert informational['can_retract'] is False
        assert owner.post('/api/notebook/research-requests/' + informational['id'] + '/retraction', research_body(informational)).status_code == 422
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionCredit)) == 0


@pytest.mark.parametrize('kind', ['scheme', 'research'])
def test_supporting_note_access_and_compact_summary_privacy(system, kind):
    _, owner, reviewer, reader, _ = system
    support = create(owner, content(title='PRIVATE_SUPPORT_TITLE', body='PRIVATE_SUPPORT_BODY'))
    unavailable = create(reader)
    note = create(owner, scheme_content() if kind == 'scheme' else content())
    request_publication(owner, note); approve(reviewer, note)
    record = submit_scheme(owner, note).json()['proposal'] if kind == 'scheme' else submit_research(owner, note, purpose='map_nomination')
    prefix = '/api/protocol/proposals/' if kind == 'scheme' else '/api/notebook/research-requests/'
    endpoint = prefix + record['id']
    body = scheme_body(record) if kind == 'scheme' else research_body(record)
    assert owner.post(endpoint + '/retraction', {**body, 'supporting_note': source_ref(unavailable)}).status_code == 404
    body = {**body, 'reason': 'PRIVATE_RETRACTION_REASON', 'supporting_note': source_ref(support)}
    assert owner.post(endpoint + '/retraction', body).status_code == 200
    key = 'proposal' if kind == 'scheme' else 'request'
    assert owner.client.get(endpoint).json()[key]['retraction']['supporting_note'] == source_ref(support)
    reviewer_view = reviewer.client.get(endpoint).json()[key]
    assert reviewer_view['retraction']['reason'] == body['reason']
    assert reviewer_view['retraction']['supporting_note'] is None
    for actor in (owner, reviewer, reader):
        rendered = json.dumps(detail(actor, note)) + json.dumps(listed(actor, note['id']))
        assert support['id'] not in rendered and support['content_sha256'] not in rendered
        assert body['reason'] not in rendered and 'PRIVATE_SUPPORT' not in rendered
    assert detail(reader, note)['revisions'][0]['requests'] == []
    request_publication(owner, support); approve(reviewer, support)
    assert reviewer.client.get(endpoint).json()[key]['retraction']['supporting_note'] == source_ref(support)


def test_curated_proposal_metadata_retains_private_withdrawal_flags(system):
    _, owner, reviewer, reader, _ = system
    source = notebook.curated_collection()[0]
    ref = source['revisions'][0]
    note = {'id': source['id'], 'revision': ref['revision'], 'content_sha256': ref['content_sha256']}
    record = submit_research(owner, note, purpose='visualization_nomination')
    body = research_body(record, reason='PRIVATE_CURATED_REASON')
    assert owner.post('/api/notebook/research-requests/' + record['id'] + '/retraction', body).status_code == 200
    for actor in (owner, reviewer):
        page = actor.client.get('/api/notebook/notes?include_curated=true&scope=literature&proposals_only=true').json()
        entry = next(n for n in page['notes'] if n['id'] == note['id'])
        assert entry['proposal_revision']['requests'][0]['retracted'] is True
        index = actor.client.get('/api/notebook/research-requests?curated_proposals=true').json()
        assert index['requests'][0]['retracted'] is True
        assert body['reason'] not in json.dumps(page) + json.dumps(index)
    assert reader.client.get('/api/notebook/notes?include_curated=true&scope=literature&proposals_only=true').json()['notes'] == []


def test_concurrent_withdrawal_retries_make_one_terminal_record(system):
    app, owner, *_ = system
    record = submit_research(owner, create(owner), purpose='procedure')
    path = '/api/notebook/research-requests/' + record['id'] + '/retraction'
    body = research_body(record)
    actors = [Actor(app, owner.identifier), Actor(app, owner.identifier)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda actor: actor.post(path, body), actors))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(withdrawals.ProposalRetraction)) == 1


@pytest.mark.parametrize('already_awarded', [False, True])
def test_withdrawal_blocks_new_credit_but_does_not_erase_existing_award(admission, already_awarded):
    db, settings, args, author = admission
    prior = award_scheme_credit(db, settings, **args) if already_awarded else None
    with db.transaction(write=True) as session:
        target = session.get(protocol_proposals.ProtocolProposal, args['proposal_id'])
        user = session.get(User, author)
        body = withdrawals.SchemeRetractionInput.model_validate(scheme_body({'revision': target.revision, 'content_sha256': target.content_sha256}))
        withdrawals.withdraw(session, withdrawals.SCHEME, target, user, 12, body,
            revision=target.revision, digest=target.content_sha256)
    if already_awarded:
        retry = award_scheme_credit(db, settings, **args)
        assert retry['created'] is False and retry['credit'] == prior['credit']
    else:
        with pytest.raises(AdmissionCreditError, match='retracted'):
            award_scheme_credit(db, settings, **args)
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionCredit)) == int(already_awarded)
