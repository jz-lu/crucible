"""First-ten personal milestones, derived without changing points or history.

A badge requires an actual new personal best in an exact registered release.
Earlier higher recoveries reserve lower places, but never manufacture lower-tier
badges. SQL ranks distinct people before pagination; repeated attempts collapse
before the comparison. Highlights retain the tier/place Pareto frontier.
"""
from sqlalchemy import Integer, String, and_, cast, column, func, literal, or_, select, values

from . import registry, scoring
from .db import Challenge


PAGE_SIZE = 20


def _registered_profiles():
    rows = []
    for (scheme_id, version), adapter in registry.ADAPTERS.items():
        for profile in adapter.manifests():
            tier = profile.get("instance_size", profile.get("parameter_bits"))
            if type(tier) is int and tier > 0:
                rows.append((scheme_id, version, profile["id"], tier))
    # VALUES avoids SQLite's compound-SELECT limit with the >500 historical
    # profile tuples. This relation contains trusted metadata, never user data.
    return values(column("scheme_id", String), column("scheme_version", String),
                  column("profile_id", String), column("tier", Integer)).data(rows).cte("milestone_profiles")


def _places(user_id, dialect):
    profiles = _registered_profiles()
    receipts = scoring.correct_receipts()
    public_columns = [field for field in receipts.selected_columns if field.key != "tier"]
    # Compare the original JSON scalar representation to the trusted integer.
    # This rejects strings, booleans, floats, absent sizes and forged high tiers
    # before windows; no untrusted scalar is cast to an integer by this query.
    declared_tier = func.coalesce(
        func.nullif(cast(Challenge.manifest["instance_size"], String), "null"),
        func.nullif(cast(Challenge.manifest["parameter_bits"], String), "null"),
    )
    valid = (receipts.with_only_columns(*public_columns, profiles.c.tier.label("tier"),
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
    valid = valid.cte("milestone_valid")
    ordered = select(valid, func.max(valid.c.tier).over(
        partition_by=(valid.c.user_id, valid.c.scheme_id, valid.c.scheme_version),
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
    # the opposing relation, including earlier higher recoveries in this release.
    fields = [candidate.c[name] for name in
              ("user_id", "submission_id", "received_at", "scheme_id", "scheme_version", "profile_id", "tier")]
    return (select(*fields, place.label("place"))
            .join(opponent, and_(opponent.c.scheme_id == candidate.c.scheme_id,
                                 opponent.c.scheme_version == candidate.c.scheme_version,
                                 opponent.c.tier >= candidate.c.tier, arrived))
            .where(candidate.c.user_id == user_id).group_by(*fields)
            .having(place <= 10)).cte("milestone_places")


def winning_keys(records):
    """Reuse the existing score decision, including its exact winning tier."""
    return {(record["scheme_id"], record["scheme_version"], score["early_recovery_submission_id"],
             score["early_recovery_tier"])
            for record in records if record.get("scoring_eligible")
            and (score := record.get("score")) and score.get("early_recovery", 0) > 0}


def winning_keys_for_user(session, user_id):
    _, families = scoring.score_relations()
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
        other.c.tier >= places.c.tier, other.c.place <= places.c.place,
        or_(other.c.tier > places.c.tier, other.c.place < places.c.place),
    ).correlate(places).exists()
    highlights = session.execute(select(places).where(~dominated).order_by(
        places.c.scheme_id, places.c.scheme_version, places.c.tier.desc(), places.c.place)).all()
    history = _page(session, places, formatter, winners, PAGE_SIZE, 0)
    return {"highlights": [_data(row, formatter, winners) for row in highlights],
            "history": history["milestones"], "pagination": history["pagination"]}
