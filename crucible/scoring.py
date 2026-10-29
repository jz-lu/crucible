"""Versioned scoring policy; points derive from validated receipts and ratings.

Only explicitly enrolled releases earn recovery points. Publishing a replacement
release cannot silently multiply points or reinterpret an archived raw tier.
Repeated hourly receipts and intermediate tiers never add cumulative points.
"""
from functools import lru_cache
import json
import time

from sqlalchemy import Boolean, Integer, JSON, String, and_, case, cast, column, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from .db import Challenge, Method, Submission, User
from .scoring_awards import ADMISSION_POLICY_ID, ADMISSION_POINTS, ACTIVE_WEEK_POINTS, admission_points_relation


POLICY_ID = "crucible-points-v11"
FRONTIER_POINTS = 100

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
SCORING = {"status": "active", "policy_id": POLICY_ID, "ordering": "points_desc_handle",
           "scheme_admissions": {"policy_id": ADMISSION_POLICY_ID, "admission_points": ADMISSION_POINTS,
                                 "completed_active_week_points": ACTIVE_WEEK_POINTS}}


def eligible_record(scheme_id, version, profile_id, tier):
    release = FAMILY_RELEASES.get(scheme_id)
    return bool(release and release["version"] == version and type(tier) is int
                and 1 <= tier <= min(release["max_tier"], len(release["progress_weights"])) and profile_id == f'{release["profile_prefix"]}{tier}')


def progress_weight(scheme_id, version, profile_id, tier):
    """Historical v8/v10 weight (not current points), or None if unenrolled."""
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


