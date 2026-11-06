"""Exact new-policy repairs preserve original requests, findings and accounting."""
from dataclasses import replace
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import event, select

from crucible import beta_access, notebook, notebook_moderation
from crucible import publication_screening_retry as retry, publication_screening_jobs as jobs
from crucible.publication_screening_models import ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob
from crucible.publication_screening_worker import policy_for
from test_notebook import revise, publication_body
from test_notebook_publication_cancellation import cancel
from test_notebook_trash import delete
from test_publication_screening_adoption import manual_request, rows, settings
from test_publication_screening_integration import screened, configure, run, SyntheticProvider


@pytest.fixture(autouse=True)
def enabled_control(screened):
    configure(screened)


def failed_request(system, actor=None, *, error='input_too_large'):
    saved, publication = manual_request(system, actor)
    current = policy_for(settings(system))
    previous = replace(current, model_configuration={**current.model_configuration,
        'input_admission': 'synthetic-legacy-byte-bound'})
    assert previous.id != current.id
    with system.app.state.db.transaction(write=True) as session:
        job = jobs.enqueue(session, session.get(notebook.NotebookPublication, publication['id']),
            policy=previous, now=system.now[0]-120)
        job.status, job.attempt_count, job.error_code = 'human_review', 1, error
        job.last_started_at, job.finished_at = system.now[0]-110, system.now[0]-100
        if session.get(ScreeningBudget, '2026-11') is None:
            session.add(ScreeningBudget(month='2026-11', spent_micro_usd=0, reserved_micro_usd=0))
            session.flush()
        attempt = ScreeningAttempt(id=str(uuid4()), job_id=job.id, number=1, lease_token=str(uuid4()),
            budget_month='2026-11', reserved_micro_usd=previous.reserve_micro_usd,
            charged_micro_usd=0, usage_known=True, started_at=system.now[0]-110,
            lease_expires_at=system.now[0]-80, finished_at=system.now[0]-100, error_code=error)
        session.add(attempt); session.flush()
        ids = {'publication': publication['id'], 'job': job.id, 'attempt': attempt.id}
    return saved, ids


def preview(system, ids, **overrides):
    return retry.preview(system.app.state.db, settings(system),
        **{'actor_id': system.reviewer.identifier, 'publication_ids': ids,
           'clock': lambda: system.now[0], **overrides})


def apply(system, plan, **overrides):
    return retry.apply_plan(system.app.state.db, settings(system), plan,
        **{'actor_id': system.reviewer.identifier, 'authorization_reference': 'explicit-repair-test',
           'reason': 'Correct a conservative input admission limit.', 'clock': lambda: system.now[0], **overrides})


def audits(system):
    return [row for row in rows(system, beta_access.AccessEvent) if row['action'] == retry.ACTION]


def rehash(plan):
    plan['plan_sha256'] = jobs.digest({key: value for key, value in plan.items() if key != 'plan_sha256'})
    return plan


def test_exact_cross_author_repair_is_atomic_idempotent_and_preserves_all_old_rows(screened):
    note, first = failed_request(screened)
    _, second = failed_request(screened, screened.reader)
    # A subsequent private revision is neither replaced nor selected for publication.
    newer = revise(screened.owner, note)
    before = {model: rows(screened, model) for model in (
        notebook.NotebookPublication, notebook.NotebookRevision, ScreeningJob, ScreeningAttempt, ScreeningBudget)}
    plan = preview(screened, [first['publication'], second['publication']])
    assert len(plan['requests']) == 2 and plan['skipped'] == []
    assert {item['revision'] for item in plan['requests']} == {1}
    assert note['revisions'][0]['content']['body'] not in str(plan)
    assert audits(screened) == [] and rows(screened, ScreeningJob) == before[ScreeningJob]
    result = apply(screened, plan)
    assert result['queued'] == 2
    for model in (notebook.NotebookPublication, notebook.NotebookRevision, ScreeningAttempt, ScreeningBudget):
        assert rows(screened, model) == before[model]
    old_jobs = {row['id']: row for row in rows(screened, ScreeningJob)}
    assert all(old_jobs[row['id']] == row for row in before[ScreeningJob])
    recorded = audits(screened)
    assert len(recorded) == 2
    for audit in recorded:
        details = audit['details']
        assert audit['actor_id'] == screened.reviewer.identifier
        assert details['authority_basis'] == 'platform_policy_repair'
        assert details['publication_basis'] == 'original_publication_request'
        assert details['fresh_author_consent_recorded'] is False
        assert details['plan_sha256'] == plan['plan_sha256']
        assert details['previous_job_id'] in {first['job'], second['job']}
        assert not {'title', 'body', 'email', 'consented_at'} & details.keys()
    all_jobs = rows(screened, ScreeningJob)
    replay = apply(screened, plan)
    assert replay['queued'] == 0
    assert {item['status'] for item in replay['outcomes']} == {'already_retried'}
    assert rows(screened, ScreeningJob) == all_jobs and audits(screened) == recorded
    provider = SyntheticProvider()
    assert run(screened, provider)['status'] == 'published'
    assert provider.inputs[0].content_sha256 in {item['content_sha256'] for item in plan['requests']}
    assert newer['revision'] == 2


