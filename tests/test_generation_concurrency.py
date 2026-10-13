"""Generation performs no database work until a short revalidated commit phase."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from crucible import dh_v5, registry
from crucible.app import create_app, current_round
from crucible.config import Settings
from crucible.db import AdapterVersion, Challenge, Method, ProfileIncident
from test_api import Actor

ISSUE = {"scheme_id": "teaching-dh-kem", "scheme_version": "5", "profile_id": "dh-v5-tier-1"}


@pytest.fixture
def generation(tmp_path):
    now = [3_600_600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'generation.db'}", b"g" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    actor = Actor(app)
    actor.register("generation_solver")
    yield SimpleNamespace(app=app, actor=actor, now=now)
    actor.client.close()
    app.state.db.engine.dispose()


def count(system, model):
    with system.app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def block_generator(monkeypatch):
    entered, release = Event(), Event()
    actual = dh_v5.generate
    def blocked(profile_id):
        entered.set()
        assert release.wait(8), "Test did not release the generator"
        return actual(profile_id)
    monkeypatch.setattr(dh_v5, "generate", blocked)
    return entered, release


def test_unrelated_sqlite_write_completes_while_generation_is_blocked(generation, monkeypatch):
    entered, release = block_generator(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(generation.actor.post, "/api/challenges", ISSUE)
        assert entered.wait(3)
        try:
            other = pool.submit(generation.actor.post, "/api/methods", {
                "name": "Concurrent documentation", "pseudocode": "Document work while key generation continues."})
            response = other.result(timeout=3)
            assert response.status_code == 200, response.text
            assert not future.done(), "Generation is still waiting independently of the database"
            assert count(generation, Method) == 1
        finally:
            release.set()
        assert future.result(timeout=3).status_code == 200
    assert count(generation, Challenge) == 1


def test_duplicate_generations_converge_on_one_durable_instance(generation, monkeypatch):
    barrier = Barrier(3)
    actual = dh_v5.generate
    def simultaneous(profile_id):
        barrier.wait(timeout=5)
        return actual(profile_id)
    monkeypatch.setattr(dh_v5, "generate", simultaneous)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(generation.actor.post, "/api/challenges", ISSUE) for _ in range(3)]
        responses = [future.result(timeout=8) for future in futures]
    assert [response.status_code for response in responses] == [200, 200, 200]
    challenges = [response.json()["challenge"] for response in responses]
    assert challenges[0] == challenges[1] == challenges[2]
    assert count(generation, Challenge) == 1
    assert count(generation, ProfileIncident) == 0


def test_generation_finishing_at_the_deadline_cannot_issue_in_either_round(generation, monkeypatch):
    entered, release = block_generator(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generation.actor.post, "/api/challenges", ISSUE)
        assert entered.wait(3)
        generation.now[0] = current_round(generation.now[0])["ends_at"]
        release.set()
        response = future.result(timeout=3)
    assert response.status_code == 409, response.text
    assert count(generation, Challenge) == 0
    assert count(generation, ProfileIncident) == 0


@pytest.mark.parametrize("change,status", [("logout", 401), ("release", 409), ("source_pin", 409), ("paused", 503)])
def test_generation_rechecks_authority_release_pin_and_hold_before_persisting(generation, monkeypatch, change, status):
    entered, release = block_generator(monkeypatch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generation.actor.post, "/api/challenges", ISSUE)
        assert entered.wait(3)
        try:
            if change == "logout":
                assert generation.actor.post("/api/auth/logout", {}).status_code == 200
            elif change == "release":
                monkeypatch.setitem(registry.CURRENT, "teaching-dh-kem", "4")
            else:
                with generation.app.state.db.transaction(write=True) as session:
                    if change == "source_pin":
                        session.get(AdapterVersion, ("teaching-dh-kem", "5")).source_sha256 = "0" * 64
                    else:
                        session.add(ProfileIncident(id=str(uuid4()), profile_id=ISSUE["profile_id"],
                            scheme_version="5", created_at=generation.now[0], diagnostics={"reason": "concurrent_hold"}))
        finally:
            release.set()
        response = future.result(timeout=3)
    assert response.status_code == status, response.text
    assert count(generation, Challenge) == 0
    assert count(generation, ProfileIncident) == (1 if change == "paused" else 0)


def test_automatic_generation_rechecks_round_activation_after_preflight(generation, monkeypatch):
    initial = generation.actor.post("/api/challenges", ISSUE)
    assert initial.status_code == 200
    round_id = initial.json()["challenge"]["round_id"]
    entered, release = block_generator(monkeypatch)
    body = {**ISSUE, "profile_id": "dh-v5-tier-2", "active_round_id": round_id}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(generation.actor.post, "/api/challenges", body)
        assert entered.wait(3)
        try:
            with generation.app.state.db.transaction(write=True) as session:
                session.execute(delete(Challenge))
        finally:
            release.set()
        response = future.result(timeout=3)
    assert response.status_code == 409, response.text
    assert count(generation, Challenge) == 0
