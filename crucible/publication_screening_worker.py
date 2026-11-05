"""Explicit publication-screening worker; importing it never reads an API key.

The database lease and provider call are separate. A passing result grants only
visibility of its exact requested revision, with automated provenance; it never
admits a scheme, executes an artifact, changes a bounty or awards points.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import re
import time

from sqlalchemy import event, func, select

from . import publication_screening_jobs as jobs
from .publication_screening_models import ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob
from .publication_screening_policy import (
    DEFAULT_MAX_INPUT_TOKENS, INPUT_FORMAT_VERSION, OUTPUT_SCHEMA_SHA256, POLICY_VERSION,
    PROMPT_SHA256, ScreeningCoverageError, ScreeningValidationError, canonical_json,
    prepare_input, validate_verdict,
)
from .publication_screening_provider import (
    OpenAIResponsesProvider, ProviderResult, ScreeningProviderError,
)

# Integer micro-USD per million tokens. Cached inputs are conservatively charged
# at the full input rate. Prices are a policy pin, not a live billing assertion.
RATES = {
    'gpt-6-luna': (100_000, 500_000, 'none'),
    'gpt-5.4-mini': (750_000, 4_500_000, 'none'),
    'gpt-6.1-sol': (2_000_000, 10_000_000, 'low'),
}
MAX_OUTPUT_TOKENS = 1500
PROVIDER_TIMEOUT_SECONDS = 45
LEASE_SECONDS = 120
DATABASE_CONNECT_TIMEOUT_SECONDS = 5
DATABASE_STATEMENT_TIMEOUT_MS = 10_000
DATABASE_LOCK_TIMEOUT_MS = 5_000
DATABASE_IDLE_TRANSACTION_TIMEOUT_MS = 10_000


def worker_database(url):
    """Bound only this serial worker's PostgreSQL operations, never HTTP work.

    Claim, authorization and settlement transactions are short; no provider
    call spans a transaction. Lock waits stop before the statement deadline.
    Interrupted/uncertain transactions retain the durable lease and reserved
    budget for recovery instead of authorizing a new unaccounted call.
    """
    from .db import Database
    db = Database(url)
    if not db.sqlite:
        original_options = None

        @event.listens_for(db.engine, 'do_connect')
        def bounded_connection(_dialect, _record, _arguments, parameters):
            # The hook runs before every new DBAPI connection and overrides a
            # weaker URL option. Preserve verified TLS and unrelated settings.
            nonlocal original_options
            parameters['connect_timeout'] = DATABASE_CONNECT_TIMEOUT_SECONDS
            if original_options is None:
                original_options = parameters.get('options') or ''
            parameters['options'] = (original_options +
                f' -c statement_timeout={DATABASE_STATEMENT_TIMEOUT_MS}'
                f' -c lock_timeout={DATABASE_LOCK_TIMEOUT_MS}'
                f' -c idle_in_transaction_session_timeout={DATABASE_IDLE_TRANSACTION_TIMEOUT_MS}').strip()
    return db


def _cost(inputs, outputs, rates):
    return (inputs * rates[0] + outputs * rates[1] + 999_999) // 1_000_000


def policy_for(settings):
    model, mode = settings.publication_screening_model, settings.publication_screening_mode
    if model not in RATES or mode not in ('disabled', 'shadow', 'automatic'):
        raise ValueError('Unsupported publication screening configuration.')
    rates = RATES[model]
    return jobs.ScreeningPolicy(
        policy_version=POLICY_VERSION, prompt_sha256=PROMPT_SHA256,
        input_format_version=INPUT_FORMAT_VERSION, model=model,
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS, max_output_tokens=MAX_OUTPUT_TOKENS,
        reserve_micro_usd=_cost(DEFAULT_MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, rates),
        model_configuration={
            'publication_mode': mode, 'provider': 'openai-responses',
            'reasoning_effort': rates[2], 'store': False, 'stream': False,
            'tools': [], 'tool_choice': 'none', 'output_schema_sha256': OUTPUT_SCHEMA_SHA256,
            'timeout_seconds': PROVIDER_TIMEOUT_SECONDS,
            'input_micro_usd_per_million_tokens': rates[0],
            'output_micro_usd_per_million_tokens': rates[1],
            'cost_policy': 'full-input-rate-ceiling-v1',
        })


class _Unavailable(Exception):
    def __init__(self, code='publication_unavailable'):
        self.code = code
        super().__init__(code)


def _target(session, identity):
    """Lock account → note → publication, as in normal notebook mutations."""
    from . import beta_access, notebook, notebook_moderation, notebook_trash
    user = beta_access.lock_user(session, identity.author_id)
    if user is None:
        raise _Unavailable()
    account = session.get(beta_access.AccountState, user.id, populate_existing=True)
    membership = session.get(beta_access.BetaMembership, user.id, populate_existing=True)
    if (account is not None and account.suspended_at is not None) or membership is None or membership.status != 'active':
        raise _Unavailable()
    revision = session.get(notebook.NotebookRevision, identity.revision_id, populate_existing=True)
    if revision is None:
        raise _Unavailable()
    note = session.scalar(select(notebook.NotebookNote).where(notebook.NotebookNote.id == revision.note_id)
                          .with_for_update().execution_options(populate_existing=True))
    publication = session.scalar(select(notebook.NotebookPublication).where(
        notebook.NotebookPublication.id == identity.publication_id).with_for_update()
        .execution_options(populate_existing=True))
    if (note is None or note.author_id != user.id or publication is None
            or publication.author_id != user.id or publication.revision_id != revision.id
            or revision.content_sha256 != identity.content_sha256 or publication.decision is not None
            or publication.decision_source is not None or publication.reviewer_id is not None
            or publication.review_key is not None or publication.review_sha256 is not None
            or publication.test_only_self_review
            or notebook_trash.active(session, note.id) is not None
            or session.get(notebook_moderation.PublicationWithdrawal, revision.id) is not None):
        raise _Unavailable()
    try:
        actual_hash = hashlib.sha256(canonical_json(revision.content).encode()).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _Unavailable('coverage_incomplete') from None
    if actual_hash != identity.content_sha256:
        raise _Unavailable('coverage_incomplete')
    return publication, revision


def _publish(session, job, receipt, now):
    try:
        publication, _revision = _target(session, job)
    except _Unavailable:
        return False
    if (receipt.revision_id != job.revision_id or receipt.content_sha256 != job.content_sha256
            or job.policy.get('model_configuration', {}).get('publication_mode') != 'automatic'):
        return False
    publication.decision, publication.decision_source = 'approved', 'automated'
    publication.reviewer_id = publication.review_key = publication.review_sha256 = None
    publication.reviewed_at = now
    publication.reason = None
    publication.test_only_self_review = False
    return True


def _usage(result, policy):
    if not isinstance(result, ProviderResult) or type(result.usage) is not dict:
        raise _Unavailable('usage_invalid')
    usage = result.usage
    expected = {'input_tokens', 'output_tokens', 'cached_input_tokens', 'reasoning_tokens'}
    if set(usage) != expected or any(type(value) is not int or value < 0 for value in usage.values()):
        raise _Unavailable('usage_invalid')
    if (usage['input_tokens'] > policy.max_input_tokens or usage['output_tokens'] > policy.max_output_tokens
            or usage['cached_input_tokens'] > usage['input_tokens']
            or usage['reasoning_tokens'] > usage['output_tokens']):
        raise _Unavailable('usage_invalid')
    if (type(result.model) is not str or not re.fullmatch(
            re.escape(policy.model) + r'(?:-\d{4}-\d{2}-\d{2})?', result.model)
            or type(result.response_id) is not str
            or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}', result.response_id)):
        raise _Unavailable('invalid_response')
    return dict(usage), _cost(usage['input_tokens'], usage['output_tokens'], RATES[policy.model])


def _failure(db, lease, code, clock, *, retryable=False, actual_cost=None):
    status = jobs.fail(db, lease, code=code, retryable=retryable, now=clock(),
                       actual_cost_micro_usd=actual_cost, clock=clock)
    return {'status': status, 'job_id': lease.job_id, 'error_code': code}


def run_once(db, settings, provider, *, clock=time.time):
    """One durable attempt with an injected provider. Never enable operator control."""
    policy = policy_for(settings)
    enabled = settings.publication_screening_mode != 'disabled'
    lease = jobs.claim(db, policy=policy, enabled=enabled,
        monthly_budget_micro_usd=settings.publication_screening_monthly_budget_micro_usd,
        now=clock(), lease_seconds=LEASE_SECONDS, clock=clock)
    if lease is None:
        return {'status': 'idle'}
    # All preflight failures precede transport, so zero cost is known.
    try:
        with db.transaction(write=True) as session:
            control = session.get(ScreeningControl, 1, populate_existing=True)
            if control is None or control.mode == 'disabled':
                raise _Unavailable('disabled')
            if control.policy_id != policy.id:
                raise _Unavailable('policy_changed')
            if control.active_job_id != lease.job_id or control.lease_token != lease.token:
                raise _Unavailable()
            _publication, revision = _target(session, lease)
            content = revision.content
        prepared = prepare_input(content, max_input_tokens=policy.max_input_tokens)
        if prepared.content_sha256 != lease.content_sha256:
            raise _Unavailable('coverage_incomplete')
        # Account/note locks and complete-input preparation can take time. Do
        # not knowingly dispatch an expired or superseded lease after that wait.
        # Read the clock AFTER this fresh query; no DB transaction spans send.
        with db.transaction(write=True) as session:
            # Membership or the note can change during preparation. Recheck
            # authorization after it, as well as after the provider returns.
            _target(session, lease)
            control = session.get(ScreeningControl, 1, populate_existing=True)
            now = clock()
            if (control is None or control.active_job_id != lease.job_id
                    or control.lease_token != lease.token or control.lease_expires_at is None
                    or now >= min(lease.expires_at, control.lease_expires_at)):
                raise _Unavailable()
            if control.mode == 'disabled':
                raise _Unavailable('disabled')
            if control.policy_id != policy.id:
                raise _Unavailable('policy_changed')
    except ScreeningCoverageError as exc:
        code = ('input_too_large' if exc.code in ('input_budget_exceeded', 'content_size_limit',
                                                'input_structure_limit') else 'unsupported_input')
        return _failure(db, lease, code, clock, actual_cost=0)
    except _Unavailable as exc:
        return _failure(db, lease, exc.code, clock, actual_cost=0)
    except Exception:
        # Never print a DB/serialization exception, which might contain values.
        return _failure(db, lease, 'internal_error', clock, actual_cost=0)

    actual_cost = None
    try:
        response = provider.screen(prepared)  # No transaction or lock is held.
        usage, actual_cost = _usage(response, policy)
        verdict = validate_verdict(response.verdict, prepared)
        result = {**verdict, 'input_sha256': prepared.input_sha256,
            'input_format_version': prepared.input_format_version, 'full_coverage': True,
            'provider_response_id': response.response_id, 'usage': usage}
    except ScreeningProviderError as exc:
        if exc.code == 'provider_timeout':
            code, retryable = 'provider_timeout', exc.retryable
        elif exc.code in ('provider_transport_error', 'provider_http_error', 'provider_rate_limited') and exc.retryable:
            code, retryable = 'provider_unavailable', True
        elif exc.code in ('provider_authentication_failed', 'provider_access_denied',
                          'provider_quota_exhausted', 'provider_configuration_error'):
            code, retryable = exc.code, False
        elif exc.code == 'missing_or_invalid_api_key':
            return _failure(db, lease, 'provider_unconfigured', clock, actual_cost=0)
        else:
            code, retryable = 'invalid_response', False
        return _failure(db, lease, code, clock, retryable=retryable)
    except ScreeningValidationError:
        return _failure(db, lease, 'invalid_response', clock, actual_cost=actual_cost)
    except _Unavailable as exc:
        return _failure(db, lease, exc.code, clock, actual_cost=actual_cost)
    except Exception:
        return _failure(db, lease, 'internal_error', clock, actual_cost=actual_cost)

    try:
        status = jobs.finish(db, lease, verdict=verdict['outcome'], result=result,
            model_id=response.model, actual_cost_micro_usd=actual_cost, policy=policy,
            enabled=enabled, automatic=(settings.publication_screening_mode == 'automatic'),
            now=clock(), publish=_publish, content_sha256=prepared.content_sha256,
            full_coverage=True, clock=clock)
        return {'status': status, 'job_id': lease.job_id}
    except Exception:
        # finish rolls back the entire receipt/publication transaction. A failed
        # callback never causes another provider call; settle it into human review.
        return _failure(db, lease, 'internal_error', clock, actual_cost=actual_cost)


def status(db):
    """Operational counts and cost only; no titles, content, findings or secrets."""
    with db.transaction() as session:
        control = session.get(ScreeningControl, 1)
        return {'control': None if control is None else {
            'mode': control.mode, 'policy_id': control.policy_id,
            'monthly_budget_micro_usd': control.monthly_budget_micro_usd,
            'call_leased': control.active_job_id is not None,
            'lease_expires_at': control.lease_expires_at},
            'jobs': dict(session.execute(select(ScreeningJob.status, func.count())
                .group_by(ScreeningJob.status)).all()),
            'attempt_count': session.scalar(select(func.count()).select_from(ScreeningAttempt)),
            'budgets': [{'month': row.month, 'spent_micro_usd': row.spent_micro_usd,
                         'reserved_micro_usd': row.reserved_micro_usd}
                        for row in session.scalars(select(ScreeningBudget).order_by(ScreeningBudget.month.desc()).limit(12))]}


class _EnvironmentProvider:
    """Resolve only the named team key, lazily when an eligible job reaches send."""
    def __init__(self, policy, key_env):
        self.policy, self.key_env = policy, key_env

    def screen(self, prepared):
        return OpenAIResponsesProvider.from_env(model=self.policy.model, key_env=self.key_env,
            timeout_seconds=PROVIDER_TIMEOUT_SECONDS, max_output_tokens=self.policy.max_output_tokens,
            reasoning_effort=self.policy.configuration['model_configuration']['reasoning_effort']).screen(prepared)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    configure = commands.add_parser('configure', help='Explicitly set durable mode, policy and monthly cap.')
    configure.add_argument('--mode', choices=('disabled', 'shadow', 'automatic'), required=True)
    configure.add_argument('--model', choices=tuple(RATES))
    configure.add_argument('--monthly-budget-micro-usd', type=int, required=True)
    commands.add_parser('status', help='Read safe operational counts; no API key needed.')
    run = commands.add_parser('run', help='Process explicitly submitted work; may send exact note content.')
    runs = run.add_mutually_exclusive_group(required=True)
    runs.add_argument('--once', action='store_true')
    runs.add_argument('--loop', action='store_true')
    run.add_argument('--poll-seconds', type=float, default=1)
    run.add_argument('--key-env', default='CRUCIBLE_PUBLICATION_SCREENING_API_KEY')
    args = parser.parse_args(argv)
    if args.command == 'run' and (not 1 <= args.poll_seconds <= 60
            or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', args.key_env)):
        parser.error('Use a 1–60 second poll interval and a named uppercase key variable.')
    from .config import load_settings
    db = None
    try:
        settings = load_settings()
        db = worker_database(settings.database_url)
        if args.command == 'configure':
            settings = replace(settings, publication_screening_mode=args.mode,
                publication_screening_model=args.model or settings.publication_screening_model,
                publication_screening_monthly_budget_micro_usd=args.monthly_budget_micro_usd)
            result = jobs.configure(db, policy=policy_for(settings), mode=args.mode,
                monthly_budget_micro_usd=args.monthly_budget_micro_usd, now=time.time())
            print(json.dumps(result, sort_keys=True))
        elif args.command == 'status':
            print(json.dumps(status(db), sort_keys=True))
        else:
            provider = _EnvironmentProvider(policy_for(settings), args.key_env)
            while True:
                result = run_once(db, settings, provider)
                if args.once or result['status'] != 'idle':
                    print(json.dumps(result, sort_keys=True), flush=True)
                if args.once:
                    break
                # Drain eligible work immediately; idle polling is quiet and
                # bounded, and never causes a provider call while browsing.
                if result['status'] == 'idle':
                    time.sleep(args.poll_seconds)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Even connection exceptions can contain credentials. The private
        # operator can inspect configuration independently; never echo here.
        print(json.dumps({'status': 'error', 'error_code': 'screening_worker_failed'}))
        return 1
    finally:
        if db is not None:
            db.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
