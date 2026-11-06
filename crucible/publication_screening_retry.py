"""Explicit, audited repair of exact publication requests blocked at preflight.

This is not a general retry queue. An administrator reviews a short, fresh plan
of named requests. A new policy gets a new job; old attempts, decisions and costs
remain unchanged. No provider call, HTTP route or automatic sweep lives here.
"""
import argparse
import json
from pathlib import Path
import time
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import load_only

from . import beta_access, notebook, notebook_moderation, notebook_trash
from . import publication_screening_adoption as adoption
from . import publication_screening_jobs as jobs
from .publication_screening_models import ScreeningAttempt, ScreeningJob

ACTION = 'publication_screening_retried'
FORMAT = 'crucible-screening-retry-plan-v1'
MAX_BATCH = 25
MAX_PLAN_AGE_SECONDS = 3600
PREFLIGHT_ERRORS = frozenset({'input_too_large', 'input_token_limit',
    'input_content_size_limit', 'input_structure_limit'})


class ExactRetry(adoption.ExactRequest):
    previous_job_id: UUID
    previous_policy_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    previous_attempt_id: UUID
    previous_attempt_number: int = Field(strict=True, ge=1, le=1)
    previous_error_code: Literal['input_too_large', 'input_token_limit',
                                'input_content_size_limit', 'input_structure_limit']
    previous_finished_at: float = Field(ge=0)


class SkippedRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    publication_id: UUID
    status: Literal['request_ineligible', 'author_ineligible', 'not_preflight_failure',
                    'same_policy', 'active_lease']


class RetryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    format: Literal['crucible-screening-retry-plan-v1']
    actor_id: UUID
    policy_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    created_at: float = Field(ge=0)
    requests: list[ExactRetry] = Field(max_length=MAX_BATCH)
    skipped: list[SkippedRequest] = Field(max_length=MAX_BATCH)
    plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _records(session, publication_id, *, lock=False):
    publication = session.scalar(select(notebook.NotebookPublication).where(
        notebook.NotebookPublication.id == publication_id))
    if publication is None:
        return None
    revision = session.get(notebook.NotebookRevision, publication.revision_id, options=[
        load_only(notebook.NotebookRevision.id, notebook.NotebookRevision.note_id,
                  notebook.NotebookRevision.revision, notebook.NotebookRevision.content_sha256,
                  raiseload=True)])
    if revision is None:
        return None
    query = select(notebook.NotebookNote).where(notebook.NotebookNote.id == revision.note_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    note = session.scalar(query)
    if lock:
        publication = session.scalar(select(notebook.NotebookPublication).where(
            notebook.NotebookPublication.id == publication_id).with_for_update()
            .execution_options(populate_existing=True))
    if (note is None or publication is None or note.author_id != publication.author_id
            or publication.revision_id != revision.id):
        return None
    return note, revision, publication


def _identity(records):
    note, revision, publication = records
    return {'publication_id': publication.id, 'author_id': publication.author_id,
        'note_id': note.id, 'revision_id': revision.id, 'revision': revision.revision,
        'content_sha256': revision.content_sha256, 'requested_at': publication.requested_at}


def _request_status(session, records):
    note, revision, publication = records
    if adoption._active(session, publication.author_id) is None:
        return 'author_ineligible'
    if (note.latest_revision == 0 or notebook_trash.active(session, note.id) is not None
            or session.get(notebook_moderation.PublicationWithdrawal, revision.id) is not None
            or not adoption._pending(publication)):
        return 'request_ineligible'
    return None


def _latest(session, publication_id, *, lock=False):
    query = select(ScreeningJob).where(ScreeningJob.publication_id == publication_id).order_by(
        ScreeningJob.created_at.desc(), ScreeningJob.id).limit(1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalar(query)


def _failure(session, job, identity, *, new_policy_id, active_job_id, lock=False):
    if job is None:
        return None, 'not_preflight_failure'
    if job.policy_id == new_policy_id:
        return None, 'same_policy'
    if job.id == active_job_id:
        return None, 'active_lease'
    if (job.publication_id != identity['publication_id'] or job.revision_id != identity['revision_id']
            or job.author_id != identity['author_id'] or job.content_sha256 != identity['content_sha256']
            or job.status != 'human_review' or job.error_code not in PREFLIGHT_ERRORS
            or job.finished_at is None or job.attempt_count != 1
            or type(job.policy) is not dict or jobs.digest(job.policy) != job.policy_id
            or type(job.policy.get('model_configuration')) is not dict
            or job.policy['model_configuration'].get('publication_mode') != 'automatic'):
        return None, 'not_preflight_failure'
    query = select(ScreeningAttempt).where(ScreeningAttempt.job_id == job.id).order_by(
        ScreeningAttempt.number.desc(), ScreeningAttempt.id).limit(1)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    attempt = session.scalar(query)
    if (attempt is None or attempt.number != job.attempt_count
            or attempt.error_code != job.error_code or attempt.finished_at is None
            or attempt.outcome is not None or attempt.result is not None or attempt.result_sha256 is not None
            or attempt.model_id is not None or attempt.usage_known is not True
            or attempt.charged_micro_usd != 0):
        return None, 'not_preflight_failure'
    # Deterministic preflight failures never retry automatically. More than one
    # attempt needs individual investigation of prior dispatch/cost ambiguity.
    if session.scalar(select(ScreeningAttempt.id).where(ScreeningAttempt.job_id == job.id,
            ScreeningAttempt.id != attempt.id).limit(1)):
        return None, 'not_preflight_failure'
    return {**identity, 'previous_job_id': job.id, 'previous_policy_id': job.policy_id,
        'previous_attempt_id': attempt.id, 'previous_attempt_number': attempt.number,
        'previous_error_code': attempt.error_code, 'previous_finished_at': attempt.finished_at}, None


def preview(db, settings, *, actor_id, publication_ids, clock=time.time):
    """Read-only plan for an explicit allowlist; never enumerate all requests."""
    actor_id = str(UUID(str(actor_id)))
    if not isinstance(publication_ids, (list, tuple)) or not 1 <= len(publication_ids) <= MAX_BATCH:
        raise ValueError('Name 1–25 exact publication requests.')
    identifiers = [str(UUID(str(identifier))) for identifier in publication_ids]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Name distinct publication requests.')
    now, policy = jobs._time(clock()), adoption._policy(settings)
    requests, skipped = [], []
    with db.transaction() as session:
        adoption._operator(session, settings, actor_id)
        control = session.get(jobs.ScreeningControl, 1)
        for identifier in identifiers:
            records = _records(session, identifier)
            reason = _request_status(session, records) if records else 'request_ineligible'
            if reason is None:
                request, reason = _failure(session, _latest(session, identifier), _identity(records),
                    new_policy_id=policy.id, active_job_id=control.active_job_id if control else None)
                if reason is None:
                    requests.append(ExactRetry.model_validate(request).model_dump(mode='json'))
            if reason is not None:
                skipped.append({'publication_id': identifier, 'status': reason})
    plan = {'format': FORMAT, 'actor_id': actor_id, 'policy_id': policy.id,
        'created_at': now, 'requests': requests, 'skipped': skipped}
    return {**plan, 'plan_sha256': jobs.digest(plan)}


def _replay_audit(session, job, identity, plan_hash, actor_id):
    if (job.revision_id != identity['revision_id'] or job.author_id != identity['author_id']
            or job.content_sha256 != identity['content_sha256']):
        return None
    audits = session.scalars(select(beta_access.AccessEvent).where(
        beta_access.AccessEvent.action == ACTION, beta_access.AccessEvent.user_id == identity['author_id'],
        beta_access.AccessEvent.actor_id == actor_id,
        beta_access.AccessEvent.details['job_id'].as_string() == job.id).limit(2))
    for audit in audits:
        details = audit.details
        if (type(details) is dict and details.get('job_id') == job.id
                and details.get('policy_id') == job.policy_id and details.get('plan_sha256') == plan_hash
                and details.get('format') == 'crucible-screening-retry-audit-v1'
                and details.get('authority_basis') == 'platform_policy_repair'
                and details.get('publication_basis') == 'original_publication_request'
                and details.get('fresh_author_consent_recorded') is False
                and all(details.get(key) == value for key, value in identity.items())):
            return audit.id
    return None


def apply_plan(db, settings, plan, *, actor_id, authorization_reference, reason, clock=time.time):
    """New-policy jobs and repair audit commit together; originals stay intact.

    Locks control → sorted operator/authors → note → publication → jobs/attempt.
    Human decisions, cancellation and access changes are rechecked under those
    same locks. A repeated exact plan returns its existing job without dispatch.
    """
    parsed = RetryPlan.model_validate(plan)
    values = parsed.model_dump(mode='json')
    plan_hash = values.pop('plan_sha256')
    actor_id = str(UUID(str(actor_id)))
    identifiers = [str(item.publication_id) for item in (*parsed.requests, *parsed.skipped)]
    if (jobs.digest(values) != plan_hash or values['actor_id'] != actor_id
            or not 1 <= len(identifiers) <= MAX_BATCH or len(set(identifiers)) != len(identifiers)):
        raise ValueError('Use the exact bounded reviewed plan and its operator.')
    if (not isinstance(authorization_reference, str) or not 1 <= len(authorization_reference.strip()) <= 200
            or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500
            or any(ord(char) < 32 for char in authorization_reference + reason)):
        raise ValueError('Provide a bounded plain-text authorization reference and reason.')
    now, policy = jobs._time(clock()), adoption._policy(settings)
    if policy.id != parsed.policy_id or not 0 <= now - parsed.created_at <= MAX_PLAN_AGE_SECONDS:
        raise HTTPException(409, 'The repair plan must be at most one hour old and match the current policy.')
    outcomes = [item.model_dump(mode='json') for item in parsed.skipped]
    with db.transaction(write=True) as session:
        control = jobs.lock_for_cancellation(session)
        if control is None or control.mode != 'automatic' or control.policy_id != policy.id:
            raise HTTPException(409, 'Enable the exact automatic policy before applying its repair plan.')
        for identifier in sorted({actor_id, *(str(item.author_id) for item in parsed.requests)}):
            beta_access.lock_user(session, identifier)
        adoption._operator(session, settings, actor_id)
        for item in parsed.requests:
            identity = item.model_dump(mode='json')
            outcome = {'publication_id': identity['publication_id']}
            outcomes.append(outcome)
            records = _records(session, identity['publication_id'], lock=True)
            if records is None or any(identity[key] != value for key, value in _identity(records).items()):
                outcome['status'] = 'identity_changed'; continue
            existing = session.scalar(select(ScreeningJob).where(
                ScreeningJob.publication_id == identity['publication_id'], ScreeningJob.policy_id == policy.id)
                .with_for_update().execution_options(populate_existing=True))
            if not 0 <= jobs._time(clock()) - parsed.created_at <= MAX_PLAN_AGE_SECONDS:
                raise HTTPException(409, 'The repair plan expired while waiting for its records.')
            if existing is not None:
                audit_id = _replay_audit(session, existing, identity, plan_hash, actor_id)
                outcome.update(status='already_retried' if audit_id else 'already_screened', job_id=existing.id)
                if audit_id:
                    outcome['audit_event_id'] = audit_id
                continue
            status = _request_status(session, records)
            if status is not None:
                outcome['status'] = status; continue
            previous = _latest(session, identity['publication_id'], lock=True)
            current, status = _failure(session, previous, _identity(records), new_policy_id=policy.id,
                active_job_id=control.active_job_id, lock=True)
            if status is not None or current != identity:
                outcome['status'] = status or 'history_changed'; continue
            timestamp = jobs._time(clock())
            if not 0 <= timestamp - parsed.created_at <= MAX_PLAN_AGE_SECONDS:
                raise HTTPException(409, 'The repair plan expired before enqueueing.')
            if timestamp <= previous.created_at:
                raise HTTPException(409, 'The new job timestamp must follow the previous job.')
            publication = records[2]
            job = jobs.enqueue(session, publication, policy=policy, now=timestamp)
            audit = beta_access.AccessEvent(id=str(uuid4()), user_id=identity['author_id'], actor_id=actor_id,
                action=ACTION, created_at=timestamp, details={
                    'format': 'crucible-screening-retry-audit-v1',
                    'authority_basis': 'platform_policy_repair',
                    'publication_basis': 'original_publication_request', 'fresh_author_consent_recorded': False,
                    'authorization_reference': authorization_reference, 'reason': reason,
                    'plan_sha256': plan_hash, 'plan_created_at': parsed.created_at,
                    **identity, 'job_id': job.id, 'policy_id': policy.id})
            session.add(audit)
            session.flush()
            outcome.update(status='queued', job_id=job.id, audit_event_id=audit.id)
    return {'format': 'crucible-screening-retry-result-v1', 'plan_sha256': plan_hash,
        'queued': sum(item['status'] == 'queued' for item in outcomes), 'outcomes': outcomes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor-id', required=True)
    parser.add_argument('--publication-id', action='append', default=[])
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--execute-reviewed', action='store_true')
    parser.add_argument('--authorization-reference')
    parser.add_argument('--reason')
    args = parser.parse_args(argv)
    if bool(args.apply_plan) != args.execute_reviewed:
        parser.error('Applying requires both --apply-plan and --execute-reviewed.')
    if args.apply_plan:
        if args.publication_id or not args.authorization_reference or not args.reason:
            parser.error('Apply the exact saved plan with an authorization reference and reason; do not add IDs.')
    elif not 1 <= len(args.publication_id) <= MAX_BATCH:
        parser.error('Preview requires 1–25 explicit --publication-id values.')
    from .config import load_settings
    from .publication_screening_worker import worker_database
    db = None
    try:
        settings = load_settings()
        db = worker_database(settings.database_url)
        if args.apply_plan:
            with args.apply_plan.open('rb') as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError('Repair plan exceeds its bounded format.')
            result = apply_plan(db, settings, json.loads(raw), actor_id=args.actor_id,
                authorization_reference=args.authorization_reference, reason=args.reason)
        else:
            result = preview(db, settings, actor_id=args.actor_id, publication_ids=args.publication_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # A database exception or provider configuration can contain secrets.
        print(json.dumps({'status': 'failed', 'error_code': 'screening_retry_failed',
            'apply_outcome': 'unknown_inspect_before_retry' if args.apply_plan else 'not_attempted'}))
        return 1
    finally:
        if db is not None:
            db.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
