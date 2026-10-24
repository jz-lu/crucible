"""First-ten personal milestones, derived without changing points or history.

A badge requires an actual new personal best in an exact registered release.
For releases explicitly allowing inheritance, earlier higher recoveries reserve
lower places and highlights retain the tier/place Pareto frontier. Exact-only
profiles have separate first-ten places and highlights, even when a person has
already recovered another, higher-numbered profile. SQL ranks distinct people
before pagination and collapses repeated attempts before comparison.
"""
from functools import lru_cache

from sqlalchemy import Boolean, Integer, String, and_, case, cast, column, func, literal, or_, select

from . import registry, scoring
from .db import Challenge


PAGE_SIZE = 20


@lru_cache(maxsize=8)
def _profile_rows(identity, include_standard):
    rows = []
    for scheme_id, version, adapter, _, _ in identity:
        for identifier, tier, kind, label, inherits in registry.presentation_record_profiles(adapter):
            if kind == "standard-parameter-set" and not include_standard:
                continue
            if type(tier) is int and tier > 0:
                rows.append((scheme_id, version, identifier, tier, kind, label, inherits))
    return tuple(rows)


def _registered_profiles(dialect="sqlite", *, include_standard=False):
    # Reuse small immutable tuples, not the full manifests and artifact hashes,
    # across receipt queries. Registry/provider identity changes invalidate it.
    rows = _profile_rows(registry.metadata_identity(), include_standard)
    # One typed JSON input avoids recompiling thousands of VALUES parameters
    # on every request. This relation contains trusted metadata, never user data.
    return scoring.metadata_relation("milestone_profiles", (column("scheme_id", String), column("scheme_version", String),
                  column("profile_id", String), column("tier", Integer),
                  column("profile_kind", String), column("profile_label", String),
                  column("record_inheritance", Boolean)), rows, dialect)


def registered_receipts(dialect, user_ids=None, *, include_standard=False):
    """Validate actual frozen profile identity and integer tier before ranking."""
    profiles = _registered_profiles(dialect, include_standard=include_standard)
    receipts = scoring.correct_receipts(user_ids)
    registered_fields = ("tier", "profile_kind", "profile_label", "record_inheritance")
    public_columns = [field for field in receipts.selected_columns if field.key not in registered_fields]
    # Compare the original JSON scalar representation to the trusted integer.
    # This rejects strings, booleans, floats, absent sizes and forged high tiers
    # before windows; no untrusted scalar is cast to an integer by this query.
    declared_tier = func.coalesce(
        func.nullif(cast(Challenge.manifest["instance_size"], String), "null"),
        func.nullif(cast(Challenge.manifest["parameter_bits"], String), "null"),
    )
    valid = (receipts.with_only_columns(*public_columns, *(profiles.c[name] for name in registered_fields),
                                       maintain_column_froms=True)
             .join(profiles, and_(profiles.c.scheme_id == Challenge.scheme_id,
                                  profiles.c.scheme_version == Challenge.scheme_version,
                                  profiles.c.profile_id == Challenge.profile_id))
             .where(declared_tier == cast(profiles.c.tier, String)))
    if dialect == "sqlite":
        # SQLite JSON_EXTRACT represents JSON true as integer 1. Check the
        # original JSON type so a boolean cannot masquerade as a Tier 1 integer.
        size_type = func.json_type(Challenge.manifest, "$.instance_size")
        parameter_type = func.json_type(Challenge.manifest, "$.parameter_bits")
        valid = valid.where(or_(size_type == "integer", and_(
            or_(size_type.is_(None), size_type == "null"), parameter_type == "integer")))
    return valid


