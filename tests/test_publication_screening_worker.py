"""Worker checks with synthetic notes/responses; no keys or network are used."""
from dataclasses import replace
import base64
import json
from pathlib import Path
import subprocess
import sys
import textwrap
from uuid import uuid4

import pytest
import httpx
from sqlalchemy import select

from crucible import beta_access, publication_screening_jobs as jobs, publication_screening_worker as worker
from crucible.app import create_app
from crucible.notebook import NotebookRevision
from crucible.notebook_trash import NotebookDeletion
from crucible.publication_screening_models import AutomaticPublication, ScreeningAttempt, ScreeningControl, ScreeningJob
from crucible.publication_screening_provider import COUNT_ENDPOINT, ENDPOINT, OpenAIResponsesProvider, ProviderResult, ScreeningProviderError
from test_notebook import Actor, approve, content, create, publication_body
from test_publication_screening_integration import (
    SyntheticProvider, configure, counts, publication, request, run, screened,
)


def test_fresh_worker_cli_claims_prepares_and_publishes_with_synthetic_provider(screened):
    """App imports in the pytest process must not hide worker startup defects."""
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    script = textwrap.dedent('''
        import json, sys
        assert 'crucible.notebook' not in sys.modules
        def no_network(event, args):
            if event in {'socket.connect', 'socket.getaddrinfo'}:
                raise AssertionError('The synthetic worker must never access a network.')
        sys.addaudithook(no_network)
        from crucible import publication_screening_worker as worker
        from crucible.publication_screening_provider import ProviderResult
        inputs = json.loads(sys.stdin.read())
        worker.time.time = lambda: inputs['now']
        calls = []
        def synthetic_screen(self, prepared):
            assert prepared.content_sha256 == inputs['content_sha256']
            assert prepared.fields and prepared.input_json
            calls.append(prepared.content_sha256)
            return ProviderResult(
                verdict={'outcome':'no_concern_found', 'concerns':[], 'limitations':[]},
                model='gpt-6-luna', response_id='synthetic-fresh-worker',
                usage={'input_tokens':100, 'output_tokens':30,
                       'cached_input_tokens':0, 'reasoning_tokens':0})
        worker._EnvironmentProvider.screen = synthetic_screen
        assert worker.main(['run', '--once']) == 0
        assert calls == [inputs['content_sha256']]
        # A second invocation is idle: the completed request is not dispatched twice.
        assert worker.main(['run', '--once']) == 0
        assert len(calls) == 1
    ''')
    # No inherited provider keys, hosted URLs or user configuration. Only the
    # parent fixture's disposable SQLite database and explicit synthetic settings.
    child = subprocess.run([sys.executable, '-B', '-c', script],
        cwd=Path(worker.__file__).resolve().parents[1],
        env={'CRUCIBLE_ENV':'development', 'DATABASE_URL':screened.app.state.settings.database_url,
             'CRUCIBLE_SECRET':base64.b64encode(b's' * 32).decode(),
             'CRUCIBLE_ORIGIN':'http://testserver', 'CRUCIBLE_ACCESS_MODE':'beta',
             'CRUCIBLE_PUBLICATION_SCREENING_MODE':'automatic',
             'CRUCIBLE_PUBLICATION_SCREENING_MODEL':'gpt-6-luna',
             'CRUCIBLE_PUBLICATION_SCREENING_BUDGET_MICRO_USD':'10000000'},
        input=json.dumps({'now':screened.now[0], 'content_sha256':saved['content_sha256']}),
        text=True, capture_output=True, timeout=20)
    assert child.returncode == 0, (child.stdout, child.stderr)
    replies = [json.loads(line) for line in child.stdout.splitlines()]
    assert [reply['status'] for reply in replies] == ['published', 'idle']
    assert publication(screened, saved)['decision_source'] == 'automated'
    assert counts(screened, (ScreeningAttempt, AutomaticPublication)) == {
        'publication_screening_attempts':1, 'notebook_automatic_publications':1}


