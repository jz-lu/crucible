"""Participant pages preserve evidence; scores reward progress without repeats."""
from collections import defaultdict
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from crucible import registry
from crucible.db import Challenge, Method, Submission, User
from test_calibrated_api import current_system


def add_user(app, handle):
    user_id = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        session.add(User(id=user_id, handle=handle, password_hash="private-test-password-hash",
                         created_at=3_600_000.0))
    return user_id


def receipt(app, user_id, tier, tick=0, *, scheme="teaching-dh-kem", version=None,
            correct=True, submission_id=None, mutate=None):
    version = version or ("6" if scheme == "teaching-dh-kem" else "3")
    profile = f'{"dh" if scheme == "teaching-dh-kem" else "rsa"}-v{version}-tier-{tier}'
    received = 3_600_600.0 + tick * 3600
    round_id = int(received) // 3600
    challenge_id, method_id = str(uuid4()), str(uuid4())
    identifier = submission_id or str(uuid4())
    with app.state.db.transaction(write=True) as session:
        session.add(Method(id=method_id, user_id=user_id, name="Private fixture method",
                           pseudocode="private-pseudocode-not-in-person", created_at=received - 10))
        session.add(Challenge(id=challenge_id, user_id=user_id, scheme_id=scheme,
                              scheme_version=version, profile_id=profile, round_id=round_id,
                              issued_at=received-10, starts_at=round_id*3600, ends_at=(round_id+1)*3600,
                              public_data={}, manifest={"instance_size": tier},
                              target_mac="private-target-mac", correctness_passed=True))
        session.flush()
        result = Submission(id=identifier, challenge_id=challenge_id, user_id=user_id,
                            method_id=method_id, profile_id=profile, scheme_version=version,
                            round_id=round_id, idempotency_key=str(uuid4()), payload_mac="private-payload-mac",
                            correct=correct, received_at=received, elapsed_seconds=10,
                            compute={"hardware": "private-compute-not-in-person"})
        if mutate:
            mutate(result, session)
        session.add(result)
    return {"user_id": user_id, "scheme": scheme, "tier": tier, "received_at": received,
            "submission_id": identifier, "version": version, "correct": correct}


def people(app, **params):
    response = TestClient(app).get("/api/people", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def person(app, handle):
    response = TestClient(app).get(f"/api/people/{handle}")
    assert response.status_code == 200, response.text
    return response.json()["person"]


def test_zero_result_accounts_have_shared_zero_rank_and_bounded_pagination(current_system):
    app, _, _ = current_system
    for handle in ("charlie", "alice", "bob"):
        add_user(app, handle)
    first = people(app, limit=2)
    assert [row["handle"] for row in first["people"]] == ["alice", "bob"]
    assert first["pagination"] == {"limit": 2, "offset": 0, "total": 3, "has_more": True}
    for row in first["people"]:
        assert row["points"] == 0 and row["rank"] == 1
        assert row["score_breakdown"] == {"progress": 0, "early_recovery": 0, "scheme_contributions": 0}
        assert row["correct_submission_count"] == row["schemes_recovered"] == 0
        assert row["accepted_proposal_count"] == 0
        assert row["records"] == [] and row["score_status"] == "active"
    last = people(app, limit=2, offset=2)
    assert [row["handle"] for row in last["people"]] == ["charlie"]
    assert not last["pagination"]["has_more"]
    assert people(app, offset=99)["people"] == []
    assert first["scoring"]["policy_id"] == "crucible-points-v2"


@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}])
def test_invalid_page_bounds_are_rejected(current_system, query):
    app, _, _ = current_system
    assert TestClient(app).get("/api/people", params=query).status_code == 422


def test_profiles_normalize_handles_and_do_not_publish_private_or_incorrect_evidence(current_system):
    app, _, _ = current_system
    user = add_user(app, "alice")
    good = receipt(app, user, 2)
    bad = receipt(app, user, 9, 1, correct=False)
    data = person(app, "ALICE")
    assert data["handle"] == "alice" and data["joined_at"] == 3_600_000.0
    assert data["correct_submission_count"] == 1 and data["schemes_recovered"] == 1
    assert data["records"][0]["submission_id"] == good["submission_id"]
    assert data["recent_recoveries"][0]["submission_id"] == good["submission_id"]
    assert not data["recent_recoveries_has_more"]
    encoded = json.dumps(data) + json.dumps(people(app))
    for private in ("private-test-password-hash", "private-pseudocode-not-in-person",
                    "private-target-mac", "private-payload-mac", "private-compute-not-in-person",
                    bad["submission_id"], user):
        assert private not in encoded
    assert TestClient(app).get("/api/people/missing").status_code == 404
    assert TestClient(app).get("/api/people/a%20b").status_code == 404
    assert TestClient(app).get("/api/people/%C3%A9clair").status_code == 404


