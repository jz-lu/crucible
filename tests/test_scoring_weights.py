"""Historical weights preserve enrollment; current awards are flat frontier credit."""
import pytest
import json
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from crucible import progressions, registry, scoring
from test_calibrated_api import current_system
from test_people import add_user, person, registered_receipt


# Independent policy fixtures: these counts belong to v9, not registry metadata.
COUNTS = {"teaching-dh-kem": 50, "rsa-kem": 50, "dhkem-x25519": 50,
          "ml-kem": 6, "frodokem": 80, "sntrup": 27, "hqc": 66,
          "classic-mceliece": 50}


def test_frozen_integer_weights_cover_all_current_configurations():
    assert scoring.POLICY_ID == "crucible-points-v11"
    for scheme, count in COUNTS.items():
        release = scoring.FAMILY_RELEASES[scheme]
        weights = release["progress_weights"]
        assert isinstance(weights, tuple)
        assert len(weights) == release["max_tier"] == count
        assert all(type(weight) is int and weight > 0 for weight in weights)
        assert all(a < b for a, b in zip(weights, weights[1:]))
        assert weights == tuple(500 * ordinal // count for ordinal in range(1, count + 1))
        assert weights[-1] == 500
        for ordinal, weight in enumerate(weights, 1):
            assert scoring.progress_weight(scheme, release["version"],
                release["profile_prefix"] + str(ordinal), ordinal) == weight
        assert scoring.progress_weight(scheme, release["version"],
            release["profile_prefix"] + str(count + 1), count + 1) is None
        assert scoring.progress_weight(scheme, "unapproved-new-release",
            release["profile_prefix"] + "1", 1) is None


@pytest.mark.parametrize("scheme", COUNTS)
def test_each_family_endpoint_has_equal_first_and_later_solver_caps(current_system, scheme):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    adapter = registry.adapter_for(scheme)
    count = COUNTS[scheme]
    best = registered_receipt(app, alice, adapter, count, 0)
    registered_receipt(app, alice, adapter, count, 1)
    registered_receipt(app, alice, adapter, 1, 2)
    registered_receipt(app, bob, adapter, count, 3)
    first, later = person(app, "alice"), person(app, "bob")
    assert first["points"] == 100
    assert first["score_breakdown"] == {"progress": 0, "early_recovery": 100,
                                         "scheme_contributions": 0, "note_contributions": 0, "bounty_points": 0}
    assert first["records"][0]["score"]["early_recovery_submission_id"] == best["submission_id"]
    assert later["points"] == 0
    assert later["score_breakdown"]["early_recovery"] == 0


@pytest.mark.parametrize("scheme", COUNTS)
def test_step_by_step_and_direct_recovery_have_identical_totals(current_system, scheme):
    app, _, _ = current_system
    user = add_user(app, "alice")
    adapter = registry.adapter_for(scheme)
    registered_receipt(app, user, adapter, 5, 0)
    before = person(app, "alice")
    for ordinal in range(1, 6):
        registered_receipt(app, user, adapter, ordinal, ordinal)
    after = person(app, "alice")
    assert before["score_breakdown"] == after["score_breakdown"]
    assert after["points"] == 100


@pytest.mark.parametrize("scheme,ordinal,expected", [("sntrup", 17, 314)])
def test_off_route_configuration_keeps_its_weight_when_default_navigation_changes(
        current_system, monkeypatch, scheme, ordinal, expected):
    app, _, _ = current_system
    user = add_user(app, "alice")
    adapter = registry.adapter_for(scheme)
    profile = adapter.manifests()[ordinal - 1]
    route = progressions.for_release(scheme, adapter.SCHEME_VERSION, adapter.manifests())
    assert profile["id"] in route["additional_configuration_ids"]
    registered_receipt(app, user, adapter, ordinal, 0)
    assert person(app, "alice")["points"] == 100
    # Scoring must not consult the display progression, adapter enumeration or
    # current release selection when pricing an already enrolled receipt.
    monkeypatch.setattr(progressions, "for_release", lambda *args, **kwargs: None)
    monkeypatch.setitem(registry.CURRENT, scheme, "future-presentation")
    participants, _ = scoring.score_relations()
    with app.state.db.transaction() as session:
        assert session.scalar(select(participants.c.points).where(participants.c.id == user)) == 100


def test_postgresql_scoring_compiles_with_pinned_ordinal_frontier():
    participants, families = scoring.score_relations("postgresql")
    for relation in (participants, families):
        sql = str(select(relation).compile(dialect=postgresql.dialect(),
                                          compile_kwargs={"literal_binds": True}))
        assert "score_thresholds" not in sql
        assert "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING" in sql
        assert "max(score_achievements.tier)" in sql
        assert "json_type(" not in sql  # SQLite-only validation must not leak.
        bound = select(relation).compile(dialect=postgresql.dialect()).params.values()
        profiles = next(json.loads(value) for value in bound
                        if isinstance(value, str) and value.startswith('[{"scheme_id":'))
        assert next(row for row in profiles if row['profile_id'] == 'frodo-lwe-v3-tier-80') == {
            'scheme_id': 'frodokem', 'scheme_version': 'anchor-research-v3',
            'profile_id': 'frodo-lwe-v3-tier-80', 'tier': 80,
            'record_inheritance': False, 'progress_points': 500}


def test_v8_hqc_enrollment_is_preserved_as_history_not_current_eligibility():
    historical = scoring.HISTORICAL_FAMILY_RELEASES["crucible-points-v8"]["hqc"]
    assert historical["version"] == "growth-research-v2"
    assert historical["max_tier"] == 27
    assert historical["record_inheritance"] is False
    assert historical["progress_weights"] == tuple(500*i//27 for i in range(1, 28))
    assert historical["progress_weights"][23] == 444
    for ordinal in range(1, 28):
        assert scoring.progress_weight("hqc", historical["version"],
            historical["profile_prefix"] + str(ordinal), ordinal) is None
    current = scoring.FAMILY_RELEASES["hqc"]
    assert current["version"] == "modeled-research-v3"
    assert current["progress_weights"][23] == 181
    assert current["progress_weights"][-1] == historical["progress_weights"][-1] == 500