@pytest.mark.parametrize('model,reserve,reasoning', [
    ('gpt-6-luna', 13550, 'none'), ('gpt-5.4-mini', 102750, 'none'), ('gpt-6.1-sol', 271000, 'low'),
])
def test_policy_pins_cost_model_schema_and_publication_consent(screened, model, reserve, reasoning):
    settings = replace(screened.app.state.settings, publication_screening_model=model)
    policy = worker.policy_for(settings)
    assert policy.max_input_tokens == 128000 and policy.max_output_tokens == 1500
    assert policy.reserve_micro_usd == reserve
    configuration = policy.configuration['model_configuration']
    assert configuration['reasoning_effort'] == reasoning
    assert configuration['publication_mode'] == 'automatic'
    assert configuration['store'] is False and configuration['tools'] == []
    assert configuration['output_schema_sha256'] == worker.OUTPUT_SCHEMA_SHA256
    assert configuration['timeout_seconds'] < worker.LEASE_SECONDS
    assert configuration['input_admission'] == 'utf8-bound-or-responses-count-v1'
    shadow = worker.policy_for(replace(settings, publication_screening_mode='shadow'))
    assert shadow.id != policy.id


def shadow_request(screened):
    settings = replace(screened.app.state.settings, publication_screening_mode='shadow')
    app = create_app(settings, clock=lambda: screened.now[0])
    author = Actor(app, screened.owner.identifier)
    try:
        saved = create(author)
        assert request(author, saved).status_code == 200
    finally:
        author.client.close()
        app.state.db.engine.dispose()
    return settings, saved


def test_shadow_consent_cannot_be_promoted_by_later_automatic_configuration(screened):
    shadow, saved = shadow_request(screened)
    configure(screened)  # Current automatic policy cannot reinterpret shadow consent.
    provider = SyntheticProvider()
    run(screened, provider)
    assert provider.inputs == [] and publication(screened, saved)['decision'] is None
    with screened.app.state.db.transaction() as session:
        job = session.scalar(select(ScreeningJob))
        assert job.status == 'human_review' and job.error_code == 'policy_changed'
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


@pytest.mark.parametrize('historical', ['v2', 'v3_byte_bound'])
def test_new_policy_does_not_retarget_historical_publication_request(screened, monkeypatch, historical):
    current = worker.policy_for(screened.app.state.settings)
    prior_configuration = {key: value for key, value in current.model_configuration.items() if key != 'input_admission'}
    prior = replace(current, model_configuration=prior_configuration)
    if historical == 'v2':
        prior_configuration['output_schema_sha256'] = 'c3d4235ada5acb9978c429027927e084bcfec2d3933be22608b0aa11ae3f23b6'
        prior = replace(prior, policy_version='crucible-publication-screen-v2',
            prompt_sha256='ab624be768ba898299dad9f7fd7d47b02625efc06983dea5c55eef141a0a1126',
            model_configuration=prior_configuration)
    assert current.policy_version == 'crucible-publication-screen-v3' and prior.id != current.id
    saved = create(screened.owner)
    with monkeypatch.context() as patch:
        # Submit normally under the exact historical policy; never rewrite a
        # current job into a legacy fixture or send a note through an old model.
        patch.setattr(worker, 'policy_for', lambda settings: prior)
        assert request(screened.owner, saved).status_code == 200
    before = publication(screened, saved)
    configure(screened)
    provider = SyntheticProvider()
    run(screened, provider)
    assert provider.inputs == [] and publication(screened, saved) == before
    with screened.app.state.db.transaction() as session:
        job = session.scalar(select(ScreeningJob))
        assert job.policy_id == prior.id and job.policy == prior.configuration
        assert job.content_sha256 == saved['content_sha256'] and job.attempt_count == 0
        assert job.status == 'human_review' and job.error_code == 'policy_changed'
    assert counts(screened, (ScreeningAttempt, AutomaticPublication)) == {
        'publication_screening_attempts': 0, 'notebook_automatic_publications': 0}


