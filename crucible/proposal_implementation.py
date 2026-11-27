"""Human-recorded follow-through on exact scientific decisions.

Progress is append-only and separate from approval, admission, deployment and
points. Evidence links are recorded, never fetched or executed by this service.
"""
import base64
import json
import math
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, and_, func, literal, or_, select, union_all
from sqlalchemy.orm import Mapped, load_only, mapped_column

from .db import Base, User

SCOPE = ("Implementation progress recorded by a reviewer. Completion records the evidence below; "
         "it does not publish research, activate a scheme, deploy code or award points.")
MAX_EVENTS = 200


class ImplementationEvent(Base):
    __tablename__ = "proposal_implementation_events"
    __table_args__ = (
        CheckConstraint("(scheme_review_id IS NOT NULL AND research_request_id IS NULL) OR "
                        "(scheme_review_id IS NULL AND research_request_id IS NOT NULL)", name="ck_implementation_one_decision"),
        CheckConstraint("status IN ('in_progress', 'completed')", name="ck_implementation_status"),
        CheckConstraint("sequence > 0", name="ck_implementation_sequence"),
        UniqueConstraint("scheme_review_id", "sequence", name="uq_implementation_scheme_sequence"),
        UniqueConstraint("research_request_id", "sequence", name="uq_implementation_research_sequence"),
        UniqueConstraint("actor_id", "idempotency_key", name="uq_implementation_actor_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scheme_review_id: Mapped[str | None] = mapped_column(ForeignKey("protocol_proposal_reviews.id"), nullable=True)
    research_request_id: Mapped[str | None] = mapped_column(ForeignKey("research_assessments.request_id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    decision_sha256: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


def evidence_url(value):
    """Canonical safe reference; validation does not assert that a URL is true."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise ValueError("Use a reference URL without whitespace or control characters.")
    try:
        value.encode("utf-8")
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and parsed.username is None and parsed.password is None and parsed.port in (None, 443):
            return value
        if parsed.scheme or parsed.netloc or parsed.path not in ("", "/") or parsed.query:
            raise ValueError
        route, separator, query = parsed.fragment.partition("?")
        params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
        if "loc" in params:
            from .notebook_locs import number
            if set(params) != {"loc", "note", "revision", "sha256", "passage"} or len(params["loc"]) != 1 or number(params["loc"][0]) is None:
                raise ValueError
        if route != "notebook" or not separator or not {"note", "revision", "sha256"} <= params.keys():
            raise ValueError
        from .notebook_graph import references
        expanded = "/#notebook?" + urlencode({key: values[0] for key, values in params.items() if key != "loc"})
        if any(len(values) != 1 for values in params.values()) or len(list(references({"references": [{"url": expanded}]}))) != 1:
            raise ValueError
        return "/#notebook?" + urlencode({key: values[0] for key, values in params.items()})
    except (ValueError, TypeError):
        raise ValueError("Use an HTTPS evidence URL or an exact note revision link.") from None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("label")
    @classmethod
    def valid_label(cls, value):
        from .notebook import _inspect
        _inspect(value)
        return value

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        return evidence_url(value)


class EventInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    decision_id: UUID
    expected_event_id: UUID | None
    status: Literal["in_progress", "completed"]
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=8)
    idempotency_key: UUID

    @field_validator("summary")
    @classmethod
    def valid_summary(cls, value):
        from .notebook import _inspect
        _inspect(value)
        return value


def _event_filter(kind, decision_id):
    return (ImplementationEvent.scheme_review_id if kind == "scheme" else ImplementationEvent.research_request_id) == decision_id


def _head_columns():
    return load_only(ImplementationEvent.id, ImplementationEvent.scheme_review_id,
                     ImplementationEvent.research_request_id, ImplementationEvent.status,
                     ImplementationEvent.created_at, raiseload=True)


def _receipt(event):
    return {"id": event.id, "status": event.status, "summary": event.summary, "evidence": event.evidence}


def _resolve(session, kind, identifier, user, settings, *, decision_id=None, lock=False):
    from . import notebook_moderation, protocol_proposals as schemes, research_reviews as research, proposal_retractions
    model = schemes.ProtocolProposal if kind == "scheme" else research.ResearchRequest
    query = select(model).where(model.id == identifier)
    record = session.scalar(query.with_for_update() if lock else query)
    reviewer = schemes.is_reviewer(settings, user)
    if record is None or record.author_id != user.id and not reviewer:
        raise HTTPException(404, "Proposal not found.")
    if kind == "research" and notebook_moderation.packet_is_withheld(session, record, user, settings):
        raise HTTPException(404, "Proposal not found.")
    if kind == "scheme":
        query = select(schemes.ProtocolProposalReview).where(schemes.ProtocolProposalReview.proposal_id == record.id)
        query = query.where(schemes.ProtocolProposalReview.id == decision_id) if decision_id else query.where(schemes.ProtocolProposalReview.revision == record.revision)
        decision = session.scalar(query)
        current = decision is None or decision.revision == record.revision and decision.content_sha256 == record.content_sha256
        accepted = bool(decision and decision.decision == "approved" and not decision.checks.get("test_only_self_review") and decision.reviewer_id != record.author_id
                        and (not current or record.status == "approved"))
        identity, digest = (decision.id, decision.content_sha256) if decision else (None, record.content_sha256)
        revision = decision.revision if decision else record.revision
        independent = record.author_id != user.id
        title, route = record.specification["name"], "/#propose?proposal=" + record.id
        if not current:
            original = session.scalar(select(schemes.ProtocolProposalRevision).where(
                schemes.ProtocolProposalRevision.proposal_id == record.id,
                schemes.ProtocolProposalRevision.revision == revision,
                schemes.ProtocolProposalRevision.content_sha256 == digest))
            if original is None:
                raise HTTPException(409, "The exact reviewed proposal revision is unavailable.")
            title = original.specification["name"]
        base_status = "changes_requested" if decision and decision.decision == "changes_requested" else "awaiting_review"
    else:
        decision = session.get(research.ResearchAssessment, record.id)
        current = True
        accepted = bool(decision and decision.outcome == "recommend" and not decision.test_only_self_review and
                        decision.reviewer_id != record.author_id and record.purpose in research.PROPOSAL_PURPOSES)
        identity, digest, revision = (record.id if decision else None), research.packet_hash(record), None
        independent = record.author_id != user.id and all(source["author_id"] != user.id for source in record.sources)
        title, route = record.title, "/#notebook?research_request=" + record.id
        base_status = {"decline": "declined", "changes_requested": "changes_requested"}.get(decision.outcome, "awaiting_review") if decision else "awaiting_review"
    if decision_id and (decision is None or identity != decision_id):
        raise HTTPException(404, "Proposal decision not found.")
    retracted = proposal_retractions.get(session, proposal_retractions.SCHEME if kind == "scheme" else proposal_retractions.RESEARCH, record.id) is not None
    eligible = accepted and current and not retracted
    return {"record": record, "decision": decision, "kind": kind, "target_id": record.id, "title": title,
            "url": route, "decision_id": identity, "decision_sha256": digest, "source_revision": revision,
            "accepted": accepted, "superseded": not current, "retracted": retracted, "eligible": eligible,
            "can_update": eligible and reviewer and independent,
            "base_status": "retracted" if retracted else "awaiting_implementation" if accepted else base_status}


def _serialize(session, state, *, history=True):
    query = select(ImplementationEvent, User.handle).join(User, User.id == ImplementationEvent.actor_id)
    if not history:
        query = query.options(_head_columns())
    rows = session.execute(query
        .where(_event_filter(state["kind"], state["decision_id"]))
        .order_by(ImplementationEvent.sequence.desc()).limit(MAX_EVENTS if history else 1)).all() if state["decision_id"] else []
    latest = rows[0][0] if rows else None
    result = {key: state[key] for key in ("kind", "target_id", "title", "url", "decision_id", "decision_sha256", "source_revision", "eligible", "can_update", "superseded")}
    result.update(status="retracted" if state["retracted"] else latest.status if latest else state["base_status"],
                  head_event_id=latest.id if latest else None, updated_at=latest.created_at if latest else None,
                  actor={"handle": rows[0][1]} if rows else None, scope=SCOPE)
    result["history_url"] = f'/api/proposal-implementations/{state["kind"]}/{state["target_id"]}' + (
        '?decision_id=' + state['decision_id'] if state['decision_id'] else '')
    if history:
        result["events"] = [{"id": event.id, "status": event.status, "summary": event.summary,
                             "evidence": event.evidence, "created_at": event.created_at, "actor": {"handle": handle}}
                            for event, handle in reversed(rows)]
    return result


def _check_evidence(session, state, evidence):
    """Internal references must be exact and readable by the proposal author."""
    from . import notebook, research_reviews, note_sections, statement_workflow
    author = session.get(User, state["record"].author_id)
    for item in evidence:
        if not item.url.startswith("/#"):
            continue
        params = {key: values[0] for key, values in parse_qs(urlsplit(item.url).fragment.partition("?")[2]).items()}
        if "loc" in params:
            from .notebook_locs import checked_link
            checked = checked_link({key: [value] for key, value in params.items()}, session)
            if checked is None:
                raise HTTPException(404, "Evidence passage not found.")
            params = {key: value[0] for key, value in checked.items()}
        ref = research_reviews.SourceRef(note_id=params["note"], revision=int(params["revision"]), content_sha256=params["sha256"])
        source = research_reviews.source_snapshot(session, author, ref)
        for kind in ("note_section", "statement", "argument", "passage"):
            if kind in params:
                if kind == "note_section":
                    note_sections.section_record(source["content"], params[kind])
                elif kind == "passage":
                    try:
                        note_sections.resolve_passage(source["content"], params[kind])
                    except ValueError as error:
                        raise HTTPException(422, str(error)) from error
                else:
                    statement_workflow.selected_record(source["content"], kind, params[kind])
        # The updater must also have permission; source_snapshot deliberately
        # never turns reviewer status into unrestricted access to private notes.
        # Author-owned evidence is allowed only when already in this packet.
        if ref.note_id.startswith("curated:"):
            continue
        revision = session.scalar(select(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == ref.note_id,
            notebook.NotebookRevision.revision == ref.revision))
        published = session.scalar(select(notebook.NotebookPublication.id).where(notebook.NotebookPublication.revision_id == revision.id,
            notebook.publication_screening.approved_clause(allow_development=False)))
        if not published:
            if state["kind"] == "scheme":
                from .proposal_notes import source_record
                linked = source_record(session, state["target_id"], state["source_revision"])
                allowed = linked and (linked["id"], linked["revision"], linked["content_sha256"]) == (ref.note_id, ref.revision, ref.content_sha256)
            else:
                allowed = any((row["note_id"], row["revision"], row["content_sha256"]) == (ref.note_id, ref.revision, ref.content_sha256) for row in state["record"].sources)
            if not allowed:
                raise HTTPException(422, "Publish the supporting note first, or use an exact revision already submitted with this proposal.")


def compact(session, kind, record, *, revision=None):
    """Safe scalar status for an already-authorized proposal projection."""
    from . import protocol_proposals as schemes, research_reviews as research
    if kind == "scheme":
        query = select(schemes.ProtocolProposalReview).where(schemes.ProtocolProposalReview.proposal_id == record.id,
            schemes.ProtocolProposalReview.revision == (revision if revision is not None else record.revision))
        if revision is None:
            query = query.where(schemes.ProtocolProposalReview.content_sha256 == record.content_sha256)
        decision = session.scalar(query)
    else:
        decision = session.get(research.ResearchAssessment, record.id)
    accepted = bool(decision and ((decision.decision == "approved" and (revision is not None or record.status == "approved") and
        not decision.checks.get("test_only_self_review")) if kind == "scheme" else
        (decision.outcome == "recommend" and not decision.test_only_self_review and record.purpose in research.PROPOSAL_PURPOSES)))
    if not accepted or decision.reviewer_id == record.author_id:
        return None
    identity = decision.id if kind == "scheme" else record.id
    row = session.execute(select(ImplementationEvent, User.handle).options(_head_columns()).join(User, User.id == ImplementationEvent.actor_id)
        .where(_event_filter(kind, identity)).order_by(ImplementationEvent.sequence.desc()).limit(1)).first()
    return {"kind": kind, "target_id": record.id, "decision_id": identity,
            "status": row[0].status if row else "awaiting_implementation", "head_event_id": row[0].id if row else None,
            "updated_at": row[0].created_at if row else None, "actor": {"handle": row[1]} if row else None}


def for_record(session, kind, record, user, settings, *, history=True):
    return _serialize(session, _resolve(session, kind, record.id, user, settings), history=history)


def decorate_requests(session, records):
    """Batch progress labels only after the caller has filtered private requests."""
    from .research_reviews import PROPOSAL_PURPOSES
    selected = []
    for record in records:
        decision_id = record.pop("_implementation_decision_id", None)
        kind = "scheme" if record["kind"] == "scheme_admission" else "research"
        if (record.get("retracted") or record.get("test_only_self_review") or
                record["status"] != ("approved" if kind == "scheme" else "recommend") or
                kind == "research" and record["kind"] not in PROPOSAL_PURPOSES):
            continue
        identity = decision_id if kind == "scheme" else record["id"]
        if identity:
            selected.append((record, kind, identity))
    if not selected:
        return
    ranked = select(ImplementationEvent.id, func.row_number().over(
        partition_by=(ImplementationEvent.scheme_review_id, ImplementationEvent.research_request_id),
        order_by=ImplementationEvent.sequence.desc()).label("position")).where(
            or_(*[_event_filter(kind, identity) for _, kind, identity in selected])).subquery()
    heads = {}
    for event, handle in session.execute(select(ImplementationEvent, User.handle).options(_head_columns()).join(User, User.id == ImplementationEvent.actor_id)
            .join(ranked, ranked.c.id == ImplementationEvent.id).where(ranked.c.position == 1)):
        heads[("scheme", event.scheme_review_id) if event.scheme_review_id else ("research", event.research_request_id)] = event, handle
    for record, kind, identity in selected:
        event, handle = heads.get((kind, identity), (None, None))
        record["implementation"] = {"kind": kind, "target_id": record["id"], "decision_id": identity,
            "status": event.status if event else "awaiting_implementation", "head_event_id": event.id if event else None,
            "updated_at": event.created_at if event else None, "actor": {"handle": handle} if event else None}


def _cursor(value):
    try:
        decoded = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        timestamp, kind, identifier = decoded
        if not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp) or kind not in {"scheme", "research"} or str(UUID(identifier)) != identifier:
            raise ValueError
        return timestamp, kind, identifier
    except (ValueError, TypeError, IndexError, UnicodeError):
        raise HTTPException(422, "Invalid implementation queue cursor.") from None


def register_routes(app, db, settings, clock, user_for, throttle):
    from . import protocol_proposals as schemes, research_reviews as research, proposal_retractions, notebook

    def account(request, session, *, mutation=False):
        if "authorization" in request.headers:
            raise HTTPException(403, "Implementation tracking currently requires your browser account.")
        return user_for(request, session, mutation=mutation)

    @app.get("/api/proposal-implementations")
    def queue(request: Request, scope: Literal["review", "mine"] = "review",
              after: str | None = Query(default=None, max_length=256), limit: int = Query(default=25, ge=1, le=50)):
        with db.transaction() as session:
            user = account(request, session)
            if scope == "review" and not schemes.is_reviewer(settings, user):
                raise HTTPException(403, "A configured reviewer is required.")
            p, d, r, a = schemes.ProtocolProposal, schemes.ProtocolProposalReview, research.ResearchRequest, research.ResearchAssessment
            left = select(literal("scheme").label("kind"), p.id.label("target_id"), p.specification["name"].as_string().label("title"),
                p.author_id.label("author_id"), d.id.label("decision_id"), d.created_at.label("decided_at"),
                d.content_sha256.label("digest"), d.revision.label("revision")).join(d, and_(d.proposal_id == p.id, d.revision == p.revision, d.content_sha256 == p.content_sha256)).where(
                p.status == "approved", d.decision == "approved", d.reviewer_id != p.author_id,
                or_(d.checks["test_only_self_review"].as_boolean().is_(None), d.checks["test_only_self_review"].as_boolean().is_(False)),
                p.id.not_in(proposal_retractions.target_ids(proposal_retractions.SCHEME)))
            right = select(literal("research").label("kind"), r.id.label("target_id"), r.title.label("title"),
                r.author_id.label("author_id"), a.request_id.label("decision_id"), a.created_at.label("decided_at"),
                r.request_sha256.label("digest"), literal(None).label("revision")).join(a, a.request_id == r.id).where(
                r.purpose.in_(research.PROPOSAL_PURPOSES), a.outcome == "recommend", a.test_only_self_review.is_(False),
                a.reviewer_id != r.author_id, r.id.not_in(proposal_retractions.target_ids(proposal_retractions.RESEARCH)))
            if scope == "mine":
                left, right = left.where(p.author_id == user.id), right.where(r.author_id == user.id)
                if not schemes.is_reviewer(settings, user):
                    from .notebook_moderation import PublicationWithdrawal
                    from .notebook_visibility import hidden_ids
                    v = notebook.NotebookRevision
                    withheld = [select(PublicationWithdrawal.revision_id).join(v, v.id == PublicationWithdrawal.revision_id).where(
                        v.note_id == r.sources[index]["note_id"].as_string(), v.revision == r.sources[index]["revision"].as_integer(),
                        v.content_sha256 == r.sources[index]["content_sha256"].as_string(),
                        r.sources[index]["author_id"].as_string() != user.id).exists() for index in range(8)]
                    right = right.where(~or_(*withheld))
                    private_sources = [select(v.id).where(v.id.in_(hidden_ids()),
                        v.note_id == r.sources[index]["note_id"].as_string(), v.revision == r.sources[index]["revision"].as_integer(),
                        v.content_sha256 == r.sources[index]["content_sha256"].as_string(),
                        r.sources[index]["author_id"].as_string() != user.id).exists() for index in range(8)]
                    right = right.where(~or_(*private_sources))
            combined = union_all(left, right).subquery()
            query = select(combined, User.handle.label("author_handle")).join(User, User.id == combined.c.author_id)
            if after:
                timestamp, kind, identifier = _cursor(after)
                query = query.where(or_(combined.c.decided_at < timestamp,
                    and_(combined.c.decided_at == timestamp, combined.c.kind > kind),
                    and_(combined.c.decided_at == timestamp, combined.c.kind == kind, combined.c.target_id > identifier)))
            rows = session.execute(query.order_by(combined.c.decided_at.desc(), combined.c.kind, combined.c.target_id).limit(limit + 1)).mappings().all()
            page = rows[:limit]
            # One bounded query for heads; no note body or frozen review packet
            # is fetched merely to show the implementation queue.
            filters = [_event_filter(row["kind"], row["decision_id"]) for row in page]
            heads = {}
            if filters:
                ranked = select(ImplementationEvent.id, func.row_number().over(partition_by=(ImplementationEvent.scheme_review_id, ImplementationEvent.research_request_id), order_by=ImplementationEvent.sequence.desc()).label("position")).where(or_(*filters)).subquery()
                for event, handle in session.execute(select(ImplementationEvent, User.handle).options(_head_columns()).join(User, User.id == ImplementationEvent.actor_id)
                    .join(ranked, ranked.c.id == ImplementationEvent.id).where(ranked.c.position == 1)):
                    heads[("scheme", event.scheme_review_id) if event.scheme_review_id else ("research", event.research_request_id)] = (event, handle)
            items = []
            for row in page:
                event, handle = heads.get((row["kind"], row["decision_id"]), (None, None))
                items.append({"kind": row["kind"], "target_id": row["target_id"], "title": row["title"],
                    "url": ("/#propose?proposal=" if row["kind"] == "scheme" else "/#notebook?research_request=") + row["target_id"],
                    "decision_id": row["decision_id"], "source_revision": row["revision"], "author": {"handle": row["author_handle"]},
                    "status": event.status if event else "awaiting_implementation", "head_event_id": event.id if event else None,
                    "updated_at": event.created_at if event else None, "actor": {"handle": handle} if event else None,
                    "eligible": True, "can_update": False, "superseded": False, "scope": SCOPE})
            last = page[-1] if len(rows) > limit else None
            cursor = base64.urlsafe_b64encode(json.dumps([last["decided_at"], last["kind"], last["target_id"]]).encode()).decode().rstrip("=") if last else None
            return {"implementations": items, "next_after": cursor}

    @app.get("/api/proposal-implementations/{kind}/{target_id}")
    def detail(kind: Literal["scheme", "research"], target_id: UUID, request: Request, decision_id: UUID | None = None):
        with db.transaction() as session:
            user = account(request, session)
            state = _resolve(session, kind, str(target_id), user, settings, decision_id=str(decision_id) if decision_id else None)
            return {"implementation": _serialize(session, state)}

    @app.post("/api/proposal-implementations/{kind}/{target_id}/events")
    def update(kind: Literal["scheme", "research"], target_id: UUID, body: EventInput, request: Request):
        throttle(request, "proposal-implementation", 60, 3600)
        commitment = notebook._hash({"kind": kind, "target_id": str(target_id), **body.model_dump(mode="json", exclude={"idempotency_key"})})
        with db.transaction(write=True) as session:
            user = account(request, session, mutation=True)
            if not schemes.is_reviewer(settings, user):
                raise HTTPException(403, "A configured reviewer is required.")
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
            state = _resolve(session, kind, str(target_id), user, settings, decision_id=str(body.decision_id), lock=True)
            replay = session.scalar(select(ImplementationEvent).where(ImplementationEvent.actor_id == user.id, ImplementationEvent.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This update identifier was already used for a different request.")
                return {"implementation": _serialize(session, state), "event": _receipt(replay)}
            if not state["eligible"]:
                raise HTTPException(409, "Implementation tracking requires the current, independently approved decision on an active proposal.")
            if not state["can_update"]:
                raise HTTPException(403, "An independent reviewer must record implementation progress.")
            head = session.scalar(select(ImplementationEvent).where(_event_filter(kind, state["decision_id"]))
                .order_by(ImplementationEvent.sequence.desc()).limit(1))
            if (head.id if head else None) != (str(body.expected_event_id) if body.expected_event_id else None):
                raise HTTPException(409, "Implementation progress changed. Refresh before recording another update.")
            if head and head.sequence >= MAX_EVENTS:
                raise HTTPException(409, "Implementation history capacity reached; existing evidence is preserved.")
            evidence = [item.model_dump() for item in body.evidence]
            if head and head.status == body.status:
                if body.status == "completed":
                    raise HTTPException(409, "This implementation is already completed. Reopen it before recording further work.")
                if head.summary == body.summary and head.evidence == evidence:
                    raise HTTPException(409, "This progress update is already recorded. Describe the new work or add new evidence.")
            if body.status == "completed" and (head is None or not body.evidence):
                raise HTTPException(422, "Start implementation first, then record a completion summary and at least one concrete evidence reference.")
            _check_evidence(session, state, body.evidence)
            event = ImplementationEvent(id=str(uuid4()), scheme_review_id=state["decision_id"] if kind == "scheme" else None,
                research_request_id=state["decision_id"] if kind == "research" else None, sequence=head.sequence + 1 if head else 1,
                decision_sha256=state["decision_sha256"], actor_id=user.id, created_at=clock(), status=body.status,
                summary=body.summary, evidence=evidence,
                idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            session.add(event)
            session.flush()
            return {"implementation": _serialize(session, state), "event": _receipt(event)}