def _places(user_id, dialect):
    valid = registered_receipts(dialect).cte("milestone_valid")
    ordered = select(valid, func.max(valid.c.tier).over(
        partition_by=(valid.c.user_id, valid.c.scheme_id, valid.c.scheme_version,
                      case((valid.c.record_inheritance.is_(False), valid.c.profile_id), else_="")),
        order_by=(valid.c.received_at, valid.c.submission_id), rows=(None, -1),
    ).label("previous_best")).cte("milestone_ordered")
    improvements = select(ordered).where(or_(ordered.c.previous_best.is_(None),
                                             ordered.c.tier > ordered.c.previous_best)).cte("milestone_improvements")
    candidate = improvements.alias("milestone_candidate")
    opponent = improvements.alias("milestone_opponent")
    arrived = or_(opponent.c.received_at < candidate.c.received_at,
                  and_(opponent.c.received_at == candidate.c.received_at,
                       opponent.c.submission_id <= candidate.c.submission_id))
    place = func.count(func.distinct(opponent.c.user_id))
    # Owner filtering applies only to candidate rows. Every account remains in
    # the opposing relation. A higher profile qualifies only where the exact
    # registered release explicitly permits inherited recognition.
    fields = [candidate.c[name] for name in
              ("user_id", "submission_id", "received_at", "scheme_id", "scheme_version", "profile_id", "tier",
               "profile_kind", "profile_label", "record_inheritance")]
    return (select(*fields, place.label("place"))
            .join(opponent, and_(opponent.c.scheme_id == candidate.c.scheme_id,
                                 opponent.c.scheme_version == candidate.c.scheme_version,
                                 or_(and_(candidate.c.record_inheritance.is_(False),
                                          opponent.c.profile_id == candidate.c.profile_id),
                                     and_(candidate.c.record_inheritance.is_(True),
                                          opponent.c.tier >= candidate.c.tier)), arrived))
            .where(candidate.c.user_id == user_id).group_by(*fields)
            .having(place <= 10)).cte("milestone_places")


def winning_keys(records):
    """Reuse the existing score decision, including its exact winning tier."""
    return {(record["scheme_id"], record["scheme_version"], score["early_recovery_submission_id"],
             score["early_recovery_tier"])
            for record in records if record.get("scoring_eligible")
            and (score := record.get("score")) and score.get("early_recovery", 0) > 0}


def winning_keys_for_user(session, user_id):
    _, families = scoring.score_relations(session.get_bind().dialect.name)
    rows = session.execute(select(families).where(families.c.user_id == user_id,
                                                 families.c.early_recovery > 0)).all()
    return {(row.scheme_id, scoring.FAMILY_RELEASES[row.scheme_id]["version"],
             row.early_recovery_submission_id, row.early_recovery_tier) for row in rows}


def _data(row, formatter, winners):
    record = formatter(row)
    record.update(place=row.place, counts_toward_bonus=bool(
        record["scoring_eligible"] and (row.scheme_id, row.scheme_version, row.submission_id, row.tier) in winners))
    return record


def _page(session, places, formatter, winners, limit, offset):
    rows = session.execute(select(places).order_by(places.c.received_at.desc(), places.c.submission_id.desc())
                           .offset(offset).limit(limit + 1)).all()
    return {"milestones": [_data(row, formatter, winners) for row in rows[:limit]],
            "pagination": {"limit": limit, "offset": offset, "has_more": len(rows) > limit}}


def page(session, user_id, formatter, winners, *, limit=PAGE_SIZE, offset=0):
    return _page(session, _places(user_id, session.get_bind().dialect.name), formatter, winners, limit, offset)


def summary(session, user_id, formatter, winners):
    places = _places(user_id, session.get_bind().dialect.name)
    other = places.alias("dominating_milestone")
    dominated = select(literal(1)).where(
        other.c.scheme_id == places.c.scheme_id,
        other.c.scheme_version == places.c.scheme_version,
        or_(places.c.record_inheritance.is_(True), other.c.profile_id == places.c.profile_id),
        other.c.tier >= places.c.tier, other.c.place <= places.c.place,
        or_(other.c.tier > places.c.tier, other.c.place < places.c.place),
    ).correlate(places).exists()
    highlights = session.execute(select(places).where(~dominated).order_by(
        places.c.scheme_id, places.c.scheme_version, places.c.tier.desc(), places.c.place)).all()
    history = _page(session, places, formatter, winners, PAGE_SIZE, 0)
    return {"highlights": [_data(row, formatter, winners) for row in highlights],
            "history": history["milestones"], "pagination": history["pagination"]}
