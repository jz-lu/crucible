"""Explicit, immutable credit for reviewed admission of a new scheme family.

Proposal approval alone awards nothing. A trusted operator must first integrate
the exact reviewed source into the active registry, then record this separate
decision. Family novelty is a manual assessment, not inferred from source hashes.
There is deliberately no HTTP write endpoint for this operator action.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import time

from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, Text, UniqueConstraint, case, cast, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from . import registry, proposal_retractions
from .db import AdapterVersion, Base, User
from .protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview, ReviewChecks


class SchemeAdmissionCredit(Base):
    __tablename__ = "scheme_admission_credits"
    __table_args__ = (
        UniqueConstraint("scheme_id", name="uq_scheme_credit_scheme"),
        UniqueConstraint("proposal_id", name="uq_scheme_credit_proposal"),
        UniqueConstraint("review_id", name="uq_scheme_credit_review"),
        UniqueConstraint("source_sha256", name="uq_scheme_credit_source"),
    )
    family_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scheme_id: Mapped[str] = mapped_column(String(100))
    scheme_version: Mapped[str] = mapped_column(String(32))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposals.id"))
    review_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposal_reviews.id"))
    revision: Mapped[int] = mapped_column(Integer)
    specification_sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str] = mapped_column(Text)


ADMISSION_POLICY_ID = "crucible-scheme-admission-points-v2"
ADMISSION_POINTS = 2000
ACTIVE_WEEK_POINTS = 100
WEEK_SECONDS = 7 * 24 * 60 * 60


class SchemeAdmissionRetirement(Base):
    """One terminal, append-only cutoff; restoring registry code cannot resume it."""
    __tablename__ = "scheme_admission_retirements"
    __table_args__ = (CheckConstraint("created_at >= 0", name="ck_admission_retirement_time"),)
    family_id: Mapped[str] = mapped_column(ForeignKey("scheme_admission_credits.family_id"), primary_key=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)


def admission_points_relation(as_of):
    """Aggregate completed active weeks at one explicit time for the whole query.

    Admission and retirement are durable decisions. Current registry contents,
    runtime availability and proposal approval alone never define active time.
    Author withdrawal provides a second terminal cutoff without editing evidence.
    """
    if isinstance(as_of, bool) or not isinstance(as_of, (int, float)) or not math.isfinite(as_of) or as_of < 0:
        raise ValueError("Scoring time must be a finite nonnegative timestamp.")
    credit, retirement = SchemeAdmissionCredit, SchemeAdmissionRetirement
    withdrawal = proposal_retractions.ProposalRetraction
    cutoff = case((retirement.created_at < as_of, retirement.created_at), else_=as_of)
    cutoff = case((withdrawal.created_at < cutoff, withdrawal.created_at), else_=cutoff)
    duration = case((cutoff > credit.created_at, cutoff - credit.created_at), else_=0)
    weeks = cast(func.floor(duration / WEEK_SECONDS), Integer)
    totals = (select(credit.user_id.label("user_id"),
                     (ADMISSION_POINTS * func.count()).label("admission_points"),
                     func.sum(weeks).label("active_weeks"),
                     (ACTIVE_WEEK_POINTS * func.sum(weeks)).label("weekly_points"))
              .outerjoin(retirement, retirement.family_id == credit.family_id)
              .outerjoin(withdrawal, (withdrawal.target_kind == proposal_retractions.SCHEME) &
                         (withdrawal.target_id == credit.proposal_id))
              .where(credit.created_at <= as_of).group_by(credit.user_id)).subquery()
    return select(totals, (totals.c.admission_points + totals.c.weekly_points).label("scheme_contributions"))


def retire_scheme_credit(db, settings, *, family_id, reviewer_id, reason, dry_run=False, clock=time.time):
    """Record a terminal family retirement now; exact retries retain the first cutoff.

    This stops future weekly recognition, not past points, publication access or
    challenge serving. Coordinate registry removal separately in the same rollout.
    There is deliberately no backdating or automatic reinstatement operation.
    """
    if reviewer_id not in settings.reviewer_ids:
        raise AdmissionCreditError("A configured reviewer must record scheme retirement.")
    if not isinstance(reason, str) or not 20 <= len(reason.strip()) <= 10000:
        raise AdmissionCreditError("Record 20 to 10000 characters explaining the retirement.")
    reason = reason.strip()
    try:
        with db.transaction(write=True) as session:
            credit = session.scalar(select(SchemeAdmissionCredit).where(
                SchemeAdmissionCredit.family_id == family_id).with_for_update())
            if credit is None:
                raise AdmissionCreditError("An existing admission credit is required.")
            if session.get(User, reviewer_id) is None:
                raise AdmissionCreditError("Reviewer account not found.")
            existing = session.get(SchemeAdmissionRetirement, family_id)
            if existing is not None:
                if existing.reviewer_id != reviewer_id or existing.reason != reason:
                    raise AdmissionCreditError("This family already has a different immutable retirement.")
                return {"created": False, "dry_run": dry_run, "retirement": credit_data(existing)}
            timestamp = clock()
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < max(0, credit.created_at):
                raise AdmissionCreditError("Retirement time must be finite and at or after admission.")
            record = SchemeAdmissionRetirement(family_id=family_id, reviewer_id=reviewer_id,
                                              created_at=timestamp, reason=reason)
            if not dry_run:
                session.add(record)
                session.flush()
            return {"created": not dry_run, "dry_run": dry_run, "retirement": credit_data(record)}
    except IntegrityError:
        raise AdmissionCreditError("This family has a conflicting retirement; no event was added.") from None


class AdmissionCreditError(ValueError):
    """The operator request cannot establish a new, uniquely credited admission."""


def credit_data(record):
    return {column.name: getattr(record, column.name) for column in record.__table__.columns}


def _digest(specification):
    return hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def award_scheme_credit(db, settings, *, family_id, scheme_id, scheme_version,
                        proposal_id, review_id, reviewer_id, evidence,
                        confirm_new_family=False, dry_run=False, clock=time.time):
    """Validate and record a once-per-family admission; exact retries are inert.

