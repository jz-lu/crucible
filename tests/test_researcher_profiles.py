"""Self-declared profile metadata never changes identity, scores or permissions."""
from concurrent.futures import ThreadPoolExecutor
import importlib
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select, update

from crucible import beta_access as beta, dh_v4, registry
from crucible.db import LoginSession, User
from crucible.researcher_profiles import ResearcherProfile
from test_beta_boundary import connect, private_app


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    # Exercise real application admission with an existing pure-Python adapter;
    # this profile-only suite neither runs nor bypasses native artifact checks.
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


def payload(**values):
    return {"display_name": "Ada Example", "affiliation": "Independent", "position": "Researcher",
            "website": "https://example.org/research", "expected_revision": 0,
            "idempotency_key": str(uuid4()), **values}


def put(actor, body):
    return actor.client.put("/api/me/profile", json=body,
                            headers={"Origin": "http://testserver", "X-CSRF-Token": actor.csrf})


def count(app):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(ResearcherProfile))


def test_absent_profile_is_optional_without_creating_rows(private_app):
    actor = private_app.test_login("participant")
    response = actor.client.get("/api/me/profile")
    assert response.status_code == 200
    expected = {"display_name": "", "affiliation": "", "position": "", "website": "", "revision": 0, "updated_at": None}
    assert response.json()["profile"] == expected
    assert response.headers["cache-control"] == "no-store"
    detail = actor.client.get("/api/people/participant?include_milestones=false").json()["person"]
    assert detail["researcher_profile"] == expected
    assert count(private_app) == 0


def test_profile_is_detail_only_member_visible_and_never_changes_handles_or_scores(private_app):
    actor = private_app.test_login("participant")
    before = actor.client.get("/api/people/participant?include_milestones=false").json()["person"]
    result = put(actor, payload(display_name="  Ada α <Example>  ", affiliation="  Independent  "))
    assert result.status_code == 200, result.text
    saved = result.json()["profile"]
    assert saved == {"display_name": "Ada α <Example>", "affiliation": "Independent", "position": "Researcher",
                     "website": "https://example.org/research", "revision": 1, "updated_at": private_app.test_now[0]}
    other = private_app.test_login("operator")
    detail = other.client.get("/api/people/PARTICIPANT?include_milestones=false").json()["person"]
    assert detail.pop("researcher_profile") == saved
    before.pop("researcher_profile")
    assert detail == before
    for compact in (False, True):
        rows = other.client.get("/api/people", params={"include_records": str(not compact).lower()}).json()["people"]
        assert all("researcher_profile" not in row for row in rows)
    assert other.client.get("/api/me/profile").json()["profile"]["revision"] == 0
    assert set(saved) == {"display_name", "affiliation", "position", "website", "revision", "updated_at"}
    with private_app.state.db.transaction() as session:
        assert session.get(User, private_app.test_member).handle == "participant"


def test_last_request_retry_is_exact_and_stale_revisions_never_overwrite(private_app):
    actor = private_app.test_login("participant")
    first = payload()
    saved = put(actor, first).json()["profile"]
    private_app.test_now[0] += 10
    assert put(actor, first).json()["profile"] == saved
    mismatch = put(actor, {**first, "affiliation": "Changed under old key"})
    assert mismatch.status_code == 409 and mismatch.json()["code"] == "profile_idempotency_conflict"
    stale = put(actor, payload())
    assert stale.status_code == 409 and stale.json()["code"] == "profile_revision_conflict"
    cleared = put(actor, payload(display_name="", affiliation="", position="", website="", expected_revision=1))
    assert cleared.status_code == 200 and cleared.json()["profile"]["revision"] == 2
    assert all(cleared.json()["profile"][field] == "" for field in ("display_name", "affiliation", "position", "website"))
    assert put(actor, first).status_code == 409
    assert actor.client.get("/api/me/profile").json()["profile"] == cleared.json()["profile"]
    assert count(private_app) == 1