@pytest.mark.parametrize('error', sorted(retry.PREFLIGHT_ERRORS))
def test_only_explicit_preflight_codes_are_eligible(screened, error):
    _, ids = failed_request(screened, error=error)
    assert len(preview(screened, [ids['publication']])['requests']) == 1


@pytest.mark.parametrize('change', ['model_concern', 'provider_error', 'hash_error', 'running', 'queued',
    'unknown_usage', 'charged', 'result', 'result_digest', 'model_identity', 'unfinished',
    'attempt_error_changed', 'active_lease', 'same_policy', 'earlier_verdict', 'earlier_ambiguous', 'shadow'])
def test_model_results_cost_uncertainty_and_other_failures_are_never_retried(screened, change):
    _, ids = failed_request(screened)
    with screened.app.state.db.transaction(write=True) as session:
        job = session.get(ScreeningJob, ids['job'])
        attempt = session.get(ScreeningAttempt, ids['attempt'])
        if change == 'model_concern': attempt.outcome = 'human_review_needed'
        elif change == 'provider_error': job.error_code = attempt.error_code = 'provider_timeout'
        elif change == 'hash_error': job.error_code = attempt.error_code = 'coverage_incomplete'
        elif change in {'running', 'queued'}: job.status = change
        elif change == 'unknown_usage': attempt.usage_known = False
        elif change == 'charged': attempt.charged_micro_usd = 1
        elif change == 'result': attempt.result = {'concerns': ['synthetic private result']}
        elif change == 'result_digest': attempt.result_sha256 = 'a'*64
        elif change == 'model_identity': attempt.model_id = 'synthetic-model'
        elif change == 'unfinished': attempt.finished_at = None
        elif change == 'attempt_error_changed': attempt.error_code = 'input_structure_limit'
        elif change == 'active_lease': session.get(ScreeningControl, 1).active_job_id = job.id
        elif change == 'same_policy':
            policy = policy_for(settings(screened)); job.policy_id, job.policy = policy.id, policy.configuration
        elif change == 'shadow':
            job.policy = {**job.policy, 'model_configuration': {
                **job.policy['model_configuration'], 'publication_mode': 'shadow'}}
            job.policy_id = jobs.digest(job.policy)
        else:
            job.attempt_count = attempt.number = 2
            session.add(ScreeningAttempt(id=str(uuid4()), job_id=job.id, number=1, lease_token=str(uuid4()),
                budget_month='2026-11', reserved_micro_usd=1, charged_micro_usd=0,
                started_at=screened.now[0]-120, lease_expires_at=screened.now[0]-110,
                finished_at=screened.now[0]-115,
                outcome='human_review_needed' if change == 'earlier_verdict' else None,
                usage_known=True if change == 'earlier_verdict' else False))
    before = rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget)
    plan = preview(screened, [ids['publication']])
    assert plan['requests'] == [] and len(plan['skipped']) == 1
    assert apply(screened, plan)['queued'] == 0
    assert before == (rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget))
    assert audits(screened) == []


@pytest.mark.parametrize('state', ['approved', 'changes_requested', 'cancelled', 'deleted',
                                 'withdrawn', 'revoked', 'suspended'])
