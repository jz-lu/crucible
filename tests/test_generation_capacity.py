"""Admission invariants, not a claim about production hardware throughput."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError

from crucible import dh_v7, generation, registry, rsa_v4
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import ProfileIncident, RateBucket
from crucible.security import mac
from test_api import Actor


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2", None])
def test_invalid_total_capacity_is_rejected(value):
    with pytest.raises(ValueError):
        generation.GenerationLimiter(value)


@pytest.mark.parametrize("value", [0, -1, 3, 1.5, True, "1", None])
def test_invalid_account_capacity_is_rejected(value):
    with pytest.raises(ValueError):
        generation.GenerationLimiter(2, max_per_account=value)


def test_one_account_cannot_occupy_both_slots_and_snapshots_are_private():
    limiter = generation.GenerationLimiter()
    with limiter.slot("private-account-a"):
        with pytest.raises(generation.GenerationBusy):
            with limiter.slot("private-account-a"):
                pytest.fail("An account must not monopolize distinct profiles")
        with limiter.slot("private-account-b"):
            with pytest.raises(generation.GenerationBusy):
                with limiter.slot("private-account-c"):
                    pytest.fail("Capacity must reject without entering work")
            snapshot = limiter.snapshot()
            assert snapshot["inflight"] == snapshot["peak_inflight"] == 2
            assert snapshot["busy_account"] == snapshot["busy_capacity"] == 1
            assert "private-account" not in str(snapshot)
            snapshot["inflight"] = -1
            assert limiter.snapshot()["inflight"] == 2
    assert limiter.snapshot()["inflight"] == 0
    assert limiter.snapshot()["completed"] == 2


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_every_error_releases_account_and_capacity(error):
    limiter = generation.GenerationLimiter(1)
    with pytest.raises(error):
        with limiter.slot("account-a"):
            raise error("Synthetic failure")
    with limiter.slot("account-a"):
        pass
    snapshot = limiter.snapshot()
    assert snapshot["inflight"] == 0
    assert snapshot["started"] == snapshot["completed"] == 2
    assert snapshot["failed"] == 1


def test_duration_counts_completed_blocks_including_failure(monkeypatch):
    limiter = generation.GenerationLimiter()
    ticks = iter([10.0, 12.0, 20.0, 23.0])
    monkeypatch.setattr(generation.time, "monotonic", lambda: next(ticks))
    with limiter.slot("a"):
        pass
    with pytest.raises(ValueError):
        with limiter.slot("b"):
            raise ValueError("Synthetic failure")
    snapshot = limiter.snapshot()
    assert snapshot["duration_seconds"] == 5.0
    assert snapshot["max_duration_seconds"] == 3.0
    assert snapshot["completed"] == 2 and snapshot["failed"] == 1


def test_apps_have_independent_capacity():
    first = generation.GenerationLimiter(1)
    second = generation.GenerationLimiter(1)
    with first.slot("same-account"), second.slot("same-account"):
        assert first.snapshot()["inflight"] == second.snapshot()["inflight"] == 1


def test_150_concurrent_synthetic_requests_do_not_queue_or_exceed_capacity():
    requests, slots = 150, 3
    limiter = generation.GenerationLimiter(slots)
    start, release, decided, mutex = Barrier(requests + 1), Event(), Event(), Lock()
    decisions = 0

    def record_decision():
        nonlocal decisions
        with mutex:
            decisions += 1
            if decisions == requests:
                decided.set()

    def request(index):
        start.wait(timeout=15)
        try:
            with limiter.slot(f"synthetic-account-{index}"):
                record_decision()
                assert release.wait(15), "Test must release accepted work"
                return "accepted"
        except generation.GenerationBusy:
            record_decision()
            return "busy"

    with ThreadPoolExecutor(max_workers=requests) as pool:
        futures = [pool.submit(request, index) for index in range(requests)]
        try:
            start.wait(timeout=15)
            assert decided.wait(15), "Rejected requests must finish without waiting for generation"
            snapshot = limiter.snapshot()
            assert snapshot["inflight"] == snapshot["peak_inflight"] == slots
            assert snapshot["started"] == slots
            assert snapshot["busy_capacity"] == requests - slots
        finally:
            release.set()
        outcomes = [future.result(timeout=5) for future in futures]
    assert outcomes.count("accepted") == slots
    assert outcomes.count("busy") == requests - slots
    assert limiter.snapshot()["inflight"] == 0
    assert limiter.snapshot()["completed"] == slots


@pytest.fixture
def capacity_app(tmp_path):
    settings = Settings(f"sqlite:///{tmp_path / 'capacity.db'}", b"c" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: 3_600_600.0, initialize=True)
    first, second = Actor(app), Actor(app)
    first.register("capacity_first")
    second.register("capacity_second")
    yield app, first, second
    first.client.close()
    second.client.close()
    app.state.db.engine.dispose()


def issue_body(adapter, offset=0):
    return {"scheme_id": adapter.SCHEME_ID, "scheme_version": adapter.SCHEME_VERSION,
            "profile_id": adapter.manifests()[offset]["id"]}


def synthetic_result(profile_id):
    # Deliberately no cryptographic work: this fixture tests admission only.
    return SimpleNamespace(public={"synthetic_test_only": True, "profile_id": profile_id},
                           target=b"s" * 32, diagnostics={"shared_secret_matches": True})


@pytest.mark.parametrize("blocked_adapter,other_adapter", [(dh_v7, rsa_v4), (rsa_v4, dh_v7)])
def test_all_adapter_types_share_capacity_and_reads_stay_available(capacity_app, monkeypatch, blocked_adapter, other_adapter):
    app, first, second = capacity_app
    app.state.generation_limiter = generation.GenerationLimiter(1)
    entered, release = Event(), Event()

    def blocked(profile_id):
        entered.set()
        assert release.wait(10)
        return synthetic_result(profile_id)

    def unexpected(_):
        pytest.fail("A rejected request must not invoke its adapter")

    monkeypatch.setattr(blocked_adapter, "generate", blocked)
    monkeypatch.setattr(other_adapter, "generate", unexpected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.post, "/api/challenges", issue_body(blocked_adapter))
        assert entered.wait(5)
        try:
            busy = second.post("/api/challenges", issue_body(other_adapter))
            assert busy.status_code == 429 and busy.headers["retry-after"] == "3"
            for path in ("/health", "/api/session", "/api/schemes"):
                response = pool.submit(second.client.get, path).result(timeout=3)
                assert response.status_code == 200
            assert not pending.done()
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert app.state.generation_limiter.snapshot()["completed"] == 1


def test_account_limit_covers_different_profiles_but_not_other_accounts(capacity_app, monkeypatch):
    app, first, second = capacity_app
    entered, release = Event(), Event()

    def blocked(profile_id):
        if profile_id == issue_body(dh_v7)["profile_id"]:
            entered.set()
            assert release.wait(10)
        return synthetic_result(profile_id)

    monkeypatch.setattr(dh_v7, "generate", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.post, "/api/challenges", issue_body(dh_v7))
        assert entered.wait(5)
        try:
            busy = first.post("/api/challenges", issue_body(dh_v7, 1))
            assert busy.status_code == 429 and busy.headers["retry-after"] == "3"
            assert second.post("/api/challenges", issue_body(dh_v7, 1)).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert first.post("/api/challenges", issue_body(dh_v7, 1)).status_code == 200
    snapshot = app.state.generation_limiter.snapshot()
    assert snapshot["busy_account"] == 1
    assert snapshot["peak_inflight"] == 2
    assert snapshot["inflight"] == 0


def test_generation_remains_lazy_and_same_hour_reuses_existing_instance(capacity_app, monkeypatch):
    app, actor, _ = capacity_app
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        return synthetic_result(profile_id)

    monkeypatch.setattr(dh_v7, "generate", generate)
    assert actor.client.get("/api/schemes").status_code == 200
    assert actor.client.get("/api/challenges", params=issue_body(dh_v7)).json()["challenge"] is None
    assert calls == []
    first = actor.post("/api/challenges", issue_body(dh_v7))
    assert first.status_code == 200
    repeated = actor.post("/api/challenges", issue_body(dh_v7))
    assert repeated.json()["challenge"] == first.json()["challenge"]
    unopened = actor.client.get("/api/challenges", params=issue_body(dh_v7, 1))
    assert unopened.status_code == 200 and unopened.json()["challenge"] is None
    assert actor.client.get(first.json()["challenge"]["download_url"]).status_code == 200
    assert calls == [issue_body(dh_v7)["profile_id"]]
    assert app.state.generation_limiter.snapshot()["started"] == 1


@pytest.fixture
def limited_capacity_app(capacity_app):
    source_app, source_actor, _ = capacity_app
    now = [source_app.state.clock()]
    settings = replace(source_app.state.settings, rate_limits=True)
    app = create_app(settings, clock=lambda: now[0])
    actor = Actor(app)
    actor.client.cookies.clear()
    actor.client.cookies.update(source_actor.client.cookies)
    actor.csrf = source_actor.csrf
    user_id = actor.client.get("/api/session").json()["user"]["id"]
    quota_key = mac(settings.secret, "rate-v1", f"generation-account:account:{user_id}".encode())
    yield SimpleNamespace(app=app, actor=actor, now=now, quota_key=quota_key)
    actor.client.close()
    app.state.db.engine.dispose()


def test_busy_and_existing_instance_reuse_do_not_consume_generation_quota(limited_capacity_app, monkeypatch):
    system = limited_capacity_app
    bucket = int(system.now[0]) // 60
    with system.app.state.db.transaction(write=True) as session:
        session.add(RateBucket(key=system.quota_key, bucket=bucket, count=29, expires_at=(bucket + 2) * 60))
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        return synthetic_result(profile_id)

    def quota_count():
        with system.app.state.db.transaction() as session:
            return session.get(RateBucket, (system.quota_key, bucket)).count

    monkeypatch.setattr(dh_v7, "generate", generate)
    limiter = system.app.state.generation_limiter
    with limiter.slot("held-account-a"), limiter.slot("held-account-b"):
        busy = system.actor.post("/api/challenges", issue_body(dh_v7))
        assert busy.status_code == 429 and busy.headers["retry-after"] == "3"
        assert quota_count() == 29 and calls == []
    first = system.actor.post("/api/challenges", issue_body(dh_v7))
    assert first.status_code == 200 and quota_count() == 30
    repeated = system.actor.post("/api/challenges", issue_body(dh_v7))
    assert repeated.status_code == 200
    assert repeated.json()["challenge"] == first.json()["challenge"]
    assert quota_count() == 30
    denied = system.actor.post("/api/challenges", issue_body(dh_v7, 1))
    assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0
    assert calls == [issue_body(dh_v7)["profile_id"]]
    assert limiter.snapshot()["inflight"] == 0
    with system.app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ProfileIncident)) == 0


@pytest.mark.parametrize("failure_stage", ["quota", "ledger"])
def test_setup_database_failure_does_not_quarantine_profile(limited_capacity_app, monkeypatch, failure_stage):
    system = limited_capacity_app
    adapter = dh_v7  # DH now uses the same durable attempt ledger as every family.
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        return synthetic_result(profile_id)

    def fail_setup_write(connection, cursor, statement, parameters, context, executemany):
        quota = "insert into rate_buckets" in statement.lower() and system.quota_key in parameters
        ledger = "insert into generation_attempts" in statement.lower()
        if (failure_stage == "quota" and quota) or (failure_stage == "ledger" and ledger):
            raise OperationalError(statement, parameters, RuntimeError("Synthetic setup write failure"))

    monkeypatch.setattr(adapter, "generate", generate)
    event.listen(system.app.state.db.engine, "before_cursor_execute", fail_setup_write)
    try:
        with pytest.raises(OperationalError, match="Synthetic setup write failure"):
            system.actor.post("/api/challenges", issue_body(adapter))
    finally:
        event.remove(system.app.state.db.engine, "before_cursor_execute", fail_setup_write)
    assert calls == []
    assert system.app.state.generation_limiter.snapshot()["inflight"] == 0
    with system.app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ProfileIncident)) == 0
    # A subsequent healthy request must not inherit a spurious cryptographic hold.
    assert system.actor.post("/api/challenges", issue_body(adapter)).status_code == 200
    assert calls == [issue_body(adapter)["profile_id"]]