The configured reviewer must be the author of the approval, not the proposer.
The family ID and evidence explicitly assert this is a new family rather than
an alias/release of one already credited. Hash equality rejects exact source
reuse; semantic novelty still requires the trusted reviewer's judgment.
"""
    if confirm_new_family is not True:
        raise AdmissionCreditError("Explicit confirmation of a genuinely new scheme family is required.")
    if not isinstance(family_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", family_id):
        raise AdmissionCreditError("Family ID must be a stable lowercase identifier of at most 100 characters.")
    if not isinstance(evidence, str) or not 20 <= len(evidence.strip()) <= 10000:
        raise AdmissionCreditError("Record 20 to 10000 characters of admission and new-family evidence.")
    evidence = evidence.strip()
    if reviewer_id not in settings.reviewer_ids:
        raise AdmissionCreditError("A configured reviewer must record admission credit.")
    request = dict(family_id=family_id, scheme_id=scheme_id, scheme_version=scheme_version,
                   proposal_id=proposal_id, review_id=review_id, reviewer_id=reviewer_id, evidence=evidence)
    try:
        with db.transaction(write=True) as session:
            reviewer = session.scalar(select(User).where(User.id == reviewer_id).with_for_update())
            if reviewer is None:
                raise AdmissionCreditError("Reviewer account not found.")
            existing = session.get(SchemeAdmissionCredit, family_id)
            if existing:
                if any(getattr(existing, field) != value for field, value in request.items()):
                    raise AdmissionCreditError("This family already has a different immutable admission credit.")
                return {"created": False, "dry_run": dry_run, "credit": credit_data(existing)}

            proposal = session.scalar(select(ProtocolProposal).where(
                ProtocolProposal.id == proposal_id).with_for_update())
            review = session.get(ProtocolProposalReview, review_id)
            if proposal is None or review is None or review.proposal_id != proposal.id:
                raise AdmissionCreditError("Proposal and matching review are required.")
            if proposal_retractions.get(session, proposal_retractions.SCHEME, proposal.id) is not None:
                raise AdmissionCreditError("A retracted proposal cannot receive new admission credit.")
            if review.checks.get("test_only_self_review") is True:
                raise AdmissionCreditError("Development self-review cannot earn scheme contribution credit.")
            if proposal.author_id == reviewer_id or review.reviewer_id != reviewer_id:
                raise AdmissionCreditError("The approving reviewer must record this credit and cannot be its recipient.")
            if (proposal.status != "approved" or review.decision != "approved"
                    or review.revision != proposal.revision or review.content_sha256 != proposal.content_sha256):
                raise AdmissionCreditError("The latest proposal revision must have this exact approval.")
            revision = session.scalar(select(ProtocolProposalRevision).where(
                ProtocolProposalRevision.proposal_id == proposal.id,
                ProtocolProposalRevision.revision == proposal.revision))
            if (revision is None or revision.content_sha256 != proposal.content_sha256
                    or revision.specification != proposal.specification
                    or _digest(proposal.specification) != proposal.content_sha256):
                raise AdmissionCreditError("The frozen proposal revision or its content hash does not match.")
            try:
                checks = ReviewChecks.model_validate(review.checks)
            except ValueError:
                raise AdmissionCreditError("The approval's review checks are invalid.") from None
            if not all(checks.model_dump().values()):
                raise AdmissionCreditError("Every admission review check must have passed.")
            if registry.CURRENT.get(scheme_id) != scheme_version:
                raise AdmissionCreditError("The exact scheme release must already be active in the trusted registry.")
            try:
                adapter = registry.adapter_for(scheme_id, scheme_version)
                source_sha256 = hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
                manifests = adapter.manifests()
            except (ValueError, OSError, AttributeError):
                raise AdmissionCreditError("The active registered source cannot be verified.") from None
            expected_sha = (proposal.specification.get("implementation") or {}).get("sha256")
            if (proposal.specification.get("version") != scheme_version
                    or adapter.SCHEME_ID != scheme_id or adapter.SCHEME_VERSION != scheme_version
                    or not manifests or adapter.IMPLEMENTATION_SHA256 != source_sha256
                    or expected_sha != source_sha256 or review.implementation_sha256 != source_sha256
                    or any(item.get("implementation", {}).get("source_sha256") != source_sha256
                           or item.get("scheme_id") != scheme_id or item.get("version") != scheme_version
                           for item in manifests)):
                raise AdmissionCreditError("The approved specification, implementation hash and active source must match exactly.")
            pin = session.get(AdapterVersion, (scheme_id, scheme_version))
            if pin and pin.source_sha256 != source_sha256:
                raise AdmissionCreditError("The active source differs from its persisted adapter pin.")
            duplicate = session.scalar(select(SchemeAdmissionCredit).where(
                (SchemeAdmissionCredit.scheme_id == scheme_id)
                | (SchemeAdmissionCredit.proposal_id == proposal_id)
                | (SchemeAdmissionCredit.review_id == review_id)
                | (SchemeAdmissionCredit.source_sha256 == source_sha256)).limit(1))
            if duplicate:
                raise AdmissionCreditError("This scheme, proposal, review or source already has admission credit.")
            record = SchemeAdmissionCredit(**request, user_id=proposal.author_id,
                revision=proposal.revision, specification_sha256=proposal.content_sha256,
                source_sha256=source_sha256, created_at=clock())
            if not dry_run:
                session.add(record)
                session.flush()
            return {"created": not dry_run, "dry_run": dry_run, "credit": credit_data(record)}
    except IntegrityError:
        raise AdmissionCreditError("This admission conflicts with an existing credit; no additional credit was recorded.") from None
