"""Database-backed screening dispatch; provider calls never belong in transactions.

The caller runs prepare_input/provider/validate_verdict between claim and finish.
Only the trusted publication callback can apply a passing result. A durable
singleton limits calls across processes; expired ambiguous calls consume their
reserved maximum rather than making that budget available for a second call.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from .publication_screening_models import (
    AutomaticPublication, ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob,
)

OUTCOMES = {'no_concern_found', 'human_review_needed'}
TRANSIENT_ERRORS = {'provider_timeout', 'provider_unavailable'}
ERRORS = TRANSIENT_ERRORS | {'invalid_response', 'input_too_large', 'coverage_incomplete',
    'unsupported_input', 'provider_unconfigured', 'publication_unavailable', 'policy_changed',
    'disabled', 'budget_exhausted', 'lease_expired', 'usage_invalid', 'usage_exceeded_reservation',
    'internal_error', 'provider_authentication_failed', 'provider_access_denied',
    'provider_quota_exhausted', 'provider_configuration_error', 'input_token_limit',
    'input_content_size_limit', 'input_structure_limit', 'provider_count_unavailable'}
ACTOR = 'system:publication-screening'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _integer(value, minimum=0, maximum=10**12):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Use a bounded nonnegative integer.')
    return value


def _time(now):
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError('Use a finite nonnegative timestamp.')
    return now


@dataclass(frozen=True)
class ScreeningPolicy:
    policy_version: str
    prompt_sha256: str
    input_format_version: str
    model: str
    max_input_tokens: int
    max_output_tokens: int
    reserve_micro_usd: int
    max_attempts: int = 2
    model_configuration: dict = field(default_factory=dict)
    _snapshot: str = field(init=False, repr=False)

    def __post_init__(self):
        for value in (self.policy_version, self.input_format_version, self.model):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', value):
                raise ValueError('Use a bounded version/model identifier.')
        if not re.fullmatch(r'[0-9a-f]{64}', self.prompt_sha256):
            raise ValueError('Pin the exact prompt SHA-256.')
        _integer(self.max_input_tokens, 1, 1_000_000)
        _integer(self.max_output_tokens, 1, 32_000)
        _integer(self.reserve_micro_usd, 1)
        _integer(self.max_attempts, 1, 2)
        if type(self.model_configuration) is not dict:
            raise ValueError('Use plain nonsecret model configuration.')
        document = {key: getattr(self, key) for key in ('policy_version', 'prompt_sha256',
            'input_format_version', 'model', 'max_input_tokens', 'max_output_tokens',
            'reserve_micro_usd', 'max_attempts', 'model_configuration')}
        encoded = _json(document)
        if len(encoded.encode()) > 8192:
            raise ValueError('Model configuration is too large.')
        object.__setattr__(self, '_snapshot', encoded)

    @property
    def configuration(self):
        return json.loads(self._snapshot)

    @property
    def id(self):
        return hashlib.sha256(self._snapshot.encode()).hexdigest()


@dataclass(frozen=True)
class Lease:
    job_id: str
    attempt_id: str
    token: str
    publication_id: str
    revision_id: str
    author_id: str
    content_sha256: str
    policy_id: str
    expires_at: float
    reserved_micro_usd: int


def _control(session):
    return session.scalar(select(ScreeningControl).where(ScreeningControl.id == 1)
                          .with_for_update().execution_options(populate_existing=True))


def configure(db, *, policy, mode, monthly_budget_micro_usd, now):
    """Explicit trusted-operator action. A worker cannot silently re-enable itself."""
    if mode not in {'disabled', 'shadow', 'automatic'}:
        raise ValueError('Unknown screening mode.')
    _integer(monthly_budget_micro_usd)
    _time(now)
    with db.transaction(write=True) as session:
        control = _control(session)
        if control is None:
            control = ScreeningControl(id=1, mode='disabled', monthly_budget_micro_usd=0, updated_at=now)
            session.add(control)
        control.mode, control.policy_id = mode, policy.id
        control.monthly_budget_micro_usd, control.updated_at = monthly_budget_micro_usd, now
        # Keep any in-flight lease: a policy change cannot start another call
        # beside the one already dispatched under the previous configuration.
        session.flush()
        return {'mode': control.mode, 'policy_id': control.policy_id,
                'monthly_budget_micro_usd': control.monthly_budget_micro_usd}


def enqueue(session, publication, *, policy, now):
    """Enqueue within an explicitly authorized publication transaction.

    Ordinary requests have author consent. An operator policy migration uses
    publication_screening_adoption and records its distinct AccessEvent basis.
    Neither changing settings nor starting a worker creates this authority.
    """
    from .notebook import NotebookNote, NotebookRevision
    _time(now)
    if publication.decision is not None:
        return None
    existing = session.scalar(select(ScreeningJob).where(
        ScreeningJob.publication_id == publication.id, ScreeningJob.policy_id == policy.id))
    if existing:
        return existing
    target = session.execute(select(NotebookRevision.id, NotebookRevision.content_sha256)
        .join(NotebookNote, NotebookNote.id == NotebookRevision.note_id)
        .where(NotebookRevision.id == publication.revision_id,
               NotebookNote.author_id == publication.author_id)).one_or_none()
    if target is None:
        raise ValueError('The publication does not identify an owned exact revision.')
    job = ScreeningJob(id=str(uuid4()), publication_id=publication.id, revision_id=target.id,
        author_id=publication.author_id, content_sha256=target.content_sha256,
        policy_id=policy.id, policy=policy.configuration, status='queued', attempt_count=0,
        available_at=now, created_at=now)
    session.add(job)
    session.flush()
    return job


def _settle(session, attempt, now, actual_cost_micro_usd=None):
    if attempt.charged_micro_usd is not None:
        return
    charged = attempt.reserved_micro_usd if actual_cost_micro_usd is None else _integer(actual_cost_micro_usd)
    budget = session.get(ScreeningBudget, attempt.budget_month)
    if budget is None or budget.reserved_micro_usd < attempt.reserved_micro_usd:
        raise RuntimeError('Screening budget reservation is inconsistent.')
    budget.reserved_micro_usd -= attempt.reserved_micro_usd
    budget.spent_micro_usd += charged
    attempt.charged_micro_usd = charged
    attempt.usage_known = actual_cost_micro_usd is not None
    attempt.finished_at = now


def _release(control):
    control.active_job_id = control.lease_token = control.lease_expires_at = None


def lock_for_cancellation(session):
    """Lock before account/note locks, matching finish's control→account order."""
    return _control(session)


