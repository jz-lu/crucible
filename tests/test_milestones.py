"""Legacy milestone receipts stay factual under flat frontier recognition."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from crucible import registry
from crucible.db import Challenge, Submission
from test_calibrated_api import current_system
from test_people import add_user, people, person, receipt, registered_receipt


def history(app, handle="alice", **params):
    response = TestClient(app).get(f"/api/people/{handle}/milestones", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def pairs(rows):
    return [(row["tier"], row["place"]) for row in rows]


def test_exact_hqc_profiles_keep_independent_places_and_lower_tier_highlights(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    high = registered_receipt(app, alice, registry.code_hqc_v3, 66, 0)
    low = registered_receipt(app, bob, registry.code_hqc_v3, 1, 1)
    alice_low = registered_receipt(app, alice, registry.code_hqc_v3, 1, 2)
    bob_high = registered_receipt(app, bob, registry.code_hqc_v3, 66, 3)
    registered_receipt(app, alice, registry.code_hqc_v3, 1, 4)
    registered_receipt(app, bob, registry.code_hqc_v3, 66, 5)
    board = TestClient(app).get("/api/leaderboard", params={"scheme_id": "hqc",
        "profile_id": "hqc-modeled-research-v3-tier-1"}).json()["entries"]
    assert [(row["handle"], row["rank"]) for row in board] == [("bob", 1), ("alice", 2)]
    for name, expected, identifiers in (
        ("alice", [(1, 2), (66, 1)], {high["submission_id"], alice_low["submission_id"]}),
        ("bob", [(66, 2), (1, 1)], {low["submission_id"], bob_high["submission_id"]}),
    ):
        data = person(app, name)
        milestones = data["first_ten"]
        assert pairs(milestones["history"]) == expected
        assert set(pairs(milestones["highlights"])) == set(expected)
        assert {row["submission_id"] for row in milestones["history"]} == identifiers
        assert history(app, name)["milestones"] == milestones["history"]
        assert data["points"] == (100 if name == "alice" else 0)
        winner = high["submission_id"] if name == "alice" else None
        for row in milestones["history"]:
            assert row["profile_kind"] == "research-tier"
            assert row["profile_label"] == f'Tier {row["tier"]}'
            assert row["record_inheritance"] is False
            assert row["counts_toward_bonus"] == (row["submission_id"] == winner)


@pytest.mark.parametrize("ladder", [False, True])
def test_direct_or_incremental_progress_has_one_highlight_and_no_repeat_badges(current_system, ladder):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    achieved = [receipt(app, alice, tier, tier) for tier in (range(1, 21) if ladder else [20])]
    receipt(app, alice, 20, 21)
    receipt(app, alice, 1, 22)
    data = person(app, "alice")
    assert data["points"] == 100
    assert data["score_breakdown"] == {"progress": 0, "early_recovery": 100, "scheme_contributions": 0, "note_contributions": 0, "bounty_points": 0}
    assert pairs(data["first_ten"]["highlights"]) == [(20, 1)]
    assert len(data["first_ten"]["history"]) == len(achieved)
    assert data["first_ten"]["history"][0]["submission_id"] == achieved[-1]["submission_id"]
    assert sum(row["counts_toward_bonus"] for row in data["first_ten"]["history"]) == 1
    assert history(app)["milestones"] == data["first_ten"]["history"]


@pytest.mark.parametrize("higher", [20, 10])
def test_first_eight_and_third_higher_are_both_highlights_but_only_first_earns_bonus(current_system, higher):
    app, _, _ = current_system
    alice, bob, carol = (add_user(app, name) for name in ("alice", "bob", "carol"))
    first = receipt(app, alice, 8, 0)
    receipt(app, bob, higher, 1)
    receipt(app, carol, higher, 2)
    third = receipt(app, alice, higher, 3)
    before = people(app)
    data = person(app, "alice")
    assert set(pairs(data["first_ten"]["highlights"])) == {(8, 1), (higher, 3)}
    assert data["score_breakdown"]["early_recovery"] == 100
    flagged = [row for row in data["first_ten"]["highlights"] if row["counts_toward_bonus"]]
    assert [row["submission_id"] for row in flagged] == [first["submission_id"]]
    assert data["points"] == 100
    assert people(app) == before
    all_milestones = history(app)["milestones"]
    assert {first["submission_id"], third["submission_id"]} == {row["submission_id"] for row in all_milestones}
    assert [row["submission_id"] for row in all_milestones if row["counts_toward_bonus"]] == [first["submission_id"]]


def test_distinct_accounts_higher_credit_first_ten_and_receipt_ties(current_system):
    app, _, _ = current_system
    receipts = []
    for index in range(11):
        user = add_user(app, f"solver_{index:02}")
        receipts.append(receipt(app, user, 20 if index < 9 else 8, 0,
                                submission_id=f"{index:08d}-0000-4000-8000-000000000000"))
        receipt(app, user, 20 if index < 9 else 8, 1)
        receipt(app, user, 1, 2)
    tenth = history(app, "solver_09")["milestones"]
    assert pairs(tenth) == [(8, 10)]
    assert tenth[0]["submission_id"] == receipts[9]["submission_id"]
    assert not tenth[0]["counts_toward_bonus"]
    assert history(app, "solver_10")["milestones"] == []
    assert person(app, "solver_10")["first_ten"]["highlights"] == []
    assert pairs(history(app, "solver_00")["milestones"]) == [(20, 1)]


def test_archives_and_schemes_never_share_places_or_dominance(current_system, monkeypatch):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    receipt(app, bob, 50, 0, version="5")
    old = receipt(app, alice, 8, 1, version="5")
    current = receipt(app, alice, 8, 2)
    rsa = receipt(app, alice, 8, 3, scheme="rsa-kem")
    data = person(app, "alice")
    rows = {row["submission_id"]: row for row in data["first_ten"]["highlights"]}
    assert len(rows) == 3
    assert rows[old["submission_id"]]["place"] == 2
    assert not rows[old["submission_id"]]["is_current"]
    assert not rows[old["submission_id"]]["scoring_eligible"]
    assert not rows[old["submission_id"]]["counts_toward_bonus"]
    assert rows[current["submission_id"]]["place"] == rows[rsa["submission_id"]]["place"] == 1
    assert rows[current["submission_id"]]["counts_toward_bonus"]
    monkeypatch.setitem(registry.CURRENT, "teaching-dh-kem", "5")
    moved = {row["submission_id"]: row for row in history(app)["milestones"]}
    assert not moved[current["submission_id"]]["is_current"]
    assert moved[current["submission_id"]]["counts_toward_bonus"]
    assert moved[old["submission_id"]]["is_current"] and not moved[old["submission_id"]]["counts_toward_bonus"]


def test_profile_and_paged_award_flags_ignore_display_release_selection(current_system, monkeypatch):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    receipt(app, alice, 50, 0, version="5")
    earned = receipt(app, alice, 2, 1)
    highest = receipt(app, alice, 4, 2)
    for display_version in ("7", "5", "7"):
        monkeypatch.setitem(registry.CURRENT, "teaching-dh-kem", display_version)
        data = person(app, "alice")
        page = history(app)["milestones"]
        assert data["points"] == 100
        assert data["records"][0]["scheme_version"] == display_version
        assert data["first_records"][0]["submission_id"] == highest["submission_id"]
        for rows in (data["first_ten"]["history"], page):
            assert [row["submission_id"] for row in rows if row["counts_toward_bonus"]] == [earned["submission_id"]]
        assert data["first_ten"]["history"] == page


def test_invalid_profiles_sizes_and_failed_or_inconsistent_receipts_cannot_suppress_or_occupy_places(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    forged = receipt(app, alice, 50, 0)
    receipt(app, alice, 51, 1)  # Not a registered profile.
    wrong_type = receipt(app, bob, 40, 2)
    missing = receipt(app, bob, 39, 3)
    mismatch = receipt(app, bob, 38, 4)
    receipt(app, bob, 37, 5, correct=False)
    late = receipt(app, bob, 36, 6)
    with app.state.db.transaction(write=True) as session:
        for identifier, manifest in ((forged["submission_id"], {"instance_size": 999}),
                                     (wrong_type["submission_id"], {"instance_size": "40"}),
                                     (missing["submission_id"], {}),
                                     (mismatch["submission_id"], {"instance_size": 37})):
            submission = session.get(Submission, identifier)
            session.get(Challenge, submission.challenge_id).manifest = manifest
        record = session.get(Submission, late["submission_id"])
        record.received_at = session.get(Challenge, record.challenge_id).ends_at
    valid = receipt(app, alice, 8, 7)
    assert pairs(history(app)["milestones"]) == [(8, 1)]
    assert history(app)["milestones"][0]["submission_id"] == valid["submission_id"]
    assert history(app, "bob")["milestones"] == []


def test_boolean_tier_one_cannot_occupy_a_place_and_null_fallback_preserves_legacy_integers(current_system):
    app, _, _ = current_system
    alice, bob, carol = (add_user(app, name) for name in ("alice", "bob", "carol"))
    boolean = receipt(app, bob, 1, 0)
    null_fallback = receipt(app, alice, 1, 1)
    legacy = receipt(app, carol, 20, 2, version="4")
    with app.state.db.transaction(write=True) as session:
        bad = session.get(Submission, boolean["submission_id"])
        boolean_challenge = session.get(Challenge, bad.challenge_id)
        boolean_challenge.manifest = {"instance_size": True}
        # Python considers True == 1; force the JSON representation change so
        # this regression actually exercises a stored JSON boolean.
        flag_modified(boolean_challenge, "manifest")
        null = session.get(Submission, null_fallback["submission_id"])
        session.get(Challenge, null.challenge_id).manifest = {"instance_size": None, "parameter_bits": 1}
        old = session.get(Submission, legacy["submission_id"])
        old.profile_id = "dh-20"
        challenge = session.get(Challenge, old.challenge_id)
        challenge.profile_id = "dh-20"
        challenge.manifest = {"parameter_bits": 20}
    assert history(app, "bob")["milestones"] == []
    assert pairs(history(app)["milestones"]) == [(1, 1)]
    archived = history(app, "carol")["milestones"]
    assert pairs(archived) == [(20, 1)]
    assert archived[0]["scheme_version"] == "4" and archived[0]["profile_id"] == "dh-20"


def test_pagination_follows_complete_ranking_and_never_changes_highlights(current_system):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    for tier in range(1, 26):
        receipt(app, alice, tier, tier)
    profile = person(app, "alice")["first_ten"]
    assert pairs(profile["highlights"]) == [(25, 1)]
    assert [row["tier"] for row in profile["history"]] == list(range(25, 5, -1))
    assert profile["pagination"] == {"limit": 20, "offset": 0, "has_more": True}
    second = history(app, offset=20)
    assert [row["tier"] for row in second["milestones"]] == [5, 4, 3, 2, 1]
    assert second["pagination"] == {"limit": 20, "offset": 20, "has_more": False}
    assert history(app, offset=30)["milestones"] == []
    first = history(app, limit=2)
    assert pairs(first["milestones"]) == [(25, 1), (24, 1)]
    assert first["pagination"]["has_more"]


def test_empty_missing_invalid_handles_and_page_bounds(current_system):
    app, _, _ = current_system
    add_user(app, "alice")
    assert person(app, "alice")["first_ten"] == {
        "highlights": [], "history": [], "pagination": {"limit": 20, "offset": 0, "has_more": False}}
    assert history(app, "ALICE")["milestones"] == []
    client = TestClient(app)
    for handle in ("missing", "a%20b", "%C3%A9clair", "ab"):
        assert client.get(f"/api/people/{handle}/milestones").status_code == 404
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "invalid"}):
        assert client.get("/api/people/alice/milestones", params=params).status_code == 422


def test_only_public_receipt_metadata_is_returned_and_queries_do_not_write(current_system):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    correct = receipt(app, alice, 8)
    wrong = receipt(app, alice, 9, 1, correct=False)
    with app.state.db.transaction() as session:
        count_before = session.scalar(select(func.count()).select_from(Submission))
    data = history(app)["milestones"]
    assert len(data) == 1 and data[0]["submission_id"] == correct["submission_id"]
    assert set(data[0]) == {"scheme_id", "scheme_name", "scheme_version", "profile_id", "tier", "submission_id",
                           "received_at", "is_current", "scoring_eligible", "place", "counts_toward_bonus"}
    encoded = json.dumps(data)
    for private in (alice, wrong["submission_id"], "private-test-password-hash", "private-pseudocode-not-in-person",
                    "private-target-mac", "private-payload-mac", "private-compute-not-in-person"):
        assert private not in encoded
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Submission)) == count_before
