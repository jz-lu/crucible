"""Publication locks, provenance and grants on the fixed disposable PG17 fixture.

Never accepts DATABASE_URL, provider keys, remote hosts or a caller DSN. The
shared fixture requires CRUCIBLE_METADATA_POSTGRES=1, validates the exact fixed
synthetic service identity, then creates/removes only unique test roles/schema.
No actual model provider is constructed or called.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import re
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import MetaData, event, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from crucible import beta_access, notebook, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Database, User
from crucible import publication_screening_jobs as jobs
from crucible import publication_screening
from crucible import publication_screening_adoption as adoption
from crucible import publication_screening_worker as worker
from crucible.publication_screening_models import (
    AutomaticPublication, ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob,
)
from crucible.publication_screening_policy import validate_verdict
from crucible.publication_screening_provider import ProviderResult, ScreeningProviderError
from test_notebook import Actor, content, create, publication_body, revise
from test_bounty_completion_migration import seed_historical_award
from test_publication_screening_integration import SyntheticProvider as SyntheticFindingProvider
from test_publication_screening_migration import SCREENING_TABLES, seed_publications
from test_researcher_profiles_migration import snapshot
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged


@pytest.fixture
def pg_screened(postgres, monkeypatch):
    command.upgrade(postgres.config, 'head')
    command.check(postgres.config)
    assert_original_unchanged(postgres)
    monkeypatch.setattr(registry, 'ADAPTERS', {key: value for key, value in registry.ADAPTERS.items()
        if key[0] in ('teaching-dh-kem', 'rsa-kem')})
    monkeypatch.setattr(registry, 'CURRENT', {'teaching-dh-kem': '7', 'rsa-kem': '4'})
    identifiers = [str(uuid4()) for _ in range(3)]
    now = [1793793600.0]  # Fixed synthetic UTC timestamp, never a production clock.
    settings = Settings(postgres.runtime_url, b'p' * 32, origins=('http://testserver',),
        reviewer_ids=(identifiers[1],), rate_limits=False, access_mode='beta',
        publication_screening_mode='automatic', publication_screening_model='gpt-6-luna')
    app = create_app(settings, clock=lambda: now[0], initialize=False)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in zip(identifiers, ('screen_author', 'screen_reviewer', 'screen_reader')):
            session.add(User(id=identifier, handle=handle, created_at=now[0]))
        session.flush()
        for identifier in identifiers:
            beta_access.set_membership(session, identifier, 'active', now[0])
    actors = [Actor(app, identifier) for identifier in identifiers]
    policy = worker.policy_for(settings)
    jobs.configure(app.state.db, policy=policy, mode='automatic',
                   monthly_budget_micro_usd=settings.publication_screening_monthly_budget_micro_usd,
                   now=now[0])
    try:
        yield SimpleNamespace(fixture=postgres, app=app, settings=settings, policy=policy,
            db=app.state.db, owner=actors[0], reviewer=actors[1], reader=actors[2], now=now)
    finally:
        for actor in actors:
            actor.client.close()
        app.state.db.engine.dispose()


def requested(system, note=None):
    note = note or create(system.owner, content(body='Synthetic public mathematical example.'))
    response = system.owner.post(f'/api/notebook/notes/{note["id"]}/publication-requests?summary=true',
        publication_body(note, screening_consent=True, screening_policy_id=system.policy.id))
    assert response.status_code == 200, response.text
    return note, response.json()['publication_request']['id']


class SyntheticProvider:
    def __init__(self, started=None, release=None):
        self.started, self.release, self.calls = started, release, 0

    def screen(self, prepared):
        self.calls += 1
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            assert self.release.wait(10), 'Synthetic provider was not released.'
        verdict = validate_verdict({'outcome': 'no_concern_found', 'concerns': [], 'limitations': []}, prepared)
        return ProviderResult(verdict=verdict, model='gpt-6-luna', response_id='synthetic-pg-response',
            usage={'input_tokens': 100, 'output_tokens': 30, 'cached_input_tokens': 0, 'reasoning_tokens': 0})


def run(system, provider):
    return worker.run_once(system.db, system.settings, provider, clock=lambda: system.now[0])


def test_pg17_worker_database_enforces_timeouts_and_recovers_after_cancellation(postgres):
    # Preserve the caller's schema/options while overriding unsafe inherited
    # timeout values. Only this fixture's fixed synthetic database is used.
    url = make_url(postgres.runtime_url).update_query_dict({
        'connect_timeout': '30',
        'options': (f'-c search_path={postgres.schema} -c application_name=synthetic-screening-worker '
                    '-c statement_timeout=60000 -c lock_timeout=60000 '
                    '-c idle_in_transaction_session_timeout=60000'),
    }).render_as_string(hide_password=False)
    db = worker.worker_database(url)
    connection_timeouts = []

    def capture_connect(_dialect, _record, _args, parameters):
        connection_timeouts.append(parameters['connect_timeout'])

    event.listen(db.engine, 'do_connect', capture_connect)
    try:
        with db.transaction() as session:
            assert session.execute(text('SELECT current_user, current_schema()')).one() == (
                postgres.runtime, postgres.schema)
            assert session.scalar(text('SHOW application_name')) == 'synthetic-screening-worker'
            assert session.scalar(text('SHOW statement_timeout')) == '10s'
            assert session.scalar(text('SHOW lock_timeout')) == '5s'
            assert session.scalar(text('SHOW idle_in_transaction_session_timeout')) == '10s'
        assert connection_timeouts == [5]

        # Short transaction-local bounds exercise real PostgreSQL cancellation
        # without waiting the production 5/10 seconds during every CI run.
        with pytest.raises(DBAPIError) as cancelled:
            with db.transaction() as session:
                session.execute(text("SET LOCAL statement_timeout='100ms'"))
                session.execute(text('SELECT pg_sleep(1)'))
        assert cancelled.value.orig.sqlstate == '57014'

        with postgres.owner_engine.begin() as connection:
            locked = connection.scalar(text('SELECT id FROM users ORDER BY id LIMIT 1 FOR UPDATE'))
            assert locked is not None
            with pytest.raises(DBAPIError) as blocked:
                with db.transaction() as session:
                    session.execute(text("SET LOCAL lock_timeout='100ms'"))
                    session.execute(text('SELECT id FROM users WHERE id=:id FOR UPDATE'), {'id': locked})
            assert blocked.value.orig.sqlstate == '55P03'

        # Both aborted transactions are rolled back and the pooled connection
        # returns with its enforced startup defaults, ready for another attempt.
        with db.transaction() as session:
            assert session.scalar(text('SELECT 1')) == 1
            assert session.scalar(text('SHOW statement_timeout')) == '10s'
            assert session.scalar(text('SHOW lock_timeout')) == '5s'
    finally:
        db.engine.dispose()


def test_pg17_flagged_queue_filters_before_pagination_and_keeps_exact_private_summaries(pg_screened):
    system = pg_screened
    first, first_id = requested(system, create(system.owner, content(body='FIRST_PRIVATE_BODY_SENTINEL')))
    assert run(system, SyntheticFindingProvider(flagged=True))['status'] == 'human_review'
    later = revise(system.owner, first, content(body='LATER_PRIVATE_BODY_SENTINEL'))
    system.now[0] += 1
    failed, _ = requested(system, create(system.owner, content(body='FAILED_PRIVATE_BODY_SENTINEL')))

    class FailedProvider:
        def screen(self, prepared):
            raise ScreeningProviderError('provider_authentication_failed')

    assert run(system, FailedProvider())['status'] == 'human_review'
    system.now[0] += 1
    second, second_id = requested(system, create(system.owner, content(body='SECOND_PRIVATE_BODY_SENTINEL')))
    assert run(system, SyntheticFindingProvider(flagged=True))['status'] == 'human_review'
    system.now[0] += 1
    queued, _ = requested(system, create(system.owner, content(body='QUEUED_PRIVATE_BODY_SENTINEL')))

    def page(screening=None, **params):
        statements = []

        def inspect_read(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith('SELECT'):
                statements.append(statement)
                # JSON summary-field extraction is allowed; full revision
                # content or imported private metadata must never be selected.
                assert not re.search(r'\bnotebook_revisions\.content\s*(?:,|\bAS\b|\bFROM\b)', statement)
                assert 'notebook_notes.source_metadata' not in statement

        event.listen(system.db.engine, 'before_cursor_execute', inspect_read)
        try:
            response = system.reviewer.client.get('/api/notebook/review-queue', params={
                'summary': 'true', **({'screening': screening} if screening else {}), **params})
        finally:
            event.remove(system.db.engine, 'before_cursor_execute', inspect_read)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        data = response.json()
        assert statements
        for private in ('PRIVATE_BODY_SENTINEL', 'Synthetic private material', 'explanation',
                        'provider_response_id', 'synthetic-response', 'input_tokens', 'lease_token'):
            assert private not in json.dumps(data)
        return data

    default = page()
    assert default == page('all')
    assert [row['id'] for row in default['notes']] == [first['id'], failed['id'], second['id'], queued['id']]
    first_page = page('flagged', limit=1)
    assert first_page['screening'] == 'flagged'
    assert first_page['next_after'] == first_id
    assert [row['id'] for row in first_page['notes']] == [first['id']]
    record = first_page['notes'][0]
    assert record['revision'] == first['revision'] != later['revision']
    assert record['content_sha256'] == first['content_sha256']
    summary = record['revisions'][0]['publication']['screening']
    assert summary['verification'] == 'summary_only'
    assert summary['reason_code'] == 'model_concerns'
    assert summary['categories'] == [{'code': 'privacy_or_credentials', 'label': 'Privacy or credentials', 'count': 1}]
    assert (summary['concern_count'], summary['limitation_count']) == (1, 0)
    second_page = page('flagged', limit=1, after=first_page['next_after'])
    assert [row['id'] for row in second_page['notes']] == [second['id']]
    assert second_page['next_after'] is None
    ordinary = page('unflagged')
    assert [row['id'] for row in ordinary['notes']] == [failed['id'], queued['id']]
    assert ordinary['notes'][0]['revisions'][0]['publication']['screening']['reason_code'] == 'provider_authentication_failed'
    assert ordinary['notes'][1]['revisions'][0]['publication']['screening']['status'] == 'queued'
    for actor in (system.owner, system.reader):
        assert actor.client.get('/api/notebook/review-queue?screening=flagged&summary=true').status_code == 403
    assert system.reader.client.get(f'/api/notebook/notes/{first["id"]}').status_code == 404
    assert system.reviewer.client.get('/api/notebook/review-queue?screening=unknown').status_code == 422

    # Corruption never exposes a category accusation. The worker's durable
    # triage status still keeps the pending request available to a reviewer.
    with system.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt).join(ScreeningJob)
            .where(ScreeningJob.publication_id == first_id))
        original_digest = attempt.result_sha256
        attempt.result_sha256 = '0' * 64
    unavailable = page('flagged', limit=1)['notes'][0]['revisions'][0]['publication']['screening']
    assert unavailable['reason_code'] == 'findings_unavailable' and unavailable['categories'] == []
    with system.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt).join(ScreeningJob)
            .where(ScreeningJob.publication_id == first_id))
        attempt.result_sha256 = original_digest

    # PostgreSQL must not CAST malformed JSON coverage into BOOLEAN during
    # triage: that would fail the entire queue instead of degrading this row.
    with system.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt).join(ScreeningJob)
            .where(ScreeningJob.publication_id == first_id))
        original_result = attempt.result
        attempt.result = {**original_result, 'full_coverage': 'NOT_A_BOOLEAN'}
        attempt.result_sha256 = jobs.digest(attempt.result)
    unavailable = page('flagged', limit=1)['notes'][0]['revisions'][0]['publication']['screening']
    assert unavailable['reason_code'] == 'findings_unavailable' and unavailable['categories'] == []
    with system.db.transaction(write=True) as session:
        attempt = session.scalar(select(ScreeningAttempt).join(ScreeningJob)
            .where(ScreeningJob.publication_id == first_id))
        attempt.result, attempt.result_sha256 = original_result, original_digest

    decision = system.reviewer.post(f'/api/notebook/notes/{first["id"]}/publication-reviews',
        publication_body(first, decision='changes_requested', reason='Synthetic review asks for a revision.'))
    assert decision.status_code == 200, decision.text
    assert [row['id'] for row in page('flagged')['notes']] == [second['id']]
    assert first['id'] not in [row['id'] for row in page()['notes']]

    # A flag cannot transfer to a different exact content pin or override a
    # newer attempt. These are deliberately synthetic durable history rows.
    with system.db.transaction(write=True) as session:
        job = session.scalar(select(ScreeningJob).where(ScreeningJob.publication_id == second_id))
        job_id, original_pin = job.id, job.content_sha256
        job.content_sha256 = '0' * 64
    assert page('flagged')['notes'] == []
    with system.db.transaction(write=True) as session:
        job = session.get(ScreeningJob, job_id)
        job.content_sha256 = original_pin
        previous = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job_id))
        latest_id = str(uuid4())
        session.add(ScreeningAttempt(id=latest_id, job_id=job_id, number=previous.number + 1,
            lease_token=str(uuid4()), budget_month=previous.budget_month, reserved_micro_usd=0,
            charged_micro_usd=0, usage_known=True, started_at=system.now[0],
            lease_expires_at=system.now[0] + 1, finished_at=system.now[0],
            error_code='provider_unconfigured'))
    assert page('flagged')['notes'] == []
    assert second['id'] in [row['id'] for row in page('unflagged')['notes']]
    with system.db.transaction(write=True) as session:
        session.delete(session.get(ScreeningAttempt, latest_id))
    assert [row['id'] for row in page('flagged')['notes']] == [second['id']]
    with system.db.transaction(write=True) as session:
        publication = session.get(notebook.NotebookPublication, second_id)
        jobs.enqueue(session, publication, policy=replace(system.policy, policy_version='synthetic-next-policy'),
                     now=system.now[0] + 1)
    assert page('flagged')['notes'] == []
    assert [row['id'] for row in page('unflagged')['notes']] == [failed['id'], second['id'], queued['id']]


def test_pg17_attention_queue_paginates_human_work_without_stranding_disabled_automation(pg_screened):
    system = pg_screened
    flagged, flagged_id = requested(system)
    assert run(system, SyntheticFindingProvider(flagged=True))['status'] == 'human_review'
    system.now[0] += 1
    failed, failed_id = requested(system)

    class FailedProvider:
        def screen(self, prepared):
            raise ScreeningProviderError('provider_authentication_failed')

    assert run(system, FailedProvider())['status'] == 'human_review'
    system.now[0] += 1
    evaluated, evaluated_id = requested(system)

    def configure(mode='automatic', policy=None):
        jobs.configure(system.db, policy=policy or system.policy, mode=mode,
            monthly_budget_micro_usd=system.settings.publication_screening_monthly_budget_micro_usd,
            now=system.now[0])

    configure('shadow')
    assert run(system, SyntheticProvider())['status'] == 'completed'
    configure()
    system.now[0] += 1
    running, running_id = requested(system)
    lease = jobs.claim(system.db, policy=system.policy, enabled=True,
        monthly_budget_micro_usd=system.settings.publication_screening_monthly_budget_micro_usd,
        now=system.now[0])
    assert lease is not None and lease.publication_id == running_id
    system.now[0] += 1
    queued, queued_id = requested(system)
    system.now[0] += 1
    legacy, legacy_id = requested(system)
    order = [queued_id, flagged_id, running_id, failed_id, evaluated_id, legacy_id]
    with system.db.transaction(write=True) as session:
        session.delete(session.scalar(select(ScreeningJob).where(ScreeningJob.publication_id == legacy_id)))
        for index, identifier in enumerate(order):
            session.get(notebook.NotebookPublication, identifier).requested_at = system.now[0] + index

    def page(actor=None, **params):
        response = (actor or system.reviewer).client.get('/api/notebook/review-queue', params={
            'summary': 'true', 'screening': 'attention', **params})
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['screening'] == 'attention'
        for private in ('Synthetic public mathematical example.', 'Synthetic private material',
                        'explanation', 'input_tokens', 'lease_token'):
            assert private not in response.text
        return response.json()

    first = page(limit=2)
    assert [row['id'] for row in first['notes']] == [flagged['id'], failed['id']]
    assert first['next_after'] == failed_id
    fallback = first['notes'][1]['revisions'][0]['publication']['screening']
    assert fallback['reason_code'] == 'provider_authentication_failed'
    assert fallback['attention_reason'] == 'screening_unavailable'
    assert fallback['categories'] == [] and fallback['concern_count'] == 0
    second = page(limit=2, after=first['next_after'])
    assert [row['id'] for row in second['notes']] == [evaluated['id'], legacy['id']]
    assert second['next_after'] is None
    evaluation = second['notes'][0]['revisions'][0]['publication']['screening']
    assert evaluation['status'] == 'completed' and evaluation['categories'] == []
    assert evaluation['attention_reason'] == 'manual_review_needed'
    manual = second['notes'][1]['revisions'][0]['publication']['screening']
    assert manual['attention_reason'] == manual['reason_code'] == 'manual_review_needed'
    assert manual['categories'] == []
    assert system.owner.client.get('/api/notebook/review-queue?screening=attention').status_code == 403

    expected_all = [queued['id'], flagged['id'], running['id'], failed['id'], evaluated['id'], legacy['id']]

    def assert_all_need_attention(response):
        assert [row['id'] for row in response['notes']] == expected_all
        for row in response['notes']:
            if row['id'] in (queued['id'], running['id']):
                summary = row['revisions'][0]['publication']['screening']
                assert summary['categories'] == []
                assert summary['concern_count'] == summary['limitation_count'] == 0
                assert summary['attention_reason'] == 'screening_unavailable'
                assert summary['reason_code'] in ('disabled', 'policy_changed')

    # HTTP reads must not hide work when the operator has stopped automation;
    # configuring control does not itself rewrite queued/running jobs.
    configure('disabled')
    assert_all_need_attention(page())
    with system.db.transaction() as session:
        states = dict(session.execute(select(ScreeningJob.publication_id, ScreeningJob.status)
            .where(ScreeningJob.publication_id.in_([queued_id, running_id]))).all())
        assert states == {queued_id: 'queued', running_id: 'running'}
    configure()
    assert len(page()['notes']) == 4

    disabled_app = create_app(replace(system.settings, publication_screening_mode='disabled'),
        clock=lambda: system.now[0], initialize=False)
    disabled_reviewer = Actor(disabled_app, system.reviewer.identifier)
    try:
        assert_all_need_attention(page(disabled_reviewer))
    finally:
        disabled_reviewer.client.close()
        disabled_app.state.db.engine.dispose()

    configure(policy=replace(system.policy, policy_version='synthetic-updated-policy'))
    assert_all_need_attention(page())
    configure()

    # Simulate a read at lease expiry, then restore this synthetic test clock.
    # The queue itself must not reap or rewrite the running attempt.
    current_time = system.now[0]
    system.now[0] = lease.expires_at
    stale = next(row for row in page()['notes'] if row['id'] == running['id'])
    stale_summary = stale['revisions'][0]['publication']['screening']
    assert stale_summary['reason_code'] == 'lease_expired'
    assert stale_summary['attention_reason'] == 'screening_unavailable' and stale_summary['categories'] == []
    system.now[0] = current_time
    assert len(page()['notes']) == 4
    jobs.fail(system.db, lease, code='provider_unconfigured', retryable=False,
              now=system.now[0], actual_cost_micro_usd=0)
    assert [row['id'] for row in page()['notes']] == [flagged['id'], running['id'], failed['id'], evaluated['id'], legacy['id']]
    decision = system.reviewer.post(f'/api/notebook/notes/{flagged["id"]}/publication-reviews',
        publication_body(flagged, decision='changes_requested', reason='Synthetic attention review asks for revision.'))
    assert decision.status_code == 200, decision.text
    assert flagged['id'] not in [row['id'] for row in page()['notes']]


def test_pg17_migration_preserves_existing_revisions_and_applies_runtime_backup_grants(postgres):
    command.upgrade(postgres.config, '0029')
    assert_original_unchanged(postgres)
    metadata = MetaData()
    metadata.reflect(bind=postgres.owner_engine)
    metadata.remove(metadata.tables['alembic_version'])
    with postgres.owner_engine.begin() as connection:
        publications = seed_publications(connection, metadata, postgres.ids)
        seed_historical_award(connection, metadata, postgres.ids)
    with postgres.owner_engine.connect() as connection:
        before = snapshot(connection, metadata)
        sequences_before = inspect(connection).get_sequence_names()
        sequence = connection.scalar(text("SELECT pg_get_serial_sequence('notebook_numbers', 'number')"))
        sequence_state = connection.exec_driver_sql(f'SELECT last_value, is_called FROM {sequence}').one()
    assert sequences_before == ['notebook_numbers_number_seq']
    # Preserve the exact existing backup sequence grant. The screening singleton
    # always uses ID1 and must not silently introduce another SERIAL sequence.
    with postgres.owner_engine.begin() as connection:
        connection.exec_driver_sql(f'GRANT SELECT ON SEQUENCE {sequence} TO {postgres.backup}')
    old_tables = set(metadata.tables)
    command.upgrade(postgres.config, '0031')
    command.check(postgres.config)
    assert next(column for column in inspect(postgres.owner_engine).get_columns('bounty_awards')
                if column['name'] == 'poster_decision_id')['nullable'] is True
    assert set(inspect(postgres.owner_engine).get_table_names()) - {'alembic_version'} == old_tables | SCREENING_TABLES
    with postgres.runtime_engine.connect() as connection:
        assert snapshot(connection, metadata) == before
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0031'
        assert inspect(connection).get_sequence_names() == sequences_before
        assert connection.scalar(text("SELECT pg_get_serial_sequence('publication_screening_control', 'id')")) is None
        rows = dict(connection.execute(text('SELECT id, decision_source FROM notebook_publications')).all())
        for label, identifier in publications.items():
            assert rows[identifier] == ('human' if label in ('approved', 'changes', 'self_review') else None)
        assert connection.execute(text('SELECT mode, monthly_budget_micro_usd FROM publication_screening_control')).one() == ('disabled', 0)
        assert connection.scalar(text('SELECT count(*) FROM publication_screening_jobs')) == 0
        for table in SCREENING_TABLES:
            assert connection.scalar(text('SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=CAST(:table AS regclass)'),
                                     {'table': table}) == postgres.owner
            for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE'):
                assert connection.scalar(text('SELECT has_table_privilege(current_user,:table,:privilege)'),
                                         {'table': table, 'privilege': privilege})
            for privilege in ('TRUNCATE', 'REFERENCES', 'TRIGGER'):
                assert not connection.scalar(text('SELECT has_table_privilege(current_user,:table,:privilege)'),
                                             {'table': table, 'privilege': privilege})
    with postgres.backup_engine.connect() as connection:
        assert connection.scalar(text('SHOW transaction_read_only')) == 'on'
        assert connection.exec_driver_sql(f'SELECT last_value, is_called FROM {sequence}').one() == sequence_state
        for table in SCREENING_TABLES:
            assert connection.scalar(text('SELECT has_table_privilege(current_user,:table,:privilege)'),
                                     {'table': table, 'privilege': 'SELECT'})
            for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
                assert not connection.scalar(text('SELECT has_table_privilege(current_user,:table,:privilege)'),
                                             {'table': table, 'privilege': privilege})
    with pytest.raises(DBAPIError) as denied:
        with postgres.runtime_engine.begin() as connection:
            connection.exec_driver_sql('ALTER TABLE publication_screening_jobs ADD COLUMN forbidden integer')
    assert denied.value.orig.sqlstate == '42501'
    with pytest.raises(DBAPIError) as readonly:
        with postgres.backup_engine.begin() as connection:
            connection.exec_driver_sql('UPDATE publication_screening_control SET mode=mode WHERE false')
    assert readonly.value.orig.sqlstate in ('25006', '42501')
    command.downgrade(postgres.config, '0029')
    with postgres.runtime_engine.connect() as connection:
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0029'
        assert snapshot(connection, metadata) == before
        assert inspect(connection).get_sequence_names() == sequences_before
    assert not SCREENING_TABLES & set(inspect(postgres.runtime_engine).get_table_names())
    assert next(column for column in inspect(postgres.owner_engine).get_columns('bounty_awards')
                if column['name'] == 'poster_decision_id')['nullable'] is False


def test_pg17_legacy_publication_writer_survives_additive_schema_but_old_head_preflight_refuses(postgres):
    from scripts import beta_preflight

    command.upgrade(postgres.config, '0029')
    old_metadata = MetaData()
    old_metadata.reflect(bind=postgres.owner_engine)
    old_publications = old_metadata.tables['notebook_publications']
    assert 'decision_source' not in old_publications.c
    command.upgrade(postgres.config, '0031')
    reviewer_id = str(uuid4())
    # The pre-screening writer does not know about decision_source. Its exact
    # old insert/update shape remains valid during the additive transition.
    with postgres.runtime_engine.begin() as connection:
        publications = seed_publications(connection, old_metadata, postgres.ids)
        award = seed_historical_award(connection, old_metadata, postgres.ids)
        connection.execute(old_metadata.tables['users'].insert().values(
            id=reviewer_id, handle='legacy_transition_reviewer', created_at=20))
        connection.execute(old_publications.update().where(old_publications.c.id == publications['pending'])
            .values(decision='approved', reviewer_id=reviewer_id, reviewed_at=21,
                    reason='Synthetic independent legacy review.', review_key=str(uuid4()), review_sha256='f' * 64))
        assert connection.scalar(text('SELECT decision_source FROM notebook_publications WHERE id=:id'),
                                 {'id': publications['pending']}) is None
        visible = connection.execute(select(notebook.NotebookPublication.id)
            .join(notebook.NotebookRevision, notebook.NotebookRevision.id == notebook.NotebookPublication.revision_id)
            .where(publication_screening.approved_clause())).scalars().all()
        assert visible == [publications['pending']]

    settings = Settings(postgres.runtime_url, b'c' * 32, reviewer_ids=(), access_admin_ids=())
    with postgres.runtime_engine.connect() as connection:
        new = beta_preflight.inspect_database(connection, settings, ['0031'], None)
        for old_head in ('0029', '0030'):
            old = beta_preflight.inspect_database(connection, settings, [old_head], None)
            assert old == [{'check': 'database_schema', 'status': 'fail',
                           'message': "Database schema does not match this checkout's migration head."}]
        assert new[0]['check'] == 'database_schema' and new[0]['status'] == 'pass'
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0031'

    # The complete release also contains 0030. Once an independent award has no
    # poster signoff, a full rollback must refuse rather than fabricate history.
    with postgres.runtime_engine.begin() as connection:
        connection.execute(text('UPDATE bounty_awards SET poster_decision_id=NULL WHERE task_id=:id'),
                           {'id': award['task']})
    with pytest.raises(RuntimeError, match='Preserve independent-reviewer bounty awards'):
        command.downgrade(postgres.config, '0029')
    with postgres.runtime_engine.connect() as connection:
        # Transactional PostgreSQL DDL also rolls back the preceding 0031 drops.
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0031'
        assert SCREENING_TABLES <= set(inspect(connection).get_table_names())
        assert connection.scalar(text('SELECT poster_decision_id FROM bounty_awards WHERE task_id=:id'),
                                 {'id': award['task']}) is None


def test_pg17_competing_workers_reserve_exactly_one_global_call(pg_screened):
    system = pg_screened
    for _ in range(2):
        requested(system)
    barrier = Barrier(2, timeout=5)
    def claim(_):
        barrier.wait()
        return jobs.claim(system.db, policy=system.policy, enabled=True,
            monthly_budget_micro_usd=system.settings.publication_screening_monthly_budget_micro_usd,
            now=system.now[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, range(2)))
    assert sum(lease is not None for lease in leases) == 1
    lease = next(lease for lease in leases if lease is not None)
    with system.db.transaction() as session:
        budget = session.scalar(select(ScreeningBudget))
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (0, system.policy.reserve_micro_usd)
        assert session.scalar(select(func.count()).select_from(ScreeningAttempt)) == 1
        assert session.scalar(select(func.count()).select_from(ScreeningJob).where(ScreeningJob.status == 'running')) == 1
        assert session.get(ScreeningControl, 1).lease_token == lease.token
    jobs.fail(system.db, lease, code='provider_unconfigured', retryable=False,
              now=system.now[0], actual_cost_micro_usd=0)


def test_pg17_operator_kill_during_provider_call_prevents_publication(pg_screened):
    system = pg_screened
    note, publication_id = requested(system)
    started, release = Event(), Event()
    provider = SyntheticProvider(started, release)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, system, provider)
        try:
            assert started.wait(5)
            # This can commit during the model call: no transaction spans send.
            jobs.configure(system.db, policy=worker.policy_for(replace(system.settings,
                publication_screening_mode='disabled')), mode='disabled', monthly_budget_micro_usd=0,
                now=system.now[0])
        finally:
            release.set()
        assert future.result(timeout=15)['status'] == 'human_review'
    with system.db.transaction() as session:
        assert session.get(notebook.NotebookPublication, publication_id).decision is None
        assert session.get(AutomaticPublication, publication_id) is None
        assert session.scalar(select(ScreeningAttempt.charged_micro_usd)) is not None
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404


def test_pg17_human_review_holding_note_lock_wins_over_late_model_pass(pg_screened):
    system = pg_screened
    note, publication_id = requested(system)
    started, provider_release, human_locked, human_commit, worker_waiting = [Event() for _ in range(5)]
    provider = SyntheticProvider(started, provider_release)

    def before(connection, cursor, statement, parameters, context, many):
        if human_locked.is_set() and 'FROM notebook_notes' in statement and 'FOR UPDATE' in statement:
            worker_waiting.set()

    def after(connection, cursor, statement, parameters, context, many):
        if 'FROM notebook_publications' in statement and 'FOR UPDATE' in statement:
            human_locked.set()
            assert human_commit.wait(5), 'Human transaction release was not signaled.'

    with ThreadPoolExecutor(max_workers=2) as pool:
        model_future = pool.submit(run, system, provider)
        try:
            assert started.wait(5)
            # Attach after preflight, so this pauses the actual HTTP reviewer
            # while it holds both note/publication locks before committing.
            event.listen(system.db.engine, 'before_cursor_execute', before)
            event.listen(system.db.engine, 'after_cursor_execute', after)
            human_future = pool.submit(system.reviewer.post,
                f'/api/notebook/notes/{note["id"]}/publication-reviews',
                publication_body(note, decision='changes_requested', reason='Synthetic human review requires a revision.'))
            assert human_locked.wait(5)
            provider_release.set()
            assert worker_waiting.wait(3), 'Finalization must wait for the note mutation lock.'
            assert not model_future.done()
            human_commit.set()
            assert human_future.result(timeout=15).status_code == 200
            assert model_future.result(timeout=15)['status'] == 'superseded'
        finally:
            provider_release.set(); human_commit.set()
            event.remove(system.db.engine, 'before_cursor_execute', before)
            event.remove(system.db.engine, 'after_cursor_execute', after)
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, publication_id)
        assert publication.decision == 'changes_requested'
        assert publication.decision_source == 'human'
        assert publication.reviewer_id == system.reviewer.identifier
        assert session.get(AutomaticPublication, publication_id) is None
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404


def test_pg17_expiry_during_actual_account_lock_wait_retains_usage_without_publication(pg_screened):
    system = pg_screened
    note, publication_id = requested(system)
    started, provider_release, worker_waiting = Event(), Event(), Event()
    provider = SyntheticProvider(started, provider_release)

    def before(connection, cursor, statement, parameters, context, many):
        if 'FROM users' in statement and 'FOR UPDATE' in statement:
            worker_waiting.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, system, provider)
        try:
            assert started.wait(5), 'The provider boundary was not reached.'
            # Hold a real row lock after preflight but before finalization.
            # The model call holds no transaction, so this acquisition succeeds.
            with system.db.transaction(write=True) as blocker:
                beta_access.lock_user(blocker, system.owner.identifier)
                event.listen(system.db.engine, 'before_cursor_execute', before)
                try:
                    provider_release.set()
                    assert worker_waiting.wait(3), 'Finalization did not reach the account lock.'
                    assert not future.done()
                    # Move the injected clock through lease expiry while the
                    # worker is blocked. No actual two-minute sleep is needed.
                    system.now[0] += worker.LEASE_SECONDS
                finally:
                    event.remove(system.db.engine, 'before_cursor_execute', before)
            assert future.result(timeout=15)['status'] == 'human_review'
        finally:
            provider_release.set()
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, publication_id)
        assert publication.decision is None and publication.decision_source is None
        assert session.get(AutomaticPublication, publication_id) is None
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.error_code == 'lease_expired'
        assert attempt.result['outcome'] == 'no_concern_found'
        assert attempt.result['usage']['input_tokens'] == 100
        assert attempt.usage_known and attempt.charged_micro_usd == 25
        assert attempt.finished_at == system.now[0]
        budget = session.scalar(select(ScreeningBudget))
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (25, 0)
        assert session.scalar(select(ScreeningJob.status)) == 'human_review'
    assert run(system, provider) == {'status': 'idle'}
    assert provider.calls == 1
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404


def test_pg17_exact_automatic_receipt_is_durable_and_later_revision_stays_private(pg_screened):
    system = pg_screened
    first = create(system.owner, content(body='Earlier private synthetic draft.'))
    submitted = revise(system.owner, first, content(body='The exact submitted synthetic result.'))
    submitted, publication_id = requested(system, submitted)
    later = revise(system.owner, submitted, content(body='Later private synthetic draft.'))
    assert run(system, SyntheticProvider())['status'] == 'published'
    assert run(system, SyntheticProvider())['status'] == 'idle'
    with system.db.transaction() as session:
        receipt = session.get(AutomaticPublication, publication_id)
        attempt = session.get(ScreeningAttempt, receipt.attempt_id)
        assert receipt.content_sha256 == submitted['content_sha256']
        assert receipt.policy_id == system.policy.id and receipt.result_sha256 == attempt.result_sha256
        assert attempt.result['full_coverage'] is True
        assert attempt.result['usage']['input_tokens'] == 100
        assert session.get(notebook.NotebookPublication, publication_id).reviewer_id is None
        assert session.scalar(select(func.count()).select_from(AutomaticPublication)) == 1
        expected = {column.name: getattr(receipt, column.name) for column in receipt.__table__.columns}
    reopened = Database(system.fixture.runtime_url)
    try:
        with reopened.transaction() as session:
            receipt = session.get(AutomaticPublication, publication_id)
            assert {column.name: getattr(receipt, column.name) for column in receipt.__table__.columns} == expected
    finally:
        reopened.engine.dispose()
    with system.fixture.backup_engine.connect() as connection:
        assert dict(connection.execute(text('SELECT * FROM notebook_automatic_publications WHERE publication_id=:id'),
                    {'id': publication_id}).mappings().one()) == expected
    with pytest.raises(RuntimeError, match='Preserve screening'):
        command.downgrade(system.fixture.config, '0030')
    public = system.reader.client.get(f'/api/notebook/notes/{submitted["id"]}').json()['note']
    assert [item['revision'] for item in public['revisions']] == [submitted['revision']]
    for private in (first, later):
        assert system.reader.client.get(f'/api/notebook/notes/{private["id"]}?revision={private["revision"]}').status_code == 404


def retract_publication(system, note, action):
    path = 'delete' if action == 'delete' else 'publication-cancellations'
    body = publication_body(note, **({'permanent': True} if action == 'delete' else {}))
    return system.owner.post(f'/api/notebook/notes/{note["id"]}/{path}', body)


def legacy_pending(system, actor=None):
    """A real note and synthetic pre-screening publication request, with no job."""
    actor = actor or system.owner
    note = create(actor, content(body='Synthetic original submitted revision; not private later text.'))
    with system.db.transaction(write=True) as session:
        revision = session.scalar(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == note['id'],
            notebook.NotebookRevision.revision == note['revision']))
        publication = notebook.NotebookPublication(id=str(uuid4()), revision_id=revision.id,
            author_id=actor.identifier, requested_at=system.now[0],
            idempotency_key=str(uuid4()), request_sha256='a' * 64,
            test_only_self_review=False)
        session.add(publication); session.flush()
        original = {column.name: getattr(publication, column.name)
                    for column in publication.__table__.columns}
    return note, original


def adoption_settings(system):
    return replace(system.settings, access_admin_ids=(system.reviewer.identifier, system.reader.identifier))


def adoption_plan(system, actor_id=None):
    return adoption.preview(system.db, adoption_settings(system),
        actor_id=actor_id or system.reviewer.identifier, clock=lambda: system.now[0])


def apply_adoption(system, plan):
    return adoption.apply_plan(system.db, adoption_settings(system), plan,
        actor_id=plan['actor_id'], authorization_reference='synthetic-operator-authorization',
        reason='Explicit synthetic platform-policy migration; not author consent.',
        clock=lambda: system.now[0])


def adopted_events(session):
    return list(session.scalars(select(beta_access.AccessEvent).where(
        beta_access.AccessEvent.action == adoption.ACTION)))


def test_pg17_competing_operator_plans_adopt_each_author_once_and_keep_original_revision(pg_screened):
    system = pg_screened
    note, original = legacy_pending(system)
    other, other_original = legacy_pending(system, system.reader)
    plans = [adoption_plan(system, actor.identifier) for actor in (system.reviewer, system.reader)]
    later = revise(system.owner, note, content(body='LATER_UNSUBMITTED_PRIVATE_TEXT'))
    barrier = Barrier(2)

    def apply(plan):
        barrier.wait(timeout=5)
        return apply_adoption(system, plan)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, plans))
    assert sorted(result['queued'] for result in results) == [0, 2]
    assert sorted(outcome['status'] for result in results for outcome in result['outcomes']) == [
        'already_screened', 'already_screened', 'queued', 'queued']
    with system.db.transaction() as session:
        recorded = list(session.scalars(select(ScreeningJob)))
        audits = adopted_events(session)
        assert len(recorded) == len(audits) == 2
        assert {job.author_id for job in recorded} == {system.owner.identifier, system.reader.identifier}
        assert {job.revision_id for job in recorded} == {original['revision_id'], other_original['revision_id']}
        for saved in (original, other_original):
            publication = session.get(notebook.NotebookPublication, saved['id'])
            assert {column.name: getattr(publication, column.name)
                    for column in publication.__table__.columns} == saved
        assert session.get(notebook.NotebookNote, note['id']).latest_revision == later['revision']
        for audit in audits:
            job = next(job for job in recorded if job.id == audit.details['job_id'])
            assert audit.user_id == job.author_id and audit.actor_id in adoption_settings(system).access_admin_ids
            assert audit.details['authority_basis'] == 'platform_policy_migration'
            assert audit.details['fresh_author_consent_recorded'] is False
            assert audit.details['publication_basis'] == 'original_publication_request'
            assert audit.details['content_sha256'] == job.content_sha256
            assert audit.details['policy_id'] == system.policy.id
            assert audit.details['plan_sha256'] in {plan['plan_sha256'] for plan in plans}
        assert session.scalar(select(func.count()).select_from(ScreeningAttempt)) == 0
        assert all(row.reserved_micro_usd == row.spent_micro_usd == 0
                   for row in session.scalars(select(ScreeningBudget)))
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}?revision={later["revision"]}').status_code == 404


@pytest.mark.parametrize('action', ['cancel', 'delete'])
@pytest.mark.parametrize('first', ['operator', 'author'])
def test_pg17_operator_adoption_and_author_retraction_serialize_on_control(pg_screened, action, first):
    system = pg_screened
    note, original = legacy_pending(system)
    plan = adoption_plan(system)
    locked, release, waiting = Event(), Event(), Event()

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        if locked.is_set() and 'FROM publication_screening_control' in statement and 'FOR UPDATE' in statement:
            waiting.set()

    def after(_connection, _cursor, statement, _parameters, _context, _many):
        if not locked.is_set() and 'FROM publication_screening_control' in statement and 'FOR UPDATE' in statement:
            locked.set()
            assert release.wait(5), 'The first control-lock holder was not released.'

    operations = {'operator': lambda: apply_adoption(system, plan),
                  'author': lambda: retract_publication(system, note, action)}
    second = 'author' if first == 'operator' else 'operator'
    event.listen(system.db.engine, 'before_cursor_execute', before)
    event.listen(system.db.engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(operations[first])
            try:
                assert locked.wait(5)
                loser = pool.submit(operations[second])
                assert waiting.wait(3) and not loser.done()
            finally:
                release.set()
            outcomes = {first: winner.result(timeout=15), second: loser.result(timeout=15)}
    finally:
        release.set()
        event.remove(system.db.engine, 'before_cursor_execute', before)
        event.remove(system.db.engine, 'after_cursor_execute', after)
    assert outcomes['author'].status_code == 200, outcomes['author'].text
    assert outcomes['operator']['queued'] == int(first == 'operator')
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, original['id'])
        assert (publication.decision, publication.decision_source) == ('cancelled', 'author')
        recorded = list(session.scalars(select(ScreeningJob)))
        assert len(recorded) == len(adopted_events(session)) == int(first == 'operator')
        if recorded:
            assert recorded[0].status == 'cancelled'
    provider = SyntheticProvider()
    assert run(system, provider) == {'status': 'idle'} and provider.calls == 0


@pytest.mark.parametrize('first', ['operator', 'reviewer'])
def test_pg17_operator_adoption_respects_concurrent_human_decision(pg_screened, first):
    system = pg_screened
    note, original = legacy_pending(system)
    plan = adoption_plan(system)
    locked, release, waiting = Event(), Event(), Event()

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        if locked.is_set() and 'FOR UPDATE' in statement and ('FROM users' in statement or 'FROM notebook_notes' in statement):
            waiting.set()

    def after(_connection, _cursor, statement, _parameters, _context, _many):
        if not locked.is_set() and 'FROM notebook_notes' in statement and 'FOR UPDATE' in statement:
            locked.set()
            assert release.wait(5), 'The first note-lock holder was not released.'

    operations = {'operator': lambda: apply_adoption(system, plan),
        'reviewer': lambda: system.reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
            publication_body(note, decision='approved', reason='Synthetic independent human decision.'))}
    second = 'reviewer' if first == 'operator' else 'operator'
    event.listen(system.db.engine, 'before_cursor_execute', before)
    event.listen(system.db.engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(operations[first])
            try:
                assert locked.wait(5)
                loser = pool.submit(operations[second])
                assert waiting.wait(3) and not loser.done()
            finally:
                release.set()
            outcomes = {first: winner.result(timeout=15), second: loser.result(timeout=15)}
    finally:
        release.set()
        event.remove(system.db.engine, 'before_cursor_execute', before)
        event.remove(system.db.engine, 'after_cursor_execute', after)
    assert outcomes['reviewer'].status_code == 200, outcomes['reviewer'].text
    assert outcomes['operator']['queued'] == int(first == 'operator')
    provider = SyntheticProvider()
    run(system, provider)
    assert provider.calls == 0
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, original['id'])
        assert (publication.decision, publication.decision_source) == ('approved', 'human')
        assert publication.reviewer_id == system.reviewer.identifier
        assert session.get(AutomaticPublication, publication.id) is None
        assert len(adopted_events(session)) == int(first == 'operator')


@pytest.mark.parametrize('revoked', ['author', 'operator'])
def test_pg17_operator_adoption_rechecks_membership_after_actual_user_lock_wait(pg_screened, revoked):
    from fastapi import HTTPException
    system = pg_screened
    _note, original = legacy_pending(system)
    plan = adoption_plan(system)
    target = system.owner.identifier if revoked == 'author' else system.reviewer.identifier
    waiting = Event()

    def before(_connection, _cursor, statement, parameters, _context, _many):
        if ('FROM users' in statement and 'FOR UPDATE' in statement
                and target in (parameters.values() if isinstance(parameters, dict) else parameters)):
            waiting.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system.db.transaction(write=True) as session:
            beta_access.lock_user(session, target)
            event.listen(system.db.engine, 'before_cursor_execute', before)
            try:
                future = pool.submit(apply_adoption, system, plan)
                assert waiting.wait(3) and not future.done()
                beta_access.set_membership(session, target, 'revoked', system.now[0])
            finally:
                event.remove(system.db.engine, 'before_cursor_execute', before)
        if revoked == 'operator':
            with pytest.raises(HTTPException) as denied:
                future.result(timeout=15)
            assert denied.value.status_code == 403
        else:
            result = future.result(timeout=15)
            assert result['queued'] == 0 and result['outcomes'][0]['status'] == 'author_ineligible'
    with system.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ScreeningJob)) == 0
        assert adopted_events(session) == []
        assert session.get(notebook.NotebookPublication, original['id']).decision is None


def test_pg17_operator_adoption_audit_failure_rolls_back_the_job_and_allows_exact_retry(pg_screened):
    system = pg_screened
    _note, original = legacy_pending(system)
    plan = adoption_plan(system)

    def reject_audit(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('INSERT INTO ACCESS_EVENTS'):
            raise RuntimeError('Synthetic audit persistence failure')

    event.listen(system.db.engine, 'before_cursor_execute', reject_audit)
    try:
        with pytest.raises(RuntimeError, match='Synthetic audit persistence failure'):
            apply_adoption(system, plan)
    finally:
        event.remove(system.db.engine, 'before_cursor_execute', reject_audit)
    with system.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ScreeningJob)) == 0
        assert adopted_events(session) == []
        assert session.get(notebook.NotebookPublication, original['id']).decision is None
    assert apply_adoption(system, plan)['queued'] == 1
    with system.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ScreeningJob)) == len(adopted_events(session)) == 1
        assert session.scalar(select(func.count()).select_from(ScreeningAttempt)) == 0


@pytest.mark.parametrize('change', ['disabled', 'other_policy'])
def test_pg17_operator_adoption_rechecks_control_after_actual_lock_wait(pg_screened, change):
    from fastapi import HTTPException
    system = pg_screened
    _note, original = legacy_pending(system)
    plan = adoption_plan(system)
    waiting = Event()

    def before(_connection, _cursor, statement, _parameters, _context, _many):
        if 'FROM publication_screening_control' in statement and 'FOR UPDATE' in statement:
            waiting.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system.db.transaction(write=True) as session:
            control = session.scalar(select(ScreeningControl).with_for_update())
            event.listen(system.db.engine, 'before_cursor_execute', before)
            try:
                future = pool.submit(apply_adoption, system, plan)
                assert waiting.wait(3) and not future.done()
                if change == 'disabled':
                    control.mode = 'disabled'
                else:
                    control.policy_id = '0' * 64
            finally:
                event.remove(system.db.engine, 'before_cursor_execute', before)
        with pytest.raises(HTTPException) as stopped:
            future.result(timeout=15)
        assert stopped.value.status_code == 409
    with system.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ScreeningJob)) == 0
        assert adopted_events(session) == []
        assert session.get(notebook.NotebookPublication, original['id']).decision is None


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_pg17_author_retraction_during_provider_call_prevents_late_publication_and_keeps_cost(pg_screened, action):
    system = pg_screened
    note, publication_id = requested(system, create(system.owner,
        content(body='PRIVATE_PENDING_RETRACTION_SENTINEL')))
    started, release = Event(), Event()
    provider = SyntheticProvider(started, release)
    next_provider = SyntheticProvider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, system, provider)
        try:
            assert started.wait(5), 'The synthetic provider boundary was not reached.'
            # The provider is still in flight. The author action must commit
            # without waiting for it, and conservatively retain its whole cost.
            response = retract_publication(system, note, action)
            assert response.status_code == 200, response.text
            assert not future.done()
            next_note, next_publication_id = requested(system)
            # Cancellation revokes publication authority, not the transport
            # already in flight. A second worker must not bypass the one-call
            # limit merely because the author withdrew the first request.
            assert run(system, next_provider) == {'status': 'idle'}
            assert next_provider.calls == 0
            with system.db.transaction() as session:
                publication = session.get(notebook.NotebookPublication, publication_id)
                assert (publication.decision, publication.decision_source) == ('cancelled', 'author')
                assert publication.reviewer_id is None
                assert session.get(AutomaticPublication, publication_id) is None
                job = session.scalar(select(ScreeningJob).where(ScreeningJob.publication_id == publication_id))
                assert job.status == 'cancelled' and job.content_sha256 == note['content_sha256']
                attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id))
                assert attempt.charged_micro_usd == attempt.reserved_micro_usd == system.policy.reserve_micro_usd
                assert attempt.usage_known is False and attempt.finished_at == system.now[0]
                assert attempt.result is None
                budget = session.get(ScreeningBudget, attempt.budget_month)
                assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (system.policy.reserve_micro_usd, 0)
                control = session.get(ScreeningControl, 1)
                assert (control.active_job_id, control.lease_token, control.lease_expires_at) == (
                    job.id, attempt.lease_token, attempt.lease_expires_at)
                assert session.scalar(select(ScreeningJob.status).where(
                    ScreeningJob.publication_id == next_publication_id)) == 'queued'
                assert session.scalar(select(func.count()).select_from(ScreeningAttempt)) == 1
        finally:
            release.set()
        assert future.result(timeout=15)['status'] == 'stale_lease'
    assert provider.calls == 1
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404
    # Reopen through a new pool: the terminal decision and cost are durable,
    # not just retained in the API request's SQLAlchemy identity map.
    reopened = Database(system.fixture.runtime_url)
    try:
        with reopened.transaction() as session:
            publication = session.get(notebook.NotebookPublication, publication_id)
            assert (publication.decision, publication.decision_source) == ('cancelled', 'author')
            assert session.get(AutomaticPublication, publication_id) is None
            attempt = session.scalar(select(ScreeningAttempt))
            assert attempt.charged_micro_usd == system.policy.reserve_micro_usd and attempt.usage_known is False
            assert attempt.result is None
            budget = session.get(ScreeningBudget, attempt.budget_month)
            assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (system.policy.reserve_micro_usd, 0)
            control = session.get(ScreeningControl, 1)
            assert (control.active_job_id, control.lease_token, control.lease_expires_at) == (None, None, None)
            revision = session.get(notebook.NotebookRevision, publication.revision_id)
            assert revision.content_sha256 == note['content_sha256']
            if action == 'delete':
                assert revision.content == {}
                assert session.get(notebook.NotebookNote, note['id']).latest_revision == 0
            else:
                assert revision.content['body'] == 'PRIVATE_PENDING_RETRACTION_SENTINEL'
    finally:
        reopened.engine.dispose()
    assert run(system, next_provider)['status'] == 'published'
    assert next_provider.calls == 1
    assert run(system, next_provider) == {'status': 'idle'}
    assert next_provider.calls == provider.calls == 1
    assert system.reader.client.get(f'/api/notebook/notes/{next_note["id"]}').status_code == 200
    with system.db.transaction() as session:
        assert session.get(AutomaticPublication, next_publication_id) is not None
        budget = session.scalar(select(ScreeningBudget))
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (system.policy.reserve_micro_usd + 25, 0)


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_pg17_cancelled_lost_worker_expires_without_requeue_or_releasing_replacement_slot(pg_screened, action):
    system = pg_screened
    note, publication_id = requested(system)

    def claim():
        return jobs.claim(system.db, policy=system.policy, enabled=True,
            monthly_budget_micro_usd=system.settings.publication_screening_monthly_budget_micro_usd,
            now=system.now[0])

    abandoned = claim()
    assert abandoned is not None and abandoned.publication_id == publication_id
    assert retract_publication(system, note, action).status_code == 200
    _, next_publication_id = requested(system)
    # A crashed worker cannot signal that its transport has stopped. Preserve
    # the occupied slot up to its original deadline, then unblock other work.
    system.now[0] = abandoned.expires_at - 1
    assert claim() is None
    system.now[0] = abandoned.expires_at
    replacement = claim()
    assert replacement is not None and replacement.publication_id == next_publication_id
    assert jobs.fail(system.db, abandoned, code='provider_unavailable', retryable=True,
                     now=system.now[0]) == 'stale_lease'
    with system.db.transaction() as session:
        old_job = session.get(ScreeningJob, abandoned.job_id)
        assert old_job.status == 'cancelled' and old_job.attempt_count == 1
        old_attempt = session.get(ScreeningAttempt, abandoned.attempt_id)
        assert old_attempt.charged_micro_usd == system.policy.reserve_micro_usd
        assert old_attempt.usage_known is False and old_attempt.error_code == 'publication_unavailable'
        assert session.scalar(select(func.count()).select_from(ScreeningAttempt)
            .where(ScreeningAttempt.job_id == old_job.id)) == 1
        control = session.get(ScreeningControl, 1)
        assert (control.active_job_id, control.lease_token) == (replacement.job_id, replacement.token)
        budget = session.get(ScreeningBudget, old_attempt.budget_month)
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (
            system.policy.reserve_micro_usd, system.policy.reserve_micro_usd)
        assert session.get(AutomaticPublication, publication_id) is None
    # The replacement call was never sent in this synthetic lost-worker test;
    # settle it at known zero cost, then verify neither terminal job requeues.
    jobs.fail(system.db, replacement, code='provider_unconfigured', retryable=False,
              now=system.now[0], actual_cost_micro_usd=0)
    system.now[0] += worker.LEASE_SECONDS
    assert claim() is None
    with system.db.transaction() as session:
        budget = session.scalar(select(ScreeningBudget))
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (system.policy.reserve_micro_usd, 0)
        assert session.get(ScreeningJob, abandoned.job_id).status == 'cancelled'
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_pg17_queued_retraction_never_calls_provider_or_reserves_budget(pg_screened, action):
    system = pg_screened
    note, publication_id = requested(system)
    response = retract_publication(system, note, action)
    assert response.status_code == 200, response.text
    provider = SyntheticProvider()
    assert run(system, provider) == {'status': 'idle'}
    assert provider.calls == 0
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, publication_id)
        assert (publication.decision, publication.decision_source) == ('cancelled', 'author')
        assert session.get(AutomaticPublication, publication_id) is None
        assert session.scalar(select(ScreeningJob.status)) == 'cancelled'
        assert session.scalar(select(func.count()).select_from(ScreeningAttempt)) == 0
        assert all((row.spent_micro_usd, row.reserved_micro_usd) == (0, 0)
                   for row in session.scalars(select(ScreeningBudget)))


@pytest.mark.parametrize('action', ['cancel', 'delete'])
@pytest.mark.parametrize('first', ['author', 'reviewer'])
def test_pg17_human_publication_and_author_retraction_have_one_serialized_winner(pg_screened, action, first):
    system = pg_screened
    note, publication_id = requested(system)
    locked, release, waiting = Event(), Event(), Event()

    def before(connection, cursor, statement, parameters, context, many):
        if locked.is_set() and 'FROM notebook_notes' in statement and 'FOR UPDATE' in statement:
            waiting.set()

    def after(connection, cursor, statement, parameters, context, many):
        if not locked.is_set() and 'FROM notebook_notes' in statement and 'FOR UPDATE' in statement:
            locked.set()
            assert release.wait(5), 'The winning note transaction was not released.'

    def author():
        return retract_publication(system, note, action)

    def reviewer():
        return system.reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
            publication_body(note, decision='approved', reason='Synthetic independent publication review.'))

    actions = {'author': author, 'reviewer': reviewer}
    second = 'reviewer' if first == 'author' else 'author'
    event.listen(system.db.engine, 'before_cursor_execute', before)
    event.listen(system.db.engine, 'after_cursor_execute', after)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(actions[first])
            try:
                assert locked.wait(5), 'The first HTTP mutation did not hold the note row lock.'
                loser = pool.submit(actions[second])
                assert waiting.wait(3), 'The competing HTTP mutation did not reach the held row lock.'
                assert not loser.done()
            finally:
                release.set()
            accepted, rejected = winner.result(timeout=15), loser.result(timeout=15)
            assert accepted.status_code == 200, accepted.text
            assert rejected.status_code == 409, rejected.text
    finally:
        release.set()
        event.remove(system.db.engine, 'before_cursor_execute', before)
        event.remove(system.db.engine, 'after_cursor_execute', after)
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, publication_id)
        assert (publication.decision, publication.decision_source) == (
            ('cancelled', 'author') if first == 'author' else ('approved', 'human'))
        assert publication.reviewer_id == (None if first == 'author' else system.reviewer.identifier)
        assert session.get(AutomaticPublication, publication_id) is None
        revision = session.get(notebook.NotebookRevision, publication.revision_id)
        assert revision.content_sha256 == note['content_sha256']
        assert bool(revision.content) is not (first == 'author' and action == 'delete')
    visible = system.reader.client.get(f'/api/notebook/notes/{note["id"]}')
    assert visible.status_code == (404 if first == 'author' else 200)


@pytest.mark.parametrize('action', ['cancel', 'delete'])
def test_pg17_worker_finalization_wins_control_lock_without_deadlocking_author_retraction(pg_screened, action):
    system = pg_screened
    note, publication_id = requested(system)
    started, provider_release, worker_locked, worker_release, author_waiting = [Event() for _ in range(5)]
    provider = SyntheticProvider(started, provider_release)

    def control_lock(statement):
        return 'FROM publication_screening_control' in statement and 'FOR UPDATE' in statement

    def before(connection, cursor, statement, parameters, context, many):
        if worker_locked.is_set() and control_lock(statement):
            author_waiting.set()

    def after(connection, cursor, statement, parameters, context, many):
        if not worker_locked.is_set() and control_lock(statement):
            worker_locked.set()
            assert worker_release.wait(5), 'The finalization transaction was not released.'

    listening = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        model_future = pool.submit(run, system, provider)
        try:
            assert started.wait(5)
            # Pause actual finalization after control acquisition but before it
            # locks the author. A cancel/delete request must wait for control
            # without holding that author's account lock: the opposite order
            # would produce a real PostgreSQL deadlock when finish resumes.
            event.listen(system.db.engine, 'before_cursor_execute', before)
            event.listen(system.db.engine, 'after_cursor_execute', after)
            listening = True
            provider_release.set()
            assert worker_locked.wait(5)
            author_future = pool.submit(retract_publication, system, note, action)
            assert author_waiting.wait(3)
            assert not author_future.done()
            worker_release.set()
            assert model_future.result(timeout=15)['status'] == 'published'
            response = author_future.result(timeout=15)
            assert response.status_code == 409, response.text
        finally:
            provider_release.set(); worker_release.set()
            if listening:
                event.remove(system.db.engine, 'before_cursor_execute', before)
                event.remove(system.db.engine, 'after_cursor_execute', after)
    with system.db.transaction() as session:
        publication = session.get(notebook.NotebookPublication, publication_id)
        assert publication.decision == 'approved'
        assert session.get(AutomaticPublication, publication_id) is not None
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.usage_known and attempt.charged_micro_usd == 25
        budget = session.get(ScreeningBudget, attempt.budget_month)
        assert (budget.spent_micro_usd, budget.reserved_micro_usd) == (25, 0)
        assert session.get(notebook.NotebookRevision, publication.revision_id).content
    assert system.reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 200
