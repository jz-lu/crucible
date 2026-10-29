"""Reviewed research tasks, exact evidence, explicit spending and independent awards.

No submitted code is executed. All mutations reuse the app's membership, CSRF,
agent-scope and account-lock boundary. Task locks serialize reward boundaries;
allowance wallets are locked in account-ID order for multi-account refunds.
"""
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select

from . import beta_access, bounty_allowance as allowance, notebook, notebook_moderation, notebook_numbers, notebook_trash, protocol_proposals, research_reviews
from .db import User
from .bounty_models import (BountyAllowanceEvent as Event, BountyAward, BountyDecision, BountyPolicy,
                           BountySubmission, BountyTask, BountyTerms, BountyWallet, BountyWriteReceipt)

PUBLIC_STATES = ('open', 'frozen', 'completed', 'cancelled')


class NoteRef(research_reviews.SourceRef):
    pass


class TermsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1200)
    background_tags: list[str] = Field(default_factory=list, max_length=8)
    scope: int = Field(ge=1, le=5, strict=True)
    completion_criteria: str = Field(min_length=1, max_length=6000)
    verification_plan: str = Field(min_length=1, max_length=6000)
    source_notes: list[NoteRef] = Field(default_factory=list, max_length=12)

    @field_validator('title', 'summary', 'completion_criteria', 'verification_plan')
    @classmethod
    def readable(cls, value):
        notebook._inspect(value)
        if not value.strip():
            raise ValueError('Supply the task and its completion and verification criteria.')
        return value.strip()

    @field_validator('background_tags')
    @classmethod
    def tags(cls, value):
        notebook._inspect(value)
        normalized = [tag.strip() for tag in value]
        if any(not tag or len(tag) > 50 for tag in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('Use up to eight distinct short background tags.')
        return normalized

    @field_validator('source_notes')
    @classmethod
    def distinct_sources(cls, value):
        if len({(ref.note_id, ref.revision) for ref in value}) != len(value):
            raise ValueError('Cite each exact source once.')
        return value


class CreateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    terms: TermsInput
    idempotency_key: UUID


class VersionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)
    idempotency_key: UUID


class RevisionInput(CreateInput):
    expected_version: int = Field(ge=1, strict=True)


class TermsVersionInput(VersionInput):
    terms_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class AdmissionInput(TermsVersionInput):
    policy_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirm_cost_units: int = Field(ge=1, le=100, strict=True)


class BoostInput(AdmissionInput):
    units: int = Field(default=1, ge=1, le=10, strict=True)


class ReasonInput(VersionInput):
    reason: str = Field(min_length=1, max_length=6000)

    @field_validator('reason')
    @classmethod
    def explanation(cls, value):
        notebook._inspect(value)
        if not value.strip(): raise ValueError('Record a reason.')
        return value.strip()


class AdmissionChecks(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: bool = Field(strict=True)
    evidence: bool = Field(strict=True)
    verification: bool = Field(strict=True)
    duplicates: bool = Field(strict=True)
    ownership: bool = Field(strict=True)


class AdmissionDecisionInput(ReasonInput):
    terms_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['approved', 'changes_requested', 'declined']
    checks: AdmissionChecks


class SubmissionInput(TermsVersionInput):
    contribution_note: NoteRef
    claim: Literal['solution', 'partial']
    explanation: str = Field(min_length=1, max_length=6000)

    @field_validator('explanation')
    @classmethod
    def explain(cls, value):
        notebook._inspect(value)
        if not value.strip(): raise ValueError('Explain what this exact contribution establishes.')
        return value.strip()


class DecisionInput(ReasonInput):
    kind: Literal['poster', 'pipeline']
    decision: Literal['approved', 'declined']


def source(session, user, ref, *, published=False, owned=False):
    """Resolve exact pins under notebook visibility; numbers never grant access."""
    if owned and ref.note_id.startswith('curated:'):
        raise HTTPException(422, 'Submit your own independently published contribution note.')
    if not ref.note_id.startswith('curated:'):
        note = session.get(notebook.NotebookNote, ref.note_id)
        revision = session.scalar(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == ref.note_id, notebook.NotebookRevision.revision == ref.revision))
        if note is None or revision is None or notebook_trash.active(session, ref.note_id):
            raise HTTPException(404, 'Exact note revision is unavailable.')
        if owned and note.author_id != user.id:
            raise HTTPException(403, 'A contribution must cite a note you authored.')
        if published:
            publication = session.scalar(select(notebook.NotebookPublication).where(
                notebook.NotebookPublication.revision_id == revision.id,
                notebook.NotebookPublication.decision == 'approved',
                notebook.NotebookPublication.test_only_self_review.is_(False),
                notebook.NotebookPublication.reviewer_id != note.author_id,
                notebook.NotebookPublication.revision_id.not_in(notebook_moderation.withdrawn_ids())))
            if publication is None:
                raise HTTPException(409, 'This exact note revision needs independent publication approval.')
    record = research_reviews.source_snapshot(session, user, ref)
    return {**ref.model_dump(), 'title': record['content'].get('title', ''), 'author_id': record['author_id']}


