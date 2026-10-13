"""One manual start authorizes on-demand tiers only within its exact round scope."""
from dataclasses import replace
from unittest.mock import patch

import pytest
from sqlalchemy import event, func, select

from crucible import registry, rsa

# These existing cases use the explicitly pinned legacy test_api.system.
CURRENT_DH = registry.adapter_for("teaching-dh-kem", "4")
from crucible.db import AdapterVersion, Challenge
from test_api import Actor, system


def count(app, model=Challenge):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def get(actor, profile="dh-21", **query):
    response = actor.client.get("/api/challenges", params={"profile_id": profile, **query})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["round_started_round_id"] == value["round"]["id"]
    return value


def test_manual_start_enables_other_tiers_without_get_generation_or_bulk_rows(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("GET must not generate")):
        before = get(actor)
    assert before["round_started"] is False and before["challenge"] is None
    assert count(app) == 0
    first = actor.post("/api/challenges", {"profile_id": "dh-20"}).json()
    assert first["round_started"] is True
    active = first["round_started_round_id"]
    assert active == first["challenge"]["round_id"]
    assert count(app) == 1
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("GET must not generate")):
        other = get(actor, "dh-128")
        same = get(actor, "dh-20")
    assert other["round_started"] is True and other["challenge"] is None
    assert same["round_started"] is True and same["challenge"] == first["challenge"]
    assert count(app) == 1
    body = {"profile_id": "dh-128", "scheme_id": CURRENT_DH.SCHEME_ID, "scheme_version": CURRENT_DH.SCHEME_VERSION, "active_round_id": active}
    added = actor.post("/api/challenges", body)
    assert added.status_code == 200, added.text
    assert added.json()["round_started"] is True and added.json()["round_started_round_id"] == active
    assert count(app) == 2
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("Existing tier must be reused")):
        repeated = actor.post("/api/challenges", body)
    assert repeated.status_code == 200
    assert repeated.json()["challenge"] == added.json()["challenge"]
    assert repeated.json()["round_started"] is True
    assert count(app) == 2
    # The alternate documented scheme+tier selector also accepts the guard.
    by_tier = actor.post("/api/challenges", {"scheme_id": CURRENT_DH.SCHEME_ID, "instance_size": 128, "active_round_id": active})
    assert by_tier.status_code == 200 and by_tier.json()["challenge"] == added.json()["challenge"]


def test_auto_request_cannot_activate_an_unstarted_account_scheme_or_round(system):
    app, clock, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    active = get(alice)["round"]["id"]
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("Guard rejection must precede generation")):
        assert alice.post("/api/challenges", {"profile_id": "dh-20", "active_round_id": active}).status_code == 409
    assert count(app) == 0 and count(app, AdapterVersion) == 0
    first = alice.post("/api/challenges", {"profile_id": "dh-20"}).json()["challenge"]
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("Cross-account guard must reject")):
        assert bob.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": active}).status_code == 409
    with patch.object(rsa, "generate", side_effect=AssertionError("Cross-scheme guard must reject")):
        assert alice.post("/api/challenges", {"profile_id": "rsa-20", "active_round_id": active}).status_code == 409
    assert get(bob)["round_started"] is False
    assert get(alice, "rsa-20")["round_started"] is False
    assert count(app) == 1 and count(app, AdapterVersion) == 1
    assert alice.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": active + 1}).status_code == 409
    clock[0] = first["ends_at"]
    assert get(alice)["round_started"] is False
    with patch.object(CURRENT_DH, "generate", side_effect=AssertionError("Stale or unstarted round must reject")):
        assert alice.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": active}).status_code == 409
        assert alice.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": active + 1}).status_code == 409
    assert count(app) == 1
    fresh = alice.post("/api/challenges", {"profile_id": "dh-21"})
    assert fresh.status_code == 200 and fresh.json()["round_started_round_id"] == active + 1


def test_archived_release_never_activates_current_release_or_auto_access(system, monkeypatch):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, CURRENT_DH.SCHEME_ID, "2")
        old = actor.challenge("dh-20")
    archived = get(actor, "dh-20", scheme_id=CURRENT_DH.SCHEME_ID, scheme_version="2")
    assert archived["challenge"]["id"] == old["id"]
    assert archived["round_started"] is False
    assert get(actor, "dh-21", scheme_id=CURRENT_DH.SCHEME_ID, scheme_version=CURRENT_DH.SCHEME_VERSION)["round_started"] is False
    assert actor.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": old["round_id"]}).status_code == 409
    assert actor.post("/api/challenges", {"profile_id": "dh-21", "scheme_id": CURRENT_DH.SCHEME_ID,
        "scheme_version": "2", "active_round_id": old["round_id"]}).status_code == 409
    assert count(app) == 1


@pytest.mark.parametrize("guard", [None, True, False, -1, 1000.0, "1000", [], {}])
def test_explicit_invalid_guard_never_bypasses_manual_activation(system, guard):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    response = actor.post("/api/challenges", {"profile_id": "dh-20", "active_round_id": guard})
    assert response.status_code == 422
    assert count(app) == 0


def test_failed_correctness_does_not_start_round_access(system, monkeypatch):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    generated = CURRENT_DH.generate("dh-20")
    failed = replace(generated, diagnostics={**generated.diagnostics, "shared_secret_matches": False})
    monkeypatch.setattr(CURRENT_DH, "generate", lambda _: failed)
    assert actor.post("/api/challenges", {"profile_id": "dh-20"}).status_code == 503
    state = get(actor, "dh-21")
    assert state["round_started"] is False
    assert count(app) == 0
    assert actor.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": state["round"]["id"]}).status_code == 409
    assert count(app) == 0


def test_get_round_metadata_remains_coherent_when_clock_rolls_during_lookup(system):
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    first = actor.challenge("dh-20")
    old_time = first["ends_at"] - 0.001
    clock[0] = old_time
    def cross_boundary(connection, cursor, statement, parameters, context, executemany):
        if "FROM challenges" in statement:
            clock[0] = first["ends_at"] + 0.001
    event.listen(app.state.db.engine, "after_cursor_execute", cross_boundary)
    try:
        response = get(actor, "dh-21")
    finally:
        event.remove(app.state.db.engine, "after_cursor_execute", cross_boundary)
    assert response["server_time"] == old_time
    assert response["round"]["id"] == first["round_id"]
    assert response["round_started_round_id"] == first["round_id"]
    assert response["round_started"] is True
    # Even a delayed response bearing true cannot start the next server round.
    guarded = actor.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": response["round_started_round_id"]})
    assert guarded.status_code == 409
    assert count(app) == 1


def test_hour_boundary_during_automatic_generation_does_not_issue_new_row(system, monkeypatch):
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    first = actor.challenge("dh-20")
    clock[0] = first["ends_at"] - 0.001
    real_generate = CURRENT_DH.generate
    def delayed(profile_id):
        result = real_generate(profile_id)
        clock[0] = first["ends_at"]
        return result
    monkeypatch.setattr(CURRENT_DH, "generate", delayed)
    response = actor.post("/api/challenges", {"profile_id": "dh-21", "active_round_id": first["round_id"]})
    assert response.status_code == 409
    assert count(app) == 1
    assert get(actor, "dh-21")["round_started"] is False
