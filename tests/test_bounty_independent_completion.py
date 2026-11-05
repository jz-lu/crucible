"""Reviewer-only completion preserves exact evidence, fairness and atomic awards."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import beta_access
from crucible.bounty_models import BountyAllowanceEvent, BountyAward, BountyDecision, BountySubmission, BountyTask
from test_bounties import (system, assess, available, body, boost, create, decision,
                          opened, post, published, request, submit)


def decision_path(value, submission_id=None):
    return '/api/bounties/' + value['bounty']['id'] + '/submissions/' + (submission_id or value['receipt']['submission_id']) + '/decisions'


def test_independent_review_completes_without_poster_and_replay_has_one_award(system):
    app, owner, reviewer, solver, *_ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    path = decision_path(value)
    payload = body(value, kind='pipeline', decision='approved', reason='Independent exact-evidence evaluation.')
    result = post(reviewer, path, payload)
    assert result['bounty']['status'] == 'completed'
    assert result['bounty']['award']['user_id'] == solver.identifier
    assert result['bounty']['award']['points'] == 100
    assert post(reviewer, path, payload)['receipt'] == result['receipt']
    with app.state.db.transaction() as session:
        award = session.get(BountyAward, value['bounty']['id'])
        assert award.poster_decision_id is None
        assert session.get(BountyDecision, award.pipeline_decision_id).actor_id == reviewer.identifier
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 1
        assert session.scalar(select(func.count()).select_from(BountyDecision).where(BountyDecision.kind == 'poster')) == 0


def test_poster_mutation_is_retired_and_only_independent_review_controls_are_offered(system):
    app, owner, reviewer, solver, *_ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    task_path = '/api/bounties/' + value['bounty']['id']
    for actor in (owner, solver, reviewer):
        capabilities = actor.client.get(task_path).json()['bounty']['submissions'][0]['capabilities']
        assert capabilities['accept'] is False and capabilities['reject'] is False
        assert capabilities['qualify'] == (actor is reviewer)
    for choice in ('approved', 'declined'):
        post(owner, decision_path(value), body(value, kind='poster', decision=choice, reason='Legacy client request.'), 410)
    with app.state.db.transaction() as session:
        assert session.get(BountyTask, value['bounty']['id']).version == value['bounty']['version']
        assert session.scalar(select(func.count()).select_from(BountyDecision).where(BountyDecision.kind == 'poster')) == 0


@pytest.mark.parametrize('role', ['poster', 'solver'])
def test_even_configured_reviewers_cannot_review_their_own_task_or_solution(system, role):
    _, owner, reviewer, solver, _, reviewer2, _ = system
    if role == 'poster':
        value = decision(reviewer2, request(reviewer, create(reviewer)))
        value = submit(solver, value, published(solver, reviewer2))
        disallowed = reviewer
    else:
        value = submit(reviewer2, opened(system), published(reviewer2, reviewer))
        disallowed = reviewer2
    post(disallowed, decision_path(value), body(value, kind='pipeline', decision='approved', reason='Self review.'), 403)


def test_later_qualified_claim_waits_and_settlement_refunds_only_late_boosts(system):
    app, owner, reviewer, solver, supporter, funder, _ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    first = value['receipt']['submission_id']
    value = boost(funder, value, 3)
    value = submit(supporter, value, published(supporter, reviewer))
    second = value['receipt']['submission_id']
    value = boost(funder, value)
    value = assess(reviewer, value, 'pipeline', submission_id=second)
    assert value['bounty']['status'] == 'frozen' and value['bounty']['award'] is None
    assert value['bounty']['reward_points'] == 400 and available(funder) == 6
    value = assess(reviewer, value, 'pipeline', 'declined', submission_id=first)
    assert value['bounty']['status'] == 'completed'
    assert value['bounty']['award']['submission_id'] == second
    assert value['bounty']['award']['points'] == 400 and available(funder) == 7
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyAllowanceEvent).where(BountyAllowanceEvent.kind == 'refund')) == 1


def test_single_reviewer_award_cannot_convert_winners_own_funding_to_points(system):
    _, _, reviewer, solver, supporter, *_ = system
    value = boost(solver, opened(system), 2)
    value = submit(solver, value, published(solver, reviewer))
    claim = value['receipt']['submission_id']
    value = boost(supporter, value)
    result = assess(reviewer, value, 'pipeline', submission_id=claim)
    assert result['bounty']['award']['points'] == 100
    assert available(solver) == 10 and available(supporter) == 10


@pytest.mark.parametrize('revoked_role', ['poster', 'solver'])
def test_inactive_participant_prevents_award_and_rolls_back_review(system, revoked_role):
    app, owner, reviewer, solver, *_ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    revoked = owner if revoked_role == 'poster' else solver
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, revoked.identifier, 'revoked', app.state.clock())
    post(reviewer, decision_path(value), body(value, kind='pipeline', decision='approved', reason='Exact review.'), 409)
    with app.state.db.transaction() as session:
        assert session.get(BountyTask, value['bounty']['id']).version == value['bounty']['version']
        assert session.get(BountySubmission, value['receipt']['submission_id']).status == 'pending'
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 0
        assert session.scalar(select(func.count()).select_from(BountyDecision).where(BountyDecision.kind == 'pipeline')) == 0


def test_withdrawn_exact_contribution_is_rechecked_before_reviewer_award(system):
    app, _, reviewer, solver, *_ = system
    pin = published(solver, reviewer)
    value = submit(solver, opened(system), pin)
    post(reviewer, '/api/notebook/notes/' + pin['note_id'] + '/publication-withdrawals', {
        'revision': pin['revision'], 'content_sha256': pin['content_sha256'],
        'reason': 'The exact claim needs correction.', 'idempotency_key': str(uuid4())})
    post(reviewer, decision_path(value), body(value, kind='pipeline', decision='approved', reason='Review withdrawn evidence.'), 409)
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 0
        assert session.get(BountySubmission, value['receipt']['submission_id']).status == 'pending'


@pytest.mark.parametrize('changed', ['reviewer-access', 'exact-publication'])
def test_later_qualified_claim_is_revalidated_when_another_reviewer_clears_the_queue(system, changed):
    app, _, reviewer, solver, supporter, reviewer2, _ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    first = value['receipt']['submission_id']
    later_pin = published(supporter, reviewer)
    value = submit(supporter, value, later_pin)
    second = value['receipt']['submission_id']
    value = assess(reviewer, value, 'pipeline', submission_id=second)
    if changed == 'reviewer-access':
        with app.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, reviewer.identifier, 'revoked', app.state.clock())
    else:
        post(reviewer2, '/api/notebook/notes/' + later_pin['note_id'] + '/publication-withdrawals', {
            'revision': later_pin['revision'], 'content_sha256': later_pin['content_sha256'],
            'reason': 'Withdraw this exact publication.', 'idempotency_key': str(uuid4())})
    post(reviewer2, decision_path(value, first), body(value, kind='pipeline', decision='declined',
        reason='The earlier claim does not meet the criteria.'), 409)
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 0
        assert session.get(BountyTask, value['bounty']['id']).version == value['bounty']['version']
        assert session.get(BountySubmission, first).status == 'pending'
        assert session.get(BountySubmission, second).status == 'qualified'


def test_historical_poster_decision_remains_but_is_not_linked_to_new_award(system):
    app, owner, reviewer, solver, *_ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    legacy_id = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        task = session.get(BountyTask, value['bounty']['id'])
        claim = session.get(BountySubmission, value['receipt']['submission_id'])
        task.version += 1
        task.sequence += 1
        session.add(BountyDecision(id=legacy_id, task_id=task.id, submission_id=claim.id, terms_id=claim.terms_id,
            evidence_sha256=claim.note_sha256, actor_id=owner.identifier, kind='poster', decision='approved',
            reason='Historical proposer approval.', checks={}, task_version=task.version, created_at=app.state.clock()))
    current = reviewer.client.get('/api/bounties/' + value['bounty']['id']).json()
    result = assess(reviewer, current, 'pipeline', submission_id=value['receipt']['submission_id'])
    assert result['bounty']['status'] == 'completed'
    with app.state.db.transaction() as session:
        assert session.get(BountyDecision, legacy_id).reason == 'Historical proposer approval.'
        assert session.get(BountyAward, value['bounty']['id']).poster_decision_id is None


def test_two_independent_reviewers_cannot_award_same_claim_twice(system):
    app, _, reviewer, solver, _, reviewer2, _ = system
    value = submit(solver, opened(system), published(solver, reviewer))
    barrier = Barrier(2, timeout=10)
    def review(actor):
        payload = body(value, kind='pipeline', decision='approved', reason='Independent simultaneous review.')
        barrier.wait()
        return actor.post(decision_path(value), payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, [reviewer, reviewer2]))
    assert sorted(response.status_code for response in results) == [200, 409]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 1
        assert session.scalar(select(func.count()).select_from(BountyDecision).where(BountyDecision.kind == 'pipeline')) == 1
