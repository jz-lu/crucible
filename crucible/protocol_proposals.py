"""Private, immutable intake records for unreviewed KEM specifications.

Validation checks the document format only. It never executes an implementation,
retrieves a reference URL, registers an adapter, or certifies a mathematical claim.
"""
import hashlib
import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, and_, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from . import kem
from .db import Base, User
from .protocol_specs import ProtocolSpec, SPEC_MAX_BYTES, builtin_spec, canonical_spec, template_spec


VALIDATION_SCOPE = (
    "Only the specification format was checked. Correctness, security, novelty, "
    "parameter mappings, and implementation claims are unverified. A submitted "
    "proposal remains private and pending review; it is not an active challenge."
)


class ProtocolProposal(Base):
    __tablename__ = "protocol_proposals"
    __table_args__ = (
        UniqueConstraint("author_id", "idempotency_key", name="uq_protocol_proposal_idempotency"),
        Index("ix_protocol_proposal_author_time", "author_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    content_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)


class ProtocolProposalRevision(Base):
    __tablename__ = "protocol_proposal_revisions"
    __table_args__ = (
        UniqueConstraint("proposal_id", "revision", name="uq_protocol_proposal_revision"),
        UniqueConstraint("proposal_id", "idempotency_key", name="uq_protocol_revision_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposals.id"))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)


class ProtocolProposalReview(Base):
    __tablename__ = "protocol_proposal_reviews"
    __table_args__ = (
        UniqueConstraint("proposal_id", "revision", name="uq_protocol_proposal_review"),
        UniqueConstraint("reviewer_id", "idempotency_key", name="uq_protocol_review_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposals.id"))
    revision: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(32))
    content_sha256: Mapped[str] = mapped_column(String(64))
    implementation_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checks: Mapped[dict] = mapped_column(JSON)
    notes: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


class ValidationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    specification: dict[str, Any]


class ProposalInput(ValidationInput):
    idempotency_key: UUID


class RevisionInput(ProposalInput):
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReviewChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specification_complete: bool
    correctness: bool
    tier_design: bool = False
    parameter_selection: bool = False
    implementation_conformance: bool
    input_resource_safety: bool
    reproduction: bool


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "changes_requested"]
    implementation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checks: ReviewChecks
    notes: str = Field(min_length=1, max_length=10000)
    evidence: str = Field(min_length=1, max_length=10000)
    idempotency_key: UUID

    @field_validator("notes", "evidence")
    @classmethod
    def valid_text(cls, value):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Use valid Unicode text.") from None
        return value


def validation_error_response(error, *, prefix=()):
    """Field locations are useful; rejected values and validator contexts are not."""
    errors = []
    for issue in error.errors()[:50]:
        path = (*prefix, *(part for part in issue["loc"] if part not in {"body", "query"}))
        # Unknown JSON property names are user input too: bound their display and
        # escape lone surrogates so rejected text cannot break the error encoder.
        parts = [str(part)[:80].encode("utf-8", "backslashreplace").decode("utf-8") for part in path]
        errors.append({"path": ".".join(parts) or "specification",
                       "message": "Check this field's format or required value."})
    return JSONResponse({"detail": "Check the protocol specification.", "errors": errors}, status_code=422)


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _validated(specification):
    # Bound both the received document (including optional instructions) and the
    # canonical frozen record, independently of the 64 KiB request-body boundary.
    try:
        raw = _canonical_bytes(specification)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise HTTPException(422, "The specification must contain valid JSON text.") from None
    if len(raw) > SPEC_MAX_BYTES:
        raise HTTPException(413, "The protocol specification must be no larger than 48 KiB.")
    try:
        canonical = canonical_spec(ProtocolSpec.model_validate(specification))
        encoded = _canonical_bytes(canonical)
    except ValidationError as error:
        return validation_error_response(error, prefix=("specification",))
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise HTTPException(422, "The specification must contain valid JSON text within the 48 KiB limit.") from None
    if len(encoded) > SPEC_MAX_BYTES:
        raise HTTPException(413, "The canonical specification must be no larger than 48 KiB.")
    return canonical, hashlib.sha256(encoded).hexdigest()


def _validation():
    return {"status": "format-valid", "scope": VALIDATION_SCOPE}


def _proposal_data(record):
    return {"id": record.id, "status": record.status, "created_at": record.created_at,
            "revision": record.revision,
            "content_sha256": record.content_sha256, "specification": record.specification}


def is_reviewer(settings, user):
    return bool(user and user.id in settings.reviewer_ids)


def can_self_review(settings, user):
    return bool(not settings.production and settings.allow_self_review and is_reviewer(settings, user))


