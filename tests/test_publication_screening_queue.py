"""Exact pending screening triage; synthetic provider only, no API transport."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from crucible import beta_access, notebook
from crucible.publication_screening_jobs import digest
from crucible.publication_screening_models import ScreeningAttempt, ScreeningControl, ScreeningJob
from test_notebook import Actor, approve, content, create, publication_body, revise
from test_notebook_agents import agent
from test_publication_screening_integration import (screened, configure, request, run,
                                                   SyntheticProvider)


def queue(system, screening='flagged', **params):
    response = system.reviewer.client.get('/api/notebook/review-queue', params={
        'summary': 'true', 'screening': screening, **params})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    if screening != 'all':
        assert response.json()['screening'] == screening
    return response


def submitted(system, *, actor=None, **changes):
    system.now[0] += 1
    actor = actor or system.owner
    saved = create(actor, content(**changes))
    assert request(actor, saved).status_code == 200
    return saved


def flagged(system, **changes):
    saved = submitted(system, **changes)
    assert run(system, SyntheticProvider(flagged=True))['status'] == 'human_review'
    return saved


@pytest.mark.parametrize('category,label', [
    ('gratuitous_hostility', 'Hostile language'), ('spam', 'Spam or off-topic content'),
])
def test_v3_concerns_reach_private_review_without_automatic_publication(screened, category, label):
    class ScopedProvider(SyntheticProvider):
        def screen(self, prepared):
            result = super().screen(prepared)
            verdict = deepcopy(result.verdict)
            verdict['concerns'][0].update(category=category,
                explanation='SYNTHETIC_PRIVATE_LANGUAGE_FINDING',
                next_step='Check the cited passage without judging the research.')
            return replace(result, verdict=verdict)

    configure(screened)
    saved = submitted(screened, body='Synthetic passage for a bounded publication concern.')
    assert run(screened, ScopedProvider(flagged=True))['status'] == 'human_review'
    response = queue(screened)
    entry = response.json()['notes'][0]['revisions'][0]['publication']['screening']
    assert entry['categories'] == [{'code': category, 'label': label, 'count': 1}]
    assert 'SYNTHETIC_PRIVATE_LANGUAGE_FINDING' not in response.text
    detail = screened.reviewer.client.get(f'/api/notebook/notes/{saved["id"]}?revision=1').json()['note']
    publication = detail['revisions'][0]['publication']
    assert publication['screening']['reviewer']['concerns'][0]['category'] == category
    assert detail['revisions'][0]['publication_status'] == 'submitted'
    own = screened.owner.client.get(f'/api/notebook/notes/{saved["id"]}?revision=1')
    assert 'SYNTHETIC_PRIVATE_LANGUAGE_FINDING' not in own.text


def job_for(session, saved):
    return session.scalar(select(ScreeningJob).join(notebook.NotebookRevision,
        notebook.NotebookRevision.id == ScreeningJob.revision_id).where(
        notebook.NotebookRevision.note_id == saved['id'],
        notebook.NotebookRevision.revision == saved['revision']))


def pages(system, kind, limit):
    notes, cursor = [], None
    for _ in range(20):
        result = queue(system, kind, limit=limit, **({'after': cursor} if cursor else {})).json()
        notes.extend(result['notes'])
        cursor = result['next_after']
        if cursor is None:
            return notes
        assert len(result['notes']) == limit
    pytest.fail('Queue cursor did not terminate')


def test_filter_precedes_keyset_pagination_and_keeps_operational_failures_ordinary(screened):
    configure(screened)
    flags = [flagged(screened, title=f'Flag {number}') for number in range(5)]
    provider_failure = flagged(screened, title='Provider failure with earlier concerns')
    ordinary = [submitted(screened, title=f'Ordinary {number}') for number in range(7)]
    with screened.app.state.db.transaction(write=True) as session:
        failed = job_for(session, provider_failure)
        failed.error_code = 'provider_unavailable'
        budget = job_for(session, ordinary[-1])
        budget.status, budget.error_code = 'human_review', 'budget_exhausted'
        # Historical pending requests without any screening job also stay ordinary.
        session.delete(job_for(session, ordinary[0]))
        order = [ordinary[0], flags[0], ordinary[1], flags[1], ordinary[2], flags[2],
                 provider_failure, ordinary[3], flags[3], ordinary[4], flags[4], ordinary[5], ordinary[6]]
        for number, saved in enumerate(order):
            publication = session.scalar(select(notebook.NotebookPublication)
                .join(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == saved['id']))
            publication.requested_at = screened.now[0] + number // 2  # Also exercise ID tie breaking.
        expected = session.execute(select(notebook.NotebookRevision.note_id)
            .join(notebook.NotebookPublication).order_by(notebook.NotebookPublication.requested_at,
                                                       notebook.NotebookPublication.id)).scalars().all()
    flagged_ids = {item['id'] for item in flags}
    assert [item['id'] for item in pages(screened, 'flagged', 2)] == [i for i in expected if i in flagged_ids]
    plain = pages(screened, 'unflagged', 3)
    assert [item['id'] for item in plain] == [i for i in expected if i not in flagged_ids]
    fallback = next(item for item in plain if item['id'] == provider_failure['id'])
    summary = fallback['revisions'][0]['publication']['screening']
    assert summary['reason_code'] == 'provider_unavailable'
    assert summary['categories'] == [] and summary['concern_count'] == 0
    assert all(item['publication_status'] == 'submitted' for item in plain)
    default = screened.reviewer.client.get('/api/notebook/review-queue?summary=true').json()
    assert default == queue(screened, 'all').json()
    assert [item['id'] for item in default['notes']] == expected
    assert 'screening' not in default
    assert all('screening' not in item['revisions'][0]['publication'] for item in default['notes'])


def test_flag_summary_exact_revision_private_fixed_categories_and_decision_removal(screened):
    configure(screened)
    saved = flagged(screened, body='PRIVATE_BODY_MARKER\nSecond line.')
    newer = revise(screened.owner, saved, content(title='LATER_PRIVATE_DRAFT_MARKER'))
    response = queue(screened)
    row, = response.json()['notes']
    assert (row['revision'], row['content_sha256']) == (saved['revision'], saved['content_sha256'])
    small = row['revisions'][0]['publication']['screening']
    assert small == {'status': 'human_review', 'reason_code': 'model_concerns',
        'reason': 'Automated screening requested human attention.',
        'categories': [{'code': 'privacy_or_credentials', 'label': 'Privacy or credentials', 'count': 1}],
        'concern_count': 1, 'limitation_count': 0, 'verification': 'summary_only'}
    for value in ('PRIVATE_BODY_MARKER', 'LATER_PRIVATE_DRAFT_MARKER', 'Synthetic private material',
                  'Ask the author', 'provider_response_id', 'synthetic-response', 'usage', 'result_sha256',
                  'charged_micro_usd', 'lease_token', 'input_sha256'):
        assert value not in response.text
    opened = screened.reviewer.client.get(f'/api/notebook/notes/{saved["id"]}?revision=1')
    assert 'Synthetic private material' in opened.text and 'PRIVATE_BODY_MARKER' in opened.text
    path = f'/api/notebook/notes/{saved["id"]}/publication-reviews'
    bad = publication_body(saved, content_sha256=newer['content_sha256'], decision='approved', reason='Wrong revision.')
    assert screened.reviewer.post(path, bad).status_code == 409
    assert len(queue(screened).json()['notes']) == 1
    approve(screened.reviewer, saved)
    for kind in ('all', 'flagged', 'unflagged', 'attention'):
        assert queue(screened, kind).json()['notes'] == []


@pytest.mark.parametrize('mismatch', ['hash', 'author', 'revision'])
def test_wrong_job_identity_never_flags_submitted_revision(screened, mismatch):
    configure(screened)
    saved = flagged(screened)
    other = create(screened.owner)
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, saved)
        if mismatch == 'hash':
            job.content_sha256 = '0' * 64
        elif mismatch == 'author':
            job.author_id = screened.reader.identifier
        else:
            job.revision_id = session.scalar(select(notebook.NotebookRevision.id)
                .where(notebook.NotebookRevision.note_id == other['id']))
    assert queue(screened).json()['notes'] == []
    plain, = queue(screened, 'unflagged').json()['notes']
    assert plain['id'] == saved['id']
    assert 'screening' not in plain['revisions'][0]['publication']
    manual, = queue(screened, 'attention').json()['notes']
    assert manual['revisions'][0]['publication']['screening']['attention_reason'] == 'manual_review_needed'


def test_latest_job_and_attempt_override_earlier_flags_deterministically(screened):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        old = job_for(session, saved)
        policy = deepcopy(old.policy)
        policy['model_configuration']['test_variant'] = 'later-policy'
        new = ScreeningJob(id='00000000-0000-0000-0000-000000000000', publication_id=old.publication_id,
            revision_id=old.revision_id, author_id=old.author_id, content_sha256=old.content_sha256,
            policy_id=digest(policy), policy=policy, status='queued', attempt_count=0,
            available_at=old.available_at, created_at=old.created_at)
        session.add(new)
        old_id, new_id = old.id, new.id
    # Tied timestamps use ascending stable job ID, matching the full reader.
    assert queue(screened).json()['notes'] == []
    plain, = queue(screened, 'unflagged').json()['notes']
    assert plain['revisions'][0]['publication']['screening']['status'] == 'queued'
    with screened.app.state.db.transaction(write=True) as session:
        session.get(ScreeningJob, new_id).created_at -= 1
    assert len(queue(screened).json()['notes']) == 1
    with screened.app.state.db.transaction(write=True) as session:
        old = session.get(ScreeningJob, old_id)
        first = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == old.id))
        session.add(ScreeningAttempt(id=str(uuid4()), job_id=old.id, number=first.number + 1,
            lease_token=str(uuid4()), budget_month=first.budget_month,
            reserved_micro_usd=0, charged_micro_usd=0, usage_known=True,
            started_at=first.started_at + 1, lease_expires_at=first.lease_expires_at + 1,
            finished_at=first.finished_at + 1, error_code='provider_timeout'))
    assert queue(screened).json()['notes'] == []
    plain, = queue(screened, 'unflagged').json()['notes']
    small = plain['revisions'][0]['publication']['screening']
    assert small['reason_code'] == 'provider_timeout' and small['categories'] == []


@pytest.mark.parametrize('damage', ['empty_error', 'unknown_status', 'unfinished_attempt', 'missing_digest'])
def test_ineligible_worker_state_never_leaks_category_claims_into_ordinary_queue(screened, damage):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, saved)
        attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id))
        if damage == 'empty_error': job.error_code = ''
        if damage == 'unknown_status': job.status = 'unknown'
        if damage == 'unfinished_attempt': attempt.finished_at = None
        if damage == 'missing_digest': attempt.result_sha256 = None
    assert queue(screened).json()['notes'] == []
    row, = queue(screened, 'unflagged').json()['notes']
    small = row['revisions'][0]['publication']['screening']
    assert small['categories'] == [] and small['concern_count'] == 0


@pytest.mark.parametrize('damage', ['result_digest', 'policy_digest', 'input_format', 'input_digest',
                                    'category', 'excess', 'prose', 'field', 'line_type'])
def test_damaged_summary_keeps_pending_triage_without_category_claims(screened, damage):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, saved)
        attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id))
        value = deepcopy(attempt.result)
        if damage == 'policy_digest':
            job.policy = {**job.policy, 'extra': 'damaged'}
        if damage == 'input_format': value['input_format_version'] = 'unknown'
        if damage == 'input_digest': value['input_sha256'] = 'invalid'
        if damage == 'category': value['concerns'][0]['category'] = 'MODEL_CONTROLLED_LABEL'
        if damage == 'excess': value['concerns'] *= 5
        if damage == 'prose': value['concerns'][0]['explanation'] = 'x' * 601
        if damage == 'field': value['concerns'][0]['field_id'] = 'f99999'
        if damage == 'line_type': value['concerns'][0]['line_start'] = True
        attempt.result = value
        attempt.result_sha256 = '0' * 64 if damage == 'result_digest' else digest(value)
    result = queue(screened)
    row, = result.json()['notes']
    small = row['revisions'][0]['publication']['screening']
    assert small['reason_code'] == 'findings_unavailable'
    assert small['categories'] == [] and small['concern_count'] == small['limitation_count'] == 0
    assert 'MODEL_CONTROLLED_LABEL' not in result.text and 'Synthetic private material' not in result.text
    assert queue(screened, 'unflagged').json()['notes'] == []


def test_summary_digest_checks_do_not_claim_to_recompute_the_input(screened):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, saved)
        attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id))
        value = {**attempt.result, 'input_sha256': '0' * 64}
        attempt.result, attempt.result_sha256 = value, digest(value)
    row, = queue(screened).json()['notes']
    assert row['revisions'][0]['publication']['screening']['verification'] == 'summary_only'
    detail = screened.reviewer.client.get(f'/api/notebook/notes/{saved["id"]}?revision=1').json()
    findings = detail['note']['revisions'][0]['publication']['screening']['reviewer']
    assert findings['concerns'] == [] and 'could not be verified' in findings['unavailable']


def test_limitations_and_categories_are_fixed_bounded_counts(screened):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, saved)
        attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id))
        value = deepcopy(attempt.result)
        concern = value['concerns'][0]
        value['concerns'] = [concern, deepcopy(concern), {**concern, 'category': 'deceptive_content'}]
        value['limitations'] = [{'field_id': concern['field_id'], 'reason': 'MODEL_PRIVATE_LIMITATION'}]
        attempt.result, attempt.result_sha256 = value, digest(value)
    response = queue(screened)
    small = response.json()['notes'][0]['revisions'][0]['publication']['screening']
    assert small['reason_code'] == 'model_concerns_and_limitations'
    assert small['categories'] == [
        {'code': 'privacy_or_credentials', 'label': 'Privacy or credentials', 'count': 2},
        {'code': 'deceptive_content', 'label': 'Deceptive content', 'count': 1}]
    assert small['concern_count'] == 3 and small['limitation_count'] == 1
    assert 'MODEL_PRIVATE_LIMITATION' not in response.text
    with screened.app.state.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt))
        value = {**attempt.result, 'concerns': []}
        attempt.result, attempt.result_sha256 = value, digest(value)
    small = queue(screened).json()['notes'][0]['revisions'][0]['publication']['screening']
    assert small['reason_code'] == 'screening_limitations' and small['categories'] == []
    assert small['concern_count'] == 0 and small['limitation_count'] == 1


@pytest.mark.parametrize('kind', ['flagged', 'attention'])
def test_summary_batching_never_hydrates_bodies_and_filter_compiles_for_postgresql(screened, monkeypatch, kind):
    configure(screened)
    for number in range(5):
        flagged(screened, title=f'Flag {number}', body='PRIVATE_LARGE_BODY_MARKER' + 'b' * 39900)
    from crucible import publication_screening_policy
    def no_prepare(*args, **kwargs):
        pytest.fail('Queue must not prepare or hydrate submitted bodies')
    monkeypatch.setattr(publication_screening_policy, 'prepare_input', no_prepare)

    def capture(limit):
        statements, expressions = [], []
        def sql(_connection, _cursor, statement, _parameters, _context, _many): statements.append(statement)
        def expression(_connection, clause, *args): expressions.append(clause)
        event.listen(screened.app.state.db.engine, 'before_cursor_execute', sql)
        event.listen(screened.app.state.db.engine, 'before_execute', expression)
        try:
            response = queue(screened, kind, limit=limit)
        finally:
            event.remove(screened.app.state.db.engine, 'before_cursor_execute', sql)
            event.remove(screened.app.state.db.engine, 'before_execute', expression)
        return response, statements, expressions
    _, one, _ = capture(1)
    response, many, expressions = capture(5)
    assert len(response.json()['notes']) == 5 and len(one) == len(many)
    assert 'PRIVATE_LARGE_BODY_MARKER' not in response.text and len(response.content) < 15000
    for statement in many:
        projection = statement.split('FROM', 1)[0]
        assert 'notebook_revisions.content AS ' not in projection
        assert 'notebook_revisions.source_revision AS ' not in projection
        assert 'notebook_notes.source_metadata' not in projection
        assert 'publication_screening_attempts.charged_micro_usd' not in projection
        assert 'publication_screening_attempts.lease_token' not in projection
    main, = [query for query in expressions if
             'ORDER BY notebook_publications.requested_at, notebook_publications.id' in str(query)]
    assert main._limit_clause.value == 6 and main._offset_clause is None
    pg = str(main.compile(dialect=postgresql.dialect()))
    assert 'EXISTS' in pg and 'publication_screening_jobs' in pg and '->>' in pg


def test_private_queue_browser_reviewer_boundary_and_self_review_preserved(screened):
    configure(screened)
    saved = flagged(screened, actor=screened.reviewer, title='PRIVATE_FLAGGED_TITLE')
    row, = queue(screened).json()['notes']
    assert row['can_review'] is False and row['can_self_review'] is False
    assert screened.reviewer.post(f'/api/notebook/notes/{saved["id"]}/publication-reviews',
        publication_body(saved, decision='approved', reason='Self decision')).status_code == 403
    client = agent(screened.app, screened.reviewer, ['notebook:read'])
    try:
        for kind in ('flagged', 'unflagged', 'all', 'attention'):
            path = '/api/notebook/review-queue?summary=true&screening=' + kind
            assert client.get(path).status_code == 403
            assert screened.owner.client.get(path).status_code == 403
            assert screened.reader.client.get(path).status_code == 403
            assert TestClient(screened.app).get(path).status_code == 401
    finally:
        client.close()
    with screened.app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, screened.reviewer.identifier, 'revoked', screened.now[0])
    statements = []
    def sql(_connection, _cursor, statement, _parameters, _context, _many): statements.append(statement)
    event.listen(screened.app.state.db.engine, 'before_cursor_execute', sql)
    try:
        response = screened.reviewer.client.get('/api/notebook/review-queue?summary=true&screening=flagged')
    finally:
        event.remove(screened.app.state.db.engine, 'before_cursor_execute', sql)
    assert response.status_code == 401 and 'PRIVATE_FLAGGED_TITLE' not in response.text
    assert not any('publication_screening_' in statement or 'notebook_publications' in statement for statement in statements)


@pytest.mark.parametrize('params', [
    {'screening': 'unknown'}, {'screening': ''}, {'screening': 'flagged', 'after': str(uuid4())},
    {'screening': 'unflagged', 'limit': 0}, {'screening': 'flagged', 'limit': 101},
])
def test_filter_bounds_and_cursor_validation(screened, params):
    response = screened.reviewer.client.get('/api/notebook/review-queue', params={'summary': 'true', **params})
    assert response.status_code == 422


def start_running(system):
    """Reserve a real durable worker lease without calling a provider."""
    from crucible import publication_screening_jobs as jobs
    from crucible.publication_screening_worker import policy_for
    settings = system.app.state.settings
    return jobs.claim(system.app.state.db, policy=policy_for(settings), enabled=True,
        monthly_budget_micro_usd=settings.publication_screening_monthly_budget_micro_usd,
        now=system.now[0], lease_seconds=120)


def test_attention_combines_flags_fallback_shadow_and_legacy_before_pagination(screened):
    configure(screened)
    concern = flagged(screened, title='Model concern')
    failure = flagged(screened, title='Operational fallback')
    with screened.app.state.db.transaction(write=True) as session:
        job_for(session, failure).error_code = 'provider_unavailable'
    configure(screened, mode='shadow')
    shadow = submitted(screened, title='Shadow pass still needs a human')
    assert run(screened, SyntheticProvider())['status'] == 'completed'
    configure(screened)
    legacy = submitted(screened, title='Legacy manual request')
    with screened.app.state.db.transaction(write=True) as session:
        session.delete(job_for(session, legacy))
    running = submitted(screened, title='Active running check')
    lease = start_running(screened)
    assert lease is not None
    queued = submitted(screened, title='Active queued check')
    budget = submitted(screened, title='Budget unavailable')
    with screened.app.state.db.transaction(write=True) as session:
        job = job_for(session, budget)
        job.status, job.error_code = 'human_review', 'budget_exhausted'
        order = [queued, concern, running, failure, legacy, shadow, budget]
        for number, saved in enumerate(order):
            publication = session.scalar(select(notebook.NotebookPublication)
                .join(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == saved['id']))
            publication.requested_at = screened.now[0] + number
    attention = pages(screened, 'attention', 2)
    assert [item['id'] for item in attention] == [i['id'] for i in (concern, failure, legacy, shadow, budget)]
    small = {item['id']: item['revisions'][0]['publication']['screening'] for item in attention}
    assert small[concern['id']]['attention_reason'] == 'content_findings'
    assert small[concern['id']]['attention_label'] == 'Screening requested review'
    for saved in (failure, budget):
        data = small[saved['id']]
        assert data['attention_reason'] == 'screening_unavailable'
        assert data['attention_label'] == 'Screening unavailable' and data['categories'] == []
    for saved in (legacy, shadow):
        data = small[saved['id']]
        assert data['attention_reason'] == data['reason_code'] == 'manual_review_needed'
        assert data['attention_label'] == 'Manual review needed' and data['categories'] == []
    assert small[failure['id']]['reason_code'] == 'provider_unavailable'
    assert small[budget['id']]['reason_code'] == 'budget_exhausted'
    # Previous filter contracts still retain active automated work for API clients.
    assert {item['id'] for item in queue(screened, 'all').json()['notes']} == {i['id'] for i in order}
    assert {item['id'] for item in queue(screened, 'unflagged').json()['notes']} >= {running['id'], queued['id']}
    approve(screened.reviewer, concern)
    assert concern['id'] not in {item['id'] for item in queue(screened, 'attention').json()['notes']}
    # Reads never cancel or modify the live worker lease.
    with screened.app.state.db.transaction() as session:
        control = session.get(ScreeningControl, 1)
        assert control.active_job_id == lease.job_id and control.lease_token == lease.token
        assert session.get(ScreeningJob, lease.job_id).status == 'running'


@pytest.mark.parametrize('state,expected', [
    ('control_disabled', 'disabled'), ('control_policy_changed', 'policy_changed'),
    ('job_policy_changed', 'policy_changed'), ('running_expired', 'lease_expired'),
    ('running_missing', 'lease_expired'), ('running_disabled', 'disabled'),
    ('running_policy_changed', 'policy_changed'),
])
def test_attention_surfaces_nonexecutable_jobs_without_mutating_them(screened, state, expected):
    configure(screened)
    saved = submitted(screened)
    if state.startswith('running'):
        lease = start_running(screened)
        assert lease is not None
    assert queue(screened, 'attention').json()['notes'] == []
    with screened.app.state.db.transaction(write=True) as session:
        control = session.get(ScreeningControl, 1)
        job = job_for(session, saved)
        if state in ('control_disabled', 'running_disabled'):
            control.mode = 'disabled'
        elif state in ('control_policy_changed', 'running_policy_changed'):
            control.policy_id = '0' * 64
        elif state == 'job_policy_changed':
            policy = {**job.policy, 'policy_version': 'legacy-policy'}
            job.policy, job.policy_id = policy, digest(policy)
        elif state == 'running_expired':
            control.lease_expires_at = screened.now[0]
        else:
            control.active_job_id = None
        before = {column.name: getattr(job, column.name) for column in job.__table__.columns}
    row, = queue(screened, 'attention').json()['notes']
    data = row['revisions'][0]['publication']['screening']
    assert data['attention_reason'] == 'screening_unavailable'
    assert data['attention_label'] == 'Screening unavailable'
    assert data['reason_code'] == expected and data['categories'] == []
    with screened.app.state.db.transaction() as session:
        job = job_for(session, saved)
        assert {column.name: getattr(job, column.name) for column in job.__table__.columns} == before


def test_attention_missing_control_and_disabled_runtime_do_not_strand_queued_requests(screened, monkeypatch):
    saved = submitted(screened)
    # Consent enqueued the request; an operator has not enabled dispatch yet.
    row, = queue(screened, 'attention').json()['notes']
    assert row['revisions'][0]['publication']['screening']['reason_code'] == 'disabled'
    configure(screened)
    assert queue(screened, 'attention').json()['notes'] == []
    from crucible.app import create_app
    from crucible import publication_screening_worker
    app = create_app(replace(screened.app.state.settings, publication_screening_mode='disabled'),
        clock=lambda: screened.now[0])
    reviewer = Actor(app, screened.reviewer.identifier)
    def no_policy(*args, **kwargs):
        pytest.fail('Disabled runtime must not prepare a provider policy')
    monkeypatch.setattr(publication_screening_worker, 'policy_for', no_policy)
    try:
        response = reviewer.client.get('/api/notebook/review-queue?summary=true&screening=attention')
        assert response.status_code == 200, response.text
        row, = response.json()['notes']
        assert row['id'] == saved['id']
        assert row['revisions'][0]['publication']['screening']['reason_code'] == 'disabled'
    finally:
        reviewer.client.close()
        app.state.db.engine.dispose()


def test_attention_corrupt_result_is_unavailable_not_a_content_accusation(screened):
    configure(screened)
    saved = flagged(screened)
    with screened.app.state.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt))
        attempt.result_sha256 = '0' * 64
    row, = queue(screened, 'attention').json()['notes']
    assert row['id'] == saved['id']
    data = row['revisions'][0]['publication']['screening']
    assert data['attention_reason'] == 'screening_unavailable'
    assert data['attention_label'] == 'Screening unavailable'
    assert data['reason_code'] == 'findings_unavailable' and data['categories'] == []