def cancel_publications(session, publication_ids, *, control, now, erase_results=False):
    """Revoke dispatch/results while retaining conservative usage commitments.

    Caller owns the control, author, note and publication locks in that order.
    An already dispatched provider call cannot be recalled; its full reservation
    remains charged and its eventual response has no publication authority. Keep
    the occupied slot until that call returns or expires, so cancellation cannot
    bypass the global concurrency bound. Cancelled jobs are never claimed again.
    """
    if not publication_ids:
        return
    rows = list(session.scalars(select(ScreeningJob).where(
        ScreeningJob.publication_id.in_(publication_ids)).order_by(ScreeningJob.id)
        .with_for_update().execution_options(populate_existing=True)))
    for job in rows:
        if control is not None and control.active_job_id == job.id:
            attempt = session.scalar(select(ScreeningAttempt).where(
                ScreeningAttempt.lease_token == control.lease_token).with_for_update())
            if attempt is None or attempt.job_id != job.id:
                raise RuntimeError('Screening lease is inconsistent.')
            _settle(session, attempt, now)
            attempt.outcome, attempt.error_code = 'cancelled', 'publication_unavailable'
        if job.status != 'cancelled':
            _terminal(job, 'cancelled', 'publication_unavailable', now)
    if erase_results and rows:
        for attempt in session.scalars(select(ScreeningAttempt).where(
                ScreeningAttempt.job_id.in_([row.id for row in rows])).with_for_update()):
            # Findings can quote note content. Keep only their commitment and
            # usage/decision metadata after permanent content erasure.
            attempt.result = None


def _terminal(job, status, code, now):
    job.status, job.error_code, job.finished_at = status, code, now


def _recover_expired(session, control, now):
    if not control.active_job_id or control.lease_expires_at > now:
        return
    job = session.get(ScreeningJob, control.active_job_id)
    attempt = session.scalar(select(ScreeningAttempt).where(ScreeningAttempt.lease_token == control.lease_token))
    if job is None or attempt is None:
        raise RuntimeError('Screening lease is inconsistent.')
    _settle(session, attempt, now)
    if job.status == 'cancelled':
        _release(control)
        return
    attempt.error_code = 'lease_expired'
    if control.mode != 'disabled' and control.policy_id == job.policy_id and job.attempt_count < job.policy['max_attempts']:
        job.status, job.error_code, job.available_at = 'queued', 'lease_expired', now + 5
    else:
        _terminal(job, 'human_review', 'lease_expired', now)
    _release(control)


