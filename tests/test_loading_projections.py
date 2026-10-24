"""Small UI reads retain exact challenges, fresh access and full downloads."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from crucible import beta_access, dh_v4, registry
from crucible.db import Challenge
from test_beta_boundary import connect, private_app
from test_browser_admission import selects


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


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
    created = actor.post("/api/challenges?summary=true", {"profile_id": "dh-20"})
    assert created.status_code == 200, created.text
    summary = created.json()["challenge"]
    assert "public" not in summary and summary["projection"] == "summary"
    assert summary["shared_secret_bytes"] == 32
    with private_app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, summary["id"])
        public = {**stored.public_data, "large_public_fixture": "x" * 512000}
        stored.public_data = public
    full = actor.client.get("/api/challenges?profile_id=dh-20").json()["challenge"]
    with patch.object(dh_v4, "generate", side_effect=AssertionError("Existing instance must be reused")):
        with selects(private_app) as queries:
            read = actor.client.get("/api/challenges?profile_id=dh-20&summary=true")
        reused = actor.post("/api/challenges?summary=true", {"profile_id": "dh-20"})
    assert len(queries) == 3  # Fresh outer/handler access + joined instance/status.
    assert read.json()["challenge"] == reused.json()["challenge"] == summary
    assert full == {key: value for key, value in summary.items() if key not in {"projection", "shared_secret_bytes"}} | {"public": public}
    assert len(read.content) < 2000
    assert read.headers["cache-control"] == "no-store"
    download = actor.client.get(summary["download_url"]).json()
    assert download["challenge"]["public"] == public
    assert download["challenge"]["id"] == summary["id"]
    assert "projection" not in download["challenge"]


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