@lru_cache(maxsize=16)
def _metadata_json(names, rows):
    """Serialize immutable, trusted profile metadata, never receipts or rights."""
    return json.dumps([dict(zip(names, row, strict=True)) for row in rows],
                      separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def metadata_relation(name, columns, rows, dialect):
    """A typed static relation using one JSON bind instead of thousands of cells.

    The resulting statement remains SQLAlchemy-cacheable. Bound metadata is
    refreshed from the supplied immutable tuples; database results are never
    cached. Only the existing SQLite and PostgreSQL backends are supported.
    """
    payload = literal(_metadata_json(tuple(field.name for field in columns), tuple(rows)))
    if dialect == "postgresql":
        source = func.jsonb_to_recordset(cast(payload, JSONB)).table_valued(*columns).render_derived(
            name=name + "_rows", with_types=True)
        return select(*source.c).cte(name).prefix_with("MATERIALIZED")
    if dialect == "sqlite":
        source = func.json_each(payload).table_valued(column("value", JSON))
        fields = []
        for field in columns:
            value = source.c.value[field.name]
            if isinstance(field.type, Boolean):
                value = value.as_boolean()
            elif isinstance(field.type, Integer):
                value = value.as_integer()
            elif isinstance(field.type, String):
                value = value.as_string()
            else:
                raise TypeError("Unsupported trusted metadata column")
            fields.append(value.label(field.name))
        # Materialize once so JSON extraction is not repeated per receipt and
        # SQLite can index the typed columns for the subsequent profile joins.
        return select(*fields).select_from(source).cte(name).prefix_with("MATERIALIZED")
    raise ValueError("Unsupported metadata database dialect")


def score_relations(dialect="sqlite", *, as_of=None):
    """Build portable SQL aggregates; never load all receipts into Python.

    Only a strict advance beyond the prior exact-release recovery frontier can
    earn numerical credit. Its account receives 100 once in that scheme, anchored
    to their earliest advance. Later advances remain factual records. Ordering is
    deterministic by server receipt time, then receipt ID; navigation stages and
    inherited lower-profile records never change that comparison.
    """
    as_of = time.time() if as_of is None else as_of
    profile_rows = []
    for family, release in FAMILY_RELEASES.items():
        profile_rows.extend((family, release["version"], f'{release["profile_prefix"]}{tier}', tier,
                             release["record_inheritance"], weight)
                            for tier, weight in enumerate(release["progress_weights"], 1))
    profiles = metadata_relation("score_profiles", (column("scheme_id", String), column("scheme_version", String),
                      column("profile_id", String), column("tier", Integer),
                      column("record_inheritance", Boolean),
                      column("progress_points", Integer)), profile_rows, dialect)
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
    enrolled = valid.where(Submission.received_at <= as_of).cte("score_enrolled")
    # Repeated hourly successes cannot establish a new frontier. Retain the
    # first actual receipt per account/configuration before the window scan.
    attempts = select(enrolled, func.row_number().over(
        partition_by=(enrolled.c.user_id, enrolled.c.scheme_id, enrolled.c.tier),
        order_by=(enrolled.c.received_at, enrolled.c.submission_id),
    ).label("attempt_position")).cte("score_attempts")
    achievements = select(attempts).where(attempts.c.attempt_position == 1).cte("score_achievements")
    ordered = select(achievements, func.max(achievements.c.tier).over(
        partition_by=(achievements.c.scheme_id, achievements.c.scheme_version),
        order_by=(achievements.c.received_at, achievements.c.submission_id),
        rows=(None, -1),
    ).label("previous_frontier")).cte("score_frontiers")
    advances = select(ordered).where(ordered.c.tier > func.coalesce(ordered.c.previous_frontier, 0)).cte("score_advances")
    ranked = select(advances, func.row_number().over(
        partition_by=(advances.c.user_id, advances.c.scheme_id),
        order_by=(advances.c.received_at, advances.c.submission_id),
    ).label("award_position"), func.row_number().over(
        partition_by=(advances.c.user_id, advances.c.scheme_id),
        order_by=(advances.c.tier.desc(), advances.c.received_at, advances.c.submission_id),
    ).label("record_position")).cte("score_advance_positions")
    first = select(ranked).where(ranked.c.award_position == 1).cte("score_first_advance")
    highest = select(ranked).where(ranked.c.record_position == 1).cte("score_highest_advance")
    participants_by_family = select(achievements.c.user_id, achievements.c.scheme_id).distinct().cte("score_family_participants")
    families = select(participants_by_family, literal(0).label("progress"),
                      case((first.c.submission_id.is_not(None), FRONTIER_POINTS), else_=0).label("early_recovery"),
                      first.c.tier.label("early_recovery_tier"),
                      case((first.c.submission_id.is_not(None), 1), else_=None).label("early_recovery_rank"),
                      first.c.submission_id.label("early_recovery_submission_id"),
                      highest.c.submission_id.label("highest_frontier_submission_id")).outerjoin(
                          first, and_(participants_by_family.c.user_id == first.c.user_id,
                                      participants_by_family.c.scheme_id == first.c.scheme_id)).outerjoin(
                          highest, and_(participants_by_family.c.user_id == highest.c.user_id,
                                        participants_by_family.c.scheme_id == highest.c.scheme_id)).cte("score_families")
    recoveries = select(families.c.user_id, func.sum(families.c.progress).label("progress"),
                        func.sum(families.c.early_recovery).label("early_recovery")).group_by(
                            families.c.user_id).cte("score_recoveries")
    admissions = admission_points_relation(as_of).cte("score_admissions")
    progress_total = func.coalesce(recoveries.c.progress, 0)
    early_total = func.coalesce(recoveries.c.early_recovery, 0)
    scheme_total = func.coalesce(admissions.c.scheme_contributions, 0)
    from .note_ratings import author_points
    notes = author_points().cte("score_notes")
    note_total = func.coalesce(notes.c.note_contributions, 0)
    from .bounty_allowance import bounty_points_relation
    bounties = bounty_points_relation().cte("score_bounties")
    bounty_total = func.coalesce(bounties.c.bounty_points, 0)
    points = progress_total + early_total + scheme_total + note_total + bounty_total
    participants = (select(User.id.label("id"), User.handle, User.created_at,
                           progress_total.label("progress"), early_total.label("early_recovery"),
                           scheme_total.label("scheme_contributions"),
                           func.coalesce(admissions.c.admission_points, 0).label("admission_points"),
                           func.coalesce(admissions.c.active_weeks, 0).label("admission_active_weeks"),
                           func.coalesce(admissions.c.weekly_points, 0).label("admission_weekly_points"),
                           note_total.label("note_contributions"), bounty_total.label("bounty_points"), points.label("points"),
                           func.rank().over(order_by=points.desc()).label("rank"))
                    .outerjoin(recoveries, recoveries.c.user_id == User.id)
                    .outerjoin(admissions, admissions.c.user_id == User.id)
                    .outerjoin(notes, notes.c.user_id == User.id)
                    .outerjoin(bounties, bounties.c.user_id == User.id)).cte("score_participants")
    return participants, families