def test_inflight_shadow_pass_cannot_publish_after_operator_switch(screened):
    shadow, saved = shadow_request(screened)
    jobs.configure(screened.app.state.db, policy=worker.policy_for(shadow), mode='shadow',
        monthly_budget_micro_usd=shadow.publication_screening_monthly_budget_micro_usd, now=screened.now[0])
    provider = SyntheticProvider(callback=lambda: configure(screened))
    worker.run_once(screened.app.state.db, shadow, provider, clock=lambda: screened.now[0])
    assert len(provider.inputs) == 1 and publication(screened, saved)['decision'] is None
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


@pytest.mark.parametrize('change', ['revoke', 'suspend', 'trash', 'hash'])
@pytest.mark.parametrize('when', ['before_send', 'during_preparation', 'during_call'])
def test_author_and_exact_revision_are_rechecked_at_both_boundaries(screened, change, when, monkeypatch):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    def mutate():
        with screened.app.state.db.transaction(write=True) as session:
            if change == 'revoke':
                beta_access.set_membership(session, screened.owner.identifier, 'revoked', screened.now[0])
            elif change == 'suspend':
                beta_access.set_suspension(session, screened.owner.identifier, True, screened.now[0])
            elif change == 'trash':
                # An operator/legacy trash record must still be respected even
                # though the current normal UI prevents trashing pending work.
                session.add(NotebookDeletion(id=str(uuid4()), note_id=saved['id'],
                    author_id=screened.owner.identifier, revision=saved['revision'],
                    content_sha256=saved['content_sha256'], deleted_at=screened.now[0],
                    idempotency_key=str(uuid4())))
            else:
                revision = session.scalar(select(NotebookRevision).where(NotebookRevision.note_id == saved['id']))
                # Preserve the hash field to prove actual content is rehashed.
                revision.content = {**revision.content, 'body': 'Changed exact data without matching hash.'}
    if when == 'before_send':
        mutate()
    elif when == 'during_preparation':
        prepare = worker.prepare_input
        def changed_during_preparation(content, **kwargs):
            prepared = prepare(content, **kwargs)
            mutate()
            return prepared
        monkeypatch.setattr(worker, 'prepare_input', changed_during_preparation)
    provider = SyntheticProvider(callback=mutate if when == 'during_call' else None)
    run(screened, provider)
    assert len(provider.inputs) == (1 if when == 'during_call' else 0)
    assert publication(screened, saved)['decision'] is None
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


@pytest.mark.parametrize('code', ['provider_authentication_failed', 'provider_access_denied',
    'provider_quota_exhausted', 'provider_configuration_error'])
def test_permanent_provider_errors_are_distinct_human_fallbacks_without_retry(screened, code):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    class Failed(SyntheticProvider):
        def screen(self, prepared):
            raise ScreeningProviderError(code, retryable=True)
    result = run(screened, Failed())
    assert result['status'] == 'human_review' and result['error_code'] == code
    assert publication(screened, saved)['decision'] is None
    screened.now[0] += 120
    assert run(screened, Failed()) == {'status': 'idle'}
    with screened.app.state.db.transaction() as session:
        job = session.scalar(select(ScreeningJob))
        assert job.attempt_count == 1 and job.error_code == code


def test_worker_callback_lock_delay_cannot_commit_an_expired_publication(screened, monkeypatch):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    original = worker._publish
    def delayed(session, job, receipt, now):
        result = original(session, job, receipt, now)
        screened.now[0] += worker.LEASE_SECONDS
        return result
    monkeypatch.setattr(worker, '_publish', delayed)
    assert run(screened, SyntheticProvider())['status'] == 'human_review'
    assert publication(screened, saved)['decision'] is None
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}