def test_later_human_lifecycle_and_membership_changes_win(screened, state):
    note, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    if state in {'approved', 'changes_requested'}:
        response = screened.reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
            publication_body(note, decision=state, reason='Synthetic independent review.'))
        assert response.status_code == 200, response.text
    elif state == 'cancelled': assert cancel(screened.owner, note).status_code == 200
    elif state == 'deleted': assert delete(screened.owner, note).status_code == 200
    else:
        with screened.app.state.db.transaction(write=True) as session:
            if state == 'withdrawn':
                revision = session.get(notebook.NotebookPublication, ids['publication']).revision_id
                session.add(notebook_moderation.PublicationWithdrawal(revision_id=revision,
                    moderator_id=screened.reviewer.identifier, created_at=screened.now[0],
                    reason='Synthetic withdrawal', idempotency_key=str(uuid4()), request_sha256='f'*64))
            elif state == 'revoked':
                beta_access.set_membership(session, screened.owner.identifier, 'revoked', screened.now[0])
            else:
                beta_access.set_suspension(session, screened.owner.identifier, True, screened.now[0], reason='Synthetic')
    before = rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget)
    result = apply(screened, plan)
    assert result['queued'] == 0
    assert result['outcomes'][0]['status'] in {'request_ineligible', 'author_ineligible'}
    assert before == (rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget))
    assert audits(screened) == []


def test_changed_history_or_unrelated_new_job_cannot_be_reset(screened):
    _, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    with screened.app.state.db.transaction(write=True) as session:
        current = policy_for(settings(screened))
        unrelated = replace(current, model_configuration={**current.model_configuration, 'synthetic': True})
        later = jobs.enqueue(session, session.get(notebook.NotebookPublication, ids['publication']),
            policy=unrelated, now=screened.now[0]-10)
        later.status = 'human_review'
    before = rows(screened, ScreeningJob)
    assert apply(screened, plan)['queued'] == 0
    assert rows(screened, ScreeningJob) == before and audits(screened) == []


def test_existing_new_policy_without_matching_repair_audit_is_not_claimed_as_replay(screened):
    _, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    with screened.app.state.db.transaction(write=True) as session:
        jobs.enqueue(session, session.get(notebook.NotebookPublication, ids['publication']),
            policy=policy_for(settings(screened)), now=screened.now[0]-5)
    before = rows(screened, ScreeningJob)
    result = apply(screened, plan)
    assert result['queued'] == 0 and result['outcomes'][0]['status'] == 'already_screened'
    assert rows(screened, ScreeningJob) == before and audits(screened) == []


@pytest.mark.parametrize('change', ['expired', 'future', 'policy', 'digest', 'operator', 'duplicate', 'identity'])
def test_exact_fresh_plan_and_actor_binding(screened, change):
    _, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    if change == 'expired': screened.now[0] += retry.MAX_PLAN_AGE_SECONDS+1
    elif change == 'future': plan['created_at'] += 1; rehash(plan)
    elif change == 'policy': plan['policy_id'] = 'a'*64; rehash(plan)
    elif change == 'digest': plan['plan_sha256'] = 'a'*64
    elif change == 'operator': plan['actor_id'] = screened.reviewer2.identifier; rehash(plan)
    elif change == 'duplicate': plan['requests'] *= 2; rehash(plan)
    elif change == 'identity': plan['requests'][0]['content_sha256'] = 'f'*64; rehash(plan)
    before = rows(screened, ScreeningJob)
    if change == 'identity': assert apply(screened, plan)['outcomes'][0]['status'] == 'identity_changed'
    else:
        with pytest.raises((ValueError, HTTPException)): apply(screened, plan)
    assert rows(screened, ScreeningJob) == before and audits(screened) == []


@pytest.mark.parametrize('change', ['disabled', 'policy', 'operator_revoked', 'not_admin'])
def test_live_control_and_admin_are_rechecked_at_commit(screened, change):
    _, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    with screened.app.state.db.transaction(write=True) as session:
        if change == 'disabled': session.get(ScreeningControl, 1).mode = 'disabled'
        elif change == 'policy': session.get(ScreeningControl, 1).policy_id = 'a'*64
        elif change == 'operator_revoked':
            beta_access.set_membership(session, screened.reviewer.identifier, 'revoked', screened.now[0])
    with pytest.raises((ValueError, HTTPException)):
        apply(screened, plan, **({'actor_id': screened.owner.identifier} if change == 'not_admin' else {}))
    assert len(rows(screened, ScreeningJob)) == 1 and audits(screened) == []