def claim(db, *, policy, enabled, monthly_budget_micro_usd, now, lease_seconds=120, clock=None):
    """Reserve budget and one global lease; returns only identifiers, never content."""
    _time(now)
    _integer(monthly_budget_micro_usd)
    _integer(lease_seconds, 10, 600)
    with db.transaction(write=True) as session:
        control = _control(session)
        if clock is not None:
            now = _time(clock())
        if control is None:
            return None
        _recover_expired(session, control, now)
        if not enabled or control.mode == 'disabled' or control.policy_id != policy.id or control.active_job_id:
            return None
        # Stale policy requests stay available to human review; updates never
        # substitute a new model/prompt into an earlier author's request.
        stale = session.scalars(select(ScreeningJob).where(ScreeningJob.status == 'queued',
            ScreeningJob.policy_id != policy.id).limit(100))
        for job in stale:
            _terminal(job, 'human_review', 'policy_changed', now)
        history = aliased(ScreeningJob)
        last_turn = select(func.max(history.last_started_at)).where(
            history.author_id == ScreeningJob.author_id).correlate(ScreeningJob).scalar_subquery()
        job = session.scalar(select(ScreeningJob).where(ScreeningJob.status == 'queued',
            ScreeningJob.policy_id == policy.id, ScreeningJob.available_at <= now)
            .order_by(func.coalesce(last_turn, -1), ScreeningJob.created_at, ScreeningJob.id)
            .limit(1).with_for_update())
        if job is None:
            return None
        if digest(job.policy) != job.policy_id or job.policy != policy.configuration:
            _terminal(job, 'human_review', 'policy_changed', now)
            return None
        if job.attempt_count >= policy.max_attempts:
            _terminal(job, 'human_review', 'lease_expired', now)
            return None
        bucket = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m')
        budget = session.get(ScreeningBudget, bucket)
        if budget is None:
            budget = ScreeningBudget(month=bucket, spent_micro_usd=0, reserved_micro_usd=0)
            session.add(budget)
        reserve = policy.reserve_micro_usd
        limit = min(control.monthly_budget_micro_usd, monthly_budget_micro_usd)
        if budget.spent_micro_usd + budget.reserved_micro_usd + reserve > limit:
            _terminal(job, 'human_review', 'budget_exhausted', now)
            return None
        budget.reserved_micro_usd += reserve
        job.attempt_count += 1
        job.status, job.last_started_at, job.error_code = 'running', now, None
        # These audit models intentionally have no ORM relationships; persist
        # a newly opened budget bucket before inserting its referencing attempt.
        session.flush()
        token = str(uuid4())
        attempt = ScreeningAttempt(id=str(uuid4()), job_id=job.id, number=job.attempt_count,
            lease_token=token, budget_month=bucket, reserved_micro_usd=reserve,
            started_at=now, lease_expires_at=now + lease_seconds)
        session.add(attempt)
        control.active_job_id, control.lease_token, control.lease_expires_at = job.id, token, now + lease_seconds
        session.flush()
        return Lease(job.id, attempt.id, token, job.publication_id, job.revision_id, job.author_id,
                     job.content_sha256, job.policy_id, attempt.lease_expires_at, reserve)


def _active(session, control, lease, now):
    if control is None:
        return None
    _recover_expired(session, control, now)
    if control.active_job_id != lease.job_id or control.lease_token != lease.token:
        return None
    job, attempt = session.get(ScreeningJob, lease.job_id), session.get(ScreeningAttempt, lease.attempt_id)
    if job is None or attempt is None or attempt.lease_token != lease.token or attempt.job_id != job.id:
        return None
    if (job.publication_id, job.revision_id, job.author_id, job.content_sha256, job.policy_id) != (
            lease.publication_id, lease.revision_id, lease.author_id, lease.content_sha256, lease.policy_id):
        raise RuntimeError('Screening lease identity changed.')
    if now < attempt.started_at:
        raise ValueError('A screening result cannot precede its dispatch.')
    if job.status == 'cancelled':
        # The provider call has returned (or preflight stopped dispatch). Free
        # its slot, but never retain a late result or reapply publication.
        _release(control)
        return None
    if job.status != 'running':
        return None
    return job, attempt


