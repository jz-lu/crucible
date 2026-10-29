"""Public participant summaries backed by actual, exact-release receipts.

The score policy enrolls exact releases. No archived tier is rewritten.
All queries select explicit public fields; detailed evidence remains at the
existing challenge and submission endpoints under their own access rules.
"""
import re
from functools import lru_cache

from fastapi import HTTPException, Query
from sqlalchemy import case, func, select

from . import milestones, registry, research_schemes, researcher_profiles, scoring
from .db import User
from .scoring_awards import SchemeAdmissionCredit


RECENT_LIMIT = 20


def _release_name(scheme_id, version):
    """Keep published and research names attached to their exact release."""
    fallback = registry.DETAILS.get(scheme_id, {"name": scheme_id})
    try:
        adapter = registry.adapter_for(scheme_id, version)
    except ValueError:
        return fallback["name"]
    return _cached_release_name(scheme_id, version, adapter, adapter.manifests,
                                getattr(adapter, "IMPLEMENTATION_SHA256", None))


@lru_cache(maxsize=128)
def _cached_release_name(scheme_id, version, adapter, provider, source_sha256):
    fallback = registry.DETAILS.get(scheme_id, {"name": scheme_id})
    return research_schemes.details_for(adapter, fallback,
        first_manifest=registry.presentation_manifests(adapter)[0])["name"]


def _participants(session):
    participants, _ = scoring.score_relations(session.get_bind().dialect.name)
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
    standard = getattr(row, "profile_kind", None) == "standard-parameter-set"
    return {
        "scheme_id": row.scheme_id,
        "scheme_name": _release_name(row.scheme_id, row.scheme_version),
        "scheme_version": row.scheme_version,
        "profile_id": row.profile_id,
        "tier": row.tier if not standard and type(row.tier) is int and row.tier > 0 else None,
        **({"profile_kind": row.profile_kind, "profile_label": row.profile_label,
            "selection_kind": "parameter-set", "record_inheritance": False} if standard else {}),
        **({"profile_kind": row.profile_kind, "profile_label": row.profile_label,
            "selection_kind": "tier", "record_inheritance": row.record_inheritance is True}
           if getattr(row, "profile_kind", None) == "research-tier" else {}),
        "submission_id": row.submission_id,
        "received_at": row.received_at,
        "is_current": registry.CURRENT.get(row.scheme_id) == row.scheme_version,
        "scoring_eligible": scoring.eligible_record(row.scheme_id, row.scheme_version, row.profile_id, row.tier),
    }


def _headline(user):
    """Small leaderboard contract, without placeholder record/count values."""
    return {"handle": user.handle, "points": user.points, "rank": user.rank,
            "score_status": "active",
            "score_breakdown": {"progress": user.progress, "early_recovery": user.early_recovery,
                                "scheme_contributions": user.scheme_contributions,
                                "note_contributions": user.note_contributions},
            "scheme_admission_breakdown": {"policy_id": scoring.ADMISSION_POLICY_ID,
                                           "admission_points": user.admission_points,
                                           "completed_active_weeks": user.admission_active_weeks,
                                           "weekly_points": user.admission_weekly_points},
            "accepted_proposal_count": user.accepted_proposal_count}


def _family_scores(session, user_ids):
    _, families = scoring.score_relations(session.get_bind().dialect.name)
    return {(row.user_id, row.scheme_id): row for row in session.execute(
        select(families).where(families.c.user_id.in_(user_ids)))}


