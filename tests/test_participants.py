"""Catalogue activity counts disclose no unsuccessful account or attempt details."""
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from crucible import registry
from crucible.db import Challenge, Submission
from scripts.solve_rsa import recover as recover_rsa
from scripts.solve_teaching import recover as recover_dh
from test_api import Actor, system


DH = "teaching-dh-kem"
RSA = "rsa-kem"


def actor(app, handle):
    participant = Actor(app)
    participant.register(handle)
    return participant


def attempt(participant, profile, *, correct=False):
    challenge = participant.challenge(profile)
    candidate = (recover_rsa if profile.startswith("rsa-") else recover_dh)(challenge["public"])
    if not correct:
        candidate = ("1" if candidate[0] == "0" else "0") + candidate[1:]
    method = participant.method("PRIVATE_attempt_method_83a7")
    body = {"candidate": candidate, "method_id": method["id"], "idempotency_key": str(uuid4()),
            "compute": {"hardware": "PRIVATE_machine_83a7", "workers": 1, "wall_seconds": 0.1,
                        "precomputation": "None", "peak_memory": "Unknown", "notes": "PRIVATE_notes_83a7"}}
    response = participant.post(f'/api/challenges/{challenge["id"]}/submit', body)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission"]
    assert receipt["status"] == ("correct" if correct else "incorrect")
    return challenge, receipt, body


def catalogue(app):
    response = TestClient(app).get("/api/schemes")
    assert response.status_code == 200, response.text
    return {scheme["id"]: scheme for scheme in response.json()["schemes"]}


def archived(scheme, version):
    return next(release for release in scheme["previous_releases"] if release["version"] == version)


def test_empty_registered_catalogue_counts_are_zero_and_downloads_do_not_count(system):
    app, _, _ = system
    baseline = catalogue(app)
    for scheme in baseline.values():
        for release in [scheme, *scheme["previous_releases"]]:
            assert type(release["participant_count"]) is int and release["participant_count"] == 0
    reader = actor(app, "reader")
    challenge = reader.challenge("dh-20")
    assert reader.client.get(challenge["download_url"]).status_code == 200
    reader.method("A method without an answer")
    assert catalogue(app) == baseline


def test_distinct_accounts_include_correct_and_incorrect_across_profiles_and_rounds(system):
    app, clock, _ = system
    alice, bob = actor(app, "alice"), actor(app, "bob")
    attempt(alice, "dh-20", correct=True)
    attempt(alice, "dh-21", correct=False)
    attempt(bob, "dh-22", correct=False)
    clock[0] += 3600
    attempt(alice, "dh-20", correct=False)
    scheme = catalogue(app)[DH]
    assert scheme["participant_count"] == 2
    assert scheme["recovered_profile_ids"] == ["dh-20"]
    assert scheme["first_recovery"]["handle"] == "alice"
    assert catalogue(app)[RSA]["participant_count"] == 0
    attempt(alice, "rsa-20", correct=False)
    counts = catalogue(app)
    assert counts[DH]["participant_count"] == 2
    assert counts[RSA]["participant_count"] == 1


def test_explicit_compatible_profile_lineage_counts_once_and_archives_keep_their_scope(system, monkeypatch):
    app, _, _ = system
    ancient, overlap, previous, current, old_only = (actor(app, name) for name in ("ancient", "overlap", "previous", "current", "old_only"))
    with monkeypatch.context() as historical:
        historical.setitem(registry.CURRENT, DH, "1")
        attempt(ancient, "dh-20")
    with monkeypatch.context() as historical:
        historical.setitem(registry.CURRENT, DH, "2")
        attempt(overlap, "dh-36")
        attempt(old_only, "dh-35")
    with monkeypatch.context() as historical:
        historical.setitem(registry.CURRENT, DH, "3")
        attempt(previous, "dh-24")
        attempt(overlap, "dh-20")
    attempt(current, "dh-21")
    attempt(overlap, "dh-20")
    scheme = catalogue(app)[DH]
    assert scheme["participant_count"] == 4  # v2/v3 overlap user is counted once.
    assert archived(scheme, "3")["participant_count"] == 3
    assert archived(scheme, "2")["participant_count"] == 2
    assert archived(scheme, "1")["participant_count"] == 1
    # Compatibility must apply to the submitted source profile, not merely
    # another lower profile that happens to have a declaration.
    with monkeypatch.context() as restricted:
        restricted.setitem(registry.COMPATIBLE, (DH, "4"), {"2": ("dh-20",)})
        assert catalogue(app)[DH]["participant_count"] == 2  # Current accounts only.


@pytest.mark.parametrize("field,value", [
    ("profile_id", "rsa-20"), ("scheme_version", "1"),
    ("round_id", -1), ("user_id", "another-user"),
])
def test_denormalized_attempts_must_match_their_challenge_identity(system, field, value):
    app, _, _ = system
    participant = actor(app, "participant")
    other = actor(app, "other")
    challenge, _, _ = attempt(participant, "dh-20")
    if field == "user_id":
        value = other.client.get("/api/session").json()["user"]["id"]
    with app.state.db.transaction(write=True) as session:
        receipt = session.scalar(select(Submission).where(Submission.challenge_id == challenge["id"]))
        setattr(receipt, field, value)
    result = catalogue(app)
    assert result[DH]["participant_count"] == result[RSA]["participant_count"] == 0


@pytest.mark.parametrize("profile,version", [("dh-not-registered", "4"), ("dh-20", "999"), ("dh-37", "2")])
def test_unregistered_profiles_releases_and_false_compatibility_do_not_count(system, monkeypatch, profile, version):
    app, _, _ = system
    participant = actor(app, "participant")
    challenge, _, _ = attempt(participant, "dh-20")
    with app.state.db.transaction(write=True) as session:
        instance = session.get(Challenge, challenge["id"])
        receipt = session.scalar(select(Submission).where(Submission.challenge_id == instance.id))
        instance.profile_id = receipt.profile_id = profile
        instance.scheme_version = receipt.scheme_version = version
    # Even a mistaken declared overlap cannot admit a nonexistent source profile.
    monkeypatch.setitem(registry.COMPATIBLE, (DH, "4"), {version: (profile,)})
    assert catalogue(app)[DH]["participant_count"] == 0


def test_incorrect_attempt_adds_only_aggregate_count_to_public_catalogue(system):
    app, _, _ = system
    private = actor(app, "private_solver_83a7")
    before = catalogue(app)
    challenge, receipt, body = attempt(private, "dh-20", correct=False)
    response = TestClient(app).get("/api/schemes")
    result = {scheme["id"]: scheme for scheme in response.json()["schemes"]}
    expected = deepcopy(before)
    expected[DH]["participant_count"] = 1
    assert result == expected
    for secret in ("private_solver_83a7", "PRIVATE_attempt_method_83a7", "PRIVATE_machine_83a7",
                   "PRIVATE_notes_83a7", challenge["id"], receipt["id"], body["candidate"]):
        assert secret not in response.text
    assert TestClient(app).get(f'/api/submissions/{receipt["id"]}').status_code == 404


def test_participant_helper_uses_one_aggregate_query_for_all_current_and_archived_releases(system):
    app, _, _ = system
    entries = registry.catalogue()
    releases = [release for scheme in entries for release in [scheme, *scheme["previous_releases"]]]
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    with app.state.db.transaction() as session:
        event.listen(app.state.db.engine, "before_cursor_execute", capture)
        try:
            counts = registry.participant_counts(session, releases)
        finally:
            event.remove(app.state.db.engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "count(DISTINCT CASE" in statements[0]
    assert len(counts) == len(releases) and all(value == 0 for value in counts.values())
