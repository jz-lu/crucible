"""Bounded, explicitly authorized migration of human-only pending requests.

Default CLI behavior is a read-only plan. Applying an exact plan creates jobs
and operator AccessEvents atomically; it does not claim fresh author consent, alter
the original requests, call a model, or enable the worker. No HTTP endpoint.
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

from . import beta_access, notebook, notebook_moderation, notebook_trash
from . import publication_screening_jobs as jobs
from .publication_screening_models import ScreeningJob

ACTION = 'publication_screening_adopted'
FORMAT = 'crucible-screening-adoption-plan-v1'
MAX_BATCH = 25


class ExactRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    publication_id: UUID
    author_id: UUID
    note_id: UUID
    revision_id: UUID
    revision: int = Field(strict=True, ge=1, le=100)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    requested_at: float = Field(ge=0)


class AdoptionPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    format: Literal['crucible-screening-adoption-plan-v1']
    actor_id: UUID
    policy_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    cutoff: float = Field(ge=0)
    after: UUID | None
    next_after: UUID | None
    requests: list[ExactRequest] = Field(max_length=MAX_BATCH)
    plan_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def _active(session, identifier):
    user = session.get(beta_access.User, identifier, populate_existing=True)
    state = session.get(beta_access.AccountState, identifier, populate_existing=True)
    member = session.get(beta_access.BetaMembership, identifier, populate_existing=True)
    return user if (user is not None and (state is None or state.suspended_at is None)
                    and member is not None and member.status == 'active') else None


def _operator(session, settings, actor_id):
    actor = _active(session, actor_id)
    if not beta_access.is_admin(settings, actor):
        raise HTTPException(403, 'An active configured access administrator is required.')
    return actor


def _policy(settings):
    from .publication_screening_worker import policy_for
    if settings.publication_screening_mode != 'automatic':
        raise HTTPException(409, 'This migration requires the configured automatic publication policy.')
    return policy_for(settings)


def _pending(publication):
    return all(getattr(publication, field) is None for field in (
        'decision','decision_source','reviewer_id','reviewed_at','reason','review_key','review_sha256')) \
        and not publication.test_only_self_review


def preview(db, settings, *, actor_id, cutoff=None, after=None, limit=MAX_BATCH, clock=time.time):
    """Read only exact eligible metadata. A fixed cutoff/cursor bounds later pages."""
    actor_id = str(UUID(str(actor_id)))
    after = str(UUID(str(after))) if after is not None else None
    now = jobs._time(clock())
    cutoff = float(now if cutoff is None else jobs._time(cutoff))
    if cutoff > now or type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError('Use a past cutoff and a batch of 1–25 requests.')
    policy = _policy(settings)
    publication, revision, note = notebook.NotebookPublication, notebook.NotebookRevision, notebook.NotebookNote
    with db.transaction() as session:
        _operator(session,settings,actor_id)
        query = select(publication.id.label('publication_id'),publication.author_id,
            note.id.label('note_id'),revision.id.label('revision_id'),revision.revision,
            revision.content_sha256,publication.requested_at).join(revision,revision.id==publication.revision_id) \
            .join(note,note.id==revision.note_id) \
            .join(beta_access.BetaMembership,beta_access.BetaMembership.user_id==publication.author_id) \
            .outerjoin(beta_access.AccountState,beta_access.AccountState.user_id==publication.author_id) \
            .where(note.author_id==publication.author_id,note.latest_revision>0,
                publication.requested_at<=cutoff,publication.test_only_self_review.is_(False),
                *(getattr(publication,field).is_(None) for field in (
                    'decision','decision_source','reviewer_id','reviewed_at','reason','review_key','review_sha256')),
                beta_access.BetaMembership.status=='active',beta_access.AccountState.suspended_at.is_(None),
                note.id.not_in(notebook_trash.deleted_ids()),
                revision.id.not_in(notebook_moderation.withdrawn_ids()),
                ~select(ScreeningJob.id).where(ScreeningJob.publication_id==publication.id).exists())
        if after is not None:
            query = query.where(publication.id>after)
        rows = session.execute(query.order_by(publication.id).limit(limit+1)).mappings().all()
        values = [ExactRequest.model_validate(dict(row)).model_dump(mode='json') for row in rows[:limit]]
    result = {'format':FORMAT,'actor_id':actor_id,'policy_id':policy.id,'cutoff':cutoff,
        'after':after,'next_after':values[-1]['publication_id'] if len(rows)>limit else None,'requests':values}
    return {**result,'plan_sha256':jobs.digest(result)}


def apply_plan(db, settings, plan, *, actor_id, authorization_reference, reason, clock=time.time):
    """Trusted host action only; no fresh author consent or scientific review is asserted.

    Locks control → all involved users in UUID order → note → publication → jobs.
    Audit and jobs commit together. Replaying even an ambiguous commit cannot
    enqueue a second job, rewrite its policy, or reset attempts/costs.
    """
    parsed = AdoptionPlan.model_validate(plan)
    values = parsed.model_dump(mode='json')
    plan_hash = values.pop('plan_sha256')
    actor_id = str(UUID(str(actor_id)))
    if jobs.digest(values) != plan_hash or values['actor_id'] != actor_id:
        raise ValueError('The exact reviewed plan and operator identity must match.')
    identifiers = [item['publication_id'] for item in values['requests']]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError('Use distinct exact publication requests.')
    if (not isinstance(authorization_reference,str) or not 1<=len(authorization_reference.strip())<=200
            or not isinstance(reason,str) or not 1<=len(reason.strip())<=500
            or any(ord(char)<32 for char in authorization_reference+reason)):
        raise ValueError('Provide a bounded plain-text authorization reference and reason.')
    policy = _policy(settings)
    now = jobs._time(clock())
    if policy.id != parsed.policy_id or parsed.cutoff>now or any(
            item.requested_at>parsed.cutoff for item in parsed.requests):
        raise HTTPException(409, 'The plan cutoff or current publication policy no longer matches.')
    outcomes = []
    with db.transaction(write=True) as session:
        control = jobs.lock_for_cancellation(session)
        if control is None or control.mode!='automatic' or control.policy_id!=policy.id:
            raise HTTPException(409, 'The live screening control must enable the exact automatic policy before adoption.')
        for identifier in sorted({actor_id,*(str(item.author_id) for item in parsed.requests)}):
            beta_access.lock_user(session,identifier)
        _operator(session,settings,actor_id)
        for item in parsed.requests:
            identity = item.model_dump(mode='json')
            outcome = {'publication_id':identity['publication_id']}
            outcomes.append(outcome)
            if _active(session,identity['author_id']) is None:
                outcome['status'] = 'author_ineligible'; continue
            note = session.scalar(select(notebook.NotebookNote).where(
                notebook.NotebookNote.id==identity['note_id']).with_for_update()
                .execution_options(populate_existing=True))
            target = session.get(notebook.NotebookRevision,identity['revision_id'],populate_existing=True)
            publication = session.scalar(select(notebook.NotebookPublication).where(
                notebook.NotebookPublication.id==identity['publication_id']).with_for_update()
                .execution_options(populate_existing=True))
            if (note is None or target is None or publication is None
                    or note.author_id!=identity['author_id'] or target.note_id!=note.id
                    or publication.author_id!=identity['author_id'] or publication.revision_id!=target.id
                    or target.revision!=identity['revision'] or target.content_sha256!=identity['content_sha256']
                    or publication.requested_at!=identity['requested_at']):
                outcome['status'] = 'identity_changed'; continue
            if (note.latest_revision==0 or notebook_trash.active(session,note.id) is not None
                    or session.get(notebook_moderation.PublicationWithdrawal,target.id) is not None
                    or not _pending(publication)):
                outcome['status'] = 'request_ineligible'; continue
            existing = session.scalar(select(ScreeningJob.id).where(ScreeningJob.publication_id==publication.id)
                .with_for_update().limit(1))
            if existing is not None:
                outcome['status'] = 'already_screened'; continue
            timestamp = jobs._time(clock())
            job = jobs.enqueue(session,publication,policy=policy,now=timestamp)
            audit = beta_access.AccessEvent(id=str(uuid4()),user_id=identity['author_id'],actor_id=actor_id,
                action=ACTION,created_at=timestamp,details={
                    'format':'crucible-screening-adoption-audit-v1',
                    'authority_basis':'platform_policy_migration',
                    'publication_basis':'original_publication_request','fresh_author_consent_recorded':False,
                    'authorization_reference':authorization_reference,'reason':reason,
                    'plan_sha256':plan_hash,'plan_cutoff':parsed.cutoff,
                    **identity,'job_id':job.id,'policy_id':policy.id})
            session.add(audit)
            session.flush()
            outcome.update(status='queued',job_id=job.id,audit_event_id=audit.id)
    return {'format':'crucible-screening-adoption-result-v1','plan_sha256':plan_hash,
        'queued':sum(item['status']=='queued' for item in outcomes),'outcomes':outcomes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor-id',required=True)
    parser.add_argument('--cutoff',type=float)
    parser.add_argument('--after')
    parser.add_argument('--limit',type=int,default=MAX_BATCH)
    parser.add_argument('--apply-plan',type=Path)
    parser.add_argument('--execute-reviewed',action='store_true')
    parser.add_argument('--authorization-reference')
    parser.add_argument('--reason')
    args = parser.parse_args(argv)
    if bool(args.apply_plan) != args.execute_reviewed:
        parser.error('Applying requires both --apply-plan and --execute-reviewed.')
    if args.apply_plan and (args.cutoff is not None or args.after is not None or args.limit!=MAX_BATCH):
        parser.error('Apply uses the exact saved plan; do not change its paging arguments.')
    if args.apply_plan and (not args.authorization_reference or not args.reason):
        parser.error('Applying requires an authorization reference and reason.')
    from .config import load_settings
    from .publication_screening_worker import worker_database
    db = None
    try:
        settings = load_settings()
        db = worker_database(settings.database_url)
        if args.apply_plan:
            with args.apply_plan.open('rb') as source:
                raw = source.read(65537)
            if len(raw)>65536:
                raise ValueError('The plan is too large.')
            result = apply_plan(db,settings,json.loads(raw),actor_id=args.actor_id,
                authorization_reference=args.authorization_reference,reason=args.reason)
        else:
            result = preview(db,settings,actor_id=args.actor_id,cutoff=args.cutoff,
                after=args.after,limit=args.limit)
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception:
        # Neither database exceptions nor participant content belong in logs.
        print(json.dumps({'status':'error','error_code':'screening_adoption_failed',
            'apply_outcome':'unknown_inspect_before_retry' if args.apply_plan else 'not_attempted'}))
        return 1
    finally:
        if db is not None:
            db.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
