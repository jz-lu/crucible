"""Public participant summaries backed by actual, exact-release receipts.

The score policy enrolls exact releases. No archived tier is rewritten.
All queries select explicit public fields; detailed evidence remains at the
existing challenge and submission endpoints under their own access rules.
"""
import re

from fastapi import HTTPException, Query
from sqlalchemy import func, select

from . import milestones, registry, scoring
from .db import User
from .scoring_awards import SchemeAdmissionCredit


RECENT_LIMIT = 20


def _participants():
    participants, _ = scoring.score_relations()
    admissions = select(SchemeAdmissionCredit.user_id,
                        func.count().label("accepted_proposal_count")).group_by(
                            SchemeAdmissionCredit.user_id).subquery()
    # Count explicit, once-per-family admissions directly. Proposal drafts and
    # review approvals alone do not qualify, and this metric is not inferred
    # from points or coupled to the current award amount.
    return (select(participants, func.coalesce(admissions.c.accepted_proposal_count, 0)
                   .label("accepted_proposal_count"))
            .outerjoin(admissions, admissions.c.user_id == participants.c.id)).subquery()


def _record(row):
    # Tier comes from the original immutable manifest, never today's adapter.
    return {
        "scheme_id": row.scheme_id,
        "scheme_name": registry.DETAILS.get(row.scheme_id, {}).get("name", row.scheme_id),
        "scheme_version": row.scheme_version,
        "profile_id": row.profile_id,
        "tier": row.tier if type(row.tier) is int and row.tier > 0 else None,
        "submission_id": row.submission_id,
        "received_at": row.received_at,
        "is_current": registry.CURRENT.get(row.scheme_id) == row.scheme_version,
        "scoring_eligible": scoring.eligible_record(row.scheme_id, row.scheme_version, row.profile_id, row.tier),
    }


def _summaries(session, users):
    """Three aggregate queries for the whole page, independent of account count."""
    if not users:
        return []
    user_ids = [user.id for user in users]
    receipts = scoring.correct_receipts(user_ids).subquery()
    counts = {row.user_id: row for row in session.execute(
        select(receipts.c.user_id, func.count().label("recoveries"),
               func.count(func.distinct(receipts.c.scheme_id)).label("schemes"))
        .group_by(receipts.c.user_id))}
    ranked = select(receipts, func.row_number().over(
        partition_by=(receipts.c.user_id, receipts.c.scheme_id, receipts.c.scheme_version),
        order_by=(receipts.c.tier.desc(), receipts.c.received_at, receipts.c.submission_id),
    ).label("position")).subquery()
    _, families = scoring.score_relations()
    family_scores = {(row.user_id, row.scheme_id): row for row in session.execute(
        select(families).where(families.c.user_id.in_(user_ids)))}
    records = {}
    for row in session.execute(select(ranked).where(ranked.c.position == 1)
                               .order_by(ranked.c.scheme_id, ranked.c.scheme_version)):
        record = _record(row)
        score = family_scores.get((row.user_id, row.scheme_id)) if record["scoring_eligible"] else None
        record["score"] = ({"progress": score.progress, "early_recovery": score.early_recovery,
                            "total": score.progress + score.early_recovery,
                            "early_recovery_tier": score.early_recovery_tier if score.early_recovery else None,
                            "early_recovery_rank": score.early_recovery_rank if score.early_recovery else None,
                            "early_recovery_submission_id": score.early_recovery_submission_id if score.early_recovery else None}
                           if score is not None else None)
        records.setdefault(row.user_id, []).append(record)
    return [{
        "handle": user.handle,
        "points": user.points,
        "rank": user.rank,
        "score_status": "active",
        "score_breakdown": {"progress": user.progress, "early_recovery": user.early_recovery,
                            "scheme_contributions": user.scheme_contributions},
        "correct_submission_count": counts[user.id].recoveries if user.id in counts else 0,
        "schemes_recovered": counts[user.id].schemes if user.id in counts else 0,
        "accepted_proposal_count": user.accepted_proposal_count,
        "records": sorted(records.get(user.id, []), key=lambda record: (
            not record["is_current"], record["scheme_name"].casefold(), record["scheme_version"])),
    } for user in users]


def register_routes(app, db, envelope):
    """Use the catalogue's existing read access; introduce no new permissions."""
    @app.get("/api/people")
    def people(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        with db.transaction() as session:
            total = session.scalar(select(func.count()).select_from(User))
            participants = _participants()
            users = session.execute(select(participants)
                                    .order_by(participants.c.points.desc(), participants.c.handle,
                                              participants.c.id).offset(offset).limit(limit)).all()
            rows = _summaries(session, users)
        return envelope(people=rows,
                        pagination={"limit": limit, "offset": offset, "total": total,
                                    "has_more": offset + len(rows) < total},
                        scoring=scoring.SCORING)

    @app.get("/api/people/{handle}")
    def person(handle: str):
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", handle):
            raise HTTPException(404, "Participant not found.")
        with db.transaction() as session:
            participants = _participants()
            user = session.execute(select(participants)
                                   .where(participants.c.handle == handle.lower())).first()
            if user is None:
                raise HTTPException(404, "Participant not found.")
            summary = _summaries(session, [user])[0]
            recent = scoring.correct_receipts([user.id]).subquery()
            rows = session.execute(select(recent)
                                   .order_by(recent.c.received_at.desc(), recent.c.submission_id.desc())
                                   .limit(RECENT_LIMIT + 1)).all()
            summary.update(joined_at=user.created_at,
                           recent_recoveries=[_record(row) for row in rows[:RECENT_LIMIT]],
                           recent_recoveries_has_more=len(rows) > RECENT_LIMIT)
            summary["first_ten"] = milestones.summary(session, user.id, _record,
                                                      milestones.winning_keys(summary["records"]))
        return envelope(person=summary, scoring=scoring.SCORING)

    @app.get("/api/people/{handle}/milestones")
    def milestone_history(handle: str, limit: int = Query(milestones.PAGE_SIZE, ge=1, le=100),
                          offset: int = Query(0, ge=0)):
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", handle):
            raise HTTPException(404, "Participant not found.")
        with db.transaction() as session:
            user_id = session.scalar(select(User.id).where(User.handle == handle.lower()))
            if user_id is None:
                raise HTTPException(404, "Participant not found.")
            winners = milestones.winning_keys_for_user(session, user_id)
            result = milestones.page(session, user_id, _record, winners, limit=limit, offset=offset)
        return envelope(**result)
