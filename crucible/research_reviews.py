"""Exact-revision research review requests; recommendations never activate schemes.

Packets are private to the requester and configured reviewers. Authored tags or
imported note metadata cannot create an assessment. Source bodies are captured by
the server from accessible revisions, never accepted as caller-supplied evidence.
"""
import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, UniqueConstraint, and_, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from . import notebook, protocol_proposals, proposal_retractions, notebook_moderation
from .db import Base, User

PURPOSES = ('source_check', 'map_nomination', 'scoring_nomination', 'visualization_nomination', 'highlight_nomination', 'procedure')
RETIRED_PURPOSES = ('technical_review',)
PROPOSAL_PURPOSES = ('map_nomination', 'scoring_nomination', 'visualization_nomination', 'highlight_nomination', 'procedure')
SCOPE = 'A research recommendation on the frozen evidence below. It does not publish notes, change a visualization, activate a challenge, award points or select a highlight.'


class ResearchRequest(Base):
    __tablename__ = 'research_review_requests'
    __table_args__ = (UniqueConstraint('author_id', 'idempotency_key', name='uq_research_request_key'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id'), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    purpose: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(240))
    question: Mapped[str] = mapped_column(String(10000))
    sources: Mapped[list] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class ResearchAssessment(Base):
    __tablename__ = 'research_assessments'
    __table_args__ = (UniqueConstraint('reviewer_id', 'idempotency_key', name='uq_research_assessment_key'),)
    request_id: Mapped[str] = mapped_column(ForeignKey('research_review_requests.id'), primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(32))
    checks: Mapped[dict] = mapped_column(JSON)
    findings: Mapped[str] = mapped_column(String(20000))
    limitations: Mapped[str] = mapped_column(String(10000))
    test_only_self_review: Mapped[bool] = mapped_column(Boolean)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class SourceRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note_id: str = Field(min_length=1, max_length=108)
    revision: int = Field(ge=1, le=100, strict=True)
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class RequestInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    purpose: Literal['source_check', 'map_nomination', 'scoring_nomination', 'visualization_nomination', 'highlight_nomination', 'procedure']
    title: str = Field(min_length=1, max_length=240)
    question: str = Field(min_length=1, max_length=10000)
    sources: list[SourceRef] = Field(min_length=1, max_length=8)
    idempotency_key: UUID

    @field_validator('title', 'question')
    @classmethod
    def text(cls, value):
        if not value.strip():
            raise ValueError('Explain the requested decision.')
        notebook._inspect(value)
        return value


class Checks(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sources: Literal['not_checked', 'checked', 'gaps']
    mathematics: Literal['not_checked', 'checked', 'gaps']
    reproduction: Literal['not_checked', 'checked', 'gaps']


class AssessmentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    packet_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    outcome: Literal['recommend', 'changes_requested', 'decline']
    checks: Checks
    findings: str = Field(min_length=1, max_length=20000)
    limitations: str = Field(min_length=1, max_length=10000)
    idempotency_key: UUID

    @field_validator('findings', 'limitations')
    @classmethod
    def text(cls, value):
        if not value.strip():
            raise ValueError('Record evidence and the limits of the review.')
        notebook._inspect(value)
        return value


def source_snapshot(session, user, ref):
    if ref.note_id.startswith('curated:'):
        note = next((n for n in notebook.curated_collection() if n['id'] == ref.note_id), None)
        revision = next((r for r in note['revisions'] if r['revision'] == ref.revision), None) if note else None
        if revision is None:
            raise HTTPException(404, 'Source revision not found.')
        # A packet owns its frozen copy; it must not mutate the shared cache.
        content = json.loads(json.dumps(revision['content']))
        author_id = None
        origin = 'curated-research'
    else:
        note = session.get(notebook.NotebookNote, ref.note_id)
        revision = session.scalar(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == ref.note_id, notebook.NotebookRevision.revision == ref.revision))
        if note is None or revision is None or notebook.notebook_trash.active(session, ref.note_id):
            raise HTTPException(404, 'Source revision not found.')
        if note.author_id != user.id:
            shared = session.scalar(select(notebook.NotebookPublication).where(
                notebook.NotebookPublication.revision_id == revision.id,
                notebook.NotebookPublication.decision == 'approved',
                notebook.NotebookPublication.revision_id.not_in(notebook_moderation.withdrawn_ids()),
                notebook.NotebookPublication.test_only_self_review.is_(False)))
            if shared is None:
                raise HTTPException(404, 'Source revision not found.')
        content, author_id, origin = revision.content, note.author_id, 'account-note'
    if notebook._hash(content) != ref.content_sha256:
        raise HTTPException(409, 'The source hash does not match the requested revision.')
    return {**ref.model_dump(), 'author_id': author_id, 'origin': origin, 'content': content}


def packet_hash(record):
    return notebook._hash({'purpose': record.purpose, 'title': record.title,
                          'question': record.question, 'sources': record.sources})


def register_routes(app, db, settings, clock, user_for, throttle):
    def account(request, session, mutation=False):
        if 'authorization' in request.headers:
            raise HTTPException(403, 'Research review actions currently require your browser account.')
        user = user_for(request, session, mutation=mutation)
        if mutation:
            # Match notebook/protocol writes: serialize account-wide quotas
            # and retry keys across different requests on PostgreSQL too.
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
        return user

    def get_record(session, user, identifier, lock=False):
        query = select(ResearchRequest).where(ResearchRequest.id == str(identifier))
        if lock:
            query = query.with_for_update()
        record = session.scalar(query)
        if record is None or (record.author_id != user.id and not protocol_proposals.is_reviewer(settings, user)):
            raise HTTPException(404, 'Research request not found.')
        return record

    def serialize(session, record, user, *, detail=True):
        if notebook_moderation.packet_is_withheld(session, record, user, settings):
            return {'id': record.id, 'purpose': record.purpose, 'title': 'Review packet withheld',
                'created_at': record.created_at, 'question': notebook_moderation.NOTICE,
                'sources': [], 'assessment': None, 'can_review': False, 'can_retract': False,
                'withdrawn_source': True, 'packet_sha256': packet_hash(record),
                'scope': 'A source revision was withdrawn by a moderator. The frozen packet is retained for restricted audit.'}
        assessment = session.get(ResearchAssessment, record.id)
        retired = record.purpose in RETIRED_PURPOSES
        withdrawal = proposal_retractions.get(session, proposal_retractions.RESEARCH, record.id)
        self_review = record.author_id == user.id or any(s['author_id'] == user.id for s in record.sources)
        decision = None
        if assessment:
            decision = {key: getattr(assessment, key) for key in ('outcome', 'created_at', 'checks', 'findings', 'limitations', 'test_only_self_review')}
            reviewer = session.get(User, assessment.reviewer_id)
            decision['reviewer'] = {'handle': reviewer.handle}
            decision['effective'] = not assessment.test_only_self_review or (not settings.production and settings.allow_self_review)
        return {'id': record.id, 'purpose': record.purpose, 'title': record.title, 'retired': retired,
                **(proposal_retractions.detail(session, withdrawal, user) if detail else
                    proposal_retractions.compact(withdrawal.created_at if withdrawal else None)),
                'can_retract': record.author_id == user.id and record.purpose in PROPOSAL_PURPOSES and withdrawal is None,
                'created_at': record.created_at, 'question': record.question if detail else None,
                'sources': record.sources if detail else None, 'packet_sha256': packet_hash(record),
                'assessment': decision if detail else ({'outcome': decision['outcome'], 'effective': decision['effective'],
                    'test_only_self_review': decision['test_only_self_review']} if decision else None),
                **({'primary_source': {key: record.sources[0][key] for key in ('note_id', 'revision', 'content_sha256')}} if not detail else {}),
                'can_review': not retired and not assessment and withdrawal is None and protocol_proposals.is_reviewer(settings, user) and
                    (not self_review or protocol_proposals.can_self_review(settings, user)),
                'self_review': self_review, 'scope': SCOPE}

    @app.post('/api/notebook/research-requests')
    def create_request(body: RequestInput, request: Request):
        throttle(request, 'research-request', 30, 3600)
        commit = notebook._hash(body.model_dump(mode='json', exclude={'idempotency_key'}))
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            replay = session.scalar(select(ResearchRequest).where(ResearchRequest.author_id == user.id,
                ResearchRequest.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commit:
                    raise HTTPException(409, 'This identifier was used for a different research request.')
                return {'request': serialize(session, replay, user)}
            identities = [(s.note_id, s.revision) for s in body.sources]
            if len(set(identities)) != len(identities):
                raise HTTPException(422, 'Include each source revision once.')
            count = session.scalar(select(func.count()).select_from(ResearchRequest).where(ResearchRequest.author_id == user.id))
            if count >= 200:
                raise HTTPException(409, 'Research request capacity reached; existing records are preserved.')
            sources = [source_snapshot(session, user, ref) for ref in body.sources]
            if len(notebook._bytes(sources)) > 256 * 1024:
                raise HTTPException(422, 'Choose a smaller review packet; the source notes remain unchanged.')
            record = ResearchRequest(id=str(uuid4()), author_id=user.id, created_at=clock(),
                purpose=body.purpose, title=body.title, question=body.question, sources=sources,
                idempotency_key=str(body.idempotency_key), request_sha256=commit)
            session.add(record)
            session.flush()
            return {'request': serialize(session, record, user)}

    @app.get('/api/notebook/research-requests')
    def list_requests(request: Request, review: bool = False,
                      after: str | None = Query(default=None, max_length=36),
                      limit: int = Query(default=30, ge=1, le=100),
                      curated_proposals: bool = False,
                      source_note_id: str | None = Query(default=None, min_length=1, max_length=108),
                      source_revision: str | None = Query(default=None, pattern=r"^(?:[1-9][0-9]?|100)$"),
                      source_sha256: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$")):
        filters = (source_note_id, source_revision, source_sha256)
        if any(value is not None for value in filters) and not all(value is not None for value in filters):
            raise HTTPException(422, 'Supply the source note ID, revision and content hash together.')
        if curated_proposals and (review or any(value is not None for value in filters)):
            raise HTTPException(422, 'The curated proposal index cannot be combined with review or exact-source filters.')
        if not curated_proposals and limit > 50:
            raise HTTPException(422, 'Use a limit of at most 50 for research request packets.')
        with db.transaction() as session:
            user = account(request, session)
            if curated_proposals:
                # A compact index over actual requests, including assessed ones.
                # Never load a frozen source body merely to label a list entry.
                primary = (ResearchRequest.sources[0]['note_id'].as_string(),
                    ResearchRequest.sources[0]['revision'].as_integer(),
                    ResearchRequest.sources[0]['content_sha256'].as_string())
                withdrawal = proposal_retractions.ProposalRetraction
                query = (select(ResearchRequest.id, ResearchRequest.purpose, *primary,
                    ResearchAssessment.outcome, ResearchAssessment.test_only_self_review, withdrawal.created_at)
                    .select_from(ResearchRequest).outerjoin(ResearchAssessment,
                        ResearchAssessment.request_id == ResearchRequest.id)
                    .outerjoin(withdrawal, and_(withdrawal.target_kind == proposal_retractions.RESEARCH,
                        withdrawal.target_id == ResearchRequest.id))
                    .where(ResearchRequest.purpose.in_(PROPOSAL_PURPOSES), primary[0].startswith('curated:')))
                if not protocol_proposals.is_reviewer(settings, user):
                    query = query.where(ResearchRequest.author_id == user.id)
                if after:
                    query = query.where(ResearchRequest.id > after)
                rows = session.execute(query.order_by(ResearchRequest.id).limit(limit + 1)).all()
                records = [{'id': identifier, 'purpose': purpose,
                    **proposal_retractions.compact(withdrawn_at),
                    'primary_source': {'note_id': note_id, 'revision': number, 'content_sha256': digest},
                    'assessment': {'outcome': outcome, 'test_only_self_review': bool(test_only),
                        'effective': not test_only or (not settings.production and settings.allow_self_review)} if outcome else None}
                    for identifier, purpose, note_id, number, digest, outcome, test_only, withdrawn_at in rows[:limit]]
                return {'requests': records, 'next_after': rows[limit - 1][0] if len(rows) > limit else None,
                        'can_review': protocol_proposals.is_reviewer(settings, user)}
            if review and not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, 'A configured reviewer is required.')
            query = select(ResearchRequest)
            if review:
                query = query.where(~ResearchRequest.id.in_(select(ResearchAssessment.request_id)),
                    ResearchRequest.purpose.not_in(RETIRED_PURPOSES),
                    ~ResearchRequest.id.in_(proposal_retractions.target_ids(proposal_retractions.RESEARCH)))
            else:
                query = query.where(ResearchRequest.author_id == user.id)
            if source_note_id is not None:
                query = query.where(ResearchRequest.sources[0]['note_id'].as_string() == source_note_id,
                    ResearchRequest.sources[0]['revision'].as_integer() == int(source_revision),
                    ResearchRequest.sources[0]['content_sha256'].as_string() == source_sha256)
            if after:
                anchor = session.get(ResearchRequest, after)
                if anchor is None or (not review and anchor.author_id != user.id):
                    raise HTTPException(422, 'Invalid research request cursor.')
                query = query.where(or_(ResearchRequest.created_at > anchor.created_at,
                    and_(ResearchRequest.created_at == anchor.created_at, ResearchRequest.id > anchor.id)))
            rows = session.scalars(query.order_by(ResearchRequest.created_at, ResearchRequest.id).limit(limit + 1)).all()
            return {'requests': [serialize(session, r, user, detail=False) for r in rows[:limit]],
                    'next_after': rows[limit-1].id if len(rows) > limit else None,
                    'can_review': protocol_proposals.is_reviewer(settings, user)}

    @app.get('/api/notebook/research-requests/{request_id}')
    def read_request(request_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session)
            return {'request': serialize(session, get_record(session, user, request_id), user)}

    @app.post('/api/notebook/research-requests/{request_id}/retraction')
    def retract_request(request_id: UUID, body: proposal_retractions.ResearchRetractionInput, request: Request):
        throttle(request, 'research-proposal-retraction', 20, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            record = get_record(session, user, request_id, True)
            if record.author_id != user.id:
                raise HTTPException(404, 'Proposal not found.')
            if record.purpose not in PROPOSAL_PURPOSES:
                raise HTTPException(422, 'Retraction applies to concrete proposals, not source checks or technical reviews.')
            proposal_retractions.withdraw(session, proposal_retractions.RESEARCH, record, user, clock(), body,
                revision=None, digest=packet_hash(record))
            return {'request': serialize(session, record, user)}

    @app.post('/api/notebook/research-requests/{request_id}/assessments')
    def assess(request_id: UUID, body: AssessmentInput, request: Request):
        throttle(request, 'research-assessment', 60, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            if not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, 'A configured reviewer is required.')
            record = get_record(session, user, request_id, True)
            if record.purpose in RETIRED_PURPOSES:
                raise HTTPException(409, 'Technical review requests are retired. Existing evidence and assessments remain read-only.')
            self_review = record.author_id == user.id or any(s['author_id'] == user.id for s in record.sources)
            if self_review and not protocol_proposals.can_self_review(settings, user):
                raise HTTPException(403, 'Ask an independent reviewer to assess this request.')
            commit = notebook._hash({'request_id':str(request_id), **body.model_dump(mode='json', exclude={'idempotency_key'})})
            replay = session.scalar(select(ResearchAssessment).where(ResearchAssessment.reviewer_id == user.id,
                ResearchAssessment.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commit:
                    raise HTTPException(409, 'This identifier was used for a different assessment.')
                return {'request': serialize(session, record, user)}
            proposal_retractions.ensure_active(session, proposal_retractions.RESEARCH, record.id)
            if body.packet_sha256 != packet_hash(record):
                raise HTTPException(409, 'Review the exact frozen evidence packet.')
            if session.get(ResearchAssessment, record.id):
                raise HTTPException(409, 'This request already has an assessment. Create a follow-up request for new evidence.')
            if body.outcome == 'recommend' and body.checks.sources != 'checked':
                raise HTTPException(422, 'A recommendation requires checked sources; record outstanding work otherwise.')
            session.add(ResearchAssessment(request_id=record.id, reviewer_id=user.id, created_at=clock(),
                outcome=body.outcome, checks=body.checks.model_dump(), findings=body.findings,
                limitations=body.limitations, test_only_self_review=self_review,
                idempotency_key=str(body.idempotency_key), request_sha256=commit))
            session.flush()
            return {'request': serialize(session, record, user)}
