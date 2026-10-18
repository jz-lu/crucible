"""Versioned scoring policy; points derive from validated receipts and ratings.

Only explicitly enrolled releases earn recovery points. Publishing a replacement
release cannot silently multiply points or reinterpret an archived raw tier.
Repeated hourly receipts and intermediate tiers never add cumulative points.
"""
from sqlalchemy import Boolean, Integer, String, and_, case, cast, column, func, or_, select, values

from .db import Challenge, Method, Submission, User
from .scoring_awards import SchemeAdmissionCredit


POLICY_ID = "crucible-points-v10"
PERSONAL_CEILING = 500
FIRST_MULTIPLIER = 4

# Frozen policy weights, indexed by the original configuration ordinal. These are
# policy data, not computed from the registry or the displayed stage count.
# Initial convention: floor(500 * ordinal / enrolled configuration count).
# New profiles/releases require an explicit policy decision; do not rescale an
# existing tuple when navigation or configuration availability changes.
_WEIGHTS_6 = (83, 166, 250, 333, 416, 500)
_WEIGHTS_27 = (18, 37, 55, 74, 92, 111, 129, 148, 166, 185, 203, 222, 240, 259, 277, 296, 314, 333, 351, 370, 388, 407, 425, 444, 462, 481, 500)
_WEIGHTS_50 = (10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270, 280, 290, 300, 310, 320, 330, 340, 350, 360, 370, 380, 390, 400, 410, 420, 430, 440, 450, 460, 470, 480, 490, 500)
_WEIGHTS_66 = (7, 15, 22, 30, 37, 45, 53, 60, 68, 75, 83, 90, 98, 106, 113, 121, 128, 136, 143, 151, 159, 166, 174, 181, 189, 196, 204, 212, 219, 227, 234, 242, 250, 257, 265, 272, 280, 287, 295, 303, 310, 318, 325, 333, 340, 348, 356, 363, 371, 378, 386, 393, 401, 409, 416, 424, 431, 439, 446, 454, 462, 469, 477, 484, 492, 500)
_WEIGHTS_80 = (6, 12, 18, 25, 31, 37, 43, 50, 56, 62, 68, 75, 81, 87, 93, 100, 106, 112, 118, 125, 131, 137, 143, 150, 156, 162, 168, 175, 181, 187, 193, 200, 206, 212, 218, 225, 231, 237, 243, 250, 256, 262, 268, 275, 281, 287, 293, 300, 306, 312, 318, 325, 331, 337, 343, 350, 356, 362, 368, 375, 381, 387, 393, 400, 406, 412, 418, 425, 431, 437, 443, 450, 456, 462, 468, 475, 481, 487, 493, 500)
# Historical enrollment is retained for audit, never joined into current scores.
# v9 explicitly replaces HQC only; there is no receipt transfer or second award.
HISTORICAL_FAMILY_RELEASES = {
    "crucible-points-v8": {
        "hqc": {"version": "growth-research-v2", "profile_prefix": "hqc-growth-research-v2-tier-", "max_tier": 27, "progress_weights": _WEIGHTS_27, "record_inheritance": False},
    },
}
FAMILY_RELEASES = {
    "teaching-dh-kem": {"version": "7", "profile_prefix": "dh-v7-tier-", "max_tier": 50, "progress_weights": _WEIGHTS_50, "record_inheritance": True},
    "rsa-kem": {"version": "4", "profile_prefix": "rsa-v4-tier-", "max_tier": 50, "progress_weights": _WEIGHTS_50, "record_inheritance": True},
    "dhkem-x25519": {"version": "curve-compressed-v2", "profile_prefix": "ec-compressed-v2-tier-", "max_tier": 50, "progress_weights": _WEIGHTS_50, "record_inheritance": True},
    "ml-kem": {"version": "compressed-research-v4", "profile_prefix": "module-lwe-v4-tier-", "max_tier": 6, "progress_weights": _WEIGHTS_6, "record_inheritance": False},
    "frodokem": {"version": "anchor-research-v3", "profile_prefix": "frodo-lwe-v3-tier-", "max_tier": 80, "progress_weights": _WEIGHTS_80, "record_inheritance": False},
    "sntrup": {"version": "3", "profile_prefix": "sntrup-v3-tier-", "max_tier": 27, "progress_weights": _WEIGHTS_27, "record_inheritance": False},
    "hqc": {"version": "modeled-research-v3", "profile_prefix": "hqc-modeled-research-v3-tier-", "max_tier": 66, "progress_weights": _WEIGHTS_66, "record_inheritance": False},
    "classic-mceliece": {"version": "length-research-v1", "profile_prefix": "classic-mceliece-length-research-v1-tier-", "max_tier": 50, "progress_weights": _WEIGHTS_50, "record_inheritance": False},
}
SCORING = {"status": "active", "policy_id": POLICY_ID, "ordering": "points_desc_handle"}


