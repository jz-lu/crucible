import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from crucible import dh_v4 as kem
from crucible import registry, rsa
from crucible.db import Challenge, Submission
from scripts.solve_rsa import recover
from test_api import Actor, system


def rsa_answer(actor, challenge):
    method = actor.method("Trial factoring")
    return {"candidate": recover(challenge["public"]), "method_id": method["id"],
            "compute": {"hardware": "Local test CPU", "workers": 1, "wall_seconds": 0.1,
                        "precomputation": "None", "peak_memory": "Not measured", "notes": "Fixture"},
            "idempotency_key": str(uuid4())}


def test_catalogue_has_two_real_adapters_with_common_protocol_contract(system):
    app, _, _ = system
    entries = TestClient(app).get("/api/schemes").json()["schemes"]
    entries = [item for item in entries if item["id"] in (kem.SCHEME_ID, rsa.SCHEME_ID)]
    assert [item["id"] for item in entries] == [kem.SCHEME_ID, rsa.SCHEME_ID]
    assert len({profile["id"] for item in entries for profile in item["profiles"]}) == 390
    for item in entries:
        assert [profile["instance_size"] for profile in item["profiles"]] == list(range(20, 301 if item["id"] == kem.SCHEME_ID else 129))
        assert item["largest_recovered_profile_id"] is None
        assert item["first_recovery"] is None
        assert item["recovered_profile_ids"] == []
        assert all(release["recovered_profile_ids"] == [] for release in item["previous_releases"])
        assert item["next_profile_id"] == item["profiles"][0]["id"]
        assert set(item["protocol"]) == {"construction", "assumption", "public_input", "shared_secret",
                                          "correctness", "source_url", "example_url", "construction_math",
                                          "public_input_math", "correctness_argument_math"}
        assert item["protocol"]["construction"] == registry.DETAILS[item["id"]]["construction"]
        assert item["protocol"]["public_input"] == registry.DETAILS[item["id"]]["public_input"]
        assert item["protocol"]["assumption"] == item["profiles"][0]["assumption_context"]
        source = TestClient(app).get(item["protocol"]["source_url"])
        assert source.status_code == 200
        assert hashlib.sha256(source.content).hexdigest() == item["profiles"][0]["implementation"]["source_sha256"]
        example = TestClient(app).get(item["protocol"]["example_url"])
        assert example.status_code == 200
        assert item["id"].encode() in example.content


def test_rsa_full_download_recovery_and_owned_round(system):
    app, clock, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    started = alice.post("/api/challenges", {"scheme_id": rsa.SCHEME_ID, "instance_size": 23})
    assert started.status_code == 200, started.text
    challenge = started.json()["challenge"]
    assert challenge["scheme_id"] == rsa.SCHEME_ID and challenge["scheme_version"] == "1"
    assert challenge["public"]["instance_size"] == 23
    assert int(challenge["public"]["parameters"]["N"], 16).bit_length() == 23
    query = "/api/challenges?profile_id=rsa-23&scheme_id=rsa-kem&scheme_version=1"
    assert alice.client.get(query).json()["challenge"] == challenge
    assert bob.client.get(query).json()["challenge"] is None
    assert bob.client.get(challenge["download_url"]).status_code == 404
    again = alice.post("/api/challenges", {"profile_id": "rsa-23", "scheme_id": rsa.SCHEME_ID, "scheme_version": "1"})
    assert again.json()["challenge"] == challenge
    downloaded = alice.client.get(challenge["download_url"]).json()
    answer = rsa_answer(alice, challenge)
    assert recover(downloaded) == answer["candidate"]
    clock[0] += 3
    receipt = alice.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert receipt.status_code == 200 and receipt.json()["submission"]["status"] == "correct"
    assert answer["candidate"] not in receipt.text
    board = alice.client.get("/api/leaderboard?profile_id=rsa-23&scheme_id=rsa-kem&scheme_version=1").json()
    assert board["scheme_id"] == rsa.SCHEME_ID
    assert [entry["handle"] for entry in board["entries"]] == ["alice"]
    assert board["entries"][0]["elapsed_seconds"] == 3
    assert alice.client.get("/api/leaderboard?profile_id=dh-23").json()["entries"] == []
    catalogue = alice.client.get("/api/schemes").json()["schemes"]
    assert catalogue[0]["largest_recovered_profile_id"] is None
    assert catalogue[1]["largest_recovered_profile_id"] == "rsa-23"
    assert catalogue[1]["next_profile_id"] == "rsa-24"
    assert catalogue[1]["recovered_profile_ids"] == ["rsa-23"]
    assert catalogue[0]["recovered_profile_ids"] == []
    assert catalogue[1]["first_recovery"] == {"handle": "alice", "received_at": clock[0], "profile_id": "rsa-23", "scheme_version": "1", "submission_id": receipt.json()["submission"]["id"]}
    assert catalogue[0]["first_recovery"] is None
    assert alice.client.get("/api/me/submissions").json()["submissions"][0]["scheme_id"] == rsa.SCHEME_ID