@pytest.mark.parametrize('kind', ['input_cap', 'output_cap', 'negative', 'boolean', 'cached', 'reasoning', 'model', 'verdict'])
def test_invalid_provider_usage_identity_and_verdict_cannot_publish(screened, kind):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    class Invalid(SyntheticProvider):
        def screen(self, prepared):
            result = super().screen(prepared)
            usage = dict(result.usage)
            field_value = {'input_cap': ('input_tokens', 128001), 'output_cap': ('output_tokens', 1501),
                'negative': ('input_tokens', -1), 'boolean': ('input_tokens', True),
                'cached': ('cached_input_tokens', 101), 'reasoning': ('reasoning_tokens', 31)}.get(kind)
            if field_value:
                usage[field_value[0]] = field_value[1]
                return replace(result, usage=usage)
            if kind == 'model':
                return replace(result, model='other-provider-model')
            return replace(result, verdict={'outcome': 'no_concern_found', 'concerns': [],
                                          'limitations': [], 'publish': True})
    run(screened, Invalid())
    assert publication(screened, saved)['decision'] is None
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}
    with screened.app.state.db.transaction() as session:
        assert session.scalar(select(ScreeningJob.status)) == 'human_review'


def test_actual_snapshot_and_bounded_audit_metadata_retained_without_input_body(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    class Snapshot(SyntheticProvider):
        def screen(self, prepared):
            # Checked-out SQLAlchemy connections prove the call holds no DB txn.
            assert screened.app.state.db.engine.pool.checkedout() == 0
            return replace(super().screen(prepared), model='gpt-6-luna-2026-11-01')
    provider = Snapshot()
    assert run(screened, provider)['status'] == 'published'
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.model_id == 'gpt-6-luna-2026-11-01'
        assert attempt.charged_micro_usd == 25  # 100×0.1 + 30×0.5 micro-USD.
        assert attempt.result['input_sha256'] == provider.inputs[0].input_sha256
        assert attempt.result['provider_response_id'] == 'synthetic-response'
        assert attempt.result['full_coverage'] is True
        assert 'input_json' not in attempt.result and 'content' not in attempt.result
        assert 'fields' not in attempt.result


def test_cli_status_configure_and_idle_run_do_not_load_key(screened, monkeypatch, capsys):
    from crucible import config
    monkeypatch.setattr(config, 'load_settings', lambda: screened.app.state.settings)
    def forbidden(**kwargs):
        pytest.fail('An idle/configuration command must not resolve a provider key.')
    monkeypatch.setattr(worker.OpenAIResponsesProvider, 'from_env', forbidden)
    assert worker.main(['status']) == 0
    assert json.loads(capsys.readouterr().out)['attempt_count'] == 0
    assert worker.main(['configure', '--mode', 'shadow', '--monthly-budget-micro-usd', '5000000']) == 0
    configured = json.loads(capsys.readouterr().out)
    assert configured['mode'] == 'shadow'
    assert configured['policy_id'] == worker.policy_for(replace(screened.app.state.settings,
        publication_screening_mode='shadow')).id
    assert worker.main(['run', '--once']) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'idle'}


def test_cli_failure_reports_only_fixed_code_not_exception_values(screened, monkeypatch, capsys):
    from crucible import config
    def bad_settings():
        raise RuntimeError('SYNTHETIC_PASSWORD_MUST_NOT_APPEAR')
    monkeypatch.setattr(config, 'load_settings', bad_settings)
    assert worker.main(['status']) == 1
    output = capsys.readouterr().out
    assert 'SYNTHETIC_PASSWORD' not in output
    assert json.loads(output) == {'status': 'error', 'error_code': 'screening_worker_failed'}


