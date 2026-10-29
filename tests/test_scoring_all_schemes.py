"""Policy v9 pins configuration weights without inventing inherited solves."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from crucible import registry, scoring, standard_schemes
from crucible.db import Challenge, Submission
from test_calibrated_api import current_system
from test_milestones import history
from test_people import add_user, people, person, receipt, registered_receipt


EXACT = ("ml-kem", "frodokem", "sntrup", "hqc", "classic-mceliece")
INHERITED = ("teaching-dh-kem", "rsa-kem", "dhkem-x25519")


def test_all_eight_frozen_enrollments_match_registered_profiles_and_limits():
    assert set(scoring.FAMILY_RELEASES) == set(registry.CURRENT)
    for scheme_id, release in scoring.FAMILY_RELEASES.items():
        assert release["version"] == registry.CURRENT[scheme_id]
        profiles = registry.adapter_for(scheme_id).manifests()
        assert len(profiles) == release["max_tier"]
        for profile in profiles:
            assert scoring.manifest_eligible(profile)
            assert profile.get("record_inheritance", True) == release["record_inheritance"]
        assert not scoring.eligible_record(scheme_id, release["version"],
            f'{release["profile_prefix"]}{release["max_tier"]+1}', release["max_tier"]+1)
        assert not scoring.eligible_record(scheme_id, "future-release", profiles[0]["id"], 1)
    assert scoring.FAMILY_RELEASES["hqc"]["max_tier"] == 66


def test_existing_receipts_gain_points_derivationally_without_rewriting_history(current_system, monkeypatch):
    app, _, _ = current_system
    user = add_user(app, "alice")
    rows = [registered_receipt(app, user, registry.adapter_for(scheme), 1, tick)
            for tick, scheme in enumerate(scoring.FAMILY_RELEASES)]
    with app.state.db.transaction() as session:
        before = {row.id: (row.received_at, row.payload_mac, row.challenge_id) for row in session.scalars(select(Submission))}
    all_releases = scoring.FAMILY_RELEASES
    with monkeypatch.context() as previous_policy:
        previous_policy.setattr(scoring, "FAMILY_RELEASES", {name: all_releases[name] for name in INHERITED[:2]})
        assert person(app, "alice")["points"] == 200
    updated = person(app, "alice")
    assert updated["points"] == 800
    assert updated["score_breakdown"] == {"progress": 0, "early_recovery": 800, "scheme_contributions": 0, "note_contributions": 0, "bounty_points": 0}
    assert updated["correct_submission_count"] == updated["schemes_recovered"] == 8
    assert {record["submission_id"] for record in updated["records"]} == {row["submission_id"] for row in rows}
    assert sum(record["score"]["total"] for record in updated["records"]) == 800
    with app.state.db.transaction() as session:
        after = {row.id: (row.received_at, row.payload_mac, row.challenge_id) for row in session.scalars(select(Submission))}
    assert after == before


@pytest.mark.parametrize("scheme_id", EXACT)
def test_exact_profiles_rank_separately_but_have_only_one_family_total(current_system, scheme_id):
    app, _, _ = current_system
    adapter = registry.adapter_for(scheme_id)
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    high = registered_receipt(app, alice, adapter, 3, 0)
    low = registered_receipt(app, bob, adapter, 1, 1)
    registered_receipt(app, alice, adapter, 1, 2)
    bob_high = registered_receipt(app, bob, adapter, 3, 3)
    registered_receipt(app, alice, adapter, 3, 4)
    registered_receipt(app, bob, adapter, 1, 5)
    for name, points, winner, highest in (("alice", 100, high, high),
                                        ("bob", 0, None, bob_high)):
        data = person(app, name)
        assert data["points"] == points
        assert len(data["records"]) == 1
        totals = [row for row in data["records"] if row["score"] is not None]
        assert len(totals) == 1 and totals[0]["submission_id"] == highest["submission_id"]
        assert totals[0]["score"]["total"] == points
        assert totals[0]["score"]["early_recovery_submission_id"] == (winner["submission_id"] if winner else None)
        assert len(data["first_records"]) == (1 if winner else 0)
        if winner:
            assert data["first_records"][0]["submission_id"] == winner["submission_id"]
            assert data["first_records"][0]["place"] == 1
        for entries in (data["first_ten"]["history"], history(app, name)["milestones"]):
            assert [row["submission_id"] for row in entries if row["counts_toward_bonus"]] == ([winner["submission_id"]] if winner else [])
    low_profile = adapter.manifests()[0]["id"]
    board = TestClient(app).get("/api/leaderboard", params={"scheme_id": scheme_id, "profile_id": low_profile}).json()["entries"]
    assert [(row["handle"], row["rank"]) for row in board] == [("bob", 1), ("alice", 2)]


@pytest.mark.parametrize("scheme_id", INHERITED)
def test_only_registered_inheriting_releases_reserve_lower_first_places(current_system, scheme_id):
    app, _, _ = current_system
    adapter = registry.adapter_for(scheme_id)
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    registered_receipt(app, alice, adapter, 3, 0)
    registered_receipt(app, bob, adapter, 1, 1)
    assert person(app, "alice")["points"] == 100
    data = person(app, "bob")
    assert data["points"] == 0
    assert data["first_records"] == []
    assert data["first_ten"]["history"][0]["place"] == 2
    assert not data["first_ten"]["history"][0]["counts_toward_bonus"]


@pytest.mark.parametrize("incremental", [False, True])
def test_exact_progress_and_bonus_are_maxima_not_per_profile_sums(current_system, incremental):
    app, _, _ = current_system
    user = add_user(app, "alice")
    adapter = registry.adapter_for("hqc")
    for tier in (range(1, 6) if incremental else [5]):
        registered_receipt(app, user, adapter, tier, tier)
    registered_receipt(app, user, adapter, 5, 6)
    registered_receipt(app, user, adapter, 1, 7)
    data = person(app, "alice")
    assert data["points"] == 100
    assert data["score_breakdown"] == {"progress": 0, "early_recovery": 100, "scheme_contributions": 0, "note_contributions": 0, "bounty_points": 0}
    assert sum(row["score"]["total"] for row in data["records"] if row["score"]) == 100


def test_published_endpoint_and_unenrolled_releases_never_take_current_bonus(current_system, monkeypatch):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    archived = registered_receipt(app, alice, registry.pq_mceliece, 1, 0)
    current = registered_receipt(app, bob, registry.code_mceliece, 50, 1)
    registered_receipt(app, alice, registry.code_mceliece, 50, 2)
    assert person(app, "alice")["points"] == 0
    assert person(app, "bob")["points"] == 100
    records = {row["submission_id"]: row for row in person(app, "alice")["recent_recoveries"]}
    assert not records[archived["submission_id"]]["scoring_eligible"]
    assert len(person(app, "alice")["records"]) == 1
    monkeypatch.setitem(registry.CURRENT, "classic-mceliece", registry.pq_mceliece.SCHEME_VERSION)
    assert person(app, "alice")["points"] == 0
    assert person(app, "bob")["records"][0]["score"]["early_recovery_submission_id"] == current["submission_id"]


@pytest.mark.parametrize("bad_tier", [True, "1", 1.0, None])
def test_invalid_json_tier_cannot_earn_points_or_occupy_first_place(current_system, bad_tier):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    adapter = registry.adapter_for("hqc")
    bad = registered_receipt(app, alice, adapter, 1, 0)
    with app.state.db.transaction(write=True) as session:
        result = session.get(Submission, bad["submission_id"])
        challenge = session.get(Challenge, result.challenge_id)
        challenge.manifest = {"instance_size": bad_tier}
        flag_modified(challenge, "manifest")
    registered_receipt(app, bob, adapter, 1, 1)
    assert person(app, "alice")["points"] == 0
    assert person(app, "alice")["records"] == []
    assert person(app, "alice")["first_records"] == []
    assert person(app, "bob")["points"] == 100


def test_hqc_unregistered_tier_and_mismatched_manifest_cannot_inflate(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    for tick, tier in enumerate((67, 100)):
        def bind(result, session, tier=tier):
            challenge = session.get(Challenge, result.challenge_id)
            challenge.profile_id = result.profile_id = f"hqc-modeled-research-v3-tier-{tier}"
        receipt(app, user, tier, tick, scheme="hqc", version="modeled-research-v3", mutate=bind)
    assert person(app, "alice")["points"] == 0
    registered_receipt(app, user, registry.code_hqc_v3, 66, 3)
    assert person(app, "alice")["points"] == 100


def test_valid_same_tier_receipt_supplies_personal_record_after_malformed_boolean(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    bad = registered_receipt(app, user, registry.code_hqc_v3, 1, 0)
    with app.state.db.transaction(write=True) as session:
        result = session.get(Submission, bad["submission_id"])
        challenge = session.get(Challenge, result.challenge_id)
        challenge.manifest = {"instance_size": True}
        flag_modified(challenge, "manifest")
    good = registered_receipt(app, user, registry.code_hqc_v3, 1, 1)
    data = person(app, "alice")
    assert data["points"] == 100
    assert [row["submission_id"] for row in data["records"]] == [good["submission_id"]]
    assert [row["submission_id"] for row in data["first_records"]] == [good["submission_id"]]


def test_archived_projection_chooses_latest_release_without_comparing_incompatible_tiers(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    older_high = registered_receipt(app, user, registry.dh_v4, 200, 0)
    latest_release_best = registered_receipt(app, user, registry.dh_v5, 7, 1)
    registered_receipt(app, user, registry.dh_v5, 2, 2)
    data = person(app, "alice")
    assert data["points"] == 0 and data["first_records"] == []
    assert len(data["records"]) == 1
    assert data["records"][0]["submission_id"] == latest_release_best["submission_id"]
    assert data["records"][0]["tier"] == 7 and data["records"][0]["scheme_version"] == "5"
    assert older_high["submission_id"] in {row["submission_id"] for row in data["recent_recoveries"]}
    current = registered_receipt(app, user, registry.adapter_for("teaching-dh-kem"), 1, 3)
    registered_receipt(app, user, registry.dh_v4, 250, 4)
    assert person(app, "alice")["records"][0]["submission_id"] == current["submission_id"]


def test_archived_named_sets_choose_latest_recovered_set_without_invented_rank(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    registered_receipt(app, user, registry.pq_mlkem, 3, 0)
    latest = registered_receipt(app, user, registry.pq_mlkem, 1, 1)
    data = person(app, "alice")
    assert data["points"] == 0 and data["first_records"] == []
    assert len(data["records"]) == 1
    assert data["records"][0]["submission_id"] == latest["submission_id"]
    assert data["records"][0]["tier"] is None


def test_catalogue_and_challenge_presentation_follow_policy_without_editing_frozen_artifacts(current_system):
    app, _, _ = current_system
    adapter = registry.adapter_for("sntrup")
    frozen = deepcopy(adapter.manifests()[0])
    assert frozen["scoring_eligible"] is False  # Original immutable release metadata.
    live = standard_schemes.profile_presentation(frozen)
    assert live["scoring_eligible"] and live["scoring_policy_id"] == scoring.POLICY_ID
    catalogue = TestClient(app).get("/api/schemes").json()["schemes"]
    assert len(catalogue) == 8 and all(row["scoring_eligible"] for row in catalogue)
    for scheme in catalogue:
        assert scheme["scoring_policy_id"] == scoring.POLICY_ID
        assert all(profile.get("scoring_eligible", True) for profile in scheme["profiles"])
        assert all(not archived["scoring_eligible"] for archived in scheme["previous_releases"])
        assert "scoring enrollment remain pending" not in scheme.get("review_note", "")
    assert adapter.manifests()[0] == frozen
    assert standard_schemes.profile_presentation(registry.sntrup.manifests()[0])["scoring_eligible"] is False


def test_natural_tier_above_fifty_gets_full_first_bonus_without_cumulative_steps(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ('alice', 'bob'))
    adapter = registry.adapter_for('frodokem')
    assert len(adapter.manifests()) == 80
    lower = registered_receipt(app, alice, adapter, 50, 0)
    highest = registered_receipt(app, alice, adapter, 80, 1)
    registered_receipt(app, alice, adapter, 80, 2)
    registered_receipt(app, bob, adapter, 80, 3)
    data = person(app, 'alice')
    assert data['score_breakdown'] == {'progress': 0, 'early_recovery': 100, 'scheme_contributions': 0, 'note_contributions': 0, 'bounty_points': 0}
    assert data['points'] == 100
    assert data['records'][0]['score']['early_recovery_submission_id'] == lower['submission_id']
    assert data['first_records'][0]['tier'] == 80
    assert lower['submission_id'] != highest['submission_id']
    assert person(app, 'bob')['points'] == 0


def test_hqc_replacement_keeps_archived_receipts_without_transfer_or_double_award(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    archived = registered_receipt(app, alice, registry.code_hqc_v2, 27, 0)
    with app.state.db.transaction() as session:
        row = session.get(Submission, archived["submission_id"])
        before = (row.received_at, row.payload_mac, row.challenge_id, row.scheme_version)
    old_profile = person(app, "alice")
    assert old_profile["points"] == 0
    assert old_profile["first_records"] == []
    assert old_profile["records"][0]["submission_id"] == archived["submission_id"]
    assert not old_profile["records"][0]["scoring_eligible"]
    assert all(not row["counts_toward_bonus"] for row in history(app, "alice")["milestones"])
    current = registered_receipt(app, bob, registry.code_hqc_v3, 66, 1)
    registered_receipt(app, alice, registry.code_hqc_v3, 66, 2)
    alice_now, bob_now = person(app, "alice"), person(app, "bob")
    assert alice_now["points"] == 0 and bob_now["points"] == 100
    assert len(alice_now["records"]) == len(bob_now["records"]) == 1
    assert alice_now["score_breakdown"]["early_recovery"] == 0
    assert bob_now["records"][0]["score"]["early_recovery_submission_id"] == current["submission_id"]
    assert archived["submission_id"] in {row["submission_id"] for row in alice_now["recent_recoveries"]}
    with app.state.db.transaction() as session:
        row = session.get(Submission, archived["submission_id"])
        assert (row.received_at, row.payload_mac, row.challenge_id, row.scheme_version) == before