def test_higher_recovery_reserves_lower_places_and_repeats_do_not_add_points(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    high = receipt(app, alice, 4, 0)
    receipt(app, bob, 2, 1)
    receipt(app, alice, 1, 2)
    receipt(app, alice, 4, 3)
    alice_data, bob_data = person(app, "alice"), person(app, "bob")
    assert alice_data["points"] == 40 + 160
    assert alice_data["correct_submission_count"] == 3
    assert alice_data["records"][0]["submission_id"] == high["submission_id"]
    assert alice_data["records"][0]["score"]["early_recovery_rank"] == 1
    assert bob_data["points"] == 20
    assert bob_data["records"][0]["score"]["early_recovery"] == 0
    assert bob_data["records"][0]["score"]["early_recovery_rank"] is None
    assert [row["handle"] for row in people(app)["people"]] == ["alice", "bob"]


def test_only_single_best_bonus_survives_when_personal_best_later_increases(current_system):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    first = receipt(app, alice, 10, 0)
    for index in range(9):
        other = add_user(app, f"solver_{index}")
        receipt(app, other, 11, index + 1)
    receipt(app, alice, 11, 20)
    data = person(app, "alice")
    assert data["points"] == 110 + 400
    assert data["score_breakdown"] == {"progress": 110, "early_recovery": 400, "scheme_contributions": 0}
    record = data["records"][0]
    assert record["tier"] == 11 and record["score"]["early_recovery_tier"] == 10
    assert record["score"]["early_recovery_submission_id"] == first["submission_id"]


def test_only_first_earns_bonus_and_receipt_id_breaks_simultaneous_ties(current_system):
    app, _, _ = current_system
    for index in range(11):
        user = add_user(app, f"solver_{index:02}")
        receipt(app, user, 3, submission_id=f"{index:08}-0000-4000-8000-000000000000")
        receipt(app, user, 3, 1)
    rows = people(app)["people"]
    assert [row["handle"] for row in rows] == [f"solver_{index:02}" for index in range(11)]
    assert [row["points"] for row in rows] == [150] + [30] * 10
    assert [row["rank"] for row in rows] == [1] + [2] * 10
    assert rows[0]["records"][0]["score"]["early_recovery_rank"] == 1
    for row in rows[1:]:
        score = row["records"][0]["score"]
        assert score["early_recovery"] == 0
        assert score["early_recovery_rank"] is score["early_recovery_tier"] is score["early_recovery_submission_id"] is None


def test_first_nineteen_then_second_twenty_retains_only_first_nineteen_bonus(current_system):
    app, _, _ = current_system
    alice, bob, carol = (add_user(app, handle) for handle in ("alice", "bob", "carol"))
    first = receipt(app, alice, 19, 0)
    receipt(app, bob, 20, 1)
    receipt(app, alice, 20, 2)
    receipt(app, carol, 20, 3)
    data = person(app, "alice")
    assert data["points"] == 960
    assert data["score_breakdown"] == {"progress": 200, "early_recovery": 760, "scheme_contributions": 0}
    score = data["records"][0]["score"]
    assert score["early_recovery_tier"] == 19 and score["early_recovery_rank"] == 1
    assert score["early_recovery_submission_id"] == first["submission_id"]
    assert person(app, "bob")["points"] == 1000
    assert person(app, "carol")["points"] == 200


def test_second_at_twenty_without_any_first_receipt_gets_only_progress(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    receipt(app, alice, 20, 0)
    receipt(app, bob, 20, 1)
    receipt(app, bob, 19, 2)  # Earlier higher solve already reserved this place.
    receipt(app, bob, 20, 3)
    data = person(app, "bob")
    assert data["points"] == 200
    assert data["score_breakdown"] == {"progress": 200, "early_recovery": 0, "scheme_contributions": 0}


def test_archives_keep_exact_tiers_but_never_gain_current_score_or_double_family_credit(current_system, monkeypatch):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    old = receipt(app, alice, 50, 0, version="5")
    new = receipt(app, alice, 2, 1)
    rsa = receipt(app, alice, 2, 2, scheme="rsa-kem")
    data = person(app, "alice")
    assert data["points"] == 200 and data["schemes_recovered"] == 2
    records = {row["submission_id"]: row for row in data["records"]}
    assert records[old["submission_id"]]["tier"] == 50
    assert records[old["submission_id"]]["scheme_version"] == "5"
    assert records[old["submission_id"]]["score"] is None
    assert not records[old["submission_id"]]["scoring_eligible"]
    assert records[new["submission_id"]]["score"]["total"] == 100
    assert records[rsa["submission_id"]]["score"]["total"] == 100
    # Displaying another current release cannot silently change enrollment.
    monkeypatch.setitem(registry.CURRENT, "teaching-dh-kem", "5")
    assert person(app, "alice")["points"] == 200


def test_inconsistent_receipts_and_unregistered_profiles_do_not_earn_points(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    receipt(app, alice, 5, 0, mutate=lambda result, _: setattr(result, "user_id", bob))
    receipt(app, alice, 6, 1, mutate=lambda result, _: setattr(result, "profile_id", "dh-v6-tier-50"))
    receipt(app, alice, 7, 2, mutate=lambda result, _: setattr(result, "scheme_version", "5"))
    receipt(app, alice, 8, 3, mutate=lambda result, _: setattr(result, "round_id", 1))
    receipt(app, alice, 9, 4, mutate=lambda result, _: setattr(result, "received_at", 0))
    receipt(app, alice, 51, 5)  # An unknown profile cannot enroll merely through a numeric manifest.
    assert person(app, "alice")["points"] == 0
    assert person(app, "bob")["points"] == 0
    assert person(app, "alice")["correct_submission_count"] == 1
    assert person(app, "bob")["correct_submission_count"] == 0


def test_recent_history_is_bounded_and_personal_best_is_not_latest_receipt(current_system):
    app, _, _ = current_system
    alice = add_user(app, "alice")
    highest = receipt(app, alice, 20, 0)
    later = [receipt(app, alice, 1, index) for index in range(1, 24)]
    data = person(app, "alice")
    assert data["correct_submission_count"] == 24
    assert data["records"][0]["submission_id"] == highest["submission_id"]
    assert len(data["recent_recoveries"]) == 20 and data["recent_recoveries_has_more"]
    assert data["recent_recoveries"][0]["submission_id"] == later[-1]["submission_id"]
    assert data["points"] == 200 + 800


def test_page_query_count_is_constant_and_matches_independent_score_calculation(current_system):
    app, _, _ = current_system
    receipts = []
    users = {}
    for index in range(12):
        handle = f"solver_{index:02}"
        user = users[handle] = add_user(app, handle)
        for attempt in range(3):
            receipts.append(receipt(app, user, (index * 7 + attempt * 3) % 15 + 1,
                                    index + attempt * 13))
    expected = {}
    places = defaultdict(list)
    for tier in range(1, 51):
        for user in users.values():
            qualifying = [row for row in receipts if row["user_id"] == user and row["tier"] >= tier]
            if qualifying:
                row = min(qualifying, key=lambda item: (item["received_at"], item["submission_id"]))
                places[tier].append(row)
        places[tier].sort(key=lambda item: (item["received_at"], item["submission_id"]))
    for handle, user in users.items():
        highest = max(row["tier"] for row in receipts if row["user_id"] == user)
        bonuses = [40*tier for tier, rows in places.items()
                   if rows and rows[0]["user_id"] == user]
        expected[handle] = 10*highest + max(bonuses, default=0)
    statements = []
    def capture(_, __, statement, ___, ____, _____):
        if statement.lstrip().lower().startswith(("select", "with")):
            statements.append(statement)
    event.listen(app.state.db.engine, "before_cursor_execute", capture)
    try:
        data = people(app)
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", capture)
    assert {row["handle"]: row["points"] for row in data["people"]} == expected
    assert len(statements) == 5  # total, ranked people, counts, family scores, exact-release records