@pytest.mark.parametrize("path", [
    "/api/challenges?profile_id=dh-20&scheme_id=rsa-kem&scheme_version=1",
    "/api/leaderboard?profile_id=rsa-20&scheme_id=teaching-dh-kem&scheme_version=1",
    "/api/leaderboard?profile_id=rsa-20&scheme_id=rsa-kem&scheme_version=2",
    "/api/protocol/source?scheme_id=unknown&version=1",
    "/api/protocol/example?scheme_id=rsa-kem&version=999",
])
def test_crossed_scheme_or_version_is_rejected(system, path):
    app, _, _ = system
    assert TestClient(app).get(path).status_code == 404


@pytest.mark.parametrize("body,status", [
    ({"profile_id": "dh-20", "scheme_id": "rsa-kem"}, 404),
    ({"profile_id": "rsa-20", "scheme_id": "rsa-kem", "scheme_version": "2"}, 404),
    ({"scheme_id": "rsa-kem", "instance_size": 19}, 404),
    ({"scheme_id": "rsa-kem", "instance_size": 20, "scheme_version": "1"}, 200),
    ({"profile_id": "dh-20", "scheme_id": "teaching-dh-kem", "scheme_version": "1"}, 409),
    ({"profile_id": "rsa-20", "scheme_version": "1"}, 422),
    ({"scheme_id": "rsa-kem", "instance_size": 20, "scheme_version": None}, 422),
])
def test_new_challenge_selector_is_exact_and_current(system, body, status):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    assert actor.post("/api/challenges", body).status_code == status


