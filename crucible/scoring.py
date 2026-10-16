"""Versioned scoring policy; all awards derive from server-verified evidence.

Only explicitly enrolled releases earn recovery points. Publishing a replacement
release cannot silently multiply points or reinterpret an archived raw tier.
Repeated hourly receipts and intermediate tiers never add cumulative points.
"""
from sqlalchemy import Boolean, Integer, String, and_, case, cast, column, func, literal, or_, select, values

from .db import Challenge, Method, Submission, User
from .scoring_awards import SchemeAdmissionCredit


POLICY_ID = "crucible-points-v6"
FAMILY_RELEASES = {
    "teaching-dh-kem": {"version": "7", "profile_prefix": "dh-v7-tier-", "max_tier": 50, "record_inheritance": True},
    "rsa-kem": {"version": "4", "profile_prefix": "rsa-v4-tier-", "max_tier": 50, "record_inheritance": True},
    "dhkem-x25519": {"version": "curve-compressed-v2", "profile_prefix": "ec-compressed-v2-tier-", "max_tier": 50, "record_inheritance": True},
    "ml-kem": {"version": "packed-research-v3", "profile_prefix": "module-lwe-v3-tier-", "max_tier": 11, "record_inheritance": False},
    "frodokem": {"version": "anchor-research-v3", "profile_prefix": "frodo-lwe-v3-tier-", "max_tier": 80, "record_inheritance": False},
    "sntrup": {"version": "3", "profile_prefix": "sntrup-v3-tier-", "max_tier": 27, "record_inheritance": False},
    "hqc": {"version": "growth-research-v2", "profile_prefix": "hqc-growth-research-v2-tier-", "max_tier": 27, "record_inheritance": False},
    "classic-mceliece": {"version": "length-research-v1", "profile_prefix": "classic-mceliece-length-research-v1-tier-", "max_tier": 50, "record_inheritance": False},
}
SCORING = {"status": "active", "policy_id": POLICY_ID, "ordering": "points_desc_handle"}


def eligible_record(scheme_id, version, profile_id, tier):
    release = FAMILY_RELEASES.get(scheme_id)
    return bool(release and release["version"] == version and type(tier) is int
                and 1 <= tier <= release["max_tier"] and profile_id == f'{release["profile_prefix"]}{tier}')


def manifest_eligible(manifest):
    """Current score policy, separate from the frozen protocol manifest."""
    return eligible_record(manifest.get("scheme_id"), manifest.get("version"), manifest.get("id"),
                           manifest.get("instance_size", manifest.get("parameter_bits")))


def correct_receipts(user_ids=None):
    """Select only public receipt identifiers and their original frozen tier."""
    tier = func.coalesce(Challenge.manifest["instance_size"].as_integer(),
                         Challenge.manifest["parameter_bits"].as_integer())
    query = (select(Submission.user_id.label("user_id"),
                    Submission.id.label("submission_id"),
                    Submission.received_at.label("received_at"),
                    Challenge.scheme_id.label("scheme_id"),
                    Challenge.scheme_version.label("scheme_version"),
                    Challenge.profile_id.label("profile_id"), tier.label("tier"),
                    Challenge.manifest["profile_kind"].as_string().label("profile_kind"),
                    Challenge.manifest["record_inheritance"].as_boolean().label("record_inheritance"),
                    Challenge.manifest["profile_label"].as_string().label("profile_label"))
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


def score_relations(dialect="sqlite"):
    """Build portable SQL aggregates; never load all receipts into Python.

    A higher recovery reserves lower places only for an explicitly inheriting
    release. Other releases rank actual recoveries at the exact profile only.
    This matches the factual per-profile leaderboard. Only first earns a bonus.
    A user receives only their greatest such bonus in each family, even after
    many rounds or step-by-step attempts. First-ten milestone recognition is
    independent of this points policy.
    """
    profile_rows = []
    for family, release in FAMILY_RELEASES.items():
        profile_rows.extend((family, release["version"], f'{release["profile_prefix"]}{tier}', tier,
                             release["record_inheritance"]) for tier in range(1, release["max_tier"] + 1))
    profiles = values(column("scheme_id", String), column("scheme_version", String),
                      column("profile_id", String), column("tier", Integer),
                      column("record_inheritance", Boolean)).data(profile_rows).cte("score_profiles")
    receipts = correct_receipts()
    fields = [field for field in receipts.selected_columns if field.key not in {"tier", "record_inheritance"}]
    # Validate the original JSON scalar before ranking. In particular strings,
    # floats and JSON true must not become an apparently valid integer tier.
    declared_tier = func.coalesce(
        func.nullif(cast(Challenge.manifest["instance_size"], String), "null"),
        func.nullif(cast(Challenge.manifest["parameter_bits"], String), "null"))
    valid = (receipts.with_only_columns(*fields, profiles.c.tier, profiles.c.record_inheritance,
                                       maintain_column_froms=True)
             .join(profiles, and_(profiles.c.scheme_id == Challenge.scheme_id,
                                 profiles.c.scheme_version == Challenge.scheme_version,
                                 profiles.c.profile_id == Challenge.profile_id))
             .where(declared_tier == cast(profiles.c.tier, String)))
    if dialect == "sqlite":
        size_type = func.json_type(Challenge.manifest, "$.instance_size")
        parameter_type = func.json_type(Challenge.manifest, "$.parameter_bits")
        valid = valid.where(or_(size_type == "integer", and_(
            or_(size_type.is_(None), size_type == "null"), parameter_type == "integer")))
    enrolled = valid.cte("score_enrolled")
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
        *[select(literal(tier).label("threshold")) for tier in range(2, max(r["max_tier"] for r in FAMILY_RELEASES.values()) + 1)]).cte("score_tiers")
    qualifying = select(achievements, tiers.c.threshold, func.row_number().over(
        partition_by=(achievements.c.user_id, achievements.c.scheme_id, tiers.c.threshold),
        order_by=(achievements.c.received_at, achievements.c.submission_id),
    ).label("user_position")).join(tiers, or_(
        and_(achievements.c.record_inheritance.is_(True), achievements.c.tier >= tiers.c.threshold),
        and_(achievements.c.record_inheritance.is_(False), achievements.c.tier == tiers.c.threshold),
    )).cte("score_qualifying")
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
