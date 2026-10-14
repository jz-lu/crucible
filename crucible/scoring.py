"""Versioned scoring policy; all awards derive from server-verified evidence.

Only explicitly enrolled releases earn recovery points. Publishing a replacement
release cannot silently multiply points or reinterpret an archived raw tier.
Repeated hourly receipts and intermediate tiers never add cumulative points.
"""
from sqlalchemy import and_, case, func, literal, or_, select

from .db import Challenge, Method, Submission, User
from .scoring_awards import SchemeAdmissionCredit


POLICY_ID = "crucible-points-v2"
FAMILY_RELEASES = {
    "teaching-dh-kem": {"version": "6", "profile_prefix": "dh-v6-tier-"},
    "rsa-kem": {"version": "3", "profile_prefix": "rsa-v3-tier-"},
}
SCORING = {"status": "active", "policy_id": POLICY_ID, "ordering": "points_desc_handle"}


def eligible_record(scheme_id, version, profile_id, tier):
    release = FAMILY_RELEASES.get(scheme_id)
    return bool(release and release["version"] == version and type(tier) is int
                and 1 <= tier <= 50 and profile_id == f'{release["profile_prefix"]}{tier}')


def correct_receipts(user_ids=None):
    """Select only public receipt identifiers and their original frozen tier."""
    tier = func.coalesce(Challenge.manifest["instance_size"].as_integer(),
                         Challenge.manifest["parameter_bits"].as_integer())
    query = (select(Submission.user_id.label("user_id"),
                    Submission.id.label("submission_id"),
                    Submission.received_at.label("received_at"),
                    Challenge.scheme_id.label("scheme_id"),
                    Challenge.scheme_version.label("scheme_version"),
                    Challenge.profile_id.label("profile_id"), tier.label("tier"))
             .join(Challenge, Challenge.id == Submission.challenge_id)
             .join(Method, Method.id == Submission.method_id)
             .where(Submission.correct.is_(True), Challenge.correctness_passed.is_(True),
                    Submission.user_id == Challenge.user_id,
                    Submission.user_id == Method.user_id,
                    Submission.profile_id == Challenge.profile_id,
                    Submission.scheme_version == Challenge.scheme_version,
                    Submission.round_id == Challenge.round_id,
                    Submission.received_at >= Challenge.issued_at,
                    Submission.received_at < Challenge.ends_at))
    return query.where(Submission.user_id.in_(user_ids)) if user_ids is not None else query


def score_relations():
    """Build portable SQL aggregates; never load all receipts into Python.

    For each threshold, one person's earliest receipt at that tier OR HIGHER
    establishes their place. Higher solves therefore reserve lower places, just
    as on the existing per-tier leaderboard. Only first place earns a bonus.
    A user receives only their greatest such bonus in each family, even after
    many rounds or step-by-step attempts. First-ten milestone recognition is
    independent of this points policy.
    """
    receipts = correct_receipts().cte("score_receipts")
    eligible = []
    for family, release in FAMILY_RELEASES.items():
        exact_tier = case(*[(receipts.c.profile_id == f'{release["profile_prefix"]}{tier}', tier)
                            for tier in range(1, 51)], else_=None)
        eligible.append(and_(receipts.c.scheme_id == family,
                             receipts.c.scheme_version == release["version"],
                             receipts.c.tier == exact_tier))
    enrolled = select(receipts).where(or_(*eligible)).cte("score_enrolled")
    # Collapse repeated hourly successes before expanding the fifty thresholds.
    # The expansion is bounded by distinct person/family/tier achievements,
    # rather than by a participant's lifetime count of repeated submissions.
    attempts = select(enrolled, func.row_number().over(
        partition_by=(enrolled.c.user_id, enrolled.c.scheme_id, enrolled.c.tier),
        order_by=(enrolled.c.received_at, enrolled.c.submission_id),
    ).label("attempt_position")).cte("score_attempts")
    achievements = select(attempts).where(attempts.c.attempt_position == 1).cte("score_achievements")
    progress = select(achievements.c.user_id, achievements.c.scheme_id,
                      (10 * func.max(achievements.c.tier)).label("progress")).group_by(
                          achievements.c.user_id, achievements.c.scheme_id).cte("score_progress")
    tiers = select(literal(1).label("threshold")).union_all(
        *[select(literal(tier).label("threshold")) for tier in range(2, 51)]).cte("score_tiers")
    qualifying = select(achievements, tiers.c.threshold, func.row_number().over(
        partition_by=(achievements.c.user_id, achievements.c.scheme_id, tiers.c.threshold),
        order_by=(achievements.c.received_at, achievements.c.submission_id),
    ).label("user_position")).join(tiers, achievements.c.tier >= tiers.c.threshold).cte("score_qualifying")
    first = select(qualifying).where(qualifying.c.user_position == 1).cte("score_first")
    ranked = select(first, func.row_number().over(
        partition_by=(first.c.scheme_id, first.c.threshold),
        order_by=(first.c.received_at, first.c.submission_id),
    ).label("early_rank")).cte("score_ranked")
    bonus = case((ranked.c.early_rank == 1, 40 * ranked.c.threshold), else_=0)
    choices = select(ranked, bonus.label("early_recovery"), func.row_number().over(
        partition_by=(ranked.c.user_id, ranked.c.scheme_id),
        order_by=(bonus.desc(), ranked.c.threshold.desc(), ranked.c.received_at, ranked.c.submission_id),
    ).label("bonus_position")).cte("score_choices")
    best = select(choices).where(choices.c.bonus_position == 1).cte("score_best")
    families = select(progress.c.user_id, progress.c.scheme_id, progress.c.progress,
                      best.c.early_recovery, best.c.threshold.label("early_recovery_tier"),
                      best.c.early_rank.label("early_recovery_rank"),
                      best.c.submission_id.label("early_recovery_submission_id")).join(
                          best, and_(progress.c.user_id == best.c.user_id,
                                     progress.c.scheme_id == best.c.scheme_id)).cte("score_families")
    recoveries = select(families.c.user_id, func.sum(families.c.progress).label("progress"),
                        func.sum(families.c.early_recovery).label("early_recovery")).group_by(
                            families.c.user_id).cte("score_recoveries")
    admissions = select(SchemeAdmissionCredit.user_id,
                        (1000 * func.count()).label("scheme_contributions")).group_by(
                            SchemeAdmissionCredit.user_id).cte("score_admissions")
    progress_total = func.coalesce(recoveries.c.progress, 0)
    early_total = func.coalesce(recoveries.c.early_recovery, 0)
    scheme_total = func.coalesce(admissions.c.scheme_contributions, 0)
    points = progress_total + early_total + scheme_total
    participants = (select(User.id.label("id"), User.handle, User.created_at,
                           progress_total.label("progress"), early_total.label("early_recovery"),
                           scheme_total.label("scheme_contributions"), points.label("points"),
                           func.rank().over(order_by=points.desc()).label("rank"))
                    .outerjoin(recoveries, recoveries.c.user_id == User.id)
                    .outerjoin(admissions, admissions.c.user_id == User.id)).cte("score_participants")
    return participants, families