def finish(db, lease, *, verdict, result, model_id, actual_cost_micro_usd, policy, enabled,
           automatic, now, publish, content_sha256, full_coverage, clock=None):
    """Commit a prevalidated complete result and optional publication together.

    publish(session, job, receipt, now) returns True only after applying the exact
    authorized publication transition. It must not commit or call a provider.
    False leaves an audit result but no automatic-publication receipt.
    """
    _time(now)
    if verdict not in OUTCOMES or type(result) is not dict or result.get('outcome') != verdict:
        raise ValueError('A validated screening outcome is required.')
    encoded = _json(result)
    if len(encoded.encode()) > 16384 or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', model_id):
        raise ValueError('Screening result metadata is invalid.')
    if actual_cost_micro_usd is not None:
        _integer(actual_cost_micro_usd)
    with db.transaction(write=True) as session:
        control = _control(session)
        if clock is not None:
            now = _time(clock())
        active = _active(session, control, lease, now)
        if active is None:
            return 'stale_lease'
        job, attempt = active
        _settle(session, attempt, now, actual_cost_micro_usd)
        attempt.outcome, attempt.model_id = verdict, model_id
        attempt.result, attempt.result_sha256 = json.loads(encoded), hashlib.sha256(encoded.encode()).hexdigest()
        if attempt.charged_micro_usd > attempt.reserved_micro_usd:
            _terminal(job, 'human_review', 'usage_exceeded_reservation', now)
        elif content_sha256 != job.content_sha256 or full_coverage is not True:
            _terminal(job, 'human_review', 'coverage_incomplete', now)
        elif not enabled or control.mode == 'disabled':
            _terminal(job, 'human_review', 'disabled', now)
        elif control.policy_id != policy.id or job.policy_id != policy.id or digest(job.policy) != job.policy_id:
            _terminal(job, 'human_review', 'policy_changed', now)
        elif verdict == 'human_review_needed':
            _terminal(job, 'human_review', None, now)
        elif not automatic or control.mode != 'automatic':
            _terminal(job, 'completed', None, now)
        elif session.get(AutomaticPublication, job.publication_id):
            _terminal(job, 'superseded', None, now)
        else:
            receipt = AutomaticPublication(publication_id=job.publication_id, job_id=job.id,
                attempt_id=attempt.id, revision_id=job.revision_id, content_sha256=job.content_sha256,
                policy_id=job.policy_id, model_id=model_id, actor=ACTOR,
                result_sha256=attempt.result_sha256, created_at=now)
            # Account/publication locks in the callback can wait. A timestamp
            # captured before those locks is not evidence that the lease is
            # still valid. A savepoint lets us retain usage/result evidence but
            # roll back ALL callback writes if expiry occurs during that wait.
            class ExpiredPublication(Exception):
                pass
            expired = False
            try:
                with session.begin_nested():
                    applied = publish(session, job, receipt, now)
                    if clock is not None:
                        now = _time(clock())
                        if now >= min(lease.expires_at, control.lease_expires_at):
                            raise ExpiredPublication
            except ExpiredPublication:
                expired = True
            if expired:
                attempt.error_code = 'lease_expired'
                _terminal(job, 'human_review', 'lease_expired', now)
            elif applied:
                receipt.created_at = now
                session.add(receipt)
                _terminal(job, 'published', None, now)
            else:
                _terminal(job, 'superseded', None, now)
        attempt.finished_at = now
        _release(control)
        session.flush()
        return job.status


def fail(db, lease, *, code, retryable, now, actual_cost_micro_usd=None, clock=None):
    """No raw exception text is retained. Unknown usage spends the full reserve."""
    if code not in ERRORS:
        raise ValueError('Use a defined non-sensitive screening failure code.')
    _time(now)
    with db.transaction(write=True) as session:
        control = _control(session)
        if clock is not None:
            now = _time(clock())
        active = _active(session, control, lease, now)
        if active is None:
            return 'stale_lease'
        job, attempt = active
        _settle(session, attempt, now, actual_cost_micro_usd)
        attempt.error_code = code
        if attempt.charged_micro_usd > attempt.reserved_micro_usd:
            _terminal(job, 'human_review', 'usage_exceeded_reservation', now)
        elif (retryable and code in TRANSIENT_ERRORS and job.attempt_count < job.policy['max_attempts']
                and control.mode != 'disabled' and control.policy_id == job.policy_id):
            job.status, job.error_code, job.available_at = 'queued', code, now + 5
        else:
            _terminal(job, 'human_review', code, now)
        _release(control)
        session.flush()
        return job.status