def terms_for(session, task):
    record = session.scalar(select(BountyTerms).where(BountyTerms.task_id == task.id, BountyTerms.revision == task.revision))
    if record is None or record.content_sha256 != task.terms_sha256 or allowance.digest(record.terms) != task.terms_sha256:
        raise HTTPException(409, 'The immutable task terms do not match their recorded identity.')
    return record


def require_sources(session, user, terms):
    for raw in terms.terms['source_notes']:
        source(session, user, NoteRef.model_validate(raw), published=True)


def current_decisions(session, submission):
    rows = session.scalars(select(BountyDecision).where(BountyDecision.submission_id == submission.id)
                          .order_by(BountyDecision.task_version, BountyDecision.id))
    return {row.kind: row for row in rows}


def live_boosts(session, task):
    refunded = select(Event.original_event_id).where(Event.kind == 'refund', Event.original_event_id.is_not(None))
    return list(session.scalars(select(Event).where(Event.task_id == task.id, Event.kind == 'boost',
                                Event.id.not_in(refunded)).order_by(Event.task_sequence, Event.id)))


def refund_many(session, task, events, now):
    # Never take another User lock while holding a task/wallet. Authenticated
    # mutations hold their own User first; account revocation only locks User.
    identifiers = sorted({event.user_id for event in events})
    if identifiers:
        list(session.scalars(select(BountyWallet).where(BountyWallet.user_id.in_(identifiers))
                             .order_by(BountyWallet.user_id).with_for_update()))
    return [allowance.refund(session, event, task, now) for event in events]