def test_cli_loop_drains_work_immediately_and_polls_idle_quietly(screened, monkeypatch, capsys):
    from crucible import config
    monkeypatch.setattr(config, 'load_settings', lambda: screened.app.state.settings)
    results = iter([{'status': 'published'}, {'status': 'idle'}, {'status': 'idle'},
                    {'status': 'human_review'}])
    calls, sleeps = [], []
    def fake_run(*args, **kwargs):
        calls.append(len(sleeps))
        try:
            return next(results)
        except StopIteration:
            raise KeyboardInterrupt
    monkeypatch.setattr(worker, 'run_once', fake_run)
    monkeypatch.setattr(worker.time, 'sleep', sleeps.append)
    assert worker.main(['run', '--loop']) == 130
    assert sleeps == [1, 1]
    assert calls == [0, 0, 1, 2, 2]
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {'status': 'published'}, {'status': 'human_review'}]


def test_lazy_cli_provider_uses_only_named_key_and_pinned_configuration(screened, monkeypatch):
    policy = worker.policy_for(screened.app.state.settings)
    observed = []
    def fake_env(**kwargs):
        observed.append(kwargs)
        return SyntheticProvider()
    monkeypatch.setattr(worker.OpenAIResponsesProvider, 'from_env', fake_env)
    provider = worker._EnvironmentProvider(policy, 'CRUCIBLE_PUBLICATION_SCREENING_API_KEY')
    assert observed == []
    from crucible.publication_screening_policy import prepare_input
    provider.screen(prepare_input({'body': 'Only synthetic data.'}))
    assert observed == [{'model': 'gpt-6-luna', 'key_env': 'CRUCIBLE_PUBLICATION_SCREENING_API_KEY',
        'timeout_seconds': 45, 'max_output_tokens': 1500, 'max_input_tokens': 128000, 'reasoning_effort': 'none'}]


def test_preflight_refusal_spends_zero_and_never_resolves_provider_key(screened, monkeypatch):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    with screened.app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, screened.owner.identifier, 'revoked', screened.now[0])
    def forbidden(**kwargs):
        pytest.fail('Unavailable notes must never resolve a provider key.')
    monkeypatch.setattr(worker.OpenAIResponsesProvider, 'from_env', forbidden)
    provider = worker._EnvironmentProvider(worker.policy_for(screened.app.state.settings), 'SYNTHETIC_KEY')
    run(screened, provider)
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.charged_micro_usd == 0 and attempt.usage_known is True


def test_preparation_delay_cannot_dispatch_an_expired_lease(screened, monkeypatch):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    prepare = worker.prepare_input
    def delayed(content, **kwargs):
        prepared = prepare(content, **kwargs)
        screened.now[0] += worker.LEASE_SECONDS
        return prepared
    monkeypatch.setattr(worker, 'prepare_input', delayed)
    provider = SyntheticProvider()
    assert run(screened, provider)['status'] == 'stale_lease'
    assert provider.inputs == []
    assert publication(screened, saved)['decision'] is None


def test_policy_change_during_input_preparation_prevents_send(screened, monkeypatch):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    prepare = worker.prepare_input
    def disabled(content, **kwargs):
        prepared = prepare(content, **kwargs)
        configure(screened, mode='disabled')
        return prepared
    monkeypatch.setattr(worker, 'prepare_input', disabled)
    provider = SyntheticProvider()
    assert run(screened, provider)['status'] == 'human_review'
    assert provider.inputs == []


def large_request(screened):
    saved = create(screened.owner, content(extensions={'appendix': ['数学\\equation\n' * 3000] * 3}))
    assert request(screened.owner, saved).status_code == 200
    return saved


def counted_provider(handler):
    return OpenAIResponsesProvider(model='gpt-6-luna', api_key='synthetic-no-real-key',
        reasoning_effort='none', transport=httpx.MockTransport(handler))


def counted_generation():
    from test_publication_screening_provider import completed
    result = completed()
    result['model'] = 'gpt-6-luna'
    return httpx.Response(200, json=result)


def test_large_complete_note_is_counted_and_published_with_bound_admission(screened):
    configure(screened)
    saved = large_request(screened)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == COUNT_ENDPOINT:
            return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 50000})
        return counted_generation()
    assert run(screened, counted_provider(handler))['status'] == 'published'
    assert calls == [COUNT_ENDPOINT, ENDPOINT]
    assert publication(screened, saved)['decision_source'] == 'automated'
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.result['input_admission']['method'] == 'responses_input_tokens'
        assert attempt.result['input_admission']['input_tokens'] == 50000
        assert attempt.result['input_admission']['budget_units'] > 128000
        assert attempt.result['full_coverage'] is True and attempt.usage_known