def _review_data(session, record):
    reviewer = session.get(User, record.reviewer_id)
    return {"id": record.id, "revision": record.revision, "created_at": record.created_at,
            "reviewer": {"id": reviewer.id, "handle": reviewer.handle},
            "decision": record.decision, "content_sha256": record.content_sha256,
            "implementation_sha256": record.implementation_sha256,
            "checks": record.checks, "notes": record.notes, "evidence": record.evidence,
            "scope": ("Development self-review only. This decision cannot satisfy independent admission review or earn scheme contribution credit."
                      if record.checks.get("test_only_self_review") is True else
                      "Manual reviewer assessment. Approval permits trusted integration review; it does not activate a challenge or prove correctness or security.")}


def _revision_data(record):
    return {"revision": record.revision, "created_at": record.created_at,
            "content_sha256": record.content_sha256, "specification": record.specification}


def _history(session, record, before_revision=None, limit=20):
    query = select(ProtocolProposalRevision).where(ProtocolProposalRevision.proposal_id == record.id)
    if before_revision is not None:
        query = query.where(ProtocolProposalRevision.revision < before_revision)
    rows = session.scalars(query.order_by(ProtocolProposalRevision.revision.desc()).limit(limit + 1)).all()
    page = rows[:limit]
    reviews = session.scalars(select(ProtocolProposalReview).where(
        ProtocolProposalReview.proposal_id == record.id,
        ProtocolProposalReview.revision.in_([row.revision for row in page]))
        .order_by(ProtocolProposalReview.revision.desc())).all() if page else []
    return {"revisions": [_revision_data(row) for row in page],
            "reviews": [_review_data(session, row) for row in reviews],
            "next_before_revision": page[-1].revision if len(rows) > limit else None}


def _historical_proposal(session, record, revision):
    if revision.revision == record.revision:
        return _proposal_data(record)
    review = session.scalar(select(ProtocolProposalReview).where(
        ProtocolProposalReview.proposal_id == record.id, ProtocolProposalReview.revision == revision.revision))
    return {**_proposal_data(record), **_revision_data(revision),
            "created_at": record.created_at, "status": review.decision if review else "submitted"}