def refresh_outcome(session, task, settings, now):
    """Qualifying evidence freezes an offer; raw claims cannot lock the board."""
    claims = list(session.scalars(select(BountySubmission).where(BountySubmission.task_id == task.id,
                  BountySubmission.claim == 'solution', BountySubmission.status.in_(('pending', 'qualified')))
                  .order_by(BountySubmission.receipt_order)))
    qualified = next((claim for claim in claims if claim.status == 'qualified'), None)
    if qualified is None:
        task.qualified_submission_id = None
        if task.status == 'frozen': task.status = 'open'
        task.reward_points = task.base_reward_points + sum(e.reward_points for e in live_boosts(session, task))
        return
    task.status, task.qualified_submission_id = 'frozen', qualified.id
    # Qualification freezes spending, not the journal. An earlier claim may
    # still be rejected; keep funding intact until the final winner is selected.
    task.reward_points = task.base_reward_points + sum(e.reward_points for e in live_boosts(session, task)
                         if e.task_sequence <= qualified.reward_boundary and e.user_id != qualified.author_id)
    # A later approval never jumps an earlier unresolved solution claim.
    if claims[0].id != qualified.id:
        return
    decisions = current_decisions(session, qualified)
    poster, pipeline = decisions.get('poster'), decisions.get('pipeline')
    if poster is None or pipeline is None or poster.decision != 'approved' or pipeline.decision != 'approved':
        return
    if len({task.owner_id, qualified.author_id, pipeline.actor_id}) != 3 or poster.actor_id != task.owner_id:
        raise HTTPException(409, 'Distinct poster, solver and independent pipeline approval are required.')
    if pipeline.actor_id not in settings.reviewer_ids:
        raise HTTPException(409, 'An active configured reviewer must approve this exact contribution.')
    for identifier in (task.owner_id, qualified.author_id, pipeline.actor_id):
        if not allowance.eligible(session, settings, identifier, now):
            raise HTTPException(409, 'An approval participant no longer has active site access.')
    terms = terms_for(session, task)
    if any(d.terms_id != terms.id or d.evidence_sha256 != qualified.note_sha256 for d in (poster, pipeline)):
        raise HTTPException(409, 'Approvals must match the exact terms and contribution evidence.')
    # Lock every potentially refunded wallet as one set before note locks and
    # before applying any compensation. Oppositely ordered subsets can deadlock.
    funding_accounts = sorted({e.user_id for e in live_boosts(session, task)})
    if funding_accounts:
        list(session.scalars(select(BountyWallet).where(BountyWallet.user_id.in_(funding_accounts))
                             .order_by(BountyWallet.user_id).with_for_update()))
    # Moderation/trash also lock the note, so withdrawal cannot race past an
    # award's final exact-evidence check. Account locks precede these note locks.
    note_ids = {qualified.note_id} | {pin['note_id'] for pin in terms.terms['source_notes']
                                    if not pin['note_id'].startswith('curated:')}
    list(session.scalars(select(notebook.NotebookNote).where(notebook.NotebookNote.id.in_(sorted(note_ids)))
                         .order_by(notebook.NotebookNote.id).with_for_update().execution_options(populate_existing=True)))
    author = session.get(User, qualified.author_id)
    require_sources(session, author, terms)
    source(session, author, NoteRef(note_id=qualified.note_id, revision=qualified.note_revision,
                                    content_sha256=qualified.note_sha256), published=True, owned=True)
    refund_many(session, task, [e for e in live_boosts(session, task)
                               if e.task_sequence > qualified.reward_boundary or e.user_id == qualified.author_id], now)
    points = task.base_reward_points + sum(e.reward_points for e in live_boosts(session, task)
                                          if e.task_sequence <= qualified.reward_boundary)
    if session.get(BountyAward, task.id):
        raise HTTPException(409, 'This task already has its single recorded award.')
    session.add(BountyAward(task_id=task.id, submission_id=qualified.id, user_id=qualified.author_id,
                           policy_id=task.policy_id, terms_id=terms.id, poster_decision_id=poster.id,
                           pipeline_decision_id=pipeline.id, reward_boundary=qualified.reward_boundary,
                           points=points, created_at=now))
    task.status, task.reward_points, qualified.status = 'completed', points, 'awarded'
    session.flush()