def test_audit_failure_rolls_back_the_new_job_and_all_batch_changes(screened):
    _, first = failed_request(screened)
    _, second = failed_request(screened, screened.reader)
    plan = preview(screened, [first['publication'], second['publication']])
    before = rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget)
    def reject_audit(mapper, connection, target):
        if target.action == retry.ACTION:
            raise RuntimeError('Synthetic audit write failure')
    event.listen(beta_access.AccessEvent, 'before_insert', reject_audit)
    try:
        with pytest.raises(RuntimeError): apply(screened, plan)
    finally:
        event.remove(beta_access.AccessEvent, 'before_insert', reject_audit)
    assert before == (rows(screened, ScreeningJob), rows(screened, ScreeningAttempt), rows(screened, ScreeningBudget))
    assert audits(screened) == []


def test_preview_requires_explicit_bounded_allowlist_and_active_admin(screened):
    _, ids = failed_request(screened)
    for identifiers in ([], [ids['publication']]*2, [str(uuid4()) for _ in range(26)], 'all'):
        with pytest.raises(ValueError): preview(screened, identifiers)
    with pytest.raises(HTTPException) as error:
        preview(screened, [ids['publication']], actor_id=screened.owner.identifier)
    assert error.value.status_code == 403
    missing = preview(screened, [str(uuid4())])
    assert missing['requests'] == [] and missing['skipped'][0]['status'] == 'request_ineligible'


@pytest.mark.parametrize('expiry_point', ['records', 'enqueue'])
def test_plan_expiry_during_lock_wait_does_not_create_a_job(screened, expiry_point):
    _, ids = failed_request(screened)
    plan = preview(screened, [ids['publication']])
    now = screened.now[0]
    # Initial validation passes; later reads model time elapsed acquiring locks.
    times = iter([now, now+3601] if expiry_point == 'records' else [now, now, now+3601])
    with pytest.raises(HTTPException) as error:
        apply(screened, plan, clock=lambda: next(times))
    assert error.value.status_code == 409
    assert len(rows(screened, ScreeningJob)) == 1 and audits(screened) == []


def test_cli_defaults_to_exact_preview_and_requires_explicit_apply(screened, monkeypatch, capsys, tmp_path):
    from crucible import config, publication_screening_worker as worker
    _, ids = failed_request(screened)
    monkeypatch.setattr(config, 'load_settings', lambda: settings(screened))
    monkeypatch.setattr(worker, 'worker_database', lambda url: screened.app.state.db)
    actor = ['--actor-id', screened.reviewer.identifier]
    assert retry.main([*actor, '--publication-id', ids['publication']]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan['requests']) == 1 and len(rows(screened, ScreeningJob)) == 1
    path = tmp_path / 'reviewed-plan.json'
    path.write_text(json.dumps(plan))
    with pytest.raises(SystemExit):
        retry.main([*actor, '--apply-plan', str(path)])
    capsys.readouterr()
    assert len(rows(screened, ScreeningJob)) == 1 and audits(screened) == []
    assert retry.main([*actor, '--apply-plan', str(path), '--execute-reviewed',
        '--authorization-reference', 'synthetic-explicit-cli', '--reason', 'Repair input admission.']) == 0
    assert json.loads(capsys.readouterr().out)['queued'] == 1


def test_cli_bounded_plan_and_sanitized_errors(screened, monkeypatch, capsys, tmp_path):
    from crucible import config, publication_screening_worker as worker
    monkeypatch.setattr(config, 'load_settings', lambda: settings(screened))
    monkeypatch.setattr(worker, 'worker_database', lambda url: screened.app.state.db)
    path = tmp_path / 'too-large.json'
    path.write_text('sensitive-synthetic-sentinel'*3000)
    assert retry.main(['--actor-id', screened.reviewer.identifier, '--apply-plan', str(path),
        '--execute-reviewed', '--authorization-reference', 'synthetic-test', '--reason', 'Repair.']) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {'status': 'failed', 'error_code': 'screening_retry_failed',
                                  'apply_outcome': 'unknown_inspect_before_retry'}
    assert 'sensitive-synthetic-sentinel' not in output