@pytest.mark.parametrize('change', ['disable', 'policy', 'revoke', 'cancel', 'human', 'expire', 'hash'])
def test_change_during_count_prevents_generation(screened, change):
    configure(screened)
    saved = large_request(screened)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert str(request.url) == COUNT_ENDPOINT
        if change == 'disable':
            configure(screened, mode='disabled')
        elif change == 'policy':
            with screened.app.state.db.transaction(write=True) as session:
                session.get(ScreeningControl, 1).policy_id = '0' * 64
        elif change == 'revoke':
            with screened.app.state.db.transaction(write=True) as session:
                beta_access.set_membership(session, screened.owner.identifier, 'revoked', screened.now[0])
        elif change == 'cancel':
            reply = screened.owner.post(f'/api/notebook/notes/{saved["id"]}/publication-cancellations', publication_body(saved))
            assert reply.status_code == 200, reply.text
        elif change == 'human':
            approve(screened.reviewer, saved)
        elif change == 'expire':
            screened.now[0] += worker.LEASE_SECONDS
        else:
            with screened.app.state.db.transaction(write=True) as session:
                row = session.scalar(select(NotebookRevision).where(NotebookRevision.note_id == saved['id']))
                row.content = {**row.content, 'body': 'Changed without matching original hash.'}
        return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 50000})
    result = run(screened, counted_provider(handler))
    assert calls == [COUNT_ENDPOINT]
    if change == 'hash':
        assert result['error_code'] == 'coverage_incomplete'
    assert counts(screened, (AutomaticPublication,)) == {'notebook_automatic_publications': 0}
    assert publication(screened, saved)['decision'] == ('approved' if change == 'human' else 'cancelled' if change == 'cancel' else None)
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        # Cancellation and lease expiry conservatively settle in-flight work;
        # an ordinary pre-generation refusal has no model-generation usage.
        assert attempt.charged_micro_usd == (13550 if change in ('cancel', 'expire') else 0)
        assert session.get(ScreeningControl, 1).active_job_id is None


@pytest.mark.parametrize('kind,code', [('over', 'input_token_limit'), ('broken', 'provider_count_unavailable')])
def test_failed_count_never_generates_or_retries_and_releases_generation_reservation(screened, kind, code):
    configure(screened)
    saved = large_request(screened)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'object': 'response.input_tokens', 'input_tokens': 128001 if kind == 'over' else False})
    candidate = counted_provider(handler)
    result = run(screened, candidate)
    assert result['status'] == 'human_review' and result['error_code'] == code
    assert run(screened, candidate)['status'] == 'idle'
    assert calls == [COUNT_ENDPOINT]
    assert publication(screened, saved)['decision'] is None
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.charged_micro_usd == 0 and attempt.usage_known
        assert session.get(ScreeningControl, 1).active_job_id is None


def test_validated_provider_overspend_is_recorded_while_publication_is_refused(screened):
    configure(screened)
    saved = create(screened.owner)
    assert request(screened.owner, saved).status_code == 200
    class Overspent(SyntheticProvider):
        def screen(self, prepared):
            result = super().screen(prepared)
            return replace(result, usage={**result.usage, 'input_tokens': 200000})
    assert run(screened, Overspent())['status'] == 'human_review'
    assert publication(screened, saved)['decision'] is None
    with screened.app.state.db.transaction() as session:
        attempt = session.scalar(select(ScreeningAttempt))
        assert attempt.charged_micro_usd == 20015 > attempt.reserved_micro_usd
        assert attempt.usage_known is True
        assert session.scalar(select(ScreeningJob.error_code)) == 'usage_exceeded_reservation'