def test_same_version_and_corrupted_submission_profile_do_not_mix_schemes(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    # DH v1 and RSA v1 deliberately share a version string. Scheme identity
    # must still constrain evidence even if denormalized profile fields disagree.
    challenge = actor.challenge("rsa-20")
    assert actor.post(f'/api/challenges/{challenge["id"]}/submit', rsa_answer(actor, challenge)).status_code == 200
    with app.state.db.transaction(write=True) as session:
        record = session.scalar(select(Submission).where(Submission.challenge_id == challenge["id"]))
        record.profile_id = "dh-20"
    assert actor.client.get("/api/leaderboard?profile_id=dh-20&scheme_version=1").json()["entries"] == []
    with app.state.db.transaction(write=True) as session:
        record = session.scalar(select(Submission).where(Submission.challenge_id == challenge["id"]))
        record.scheme_version = "2"
    assert actor.client.get("/api/leaderboard?profile_id=dh-20&scheme_version=2").json()["entries"] == []
    dh = actor.client.get("/api/schemes").json()["schemes"][0]
    assert dh["largest_recovered_profile_id"] is None
    assert dh["first_recovery"] is None
    assert dh["recovered_profile_ids"] == []


def test_registry_rejects_cross_scheme_profile_aliases(monkeypatch):
    original = rsa.manifests
    def aliases():
        result = original()
        result[0]["id"] = "dh-20"
        return result
    monkeypatch.setattr(rsa, "manifests", aliases)
    with pytest.raises(ValueError, match="globally namespaced"):
        registry.validate_registry()


def test_rsa_adapter_pin_and_fault_are_isolated_from_dh(system, monkeypatch):
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    actor.challenge("rsa-20")
    monkeypatch.setattr(rsa, "IMPLEMENTATION_SHA256", "0" * 64)
    clock[0] += 3600
    assert actor.post("/api/challenges", {"profile_id": "rsa-20"}).status_code == 409
    assert actor.post("/api/challenges", {"profile_id": "dh-20"}).status_code == 200


def test_first_recovery_tracks_first_solver_of_largest_size(system, monkeypatch):
    app, clock, _ = system
    alice, bob, carol = (Actor(app) for _ in range(3))
    for actor, handle in ((alice, "alice"), (bob, "bob"), (carol, "carol")):
        actor.register(handle)

    def solve(actor, profile, correct=True):
        challenge = actor.challenge(profile)
        answer = rsa_answer(actor, challenge) if profile.startswith("rsa-") else actor.answer(challenge)
        if not correct:
            answer["candidate"] = "00" * 32
        response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
        assert response.status_code == 200, response.text
        return response.json()["submission"]

    def catalogue():
        return {item["id"]: item for item in alice.client.get("/api/schemes").json()["schemes"]}

    # An earlier, higher recovery of an archived release is not current progress.
    with monkeypatch.context() as historical:
        historical.setitem(registry.CURRENT, kem.SCHEME_ID, "1")
        solve(carol, "dh-36")
    assert catalogue()[kem.SCHEME_ID]["first_recovery"] is None

    clock[0] += 1
    low = solve(alice, "dh-20")
    first_low = {"handle": "alice", "received_at": clock[0], "profile_id": "dh-20", "scheme_version": "4", "submission_id": low["id"]}
    assert catalogue()[kem.SCHEME_ID]["first_recovery"] == first_low

    clock[0] += 1
    high = solve(bob, "dh-24")
    first_high = {"handle": "bob", "received_at": clock[0], "profile_id": "dh-24", "scheme_version": "4", "submission_id": high["id"]}
    assert catalogue()[kem.SCHEME_ID]["first_recovery"] == first_high

    clock[0] += 1
    solve(alice, "dh-24")
    solve(carol, "dh-36", correct=False)
    rsa_receipt = solve(carol, "rsa-25")
    progress = catalogue()
    assert progress[kem.SCHEME_ID]["largest_recovered_profile_id"] == "dh-24"
    assert progress[kem.SCHEME_ID]["first_recovery"] == first_high
    assert progress[kem.SCHEME_ID]["recovered_profile_ids"] == ["dh-20", "dh-24"]
    assert progress[rsa.SCHEME_ID]["recovered_profile_ids"] == ["rsa-25"]
    assert progress[rsa.SCHEME_ID]["first_recovery"] == {
        "handle": "carol", "received_at": clock[0], "profile_id": "rsa-25", "scheme_version": "1", "submission_id": rsa_receipt["id"]}


def test_first_recovery_breaks_receipt_ties_like_profile_leaderboard(system):
    app, clock, _ = system
    actors = [(Actor(app), handle) for handle in ("alice", "bob")]
    receipts = {}
    for actor, handle in actors:
        actor.register(handle)
        challenge = actor.challenge("rsa-22")
        response = actor.post(f'/api/challenges/{challenge["id"]}/submit', rsa_answer(actor, challenge))
        assert response.status_code == 200
        receipts[response.json()["submission"]["id"]] = handle
    winner = receipts[min(receipts)]
    client = actors[0][0].client
    scheme = next(item for item in client.get("/api/schemes").json()["schemes"] if item["id"] == rsa.SCHEME_ID)
    assert scheme["first_recovery"] == {"handle": winner, "received_at": clock[0], "profile_id": "rsa-22", "scheme_version": "1", "submission_id": min(receipts)}
    board = client.get("/api/leaderboard?profile_id=rsa-22&scheme_id=rsa-kem").json()["entries"]
    assert board[0]["handle"] == winner