def test_two_same_revision_saves_serialize_without_lost_update(private_app):
    actors = [private_app.test_login("participant") for _ in range(2)]
    bodies = [payload(affiliation="First independent draft"), payload(affiliation="Second independent draft")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda item: put(*item), zip(actors, bodies)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["profile"] for response in responses if response.status_code == 200)
    assert winner["revision"] == 1
    assert actors[0].client.get("/api/me/profile").json()["profile"] == winner


@pytest.mark.parametrize("changes", [
    {"display_name": "x" * 101}, {"affiliation": "x" * 201}, {"position": "x" * 151}, {"website": "x" * 2049},
    {"display_name": "Ada\nAnother line"}, {"affiliation": "\x00"}, {"position": "a\trole"}, {"display_name": None},
    {"website": "javascript:alert(1)"}, {"website": "//example.org"}, {"website": "https://user:pass@example.org"},
    {"website": "https://example.org\\@evil.org"}, {"website": "https://example.org/a b"},
    {"website": "https://example.org:99999"}, {"website": "https://%65xample.org"},
    {"expected_revision": True}, {"expected_revision": -1}, {"expected_revision": 0.0},
    {"idempotency_key": "not-a-uuid"}, {"orcid": "0000-0000-0000-0000"}, {"user_id": "someone-else"},
])
def test_invalid_or_unrecognized_fields_are_rejected_without_echoing_values(private_app, changes):
    actor = private_app.test_login("participant")
    response = put(actor, payload(**changes))
    assert response.status_code == 422
    assert set(response.json()) == {"detail"}
    assert "Check the format" in response.json()["detail"]
    assert count(private_app) == 0


@pytest.mark.parametrize("website", ["", "http://example.org", "https://例え.テスト/path?q=x#part", "https://[::1]:443/", "https://example.org/a%20b"])
def test_supported_websites_are_stored_without_server_fetches(private_app, website):
    actor = private_app.test_login("participant")
    response = put(actor, payload(website=website))
    assert response.status_code == 200 and response.json()["profile"]["website"] == website


def test_browser_session_csrf_origin_and_agent_boundaries(private_app):
    with TestClient(private_app) as visitor:
        assert visitor.get("/api/me/profile").status_code == 401
        assert visitor.put("/api/me/profile", json=payload()).status_code == 401
        assert visitor.get("/api/people/participant").status_code == 401
    actor = private_app.test_login("participant")
    assert actor.client.put("/api/me/profile", json=payload(), headers={"Origin": "http://testserver"}).status_code == 403
    assert actor.client.put("/api/me/profile", json=payload(), headers={"Origin": "https://elsewhere.example", "X-CSRF-Token": actor.csrf}).status_code == 403
    connection = connect(actor)
    token = actor.client.post("/api/agent/connect/poll", json={"device_code": connection["device_code"]}).json()["access_token"]
    headers = {"Authorization": "Bearer " + token, "Origin": "http://testserver", "X-CSRF-Token": actor.csrf}
    assert actor.client.get("/api/me/profile", headers=headers).status_code == 403
    assert actor.client.put("/api/me/profile", json=payload(), headers=headers).status_code == 403
    assert count(private_app) == 0


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("change", ["expire", "suspend", "remove_membership"])
def test_access_is_rechecked_after_outer_admission(private_app, monkeypatch, method, change):
    actor = private_app.test_login("participant")
    module = importlib.import_module("crucible.app")
    original = module.run_in_threadpool
    def change_access():
        with private_app.state.db.transaction(write=True) as session:
            if change == "expire":
                session.execute(update(LoginSession).where(LoginSession.user_id == private_app.test_member).values(expires_at=private_app.test_now[0]))
            elif change == "suspend":
                session.add(beta.AccountState(user_id=private_app.test_member, suspended_at=private_app.test_now[0]))
            else:
                session.execute(delete(beta.BetaMembership).where(beta.BetaMembership.user_id == private_app.test_member))
    async def after_admission(function, *args, **kwargs):
        result = await original(function, *args, **kwargs)
        if function.__name__ == "admit_browser":
            await original(change_access)
        return result
    monkeypatch.setattr(module, "run_in_threadpool", after_admission)
    response = put(actor, payload()) if method == "PUT" else actor.client.get("/api/me/profile")
    assert response.status_code == (401 if change == "expire" else 403)
    assert count(private_app) == 0