def register_routes(app, db, settings, clock, user_for, throttle):
    def account(request, session, mutation=False, *, human=False):
        if human and 'authorization' in request.headers:
            raise HTTPException(403, 'Bounty review requires a human browser account.')
        return user_for(request, session, mutation=mutation)

    def reviewer(user):
        if not protocol_proposals.is_reviewer(settings, user):
            raise HTTPException(403, 'A configured reviewer is required.')

    def task_for(session, user, identifier, *, lock=False, private_review=False):
        query = select(BountyTask).where(BountyTask.id == str(identifier))
        if lock: query = query.with_for_update().execution_options(populate_existing=True)
        task = session.scalar(query)
        visible = task and (task.owner_id == user.id or task.status in PUBLIC_STATES or
                  (private_review and protocol_proposals.is_reviewer(settings, user) and task.reservation_id is not None))
        if not visible: raise HTTPException(404, 'Bounty not found.')
        return task

    def version(task, body):
        if task.version != body.expected_version or (hasattr(body, 'terms_sha256') and task.terms_sha256 != body.terms_sha256):
            raise HTTPException(409, 'This bounty changed. Read its current terms and retry deliberately.')

    def tick(task, now):
        task.version += 1
        task.sequence += 1
        task.updated_at = now

    def identity(user):
        return {'id': user.id, 'handle': user.handle}

    def decisions_data(record):
        return {key: getattr(record, key) for key in ('id', 'kind', 'decision', 'actor_id', 'reason', 'checks', 'terms_id', 'evidence_sha256', 'created_at')}

    def task_data(session, user, task, *, detail=False, human=True, owners=None, pending_counts=None):
        owner = owners[task.owner_id] if owners is not None else session.get(User, task.owner_id)
        pending = pending_counts.get(task.id, 0) if pending_counts is not None else session.scalar(select(func.count()).select_from(BountySubmission).where(
            BountySubmission.task_id == task.id, BountySubmission.claim == 'solution',
            BountySubmission.status.in_(('pending', 'qualified'))))
        own = task.owner_id == user.id
        capabilities = {'edit': own and task.status in ('draft', 'changes_requested'),
                        'request_admission': own and task.status in ('draft', 'changes_requested'),
                        'withdraw': own and task.status in ('draft', 'pending', 'changes_requested'),
                        'cancel': own and task.status == 'open' and not pending,
                        'boost': task.status == 'open',
                        'submit': not own and task.status == 'open',
                        'review_admission': human and not own and protocol_proposals.is_reviewer(settings, user)
                                            and task.status == 'pending'}
        result = {key: getattr(task, key) for key in ('id', 'title', 'summary', 'background_tags', 'scope', 'status', 'version',
                  'revision', 'terms_sha256', 'reward_points', 'created_at', 'updated_at')}
        result.update(owner=identity(owner), pending_claim_count=pending, capabilities=capabilities)
        if not detail: return result
        terms = terms_for(session, task)
        result['terms'] = terms.terms
        result['base_reward_points'] = task.base_reward_points
        result['admission_policy_id'] = task.policy_id
        reservation = session.get(Event, task.reservation_id) if task.reservation_id else None
        result['reservation'] = ({'id': reservation.id, 'units': reservation.units, 'month': reservation.month,
            'status': 'consumed' if task.status in PUBLIC_STATES else 'released' if task.status in ('declined', 'withdrawn') else 'held'}
            if reservation else None)
        result['sources'] = []
        for pin in terms.terms['source_notes']:
            try:
                result['sources'].append(source(session, user, NoteRef.model_validate(pin), published=task.status in PUBLIC_STATES))
            except HTTPException:
                result['sources'].append({**pin, 'unavailable': True})
        rows = list(session.scalars(select(BountySubmission).where(BountySubmission.task_id == task.id)
                                   .order_by(BountySubmission.receipt_order).limit(100)))
        result['submissions'] = []
        for row in rows:
            author = session.get(User, row.author_id)
            pin = {'note_id': row.note_id, 'revision': row.note_revision, 'content_sha256': row.note_sha256}
            try: source(session, user, NoteRef.model_validate(pin), published=True)
            except HTTPException: pin = {**pin, 'unavailable': True}
            decisions = current_decisions(session, row)
            result['submissions'].append({'id': row.id, 'author': identity(author), 'contribution_note': pin,
                'claim': row.claim, 'explanation': row.explanation, 'status': row.status, 'created_at': row.created_at,
                'offered_reward_points': row.offered_reward_points, 'reward_boundary': row.reward_boundary,
                'poster_decision': decisions_data(decisions['poster']) if 'poster' in decisions else None,
                'pipeline_decision': decisions_data(decisions['pipeline']) if 'pipeline' in decisions else None,
                'capabilities': {'accept': human and own and row.author_id != user.id and task.status in ('open', 'frozen') and row.claim == 'solution',
                                 'reject': human and own and row.author_id != user.id and task.status in ('open', 'frozen') and row.claim == 'solution',
                                 'qualify': human and protocol_proposals.is_reviewer(settings, user) and user.id not in (task.owner_id, row.author_id)
                                            and task.status in ('open', 'frozen') and row.claim == 'solution'}})
        result['decisions'] = [decisions_data(row) for row in session.scalars(select(BountyDecision)
                               .where(BountyDecision.task_id == task.id).order_by(BountyDecision.task_version, BountyDecision.id).limit(200))]
        award = session.get(BountyAward, task.id)
        result['award'] = ({'submission_id': award.submission_id, 'user_id': award.user_id,
                            'points': award.points, 'created_at': award.created_at} if award else None)
        return result

    def response(session, user, task, request, effect=None):
        result = {'bounty': task_data(session, user, task, detail=True, human='authorization' not in request.headers),
                  'policy': allowance.policy_data(allowance.current_policy(session, clock())),
                  'allowance': allowance.summary(session, user, settings, clock())}
        if effect is not None: result['receipt'] = effect
        return result

    def mutation(request, body, identifier, action, apply, *, human=False):
        throttle(request, 'bounty-write', 120, 3600)
        commitment = allowance.digest({'action': action, 'task_id': str(identifier) if identifier else None,
                         'body': body.model_dump(mode='json', exclude={'idempotency_key'})})
        with db.transaction(write=True) as session:
            if action.startswith('assess:'):
                # Settlement can involve a different winner than the claim being
                # rejected. Lock all currently eligible participants in a stable
                # order BEFORE the task and ordinary authenticated actor lock.
                # Version checking after the task lock rejects changed snapshots.
                observed = account(request, session, human=human)
                observed_task = task_for(session, observed, identifier, private_review=human)
                active = select(BountySubmission.id).where(BountySubmission.task_id == observed_task.id,
                    BountySubmission.claim == 'solution', BountySubmission.status.in_(('pending', 'qualified')))
                participants = {observed.id, observed_task.owner_id}
                participants.update(session.scalars(select(BountySubmission.author_id).where(BountySubmission.id.in_(active))))
                participants.update(session.scalars(select(BountyDecision.actor_id).where(BountyDecision.submission_id.in_(active))))
                list(session.scalars(select(User).where(User.id.in_(sorted(participants))).order_by(User.id)
                                     .with_for_update().execution_options(populate_existing=True)))
            user = account(request, session, True, human=human)
            receipt = session.get(BountyWriteReceipt, (user.id, str(body.idempotency_key)))
            if receipt:
                if receipt.request_sha256 != commitment:
                    raise HTTPException(409, 'This identifier already belongs to a different bounty operation.')
                return response(session, user, task_for(session, user, receipt.task_id, private_review=human), request, receipt.effect)
            task = task_for(session, user, identifier, lock=True, private_review=human) if identifier else None
            if task: version(task, body)
            now = clock()
            task, effect = apply(session, user, task, now)
            session.flush()
            effect = {'action': action, 'task_id': task.id, 'version': task.version, 'created_at': now, **effect}
            session.add(BountyWriteReceipt(user_id=user.id, idempotency_key=str(body.idempotency_key),
                         request_sha256=commitment, task_id=task.id, effect=effect, created_at=now))
            session.flush()
            return response(session, user, task, request, effect)

    def owner_only(user, task):
        if task.owner_id != user.id: raise HTTPException(403, 'Only the bounty owner can change its draft or request.')

    def new_terms(session, user, task, body, now):
        value = body.terms.model_dump(mode='json')
        for pin in body.terms.source_notes: source(session, user, pin)
        task.terms_sha256 = allowance.digest(value)
        for key in ('title', 'summary', 'background_tags', 'scope'): setattr(task, key, value[key])
        session.flush()
        record = BountyTerms(id=str(uuid4()), task_id=task.id, revision=task.revision,
                             terms=value, content_sha256=task.terms_sha256, created_by=user.id, created_at=now)
        session.add(record)
        session.flush()

    @app.get('/api/me/bounty-allowance')
    def allowance_read(request: Request, offset: int = Query(default=0, ge=0, le=10000),
                       limit: int = Query(default=20, ge=1, le=50)):
        with db.transaction() as session:
            user = account(request, session)
            events = list(session.scalars(select(Event).where(Event.user_id == user.id)
                          .order_by(Event.wallet_version.desc()).offset(offset).limit(limit + 1)))
            return {'allowance': allowance.summary(session, user, settings, clock()),
                    'policy': allowance.policy_data(allowance.current_policy(session, clock())),
                    'ledger': [{key: getattr(e, key) for key in ('id', 'kind', 'month', 'wallet_version', 'units', 'available_delta',
                               'reward_points', 'task_id', 'original_event_id', 'created_at')} for e in events[:limit]],
                    'pagination': {'offset': offset, 'limit': limit, 'has_more': len(events) > limit}}

    def listing(request, status, sort, offset, limit, review=False):
        with db.transaction() as session:
            user = account(request, session, human=review)
            if review: reviewer(user)
            query = select(BountyTask)
            if review:
                pending = select(BountySubmission.task_id).where(BountySubmission.claim == 'solution',
                          BountySubmission.status.in_(('pending', 'qualified')))
                query = query.where(or_(BountyTask.status == 'pending', BountyTask.id.in_(pending)))
            elif status == 'mine':
                contributions = select(BountySubmission.task_id).where(BountySubmission.author_id == user.id)
                query = query.where(or_(BountyTask.owner_id == user.id,
                              and_(BountyTask.status.in_(PUBLIC_STATES), BountyTask.id.in_(contributions))))
            elif status == 'past': query = query.where(BountyTask.status.in_(('completed', 'cancelled')))
            else: query = query.where(BountyTask.status.in_(('open', 'frozen')))
            order = {'concrete': BountyTask.scope.desc(), 'open': BountyTask.scope.asc(),
                     'points-high': BountyTask.reward_points.desc(), 'points-low': BountyTask.reward_points.asc()}[sort]
            rows = list(session.scalars(query.order_by(order, BountyTask.created_at, BountyTask.id).offset(offset).limit(limit + 1)))
            displayed = rows[:limit]
            owners = {row.id: row for row in session.scalars(select(User).where(User.id.in_({t.owner_id for t in displayed})))}
            counts = dict(session.execute(select(BountySubmission.task_id, func.count()).where(
                BountySubmission.task_id.in_([t.id for t in displayed]), BountySubmission.claim == 'solution',
                BountySubmission.status.in_(('pending', 'qualified'))).group_by(BountySubmission.task_id)).all())
            return {'bounties': [task_data(session, user, row, human='authorization' not in request.headers,
                                           owners=owners, pending_counts=counts) for row in displayed],
                    'pagination': {'offset': offset, 'limit': limit, 'has_more': len(rows) > limit},
                    'policy': allowance.policy_data(allowance.current_policy(session, clock()))}

    @app.get('/api/bounties')
    def board(request: Request, status: Literal['open', 'mine', 'past'] = 'open',
              sort: Literal['concrete', 'open', 'points-high', 'points-low'] = 'concrete',
              offset: int = Query(default=0, ge=0, le=10000), limit: int = Query(default=6, ge=1, le=25)):
        return listing(request, status, sort, offset, limit)

    @app.get('/api/bounties/review-queue')
    def review_queue(request: Request, offset: int = Query(default=0, ge=0, le=10000), limit: int = Query(default=25, ge=1, le=25)):
        return listing(request, 'open', 'concrete', offset, limit, True)

    @app.get('/api/bounties/{task_id}')
    def read_task(task_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session)
            return response(session, user, task_for(session, user, task_id,
                            private_review='authorization' not in request.headers), request)

    @app.post('/api/bounties')
    def create_task(body: CreateInput, request: Request):
        def apply(session, user, task, now):
            if session.scalar(select(func.count()).select_from(BountyTask).where(BountyTask.owner_id == user.id,
                                       BountyTask.status.in_(('draft', 'pending', 'changes_requested')))) >= 100:
                raise HTTPException(409, 'Finish or withdraw an existing draft before creating another.')
            task = BountyTask(id=str(uuid4()), owner_id=user.id, status='draft', version=1, revision=1,
                             terms_sha256='', title='', summary='', background_tags=[], scope=body.terms.scope,
                             reward_points=0, base_reward_points=0, sequence=0, created_at=now, updated_at=now)
            session.add(task)
            new_terms(session, user, task, body, now)
            return task, {}
        return mutation(request, body, None, 'create', apply)

    @app.post('/api/bounties/{task_id}/revisions')
    def revise_task(task_id: UUID, body: RevisionInput, request: Request):
        def apply(session, user, task, now):
            owner_only(user, task)
            if task.status not in ('draft', 'changes_requested'):
                raise HTTPException(409, 'Open or pending task terms cannot be rewritten.')
            task.revision += 1
            tick(task, now)
            new_terms(session, user, task, body, now)
            return task, {}
        return mutation(request, body, task_id, 'revise', apply)

    @app.post('/api/bounties/{task_id}/admission-requests')
    def admission_request(task_id: UUID, body: AdmissionInput, request: Request):
        def apply(session, user, task, now):
            owner_only(user, task)
            if task.status not in ('draft', 'changes_requested'):
                raise HTTPException(409, 'This task is not awaiting an admission request.')
            require_sources(session, user, terms_for(session, task))
            wallet, policy = allowance.prepare_spending(session, user, settings, now, body.policy_id)
            cost = policy.configuration['proposal_cost_units']
            if body.confirm_cost_units != cost:
                raise HTTPException(409, 'Confirm the current proposal allowance cost.')
            tick(task, now)
            if task.reservation_id is None:
                event = allowance.append(session, wallet, policy, now, kind='reservation', event_key='reserve:' + task.id,
                         delta=-cost, units=cost, points=policy.configuration['proposal_reward_points'], task=task)
                task.reservation_id, task.policy_id = event.id, policy.id
            task.status = 'pending'
            return task, {'reservation_id': task.reservation_id}
        return mutation(request, body, task_id, 'request_admission', apply)

    @app.post('/api/bounties/{task_id}/withdrawals')
    def withdraw_task(task_id: UUID, body: ReasonInput, request: Request):
        def apply(session, user, task, now):
            owner_only(user, task)
            if task.status not in ('draft', 'pending', 'changes_requested'):
                raise HTTPException(409, 'Only a draft or pending admission can be withdrawn.')
            tick(task, now)
            if task.reservation_id:
                allowance.refund(session, session.get(Event, task.reservation_id), task, now, kind='release')
            task.status = 'withdrawn'
            return task, {'reason': body.reason}
        return mutation(request, body, task_id, 'withdraw', apply)

    @app.post('/api/bounties/{task_id}/admission-decisions')
    def admission_decision(task_id: UUID, body: AdmissionDecisionInput, request: Request):
        def apply(session, user, task, now):
            reviewer(user)
            if task.owner_id == user.id: raise HTTPException(403, 'An independent reviewer must admit the bounty.')
            if task.status != 'pending': raise HTTPException(409, 'This bounty has no pending admission request.')
            terms = terms_for(session, task)
            tick(task, now)
            decision = BountyDecision(id=str(uuid4()), task_id=task.id, terms_id=terms.id, actor_id=user.id,
                       kind='admission', decision=body.decision, reason=body.reason, checks=body.checks.model_dump(),
                       task_version=task.version, created_at=now)
            session.add(decision)
            reservation = session.get(Event, task.reservation_id)
            if body.decision == 'approved':
                if not all(body.checks.model_dump().values()):
                    raise HTTPException(422, 'Every admission check must pass before the task opens.')
                require_sources(session, user, terms)
                wallet = allowance.wallet(session, task.owner_id, now)
                allowance.append(session, wallet, session.get(BountyPolicy, reservation.policy_id), now,
                    kind='consumption', event_key='consume:' + reservation.id, delta=0, units=reservation.units,
                    points=reservation.reward_points, task=task, original=reservation, bucket=reservation.month)
                task.status = 'open'
                task.base_reward_points = task.reward_points = reservation.reward_points
            elif body.decision == 'declined':
                allowance.refund(session, reservation, task, now, kind='release')
                task.status = 'declined'
            else: task.status = 'changes_requested'
            return task, {'decision_id': decision.id}
        return mutation(request, body, task_id, 'admission_decision', apply, human=True)

    @app.post('/api/bounties/{task_id}/boosts')
    def boost_task(task_id: UUID, body: BoostInput, request: Request):
        def apply(session, user, task, now):
            if task.status != 'open': raise HTTPException(409, 'Only an open bounty can receive a boost.')
            require_sources(session, user, terms_for(session, task))
            wallet, policy = allowance.prepare_spending(session, user, settings, now, body.policy_id)
            cost = body.units * policy.configuration['boost_cost_units']
            if body.confirm_cost_units != cost:
                raise HTTPException(409, 'Confirm the total monthly allowance used by this boost.')
            tick(task, now)
            points = body.units * policy.configuration['points_per_boost']
            event = allowance.append(session, wallet, policy, now, kind='boost', event_key='boost:' + str(body.idempotency_key),
                                     delta=-cost, units=cost, points=points, task=task)
            task.reward_points += points
            return task, {'boost_id': event.id, 'spent_units': cost, 'added_points': points}
        return mutation(request, body, task_id, 'boost', apply)

    @app.post('/api/bounties/{task_id}/submissions')
    def submit(task_id: UUID, body: SubmissionInput, request: Request):
        def apply(session, user, task, now):
            if task.status != 'open': raise HTTPException(409, 'This bounty is not accepting new contributions.')
            if task.owner_id == user.id: raise HTTPException(403, 'The bounty owner cannot claim their own task.')
            terms = terms_for(session, task)
            require_sources(session, user, terms)
            source(session, user, body.contribution_note, published=True, owned=True)
            if session.scalar(select(BountySubmission.id).where(BountySubmission.task_id == task.id,
                    BountySubmission.author_id == user.id, BountySubmission.note_id == body.contribution_note.note_id,
                    BountySubmission.note_revision == body.contribution_note.revision)):
                raise HTTPException(409, 'This exact contribution has already been submitted to the bounty.')
            if session.scalar(select(func.count()).select_from(BountySubmission).where(BountySubmission.task_id == task.id)) >= 100:
                raise HTTPException(409, 'This pilot task has reached its contribution limit; contact its steward.')
            if body.claim == 'solution' and session.scalar(select(BountySubmission.id).where(BountySubmission.task_id == task.id,
                    BountySubmission.author_id == user.id, BountySubmission.claim == 'solution',
                    BountySubmission.status.in_(('pending', 'qualified')))):
                raise HTTPException(409, 'Resolve your existing claimed solution before submitting another.')
            boundary = task.sequence
            tick(task, now)
            record = BountySubmission(id=str(uuid4()), task_id=task.id, terms_id=terms.id, author_id=user.id,
                     note_id=body.contribution_note.note_id, note_revision=body.contribution_note.revision,
                     note_sha256=body.contribution_note.content_sha256, claim=body.claim, explanation=body.explanation,
                     status='pending' if body.claim == 'solution' else 'partial', receipt_order=task.sequence,
                     reward_boundary=boundary, offered_reward_points=task.reward_points, created_at=now)
            session.add(record)
            return task, {'submission_id': record.id, 'reward_boundary': boundary}
        return mutation(request, body, task_id, 'submit', apply)

    @app.post('/api/bounties/{task_id}/submissions/{submission_id}/decisions')
    def assess(task_id: UUID, submission_id: UUID, body: DecisionInput, request: Request):
        def apply(session, user, task, now):
            if task.status not in ('open', 'frozen'): raise HTTPException(409, 'This bounty cannot receive completion decisions.')
            record = session.get(BountySubmission, str(submission_id))
            if record is None or record.task_id != task.id: raise HTTPException(404, 'Contribution not found.')
            if record.claim != 'solution': raise HTTPException(409, 'A partial contribution does not request the completion award.')
            if record.status == 'declined' and body.decision == 'approved':
                raise HTTPException(409, 'A declined contribution needs a new exact evidence submission.')
            if body.kind == 'poster':
                if task.owner_id != user.id or record.author_id == user.id:
                    raise HTTPException(403, 'Only the distinct named poster may make this decision.')
            else:
                reviewer(user)
                if user.id in (task.owner_id, record.author_id):
                    raise HTTPException(403, 'Pipeline approval requires a reviewer independent of poster and solver.')
            terms = terms_for(session, task)
            if terms.id != record.terms_id: raise HTTPException(409, 'The contribution refers to different task terms.')
            if body.decision == 'approved':
                require_sources(session, user, terms)
                source(session, user, NoteRef(note_id=record.note_id, revision=record.note_revision,
                                             content_sha256=record.note_sha256), published=True)
            tick(task, now)
            decision = BountyDecision(id=str(uuid4()), task_id=task.id, submission_id=record.id, terms_id=terms.id,
                       evidence_sha256=record.note_sha256, actor_id=user.id, kind=body.kind,
                       decision=body.decision, reason=body.reason, checks={}, task_version=task.version, created_at=now)
            session.add(decision)
            if body.decision == 'declined': record.status = 'declined'
            elif body.kind == 'pipeline': record.status = 'qualified'
            session.flush()
            refresh_outcome(session, task, settings, now)
            return task, {'decision_id': decision.id, 'submission_id': record.id}
        return mutation(request, body, task_id, 'assess:' + str(submission_id), apply, human=True)

    @app.post('/api/bounties/{task_id}/cancellations')
    def cancel(task_id: UUID, body: ReasonInput, request: Request):
        def apply(session, user, task, now):
            owner_only(user, task)
            if task.status != 'open' or session.scalar(select(BountySubmission.id).where(BountySubmission.task_id == task.id,
                    BountySubmission.claim == 'solution', BountySubmission.status.in_(('pending', 'qualified')))):
                raise HTTPException(409, 'Resolve outstanding claims before cancelling an open task.')
            tick(task, now)
            refunds = refund_many(session, task, live_boosts(session, task), now)
            task.status = 'cancelled'
            return task, {'reason': body.reason, 'refunds': [{'units': e.units, 'available_units': e.available_delta,
                    'original_month': e.month} for e in refunds]}
        return mutation(request, body, task_id, 'cancel', apply)
