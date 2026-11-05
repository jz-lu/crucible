"""Synthetic durable dispatch tests: no provider, private content, or network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible.db import Base, Database, User
from crucible.notebook import NotebookNote, NotebookPublication, NotebookRevision
from crucible import publication_screening_jobs as jobs
from crucible.publication_screening_models import (
    AutomaticPublication, ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob,
)


@pytest.fixture
def system(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'screening.db'}")
    tables = [model.__table__ for model in (User, NotebookNote, NotebookRevision,
        NotebookPublication, ScreeningJob, ScreeningBudget, ScreeningAttempt,
        ScreeningControl, AutomaticPublication)]
    Base.metadata.create_all(db.engine, tables=tables)
    now = datetime(2026, 11, 4, tzinfo=timezone.utc).timestamp()
    policy = jobs.ScreeningPolicy('test-v1', 'a' * 64, 'input-v1', 'test-model', 32000, 1000, 100)
    authors = [str(uuid4()) for _ in range(2)]
    with db.transaction(write=True) as session:
        for i, author in enumerate(authors):
            session.add(User(id=author, handle=f'author{i}', created_at=now))
    jobs.configure(db, policy=policy, mode='automatic', monthly_budget_micro_usd=1000, now=now)
    yield SimpleNamespace(db=db, policy=policy, now=now, authors=authors)
    db.engine.dispose()


def queued(s, *, author=None, now=None):
    author, now = author or s.authors[0], s.now if now is None else now
    note_id, revision_id, publication_id = (str(uuid4()) for _ in range(3))
    with s.db.transaction(write=True) as session:
        # Core inserts deliberately bypass unrelated notebook projection hooks.
        session.execute(NotebookNote.__table__.insert().values(id=note_id, author_id=author,
            created_at=now, latest_revision=1, idempotency_key=str(uuid4()), request_sha256='1' * 64))
        session.execute(NotebookRevision.__table__.insert().values(id=revision_id, note_id=note_id,
            revision=1, created_at=now, content={'body': 'Synthetic input'}, content_sha256='b' * 64,
            idempotency_key=str(uuid4()), request_sha256='2' * 64))
        publication = NotebookPublication(id=publication_id, revision_id=revision_id,
            author_id=author, requested_at=now, idempotency_key=str(uuid4()), request_sha256='3' * 64,
            test_only_self_review=False)
        session.add(publication)
        session.flush()
        job = jobs.enqueue(session, publication, policy=s.policy, now=now)
        return job.id, publication_id


def claim(s, **changes):
    args = dict(policy=s.policy, enabled=True, monthly_budget_micro_usd=1000,
                now=s.now, lease_seconds=120)
    args.update(changes)
    return jobs.claim(s.db, **args)


def publish(session, job, receipt, now):
    publication = session.get(NotebookPublication, job.publication_id)
    if publication.decision is not None:
        return False
    assert receipt.revision_id == publication.revision_id
    publication.decision, publication.decision_source = 'approved', 'automated'
    publication.reviewed_at = now
    return True


def finish(s, lease, **changes):
    args = dict(verdict='no_concern_found',
        result={'outcome': 'no_concern_found', 'concerns': [], 'limitations': []},
        model_id='test-model', actual_cost_micro_usd=30, policy=s.policy, enabled=True,
        automatic=True, now=s.now + 1, publish=publish, content_sha256='b' * 64, full_coverage=True)
    args.update(changes)
    return jobs.finish(s.db, lease, **args)


def row(s, model, identity):
    with s.db.transaction() as session:
        return session.get(model, identity)


def count(s, model):
    with s.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_policy_identity_pins_complete_configuration_and_is_immutable():
    configuration = {'reasoning': 'low'}
    policy = jobs.ScreeningPolicy('test-v1', 'a' * 64, 'input-v1', 'model', 1000, 100, 50,
                                 model_configuration=configuration)
    original = policy.id
    configuration['reasoning'] = 'high'
    assert policy.id == original
    assert policy.configuration['model_configuration'] == {'reasoning': 'low'}
    policy.configuration['model_configuration']['reasoning'] = 'x'
    assert policy.id == original
    assert replace(policy, max_output_tokens=101).id != original
    assert replace(policy, prompt_sha256='b' * 64).id != original
    with pytest.raises(ValueError):
        replace(policy, reserve_micro_usd=-1)
    with pytest.raises(ValueError):
        replace(policy, max_attempts=3)


def test_enqueue_exact_identity_deduplicates_and_respects_request_transaction(system):
    s = system
    identifier, publication_id = queued(s)
    with s.db.transaction(write=True) as session:
        publication = session.get(NotebookPublication, publication_id)
        assert jobs.enqueue(session, publication, policy=s.policy, now=s.now + 5).id == identifier
        changed = replace(s.policy, prompt_sha256='d' * 64)
        assert jobs.enqueue(session, publication, policy=changed, now=s.now + 5).id != identifier
    assert count(s, ScreeningJob) == 2
    with pytest.raises(RuntimeError):
        with s.db.transaction(write=True) as session:
            publication = session.get(NotebookPublication, publication_id)
            jobs.enqueue(session, publication, policy=replace(s.policy, model='other'), now=s.now)
            raise RuntimeError('rollback synthetic request')
    assert count(s, ScreeningJob) == 2
    with s.db.transaction(write=True) as session:
        publication = session.get(NotebookPublication, publication_id)
        publication.decision = 'changes_requested'
        assert jobs.enqueue(session, publication, policy=replace(s.policy, model='third'), now=s.now) is None


def test_cross_worker_claim_reserves_only_one_global_call(system):
    s = system
    queued(s)
    queued(s, author=s.authors[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(lambda _: claim(s), range(2)))
    assert sum(lease is not None for lease in leases) == 1
    assert count(s, ScreeningAttempt) == 1
    assert row(s, ScreeningBudget, '2026-11').reserved_micro_usd == 100
    assert claim(s) is None


def test_claim_clock_is_read_after_waiting_for_control_lock(system, monkeypatch):
    s = system
    queued(s)
    clock = [s.now]
    original = jobs._control
    def waited(session):
        row = original(session)
        clock[0] += 120
        return row
    monkeypatch.setattr(jobs, '_control', waited)
    lease = claim(s, clock=lambda: clock[0])
    assert lease.expires_at == s.now + 240
    assert row(s, ScreeningAttempt, lease.attempt_id).started_at == s.now + 120


def test_finalization_rechecks_clock_after_control_lock_wait(system, monkeypatch):
    s = system
    _, publication_id = queued(s)
    lease = claim(s)
    clock = [s.now + 1]
    original = jobs._control
    def waited(session):
        result = original(session)
        clock[0] = lease.expires_at
        return result
    monkeypatch.setattr(jobs, '_control', waited)
    assert finish(s, lease, clock=lambda: clock[0]) == 'stale_lease'
    assert row(s, NotebookPublication, publication_id).decision is None
    assert count(s, AutomaticPublication) == 0


def test_expiry_during_publication_locks_rolls_back_callback_but_retains_usage(system):
    s = system
    identifier, publication_id = queued(s)
    lease = claim(s)
    clock = [s.now + 1]
    def delayed(session, job, receipt, now):
        assert publish(session, job, receipt, now)
        session.flush()
        clock[0] = lease.expires_at
        return True
    assert finish(s, lease, publish=delayed, clock=lambda: clock[0]) == 'human_review'
    assert row(s, NotebookPublication, publication_id).decision is None
    assert count(s, AutomaticPublication) == 0
    attempt = row(s, ScreeningAttempt, lease.attempt_id)
    assert attempt.result['outcome'] == 'no_concern_found'
    assert attempt.charged_micro_usd == 30 and attempt.usage_known
    assert attempt.error_code == 'lease_expired'
    assert attempt.finished_at == lease.expires_at
    assert row(s, ScreeningJob, identifier).error_code == 'lease_expired'
    assert row(s, ScreeningBudget, '2026-11').reserved_micro_usd == 0
    assert claim(s, now=clock[0] + 100) is None  # Do not buy a repeat permissive verdict.


def test_publication_and_receipt_atomic_idempotent_and_durable(system):
    s = system
    identifier, publication_id = queued(s)
    lease = claim(s)
    assert finish(s, lease) == 'published'
    assert finish(s, lease) == 'stale_lease'
    assert row(s, ScreeningJob, identifier).status == 'published'
    publication = row(s, NotebookPublication, publication_id)
    assert publication.decision == 'approved' and publication.decision_source == 'automated'
    assert publication.reviewer_id is None and publication.review_key is None
    receipt = row(s, AutomaticPublication, publication_id)
    assert (receipt.actor, receipt.job_id, receipt.attempt_id) == (jobs.ACTOR, identifier, lease.attempt_id)
    assert receipt.content_sha256 == lease.content_sha256
    budget = row(s, ScreeningBudget, '2026-11')
    assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (30, 0)
    reopened = Database(str(s.db.engine.url))
    try:
        with reopened.transaction() as session:
            assert session.get(AutomaticPublication, publication_id).result_sha256 == receipt.result_sha256
    finally:
        reopened.engine.dispose()


def test_failed_publication_callback_rolls_back_cost_receipt_and_transition(system):
    s = system
    _, publication_id = queued(s)
    lease = claim(s)
    def broken(session, job, receipt, now):
        publish(session, job, receipt, now)
        raise RuntimeError('synthetic callback failure')
    with pytest.raises(RuntimeError):
        finish(s, lease, publish=broken)
    assert row(s, NotebookPublication, publication_id).decision is None
    assert count(s, AutomaticPublication) == 0
    assert row(s, ScreeningBudget, '2026-11').reserved_micro_usd == 100
    assert row(s, ScreeningAttempt, lease.attempt_id).charged_micro_usd is None
    assert jobs.fail(s.db, lease, code='internal_error', retryable=False, now=s.now + 2,
                     actual_cost_micro_usd=30) == 'human_review'
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 30


@pytest.mark.parametrize('mode', ['disabled', 'shadow'])
def test_control_mode_prevents_automatic_publication_mid_call(system, mode):
    s = system
    identifier, _ = queued(s)
    lease = claim(s)
    jobs.configure(s.db, policy=s.policy, mode=mode, monthly_budget_micro_usd=1000, now=s.now + 0.5)
    assert claim(s, now=s.now + 0.6) is None
    assert finish(s, lease) == ('human_review' if mode == 'disabled' else 'completed')
    assert count(s, AutomaticPublication) == 0
    assert row(s, ScreeningJob, identifier).error_code == ('disabled' if mode == 'disabled' else None)


def test_policy_change_stops_inflight_publication_and_routes_old_queue_to_human(system):
    s = system
    first, _ = queued(s)
    second, _ = queued(s)
    lease = claim(s)
    new = replace(s.policy, prompt_sha256='c' * 64)
    jobs.configure(s.db, policy=new, mode='automatic', monthly_budget_micro_usd=1000, now=s.now + 0.1)
    assert claim(s, policy=new, now=s.now + 0.2) is None
    assert finish(s, lease) == 'human_review'
    assert claim(s, policy=new, now=s.now + 2) is None
    assert all(row(s, ScreeningJob, identifier).error_code == 'policy_changed' for identifier in (first, second))


def test_worker_cannot_enable_disabled_control(system):
    s = system
    queued(s)
    jobs.configure(s.db, policy=s.policy, mode='disabled', monthly_budget_micro_usd=1000, now=s.now)
    assert claim(s, enabled=True) is None
    assert row(s, ScreeningControl, 1).mode == 'disabled'
    assert count(s, ScreeningAttempt) == 0


@pytest.mark.parametrize('override,code', [
    ({'content_sha256': 'f' * 64}, 'coverage_incomplete'),
    ({'full_coverage': False}, 'coverage_incomplete'),
    ({'actual_cost_micro_usd': 101}, 'usage_exceeded_reservation'),
    ({'enabled': False}, 'disabled'),
])
def test_fail_closed_without_exact_coverage_or_budget(system, override, code):
    s = system
    identifier, _ = queued(s)
    lease = claim(s)
    assert finish(s, lease, **override) == 'human_review'
    assert row(s, ScreeningJob, identifier).error_code == code
    assert count(s, AutomaticPublication) == 0


def test_flag_is_human_queue_not_rejection_or_publication(system):
    s = system
    identifier, publication_id = queued(s)
    lease = claim(s)
    assert finish(s, lease, verdict='human_review_needed', result={
        'outcome': 'human_review_needed', 'concerns': [], 'limitations': []}) == 'human_review'
    assert row(s, NotebookPublication, publication_id).decision is None
    assert count(s, AutomaticPublication) == 0
    assert row(s, ScreeningJob, identifier).error_code is None


def test_human_decision_wins_race_without_fake_automated_receipt(system):
    s = system
    identifier, publication_id = queued(s)
    lease = claim(s)
    with s.db.transaction(write=True) as session:
        publication = session.get(NotebookPublication, publication_id)
        publication.decision, publication.decision_source = 'changes_requested', 'human'
        publication.reviewer_id, publication.reason = s.authors[1], 'Synthetic review'
    assert finish(s, lease) == 'superseded'
    assert row(s, NotebookPublication, publication_id).reason == 'Synthetic review'
    assert row(s, ScreeningJob, identifier).status == 'superseded'
    assert count(s, AutomaticPublication) == 0


def test_unknown_cost_uses_full_reservation_and_only_two_transient_attempts(system):
    s = system
    identifier, _ = queued(s)
    first = claim(s)
    assert jobs.fail(s.db, first, code='provider_timeout', retryable=True, now=s.now + 1) == 'queued'
    assert claim(s, now=s.now + 5) is None
    second = claim(s, now=s.now + 6)
    assert second and second.attempt_id != first.attempt_id
    assert jobs.fail(s.db, second, code='provider_timeout', retryable=True, now=s.now + 7) == 'human_review'
    assert row(s, ScreeningJob, identifier).attempt_count == 2
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 200
    assert row(s, ScreeningAttempt, first.attempt_id).usage_known is False
    assert claim(s, now=s.now + 20) is None


def test_invalid_responses_never_retry_toward_a_permissive_answer(system):
    s = system
    identifier, _ = queued(s)
    lease = claim(s)
    assert jobs.fail(s.db, lease, code='invalid_response', retryable=True,
                     now=s.now + 1, actual_cost_micro_usd=35) == 'human_review'
    assert row(s, ScreeningJob, identifier).attempt_count == 1
    assert count(s, ScreeningAttempt) == 1
    assert claim(s, now=s.now + 20) is None


def test_failure_overspend_is_preserved_and_never_retried(system):
    s = system
    identifier, _ = queued(s)
    lease = claim(s)
    assert jobs.fail(s.db, lease, code='provider_unavailable', retryable=True,
                     now=s.now + 1, actual_cost_micro_usd=101) == 'human_review'
    assert row(s, ScreeningJob, identifier).error_code == 'usage_exceeded_reservation'
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 101


def test_expired_lease_cannot_apply_result_or_release_replacement_lease(system):
    s = system
    identifier, _ = queued(s)
    original = claim(s, lease_seconds=10)
    assert finish(s, original, now=s.now + 10) == 'stale_lease'
    assert row(s, ScreeningAttempt, original.attempt_id).error_code == 'lease_expired'
    replacement = claim(s, now=s.now + 15, lease_seconds=10)
    assert replacement and replacement.token != original.token
    assert finish(s, original, now=s.now + 16) == 'stale_lease'
    assert row(s, ScreeningControl, 1).lease_token == replacement.token
    assert finish(s, replacement, now=s.now + 16) == 'published'
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 130
    assert count(s, AutomaticPublication) == 1
    assert row(s, ScreeningJob, identifier).attempt_count == 2


def test_second_expired_attempt_finishes_in_human_queue(system):
    s = system
    identifier, _ = queued(s)
    original = claim(s, lease_seconds=10)
    assert claim(s, now=s.now + 10) is None
    second = claim(s, now=s.now + 15, lease_seconds=10)
    assert claim(s, now=s.now + 25) is None
    assert row(s, ScreeningJob, identifier).status == 'human_review'
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 200
    assert finish(s, original, now=s.now + 26) == 'stale_lease'
    assert finish(s, second, now=s.now + 26) == 'stale_lease'


def test_budget_uses_stricter_durable_limit_and_does_not_reserve_failed_claim(system):
    s = system
    identifier, _ = queued(s)
    jobs.configure(s.db, policy=s.policy, mode='automatic', monthly_budget_micro_usd=99, now=s.now)
    assert claim(s, monthly_budget_micro_usd=10**12) is None
    assert row(s, ScreeningJob, identifier).error_code == 'budget_exhausted'
    budget = row(s, ScreeningBudget, '2026-11')
    assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (0, 0)
    assert count(s, ScreeningAttempt) == 0


def test_budget_is_charged_to_dispatch_month_even_after_month_boundary(system):
    s = system
    queued(s)
    boundary = datetime(2026, 12, 1, tzinfo=timezone.utc).timestamp()
    lease = claim(s, now=boundary - 5)
    assert finish(s, lease, now=boundary + 2) == 'published'
    assert row(s, ScreeningBudget, '2026-11').spent_micro_usd == 30
    assert row(s, ScreeningBudget, '2026-12') is None
    queued(s)
    assert claim(s, now=boundary + 3)
    assert row(s, ScreeningBudget, '2026-12').reserved_micro_usd == 100


def test_ambiguous_attempt_cannot_reuse_reserved_budget_for_retry(system):
    s = system
    identifier, _ = queued(s)
    jobs.configure(s.db, policy=s.policy, mode='automatic', monthly_budget_micro_usd=150, now=s.now)
    lease = claim(s)
    assert jobs.fail(s.db, lease, code='provider_timeout', retryable=True, now=s.now + 1) == 'queued'
    assert claim(s, now=s.now + 6) is None
    assert row(s, ScreeningJob, identifier).error_code == 'budget_exhausted'
    assert count(s, ScreeningAttempt) == 1
    budget = row(s, ScreeningBudget, '2026-11')
    assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (100, 0)


def test_known_usage_releases_unused_reservation_for_later_job(system):
    s = system
    queued(s)
    queued(s, now=s.now + 1)
    jobs.configure(s.db, policy=s.policy, mode='automatic', monthly_budget_micro_usd=130, now=s.now)
    lease = claim(s)
    assert finish(s, lease, actual_cost_micro_usd=30) == 'published'
    assert claim(s, now=s.now + 2)
    budget = row(s, ScreeningBudget, '2026-11')
    assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (30, 100)


def test_corrupted_policy_snapshot_never_dispatches_provider(system):
    s = system
    identifier, _ = queued(s)
    with s.db.transaction(write=True) as session:
        job = session.get(ScreeningJob, identifier)
        job.policy = {**job.policy, 'model': 'different-model'}
    assert claim(s) is None
    assert row(s, ScreeningJob, identifier).error_code == 'policy_changed'
    assert count(s, ScreeningAttempt) == 0


def test_dispatch_fairness_rotates_authors_instead_of_draining_one_backlog(system):
    s = system
    a, _ = queued(s, now=s.now - 3)
    queued(s, now=s.now - 2)
    b, _ = queued(s, author=s.authors[1], now=s.now - 1)
    first = claim(s)
    assert first.job_id == a
    assert finish(s, first) == 'published'
    assert claim(s, now=s.now + 2).job_id == b


def test_rejects_forged_lease_pin_without_modifying_reservation(system):
    s = system
    queued(s)
    lease = claim(s)
    with pytest.raises(RuntimeError, match='identity'):
        finish(s, replace(lease, content_sha256='e' * 64))
    assert row(s, ScreeningBudget, '2026-11').reserved_micro_usd == 100
    assert count(s, AutomaticPublication) == 0
    with pytest.raises(ValueError, match='precede'):
        finish(s, lease, now=s.now - 1)


def test_no_arbitrary_exception_text_can_enter_failure_journal(system):
    s = system
    queued(s)
    lease = claim(s)
    with pytest.raises(ValueError, match='non-sensitive'):
        jobs.fail(s.db, lease, code='secret provider response', retryable=False, now=s.now + 1)
    assert row(s, ScreeningAttempt, lease.attempt_id).error_code is None
