"""Lower leaderboard credit preserves the actual source receipt and evidence."""
from uuid import uuid4

from sqlalchemy import func, select

from crucible import dh_v3, registry
from crucible.db import Submission
from scripts.solve_rsa import recover as recover_rsa
from scripts.solve_teaching import recover as recover_dh
from test_api import Actor, system


def submit(actor, profile, *, candidate=None, correct=True):
    challenge = actor.challenge(profile)
    candidate = candidate if candidate is not None else (recover_rsa if profile.startswith("rsa-") else recover_dh)(challenge["public"])
    method = actor.method(f"Method for {profile}")
    body = {"candidate": candidate if correct else "00" * 32, "method_id": method["id"],
            "compute": {"hardware": f"Recorded hardware for {profile}", "workers": 2, "wall_seconds": 1.25,
                        "precomputation": "None", "peak_memory": "12 MiB", "notes": "Actual source evidence"},
            "idempotency_key": str(uuid4())}
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', body)
    assert response.status_code == 200, response.text
    assert response.json()["submission"]["status"] == ("correct" if correct else "incorrect")
    return challenge, response.json()["submission"], body


def board(actor, profile, version=None):
    query = {"profile_id": profile}
    if version:
        query["scheme_version"] = version
    response = actor.client.get("/api/leaderboard", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_inherited_names_are_downward_only_and_keep_earliest_qualifying_evidence(system):
    app, clock, _ = system
    alice, bob, carol = (Actor(app) for _ in range(3))
    for actor, handle in ((alice, "alice"), (bob, "bob"), (carol, "carol")):
        actor.register(handle)
    low, _, _ = submit(alice, "dh-20")
    clock[0] += 1
    exact, _, _ = submit(bob, "dh-22")
    clock[0] += 1
    high, receipt, evidence = submit(alice, "dh-24")
    clock[0] += 1
    submit(alice, "dh-22")  # Later exact evidence does not replace the first qualifying receipt.
    submit(carol, "dh-26", correct=False)
    submit(carol, "rsa-25")
    with app.state.db.transaction() as session:
        count_before = session.scalar(select(func.count(Submission.id)))
    lowest = board(alice, "dh-20")["entries"]
    assert [(row["handle"], row["profile_id"], row["is_inherited"]) for row in lowest] == [
        ("alice", "dh-20", False), ("bob", "dh-22", True)]
    assert lowest[0]["challenge_id"] == low["id"]
    middle = board(alice, "dh-22")["entries"]
    assert [(row["handle"], row["profile_id"], row["is_inherited"]) for row in middle] == [
        ("bob", "dh-22", False), ("alice", "dh-24", True)]
    inherited = middle[1]
    assert inherited["challenge_id"] == high["id"]
    assert inherited["scheme_version"] == registry.CURRENT[dh_v3.SCHEME_ID]
    assert inherited["instance_size"] == 24
    assert inherited["received_at"] == receipt["received_at"]
    assert inherited["method"]["id"] == evidence["method_id"]
    assert inherited["compute"] == evidence["compute"]
    assert [row["handle"] for row in board(alice, "dh-24")["entries"]] == ["alice"]
    assert board(alice, "dh-25")["entries"] == []
    assert [row["handle"] for row in board(alice, "rsa-20")["entries"]] == ["carol"]
    catalogue = alice.client.get("/api/schemes").json()["schemes"]
    assert catalogue[0]["recovered_profile_ids"] == ["dh-20", "dh-22", "dh-24"]
    assert catalogue[1]["recovered_profile_ids"] == ["rsa-25"]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count(Submission.id))) == count_before


def test_receipt_ties_and_top_ten_are_consistent_for_inherited_credit(system):
    app, _, _ = system
    receipts = {}
    viewer = None
    for index in range(12):
        actor = Actor(app)
        handle = f"solver_{index:02}"
        actor.register(handle)
        _, receipt, _ = submit(actor, "dh-21" if index % 2 else "dh-24")
        receipts[receipt["id"]] = handle
        viewer = actor
    entries = board(viewer, "dh-20")["entries"]
    assert len(entries) == 10
    assert [entry["handle"] for entry in entries] == [receipts[key] for key in sorted(receipts)[:10]]
    assert [entry["rank"] for entry in entries] == list(range(1, 11))
    assert all(entry["is_inherited"] for entry in entries)


def test_compatibility_is_source_profile_specific_and_archives_stay_strict(system, monkeypatch):
    monkeypatch.setitem(registry.CURRENT, dh_v3.SCHEME_ID, "3")
    app, clock, _ = system
    old, new, ancient = (Actor(app) for _ in range(3))
    for actor, handle in ((old, "old"), (new, "new"), (ancient, "ancient")):
        actor.register(handle)
    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, dh_v3.SCHEME_ID, "2")
        old_challenge, _, _ = submit(old, "dh-36")
    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, dh_v3.SCHEME_ID, "1")
        submit(ancient, "dh-24")
    before = board(old, "dh-35", "3")
    assert [(row["handle"], row["profile_id"], row["scheme_version"]) for row in before["entries"]] == [("old", "dh-36", "2")]
    assert before["entries"][0]["challenge_id"] == old_challenge["id"]
    assert board(old, "dh-37", "3")["entries"] == []
    assert board(old, "dh-37", "3")["includes_compatible_versions"] == []
    # Compatibility of the selected lower profile alone must not admit a
    # higher source profile that has no compatibility declaration of its own.
    with monkeypatch.context() as restricted:
        restricted.setitem(registry.COMPATIBLE, (dh_v3.SCHEME_ID, "3"), {"2": ("dh-20",)})
        assert board(old, "dh-20", "3")["entries"] == []
        assert [row["handle"] for row in board(old, "dh-20", "2")["entries"]] == ["old"]
    # Capture a real generated target in the fixture, without attempting a
    # resource-unbounded solve. The server still verifies its normal target MAC.
    real_generate = dh_v3.generate
    captured = {}
    def capture(profile_id):
        generated = real_generate(profile_id)
        captured[profile_id] = generated.target.hex()
        return generated
    monkeypatch.setattr(dh_v3, "generate", capture)
    new.challenge("dh-128")
    clock[0] += 1
    new_challenge, _, _ = submit(new, "dh-128", candidate=captured["dh-128"])
    current = board(old, "dh-20", "3")["entries"]
    assert [row["handle"] for row in current] == ["old", "new"]
    assert [(row["profile_id"], row["instance_size"]) for row in current] == [("dh-36", 36), ("dh-128", 128)]
    assert current[1]["challenge_id"] == new_challenge["id"]
    assert [row["handle"] for row in board(old, "dh-20", "2")["entries"]] == ["old"]
    assert [row["handle"] for row in board(old, "dh-20", "1")["entries"]] == ["ancient"]
    assert [row["handle"] for row in board(old, "dh-37", "3")["entries"]] == ["new"]
    assert board(old, "dh-128", "3")["entries"][0]["is_inherited"] is False
    assert board(old, "dh-127", "3")["entries"][0]["is_inherited"] is True