def register_routes(app, db, settings, clock, user_for, throttle):
    def accessible(session, request, proposal_id, *, owner_only=False, lock=False):
        # A reviewer may inspect others' proposals through the browser only.
        # Participant bearer credentials never inherit review authority, even
        # when a request also happens to carry the reviewer's browser cookies.
        owner_only = owner_only or "authorization" in request.headers
        try:
            user = user_for(request, session)
        except HTTPException as error:
            if error.status_code == 401:
                raise HTTPException(404, "Proposal not found.") from None
            raise
        query = select(ProtocolProposal).where(ProtocolProposal.id == proposal_id)
        if lock:
            query = query.with_for_update()
        record = session.scalar(query)
        if record is None or (record.author_id != user.id and (owner_only or not is_reviewer(settings, user))):
            raise HTTPException(404, "Proposal not found.")
        return record, user

    def reviewer_for(request, session, *, mutation=False):
        if "authorization" in request.headers:
            raise HTTPException(403, "Proposal review requires your browser account.")
        user = user_for(request, session, mutation=mutation)
        if not is_reviewer(settings, user):
            raise HTTPException(403, "A configured protocol reviewer is required.")
        return user

    @app.get("/api/protocol/spec-schema")
    def specification_schema():
        return JSONResponse(ProtocolSpec.model_json_schema(), headers={
            "Content-Disposition": 'attachment; filename="crucible-protocol-spec-schema.json"'})

    @app.get("/api/protocol/spec-template")
    def specification_template():
        return JSONResponse(template_spec(), headers={
            "Content-Disposition": 'attachment; filename="crucible-protocol-spec-template.json"'})

    @app.get("/api/protocol/spec")
    def registered_specification(scheme_id: str = Query(default=kem.SCHEME_ID, max_length=100),
                                 version: str | None = Query(default=None, max_length=32)):
        try:
            specification = builtin_spec(scheme_id, version)
        except ValueError:
            raise HTTPException(404, "Unknown scheme or version.") from None
        # Filename is constant: request-controlled text is never interpolated into
        # a response header or a filesystem path.
        return JSONResponse(specification, headers={
            "Content-Disposition": 'attachment; filename="crucible-protocol-spec.json"'})

    # Static validation path precedes the detail route, and creates no session or
    # proposal. Same-origin and JSON body limits still apply in RequestBoundary.
    @app.post("/api/protocol/proposals/validate")
    def validate_proposal(body: ValidationInput, request: Request):
        throttle(request, "protocol-validate", 30, 60)
        result = _validated(body.specification)
        if isinstance(result, JSONResponse):
            return result
        specification, content_sha256 = result
        return {"specification": specification, "content_sha256": content_sha256,
                "validation": _validation()}

    @app.post("/api/protocol/proposals")
    def submit_proposal(body: ProposalInput, request: Request):
        throttle(request, "protocol-proposal-write", 10, 3600)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            # SQLite serializes writes; PostgreSQL serializes this user's retries.
            # The database uniqueness constraint remains authoritative as well.
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
            result = _validated(body.specification)
            if isinstance(result, JSONResponse):
                return result
            specification, content_sha256 = result
            key = str(body.idempotency_key)
            record = session.scalar(select(ProtocolProposal).where(
                ProtocolProposal.author_id == user.id, ProtocolProposal.idempotency_key == key))
            if record is not None:
                initial = session.scalar(select(ProtocolProposalRevision).where(
                    ProtocolProposalRevision.proposal_id == record.id, ProtocolProposalRevision.revision == 1))
                if initial is None or initial.content_sha256 != content_sha256:
                    raise HTTPException(409, "This submission identifier was already used for a different specification.")
                result = _historical_proposal(session, record, initial)
            else:
                record = ProtocolProposal(id=str(uuid4()), author_id=user.id,
                    idempotency_key=key, created_at=clock(), status="submitted", revision=1,
                    content_sha256=content_sha256, specification=specification)
                session.add(record)
                session.flush()
                session.add(ProtocolProposalRevision(id=str(uuid4()), proposal_id=record.id,
                    revision=1, created_at=record.created_at, idempotency_key=key,
                    request_sha256=content_sha256, content_sha256=content_sha256, specification=specification))
                session.flush()
                result = _proposal_data(record)
            return {"proposal": result, "validation": _validation(), "server_time": clock()}

    @app.get("/api/me/protocol-proposals")
    def my_proposals(request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            records = session.scalars(select(ProtocolProposal).where(ProtocolProposal.author_id == user.id)
                .order_by(ProtocolProposal.created_at.desc(), ProtocolProposal.id.desc()).limit(50)).all()
            return {"proposals": [{**_proposal_data(record), "author": {"id": user.id, "handle": user.handle}}
                                  for record in records]}

    @app.get("/api/protocol/review-queue")
    def review_queue(request: Request, after: str | None = Query(default=None, max_length=36),
                     limit: int = Query(default=20, ge=1, le=50)):
        with db.transaction() as session:
            user = reviewer_for(request, session)
            query = select(ProtocolProposal).where(ProtocolProposal.status == "submitted")
            if not can_self_review(settings, user):
                query = query.where(ProtocolProposal.author_id != user.id)
            if after:
                anchor = session.get(ProtocolProposal, after)
                if anchor is None:
                    raise HTTPException(422, "Invalid review queue cursor.")
                query = query.where(or_(ProtocolProposal.created_at > anchor.created_at,
                    and_(ProtocolProposal.created_at == anchor.created_at, ProtocolProposal.id > anchor.id)))
            records = session.scalars(query.order_by(ProtocolProposal.created_at, ProtocolProposal.id).limit(limit + 1)).all()
            page = records[:limit]
            return {"proposals": [{**_proposal_data(record),
                    "author": {"id": record.author_id, "handle": session.get(User, record.author_id).handle}}
                    for record in page], "next_after": page[-1].id if len(records) > limit else None}

    @app.post("/api/protocol/proposals/{proposal_id}/revisions")
    def revise_proposal(proposal_id: str, body: RevisionInput, request: Request):
        throttle(request, "protocol-proposal-write", 10, 3600)
        with db.transaction(write=True) as session:
            user_for(request, session, mutation=True)
            record, user = accessible(session, request, proposal_id, owner_only=True, lock=True)
            result = _validated(body.specification)
            if isinstance(result, JSONResponse):
                return result
            specification, content_sha256 = result
            commitment = hashlib.sha256(_canonical_bytes({"expected_revision": body.expected_revision,
                "content_sha256": body.content_sha256, "new_content_sha256": content_sha256})).hexdigest()
            replay = session.scalar(select(ProtocolProposalRevision).where(
                ProtocolProposalRevision.proposal_id == record.id,
                ProtocolProposalRevision.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This revision identifier was already used for a different request.")
                return {"proposal": _historical_proposal(session, record, replay),
                        "validation": _validation(), "server_time": clock()}
            if record.revision != body.expected_revision or record.content_sha256 != body.content_sha256:
                raise HTTPException(409, "The proposal changed. Refresh its latest revision before submitting.")
            revision = ProtocolProposalRevision(id=str(uuid4()), proposal_id=record.id,
                revision=record.revision + 1, created_at=clock(), idempotency_key=str(body.idempotency_key),
                request_sha256=commitment, content_sha256=content_sha256, specification=specification)
            session.add(revision)
            record.revision, record.status = revision.revision, "submitted"
            record.content_sha256, record.specification = content_sha256, specification
            session.flush()
            return {"proposal": _proposal_data(record), "validation": _validation(), "server_time": clock()}

    @app.post("/api/protocol/proposals/{proposal_id}/reviews")
    def review_proposal(proposal_id: str, body: ReviewInput, request: Request):
        throttle(request, "protocol-review-write", 30, 3600)
        with db.transaction(write=True) as session:
            reviewer = reviewer_for(request, session, mutation=True)
            # Serialize a reviewer's retry key across proposals as well as this
            # proposal's decision against a simultaneous author revision.
            session.execute(select(User.id).where(User.id == reviewer.id).with_for_update())
            record = session.scalar(select(ProtocolProposal).where(ProtocolProposal.id == proposal_id).with_for_update())
            if record is None:
                raise HTTPException(404, "Proposal not found.")
            self_review = record.author_id == reviewer.id
            if self_review and not can_self_review(settings, reviewer):
                raise HTTPException(403, "Review another author's proposal; self-review is not allowed.")
            reviewed_request = body.model_dump(mode="json", exclude={"idempotency_key"})
            # Preserve exact retries of historical decisions whose request did
            # not contain the new check. This never grants a fresh approval.
            for added_check in ("tier_design", "parameter_selection"):
                if added_check not in body.checks.model_fields_set:
                    reviewed_request["checks"].pop(added_check, None)
            commitment = hashlib.sha256(_canonical_bytes({"proposal_id": record.id,
                **reviewed_request})).hexdigest()
            replay = session.scalar(select(ProtocolProposalReview).where(
                ProtocolProposalReview.reviewer_id == reviewer.id,
                ProtocolProposalReview.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This review identifier was already used for a different request.")
                return {"proposal": _proposal_data(record), "review": _review_data(session, replay), "server_time": clock()}
            if record.revision != body.expected_revision or record.content_sha256 != body.content_sha256:
                raise HTTPException(409, "The proposal changed. Review its latest revision before deciding.")
            if record.status != "submitted":
                raise HTTPException(409, "This revision already has a review. The author must submit a new revision.")
            implementation = record.specification.get("implementation") or {}
            expected_sha = implementation.get("sha256")
            if body.implementation_sha256 is not None and body.implementation_sha256 != expected_sha:
                raise HTTPException(422, "The reviewed implementation hash must match this exact specification.")
            if body.decision == "approved":
                if not record.specification.get("tier_design"):
                    raise HTTPException(422, "Approval requires a stage-progression plan (tier_design) in this exact revision. The author must add it in a new revision before review.")
                if not record.specification["tier_design"].get("parameter_selection"):
                    raise HTTPException(422, "Approval requires the parameter-selection objective, constraints, model, search domain, comparison evidence and uncertainty in this exact revision.")
                if record.specification.get("correctness", {}).get("kind") == "unknown":
                    raise HTTPException(422, "Approval requires an explicit correctness claim for review.")
                if not expected_sha or body.implementation_sha256 != expected_sha:
                    raise HTTPException(422, "Approval requires the exact implementation artifact SHA-256 from this specification.")
                if not all(body.checks.model_dump().values()):
                    raise HTTPException(422, "Approval requires every manual review check to pass.")
                if len(body.evidence) < 20:
                    raise HTTPException(422, "Record concrete review evidence, including commands, results, or artifact identifiers.")
            review_checks = body.checks.model_dump()
            if self_review:
                review_checks["test_only_self_review"] = True
            review = ProtocolProposalReview(id=str(uuid4()), proposal_id=record.id,
                revision=record.revision, reviewer_id=reviewer.id, created_at=clock(), decision=body.decision,
                content_sha256=record.content_sha256, implementation_sha256=body.implementation_sha256,
                checks=review_checks, notes=body.notes, evidence=body.evidence,
                idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            session.add(review)
            record.status = body.decision
            session.flush()
            return {"proposal": _proposal_data(record), "review": _review_data(session, review), "server_time": clock()}

    @app.get("/api/protocol/proposals/{proposal_id}/revisions")
    def revision_history(proposal_id: str, request: Request,
                         before_revision: int | None = Query(default=None, ge=1, le=2147483647),
                         limit: int = Query(default=20, ge=1, le=50)):
        with db.transaction() as session:
            record, _ = accessible(session, request, proposal_id)
            author = session.get(User, record.author_id)
            return {"proposal": _proposal_data(record), "author": {"id": author.id, "handle": author.handle},
                    **_history(session, record, before_revision, limit)}

    @app.get("/api/protocol/proposals/{proposal_id}")
    def proposal_detail(proposal_id: str, request: Request):
        with db.transaction() as session:
            record, _ = accessible(session, request, proposal_id)
            author = session.get(User, record.author_id)
            return {"proposal": _proposal_data(record), "author": {"id": author.id, "handle": author.handle},
                    **_history(session, record)}
