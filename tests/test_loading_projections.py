"""Small UI reads retain exact challenges, fresh access and full downloads."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from crucible import beta_access, dh_v4, registry
import crucible.app as app_module
from crucible.db import Challenge
from test_beta_boundary import connect, private_app
from test_browser_admission import selects


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


@pytest.mark.parametrize("contents,expected", [
    (b"a" * 40, "a" * 40), (b"0123456789abcdef" * 2 + b"01234567\n", "0123456789abcdef" * 2 + "01234567"),
    (None, None), (b"", None), (b"a" * 39, None), (b"A" * 40, None),
    (b"g" * 40, None), (b"\xff" * 40, None), (b"a" * 40 + b"\r\n", None),
    (b"a" * 40 + b"\n\n", None), (b"a" * 40 + b"\n" + b"x" * 100000, None),
])
def test_packaged_revision_marker_is_optional_and_strict(tmp_path, monkeypatch, contents, expected):
    marker = tmp_path / "SOURCE_REVISION"
    if contents is not None:
        marker.write_bytes(contents)
    monkeypatch.setattr(app_module, "SOURCE_REVISION_FILE", marker)
    assert app_module.packaged_source_revision() == expected


def test_packaged_revision_reads_at_most_42_bytes(monkeypatch):
    class BoundedMarker:
        def open(self, mode):
            assert mode == "rb"
            return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == 42
            return b"a" * limit
    monkeypatch.setattr(app_module, "SOURCE_REVISION_FILE", BoundedMarker())
    assert app_module.packaged_source_revision() is None


@pytest.fixture
def revision_app(tmp_path, monkeypatch, request):
    marker = tmp_path / "SOURCE_REVISION"
    contents = getattr(request, "param", b"a" * 40 + b"\n")
    if contents is not None:
        marker.write_bytes(contents)
    monkeypatch.setattr(app_module, "SOURCE_REVISION_FILE", marker)
    # Configure the marker before constructing the existing isolated DB fixture.
    return request.getfixturevalue("private_app"), marker


@pytest.mark.parametrize("revision_app", [None, b"invalid"], indirect=True)
def test_health_omits_missing_or_invalid_revision(revision_app):
    app, _ = revision_app
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_revision_is_startup_snapshot_not_admission_or_readiness(revision_app, monkeypatch):
    app, marker = revision_app
    with TestClient(app) as client:
        expected = {"status": "ok", "source_revision": "a" * 40}
        response = client.get("/health")
        assert response.status_code == 200 and response.json() == expected
        assert response.headers["cache-control"] == "no-store"
        marker.write_bytes(b"b" * 40 + b"\n")
        assert client.get("/health").json() == expected
        marker.unlink()
        assert client.get("/health").json() == expected
        denied = client.get("/api/schemes?projection=map")
        assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
        engine = create_engine(app.state.db.engine.url, poolclass=QueuePool,
                               pool_size=1, max_overflow=0, pool_timeout=0.01,
                               connect_args={"check_same_thread": False})
        monkeypatch.setattr(app.state.db, "sessions", sessionmaker(engine, expire_on_commit=False))
        try:
            with engine.connect():
                busy = client.get("/health")
                assert busy.status_code == 503
                assert busy.json() == {"detail": "The service is busy. Please retry shortly."}
                assert busy.headers["cache-control"] == "no-store"
            assert client.get("/health").json() == expected
        finally:
            engine.dispose()


def test_map_links_need_no_profile_catalogue_or_progress_sql(private_app, monkeypatch):
    actor = private_app.test_login("participant")
    def unused(*args, **kwargs):
        pytest.fail("Map links must not build profile descriptions or query progress")
    for name in ("navigation_catalogue", "descriptor", "participant_counts"):
        monkeypatch.setattr(registry, name, unused)
    with selects(private_app) as queries:
        response = actor.client.get("/api/schemes?projection=map")
    assert response.status_code == 200
    assert response.json()["schemes"] == [{"id": dh_v4.SCHEME_ID, "version": dh_v4.SCHEME_VERSION}]
    assert response.json()["projection"] == "map"
    assert response.json()["catalogue_revision"] == registry.catalogue_revision()
    assert len(queries) == 1 and "FROM login_sessions" in queries[0]
    assert len(response.content) < 1000 and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("query", ["&progress_only=true", "&scheme_id=teaching-dh-kem", "&version=4"])
def test_map_projection_rejects_other_projection_selectors(private_app, query):
    actor = private_app.test_login("participant")
    assert actor.client.get("/api/schemes?projection=map" + query).status_code == 422


def test_challenge_summary_omits_large_inputs_and_full_download_remains_exact(private_app):
    actor = private_app.test_login("participant")
    with selects(private_app) as created_queries:
        created = actor.post("/api/challenges?summary=true", {"profile_id": "dh-20"})
    assert created.status_code == 200, created.text
    assert not any("challenges.public_data" in query for query in created_queries)
    summary = created.json()["challenge"]
    assert "public" not in summary and summary["projection"] == "summary"
    assert summary["shared_secret_bytes"] == 32
    with private_app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, summary["id"])
        public = {**stored.public_data, "large_public_fixture": "x" * 512000}
        stored.public_data = public
    with selects(private_app) as full_queries:
        full = actor.client.get("/api/challenges?profile_id=dh-20").json()["challenge"]
    with patch.object(dh_v4, "generate", side_effect=AssertionError("Existing instance must be reused")):
        with selects(private_app) as queries:
            read = actor.client.get("/api/challenges?profile_id=dh-20&summary=true")
        with selects(private_app) as reused_queries:
            reused = actor.post("/api/challenges?summary=true", {"profile_id": "dh-20"})
        with selects(private_app) as full_reuse_queries:
            full_reuse = actor.post("/api/challenges", {"profile_id": "dh-20"})
    assert len(queries) == 3  # Fresh outer/handler access + joined instance/status.
    assert len(reused_queries) == len(full_reuse_queries)
    for statements in (queries, reused_queries):
        assert any("challenges.manifest" in query for query in statements)
        assert not any("challenges.public_data" in query for query in statements)
    for statements in (full_queries, full_reuse_queries):
        assert any("challenges.public_data" in query for query in statements)
    assert read.json()["challenge"] == reused.json()["challenge"] == summary
    assert full_reuse.json()["challenge"] == full
    assert full == {key: value for key, value in summary.items() if key not in {"projection", "shared_secret_bytes"}} | {"public": public}
    assert len(read.content) < 2000
    assert read.headers["cache-control"] == "no-store"
    download = actor.client.get(summary["download_url"]).json()
    assert download["challenge"]["public"] == public
    assert download["challenge"]["id"] == summary["id"]
    assert "projection" not in download["challenge"]


@pytest.mark.parametrize("encoding", [{"shared_secret_bytes": 64}, {}, None, "invalid"])
def test_summary_uses_stored_manifest_not_public_encoding(private_app, encoding):
    actor = private_app.test_login("participant")
    challenge = actor.challenge()
    with private_app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, challenge["id"])
        stored.public_data = {**stored.public_data, "encoding": encoding}
    with selects(private_app) as queries:
        response = actor.client.get("/api/challenges?profile_id=dh-20&summary=true")
    assert response.status_code == 200
    assert response.json()["challenge"]["shared_secret_bytes"] == 32
    assert len(queries) == 3
    assert not any("challenges.public_data" in query for query in queries)
    assert actor.client.get(challenge["download_url"]).json()["challenge"]["public"]["encoding"] == encoding


@pytest.mark.parametrize("declared,expected", [
    (None, None), (False, None), (0, None), (-1, None), (4097, None),
    ("32", None), (32.0, None), (16, 16), (4096, 4096),
])
def test_summary_reports_unavailable_for_invalid_stored_length_without_fallback(private_app, declared, expected):
    actor = private_app.test_login("participant")
    challenge = actor.challenge()
    with private_app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, challenge["id"])
        manifest = dict(stored.manifest)
        if declared is None:
            manifest.pop("shared_secret_bytes")
        else:
            manifest["shared_secret_bytes"] = declared
        # An explicit UPDATE also persists JSON type changes such as 32 ->
        # 32.0, which Python dictionary equality otherwise treats as unchanged.
        session.execute(update(Challenge).where(Challenge.id == stored.id).values(manifest=manifest))
    with patch.object(dh_v4, "generate", side_effect=AssertionError("Existing instance must be reused")):
        with selects(private_app) as queries:
            read = actor.client.get("/api/challenges?profile_id=dh-20&summary=true")
            reused = actor.post("/api/challenges?summary=true", {"profile_id": "dh-20"})
    assert read.status_code == reused.status_code == 200
    assert read.json()["challenge"]["shared_secret_bytes"] == expected
    assert reused.json()["challenge"]["shared_secret_bytes"] == expected
    assert not any("challenges.public_data" in query for query in queries)
    # Keep old public inputs readable even when their declaration needs review.
    full = actor.client.get("/api/challenges?profile_id=dh-20").json()["challenge"]
    assert full["public"] == challenge["public"]


def test_small_reads_do_not_bypass_membership_or_create_challenges(private_app):
    actor = private_app.test_login("participant")
    paths = ["/api/schemes?projection=map", "/api/challenges?profile_id=dh-20&summary=true"]
    with patch.object(dh_v4, "generate", side_effect=AssertionError("GET never generates")):
        assert actor.client.get(paths[1]).json()["challenge"] is None
    with private_app.state.db.transaction() as session:
        assert session.scalar(select(Challenge.id)) is None
    with TestClient(private_app) as anonymous:
        assert all(anonymous.get(path).status_code == 401 for path in paths)
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, "revoked", private_app.test_now[0])
    assert all(actor.client.get(path).status_code in {401, 403} for path in paths)


def test_summary_tracks_submission_account_and_hour_without_regenerating(private_app):
    actor = private_app.test_login("participant")
    challenge = actor.challenge()
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', actor.answer(challenge))
    assert response.status_code == 200, response.text
    other = private_app.test_login("operator")
    with patch.object(dh_v4, "generate", side_effect=AssertionError("Status reads never generate")):
        own = actor.client.get("/api/challenges?profile_id=dh-20&summary=true").json()
        assert own["challenge"]["status"] == "solved"
        assert own["challenge"]["submission"] == response.json()["submission"]
        assert other.client.get("/api/challenges?profile_id=dh-20&summary=true").json()["challenge"] is None
        private_app.test_now[0] = challenge["ends_at"]
        current = actor.client.get("/api/challenges?profile_id=dh-20&summary=true").json()
    assert current["challenge"] is None and current["round_started"] is False
    assert current["round"]["id"] == challenge["round_id"] + 1


def test_pool_exhaustion_is_retryable_and_capacity_recovers(private_app, monkeypatch):
    actor = private_app.test_login("participant")
    connection = connect(actor)
    token = actor.client.post('/api/agent/connect/poll', json={"device_code": connection["device_code"]}).json()["access_token"]
    engine = create_engine(private_app.state.db.engine.url, poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=0.01,
                           connect_args={"check_same_thread": False})
    monkeypatch.setattr(private_app.state.db, "sessions", sessionmaker(engine, expire_on_commit=False))
    try:
        with engine.connect():
            # Exercises both outer member admission and errors inside handlers.
            for path in ("/api/schemes?projection=map", "/api/session", "/health"):
                response = actor.client.get(path)
                assert response.status_code == 503, response.text
                assert response.json() == {"detail": "The service is busy. Please retry shortly."}
                assert response.headers["retry-after"] == "2"
                assert response.headers["cache-control"] == "no-store"
                assert "sqlite" not in response.text and "QueuePool" not in response.text
            machine = actor.client.get("/api/schemes?projection=map", headers={"Authorization": "Bearer " + token})
            assert machine.status_code == 503 and machine.headers["retry-after"] == "2"
        assert actor.client.get("/api/schemes?projection=map").status_code == 200
        assert actor.client.get("/health").status_code == 200
    finally:
        engine.dispose()