def eligible_record(scheme_id, version, profile_id, tier):
    release = FAMILY_RELEASES.get(scheme_id)
    return bool(release and release["version"] == version and type(tier) is int
                and 1 <= tier <= min(release["max_tier"], len(release["progress_weights"])) and profile_id == f'{release["profile_prefix"]}{tier}')


def progress_weight(scheme_id, version, profile_id, tier):
    """Pinned personal-progress points, or None for an unenrolled configuration."""
    if not eligible_record(scheme_id, version, profile_id, tier):
        return None
    return FAMILY_RELEASES[scheme_id]["progress_weights"][tier - 1]


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
                             release["record_inheritance"], weight)
                            for tier, weight in enumerate(release["progress_weights"], 1))
    profiles = values(column("scheme_id", String), column("scheme_version", String),
                      column("profile_id", String), column("tier", Integer),
                      column("record_inheritance", Boolean),
                      column("progress_points", Integer)).data(profile_rows).cte("score_profiles")
    receipts = correct_receipts()
    fields = [field for field in receipts.selected_columns if field.key not in {"tier", "record_inheritance"}]
    # Validate the original JSON scalar before ranking. In particular strings,
    # floats and JSON true must not become an apparently valid integer tier.
    declared_tier = func.coalesce(
        func.nullif(cast(Challenge.manifest["instance_size"], String), "null"),
        func.nullif(cast(Challenge.manifest["parameter_bits"], String), "null"))
    valid = (receipts.with_only_columns(*fields, profiles.c.tier, profiles.c.record_inheritance, profiles.c.progress_points,
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
    # Collapse repeated hourly successes before expanding the enrolled thresholds.
    # The expansion is bounded by distinct person/family/tier achievements,
    # rather than by a participant's lifetime count of repeated submissions.
    attempts = select(enrolled, func.row_number().over(
        partition_by=(enrolled.c.user_id, enrolled.c.scheme_id, enrolled.c.tier),
        order_by=(enrolled.c.received_at, enrolled.c.submission_id),
    ).label("attempt_position")).cte("score_attempts")
    achievements = select(attempts).where(attempts.c.attempt_position == 1).cte("score_achievements")
    progress = select(achievements.c.user_id, achievements.c.scheme_id,
                      func.max(achievements.c.progress_points).label("progress")).group_by(
                          achievements.c.user_id, achievements.c.scheme_id).cte("score_progress")
    thresholds = profiles.alias("score_thresholds")
    qualifying = select(achievements, thresholds.c.tier.label("threshold"),
                        thresholds.c.progress_points.label("threshold_weight"), func.row_number().over(
        partition_by=(achievements.c.user_id, achievements.c.scheme_id, thresholds.c.tier),
        order_by=(achievements.c.received_at, achievements.c.submission_id),
    ).label("user_position")).join(thresholds, and_(
        achievements.c.scheme_id == thresholds.c.scheme_id,
        achievements.c.scheme_version == thresholds.c.scheme_version,
        or_(and_(achievements.c.record_inheritance.is_(True), achievements.c.tier >= thresholds.c.tier),
            and_(achievements.c.record_inheritance.is_(False), achievements.c.tier == thresholds.c.tier)),
    )).cte("score_qualifying")
    first = select(qualifying).where(qualifying.c.user_position == 1).cte("score_first")
    ranked = select(first, func.row_number().over(
        partition_by=(first.c.scheme_id, first.c.threshold),
        order_by=(first.c.received_at, first.c.submission_id),
    ).label("early_rank")).cte("score_ranked")
    bonus = case((ranked.c.early_rank == 1, FIRST_MULTIPLIER * ranked.c.threshold_weight), else_=0)
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
    from .note_ratings import author_points
    notes = author_points().cte("score_notes")
    note_total = func.coalesce(notes.c.note_contributions, 0)
    points = progress_total + early_total + scheme_total + note_total
    participants = (select(User.id.label("id"), User.handle, User.created_at,
                           progress_total.label("progress"), early_total.label("early_recovery"),
                           scheme_total.label("scheme_contributions"), note_total.label("note_contributions"), points.label("points"),
                           func.rank().over(order_by=points.desc()).label("rank"))
                    .outerjoin(recoveries, recoveries.c.user_id == User.id)
                    .outerjoin(admissions, admissions.c.user_id == User.id)
                    .outerjoin(notes, notes.c.user_id == User.id)).cte("score_participants")
    return participants, families
