"""Explicit research enrollment preserves evidence and noncumulative awards."""
import pytest
from sqlalchemy import select

from crucible import registry, scoring, standard_schemes
from test_calibrated_api import current_system
from test_people import add_user, person, receipt, registered_receipt


RESEARCH = tuple(registry.adapter_for(scheme) for scheme in
                 ("dhkem-x25519", "ml-kem", "frodokem", "sntrup", "hqc", "classic-mceliece"))


@pytest.mark.parametrize("adapter", RESEARCH, ids=lambda adapter: adapter.SCHEME_ID)
def test_existing_research_receipts_earn_progress_and_single_first_bonus(current_system, adapter):
    app, _, _ = current_system
    user = add_user(app, "alice")
    for tier in range(1, 6):
        registered_receipt(app, user, adapter, tier, tier)
    registered_receipt(app, user, adapter, 5, 6)
    data = person(app, "alice")
    weight = {"dhkem-x25519": 50, "ml-kem": 416, "frodokem": 31, "sntrup": 92,
              "hqc": 37, "classic-mceliece": 50}[adapter.SCHEME_ID]
    assert data["points"] == 5 * weight
    assert data["score_breakdown"] == {"progress": weight, "early_recovery": 4 * weight, "scheme_contributions": 0, "note_contributions": 0}
    assert all(record["scoring_eligible"] for record in data["records"])
    profile = adapter.manifests()[4]
    assert standard_schemes.profile_presentation(profile)["scoring_eligible"]
    assert standard_schemes.beta_metadata(adapter.SCHEME_ID, profile)["scoring_eligible"]


def test_exact_tier_first_bonus_does_not_inherit_from_higher_research_result(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    adapter = registry.code_mceliece
    registered_receipt(app, alice, adapter, 5, 0)
    first_four = registered_receipt(app, bob, adapter, 4, 1)
    registered_receipt(app, bob, adapter, 5, 2)
    data = person(app, "bob")
    assert data["points"] == 210  # Best tier5 + retained first at exact tier4.
    assert data["score_breakdown"]["early_recovery"] == 160
    assert all(record["score"]["early_recovery_submission_id"] == first_four["submission_id"]
               for record in data["records"])
    assert person(app, "alice")["points"] == 250


def test_ec_preserves_its_registered_inherited_standings(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    registered_receipt(app, alice, registry.adapter_for("dhkem-x25519"), 5, 0)
    registered_receipt(app, bob, registry.adapter_for("dhkem-x25519"), 4, 1)
    assert person(app, "alice")["points"] == 250
    assert person(app, "bob")["points"] == 40


def test_hqc_bounds_and_other_releases_do_not_silently_enroll(current_system):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    release = scoring.FAMILY_RELEASES["hqc"]
    assert scoring.eligible_record("hqc", release["version"], release["profile_prefix"] + "66", 66)
    assert not scoring.eligible_record("hqc", release["version"], release["profile_prefix"] + "67", 67)
    assert not scoring.eligible_record("hqc", "next-release", release["profile_prefix"] + "1", 1)
    from crucible.db import Challenge
    def bind_unregistered(result, session):
        challenge = session.get(Challenge, result.challenge_id)
        challenge.profile_id = result.profile_id = release["profile_prefix"] + "67"
    receipt(app, alice, 67, scheme="hqc", version=release["version"], mutate=bind_unregistered)
    participants, _ = scoring.score_relations()
    with app.state.db.transaction() as session:
        assert session.scalar(select(participants.c.points).where(participants.c.id == alice)) == 0


def test_published_reference_profile_remains_unscored(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    registered_receipt(app, user, registry.dhkem_x25519, 1)
    data = person(app, "alice")
    assert data["points"] == 0
    assert not data["records"][0]["scoring_eligible"]