def _summaries(session, users, *, family_scores=None):
    """Three aggregate queries for the whole page, independent of account count."""
    if not users:
        return []
    user_ids = [user.id for user in users]
    receipts = scoring.correct_receipts(user_ids).subquery()
    counts = {row.user_id: row for row in session.execute(
        select(receipts.c.user_id, func.count().label("recoveries"),
               func.count(func.distinct(receipts.c.scheme_id)).label("schemes"))
        .group_by(receipts.c.user_id))}
    valid = milestones.registered_receipts(session.get_bind().dialect.name, user_ids,
                                           include_standard=True).subquery()
    release_key = (valid.c.user_id, valid.c.scheme_id, valid.c.scheme_version)
    named = valid.c.profile_kind == "standard-parameter-set"
    ranked = select(valid, func.max(valid.c.received_at).over(partition_by=release_key).label("release_latest"),
                    func.row_number().over(
        partition_by=release_key,
        # Named parameter sets have no comparable numeric strength rank: show
        # the latest actual recovery. Research tiers use highest actual tier.
        order_by=(case((named, 0), else_=valid.c.tier).desc(),
                  case((named, valid.c.received_at), else_=0).desc(),
                  valid.c.received_at, valid.c.submission_id),
    ).label("position")).subquery()
    if family_scores is None:
        family_scores = _family_scores(session, user_ids)
    rows = session.execute(select(ranked).where(ranked.c.position == 1)
                           .order_by(ranked.c.scheme_id, ranked.c.scheme_version)).all()
    # Project one record per family without comparing incompatible releases.
    # Prefer any current-release recovery. Otherwise select the release with
    # the most recent participation and its actual best (or named set above).
    chosen = {}
    for row in sorted(rows, key=lambda row: (
            registry.CURRENT.get(row.scheme_id) != row.scheme_version,
            -row.release_latest, row.scheme_version, row.submission_id)):
        chosen.setdefault((row.user_id, row.scheme_id), row)
    records = {}
    for row in chosen.values():
        record = _record(row)
        key = (row.user_id, row.scheme_id)
        score = family_scores.get(key) if record["scoring_eligible"] else None
        record["score"] = ({"progress": score.progress, "early_recovery": score.early_recovery,
                            "total": score.progress + score.early_recovery,
                            "early_recovery_tier": score.early_recovery_tier if score.early_recovery else None,
                            "early_recovery_rank": score.early_recovery_rank if score.early_recovery else None,
                            "early_recovery_submission_id": score.early_recovery_submission_id if score.early_recovery else None}
                           if score is not None else None)
        records.setdefault(row.user_id, []).append(record)
    return [{
        **_headline(user),
        "correct_submission_count": counts[user.id].recoveries if user.id in counts else 0,
        "schemes_recovered": counts[user.id].schemes if user.id in counts else 0,
        "records": sorted(records.get(user.id, []), key=lambda record: (
            not record["is_current"], record["scheme_name"].casefold(), record["scheme_version"])),
    } for user in users]


def _first_records(session, user_id, family_scores):
    """One actual highest first-place receipt per enrolled scheme family."""
    winners = [row.early_recovery_submission_id for (owner, _), row in family_scores.items()
               if owner == user_id and row.early_recovery > 0]
    if not winners:
        return []
    receipts = milestones.registered_receipts(session.get_bind().dialect.name, [user_id]).subquery()
    rows = session.execute(select(receipts).where(receipts.c.submission_id.in_(winners))).all()
    return sorted(({**_record(row), "place": 1} for row in rows),
                  key=lambda record: (record["scheme_name"].casefold(), record["scheme_id"]))


def register_routes(app, db, envelope):
    """Use the catalogue's existing read access; introduce no new permissions."""
    @app.get("/api/people")
    def people(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
               include_records: bool = True):
        with db.transaction() as session:
            total = session.scalar(select(func.count()).select_from(User))
            participants = _participants(session)
            users = session.execute(select(participants)
                                    .order_by(participants.c.points.desc(), participants.c.handle,
                                              participants.c.id).offset(offset).limit(limit)).all()
            rows = _summaries(session, users) if include_records else [_headline(user) for user in users]
        return envelope(people=rows,
                        pagination={"limit": limit, "offset": offset, "total": total,
                                    "has_more": offset + len(rows) < total},
                        scoring=scoring.SCORING)

    @app.get("/api/people/{handle}")
    def person(handle: str, include_milestones: bool = True):
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", handle):
            raise HTTPException(404, "Participant not found.")
        with db.transaction() as session:
            participants = _participants(session)
            user = session.execute(select(participants)
                                   .where(participants.c.handle == handle.lower())).first()
            if user is None:
                raise HTTPException(404, "Participant not found.")
            family_scores = _family_scores(session, [user.id])
            summary = _summaries(session, [user], family_scores=family_scores)[0]
            recent = milestones.registered_receipts(session.get_bind().dialect.name, [user.id],
                                                    include_standard=True).subquery()
            rows = session.execute(select(recent)
                                   .order_by(recent.c.received_at.desc(), recent.c.submission_id.desc())
                                   .limit(RECENT_LIMIT + 1)).all()
            summary.update(joined_at=user.created_at,
                           researcher_profile=researcher_profiles.profile_for(session, user.id),
                           recent_recoveries=[_record(row) for row in rows[:RECENT_LIMIT]],
                           recent_recoveries_has_more=len(rows) > RECENT_LIMIT)
            summary["first_records"] = _first_records(session, user.id, family_scores)
            if include_milestones:
                winners = {(row["scheme_id"], row["scheme_version"], row["submission_id"], row["tier"])
                           for row in summary["first_records"]}
                summary["first_ten"] = milestones.summary(session, user.id, _record, winners)
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
