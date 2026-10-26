"""Generation performs no database work until a short revalidated commit phase."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select

from crucible import dh_v7, generation as generation_work, implementation_builds, registry
from crucible.app import create_app, current_round
from crucible.config import Settings
from crucible.db import AdapterVersion, Challenge, Method, ProfileIncident
from test_api import Actor

ISSUE = {"scheme_id": "teaching-dh-kem", "scheme_version": "7", "profile_id": "dh-v7-tier-1"}
NATIVE_REVIEW = Path(__file__).parent / "fixtures" / "sntrup-3-linux-build-review.json"
NATIVE_REVIEW_SHA256 = "d29569793f0a45cba2681e0ccc04e21c567de8d0191182464583b99d685d94b2"


def reviewed_native_document():
    raw = NATIVE_REVIEW.read_bytes()
    return raw, implementation_builds.parse_manifest(raw, NATIVE_REVIEW_SHA256)


def admit_reviewed_native_fixture(system, manifest):
    """Seed only this isolated database from the retained, explicit build review.

    Native adapters still verify their actual files. This does not authorize a
    changed candidate, create a review through HTTP, or alter production pins.
    The ordinary Mac path retains the application's normal baseline behavior.
    """
    platform = implementation_builds.additional_platform_admission(manifest)
    if platform is None:
        return None
    raw, reviewed = reviewed_native_document()
    identity = manifest["scheme_id"], manifest["version"]
    assert identity == (reviewed["scheme_id"], reviewed["scheme_version"])
    pair = {key: manifest["implementation"].get(key)
            for key in ("source_sha256", "artifact_sha256")}
    assert pair == reviewed["implementation"], "Candidate differs from the reviewed test build"
    assert platform == reviewed["platform"], "Platform differs from the reviewed test build"
    with system.app.state.db.transaction(write=True) as session:
        baseline = session.get(AdapterVersion, identity)
        if baseline is None:
            baseline = AdapterVersion(scheme_id=identity[0], version=identity[1],
                **reviewed["baseline"], created_at=system.now[0])
            session.add(baseline)
            session.flush()
        assert {key: getattr(baseline, key) for key in pair} == reviewed["baseline"]
        review = implementation_builds.register_build(session, raw,
            expected_sha256=NATIVE_REVIEW_SHA256, reviewed_by="Synthetic lifecycle fixture operator",
            review_basis="Isolated test database using the exact retained reviewed SNTRUP 3 Linux declaration; no new build approval.",
            now=system.now[0])
        assert implementation_builds.implementation_is_compatible(session, manifest, baseline)
        return review.id


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
    actual = dh_v7.generate
    def blocked(profile_id):
        entered.set()
        assert release.wait(8), "Test did not release the generator"
        return actual(profile_id)
    monkeypatch.setattr(dh_v7, "generate", blocked)
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


def test_separate_apps_still_converge_on_one_durable_instance(generation, monkeypatch):
    barrier = Barrier(3)
    actual = dh_v7.generate
    def simultaneous(profile_id):
        barrier.wait(timeout=5)
        return actual(profile_id)
    monkeypatch.setattr(dh_v7, "generate", simultaneous)
    # Distinct app/engine identities simulate the uncoordinated-process case.
    apps = [create_app(generation.app.state.settings, clock=lambda: generation.now[0]) for _ in range(2)]
    actors = [generation.actor, *[Actor(app) for app in apps]]
    for actor in actors[1:]:
        actor.client.cookies.clear()
        actor.client.cookies.update(generation.actor.client.cookies)
        actor.csrf = generation.actor.csrf
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(actor.post, "/api/challenges", ISSUE) for actor in actors]
            responses = [future.result(timeout=8) for future in futures]
    finally:
        for actor, app in zip(actors[1:], apps):
            actor.client.close()
            app.state.db.engine.dispose()
    assert [response.status_code for response in responses] == [200, 200, 200]
    challenges = [response.json()["challenge"] for response in responses]
    assert challenges[0] == challenges[1] == challenges[2]
    assert count(generation, Challenge) == 1
    assert count(generation, ProfileIncident) == 0
    with generation.app.state.db.transaction() as session:
        attempts = session.scalars(select(generation_work.GenerationAttempt)).all()
        assert len(attempts) == 3
        assert all(row.outcome == "agreement" for row in attempts)
        assert sorted(row.disposition for row in attempts) == ["duplicate_discarded", "duplicate_discarded", "issued"]


def test_duplicate_does_not_consume_native_capacity_and_retry_reuses_instance(generation, monkeypatch):
    adapter = registry.adapter_for("sntrup")
    manifest = adapter.manifests()[0]
    admit_reviewed_native_fixture(generation, manifest)
    profile_id = manifest["id"]
    body = {"scheme_id": adapter.SCHEME_ID, "scheme_version": adapter.SCHEME_VERSION, "profile_id": profile_id}
    other = Actor(generation.app)
    other.register("independent_solver")
    entered, release, mutex = Event(), Event(), Lock()
    calls = []
    actual = adapter.generate
    def blocked(profile_id):
        with mutex:
            calls.append(profile_id)
            first = len(calls) == 1
        if first:
            entered.set()
            assert release.wait(8)
        return actual(profile_id)
    monkeypatch.setattr(adapter, "generate", blocked)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(generation.actor.post, "/api/challenges", body)
            assert entered.wait(3)
            try:
                duplicate = generation.actor.post("/api/challenges", body)
                assert duplicate.status_code == 429 and duplicate.headers["retry-after"] == "3"
                assert len(calls) == count(generation, generation_work.GenerationAttempt) == 1
                independent = other.post("/api/challenges", body)
                assert independent.status_code == 200, independent.text
                assert not future.done()
            finally:
                release.set()
            first = future.result(timeout=3)
            assert first.status_code == 200, first.text
        retry = generation.actor.post("/api/challenges", body)
        assert retry.status_code == 200
        assert retry.json()["challenge"] == first.json()["challenge"]
        assert len(calls) == count(generation, generation_work.GenerationAttempt) == 2
        assert count(generation, Challenge) == 2
    finally:
        other.client.close()


def test_generation_resource_abort_releases_reservation_for_retry(generation, monkeypatch):
    adapter = registry.adapter_for("sntrup")
    manifest = adapter.manifests()[0]
    admit_reviewed_native_fixture(generation, manifest)
    profile_id = manifest["id"]
    body = {"scheme_id": adapter.SCHEME_ID, "scheme_version": adapter.SCHEME_VERSION, "profile_id": profile_id}
    actual = adapter.generate
    def abort(profile_id):
        raise adapter.GenerationResourceLimit("Synthetic resource abort")
    monkeypatch.setattr(adapter, "generate", abort)
    assert generation.actor.post("/api/challenges", body).status_code == 503
    monkeypatch.setattr(adapter, "generate", actual)
    assert generation.actor.post("/api/challenges", body).status_code == 200
    assert count(generation, Challenge) == 1
    assert count(generation, ProfileIncident) == 0
    with generation.app.state.db.transaction() as session:
        rows = session.scalars(select(generation_work.GenerationAttempt)).all()
        assert {(row.outcome, row.disposition) for row in rows} == {("generation_abort", "unissued"), ("agreement", "issued")}


def test_native_fixture_rejects_unreviewed_candidate_before_creating_baseline(generation):
    _, reviewed = reviewed_native_document()
    manifest = {"scheme_id": reviewed["scheme_id"], "version": reviewed["scheme_version"],
        "implementation": {**reviewed["implementation"], "admission": {
            "format": implementation_builds.ADMISSION_FORMAT, "kind": "reviewed-additional-platform",
            "platform": reviewed["platform"]}}}
    changed = deepcopy(manifest)
    changed["implementation"]["artifact_sha256"] = "0" * 64
    with pytest.raises(AssertionError, match="Candidate differs"):
        admit_reviewed_native_fixture(generation, changed)
    assert count(generation, AdapterVersion) == 0
    assert count(generation, implementation_builds.ImplementationBuild) == 0


def test_native_fixture_never_reactivates_revoked_review(generation):
    _, reviewed = reviewed_native_document()
    manifest = {"scheme_id": reviewed["scheme_id"], "version": reviewed["scheme_version"],
        "implementation": {**reviewed["implementation"], "admission": {
            "format": implementation_builds.ADMISSION_FORMAT, "kind": "reviewed-additional-platform",
            "platform": reviewed["platform"]}}}
    review_id = admit_reviewed_native_fixture(generation, manifest)
    with generation.app.state.db.transaction(write=True) as session:
        implementation_builds.revoke_build(session, review_id, reason="Synthetic test revocation", now=generation.now[0])
        assert not implementation_builds.implementation_is_compatible(session, manifest)
    with pytest.raises(ValueError, match="revoked"):
        admit_reviewed_native_fixture(generation, manifest)
    assert count(generation, AdapterVersion) == 1
    assert count(generation, implementation_builds.ImplementationBuild) == 1


def test_generation_finishing_at_the_deadline_cannot_issue_in_either_round(generation, monkeypatch):
    original_round = current_round(generation.now[0])["id"]
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
    with generation.app.state.db.transaction() as session:
        attempt, = session.scalars(select(generation_work.GenerationAttempt)).all()
        assert (attempt.outcome, attempt.disposition) == ("agreement", "generated_unissued")
    account_id = generation.actor.client.get("/api/session").json()["user"]["id"]
    old_key = (generation.app.state.db.engine, account_id, ISSUE["scheme_id"], ISSUE["scheme_version"], ISSUE["profile_id"], original_round)
    with generation_work.instance_reservation(old_key):
        pass  # The abandoned round's reservation must not remain in memory.
    assert generation.actor.post("/api/challenges", ISSUE).status_code == 200


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
                        session.get(AdapterVersion, ("teaching-dh-kem", "7")).source_sha256 = "0" * 64
                    else:
                        session.add(ProfileIncident(id=str(uuid4()), profile_id=ISSUE["profile_id"],
                            scheme_version="7", created_at=generation.now[0], diagnostics={"reason": "concurrent_hold"}))
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
    body = {**ISSUE, "profile_id": "dh-v7-tier-2", "active_round_id": round_id}
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
