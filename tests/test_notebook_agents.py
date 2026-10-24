"""Opt-in notebook credentials preserve ownership, exact pins and human review."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from crucible import machine_auth, notebook
from crucible.app import create_app
from crucible.security import token_hash
from test_notebook import (system, content, create, revise, publication_body,
                           request_publication, approve, workspace, count, Actor)
from test_research_reviews import submit


def agent(app, actor, scopes=machine_auth.NOTEBOOK_SCOPES):
    # Use the real device-approval flow against isolated fixture accounts.
    client = TestClient(app)
    started = client.post("/api/agent/connect/start", json={"scopes": list(scopes)}).json()
    approved = actor.post(f'/api/agent/connect/{started["user_code"]}/approve', {})
    assert approved.status_code == 200, approved.text
    exchanged = client.post("/api/agent/connect/poll", json={"device_code": started["device_code"]}).json()
    assert exchanged["scopes"] == list(scopes)
    client.headers["Authorization"] = "Bearer " + exchanged["access_token"]
    return client


def test_default_and_pending_connections_do_not_gain_notebook_permissions(system, monkeypatch):
    app, owner, *_ = system
    client = TestClient(app)
    started = client.post("/api/agent/connect/start", json={}).json()
    assert started["scopes"] == list(machine_auth.DEFAULT_SCOPES)
    monkeypatch.setattr(machine_auth, "DEFAULT_SCOPES", machine_auth.SCOPES)
    preview = owner.client.get(f'/api/agent/connect/{started["user_code"]}').json()["connection"]
    assert preview["scopes"] == started["scopes"]
    assert owner.post(f'/api/agent/connect/{started["user_code"]}/approve', {}).status_code == 200
    result = client.post("/api/agent/connect/poll", json={"device_code": started["device_code"]}).json()
    assert result["scopes"] == started["scopes"]
    client.headers["Authorization"] = "Bearer " + result["access_token"]
    assert client.get("/api/notebook/export").status_code == 403


@pytest.mark.parametrize("scopes", [[], ["*"], ["notebook:read", "notebook:read"],
                                   ["notebook:review"], ["notebook:read", 1]])
def test_scope_requests_are_bounded_and_explicit(system, scopes):
    app, *_ = system
    assert TestClient(app).post("/api/agent/connect/start", json={"scopes": scopes}).status_code == 422


def test_owner_reads_do_not_inherit_reviewer_access_or_cookies(system):
    app, owner, reviewer, reader, _ = system
    foreign = create(owner)
    request_publication(owner, foreign)
    own = create(reviewer)
    client = agent(app, reviewer, ["notebook:read"])
    client.cookies.update(owner.client.cookies)
    for suffix in ("", "?revision=1"):
        assert client.get(f'/api/notebook/notes/{foreign["id"]}' + suffix).status_code == 404
    response = client.get(f'/api/notebook/notes/{own["id"]}').json()["note"]
    assert response["owner"]["handle"] == "reviewer"
    assert not response["can_review"] and not response["can_moderate"] and not response["can_self_review"]
    page = client.get("/api/notebook/notes").json()
    assert [note["id"] for note in page["notes"]] == [own["id"]]
    assert not any(page["capabilities"].values())
    assert client.get("/api/notebook/notes?include_curated=true").status_code == 422
    assert client.get("/api/notebook/notes?scope=human").status_code == 422
    assert [note["id"] for note in client.get("/api/notebook/export").json()["notes"]] == [own["id"]]
    assert client.get("/api/notebook/export?note_id=" + foreign["id"]).status_code == 404
    assert client.get("/api/notebook/review-queue").status_code == 403
    assert client.post("/api/notebook/notes", json={"content": content(), "idempotency_key": str(uuid4())}).status_code == 403


def test_writes_and_publication_are_separate_scopes_with_exact_retries(system):
    app, owner, reviewer, reader, _ = system
    writer = agent(app, owner, ["notebook:write"])
    publisher = agent(app, owner, ["notebook:publish"])
    payload = {"content": content(), "idempotency_key": str(uuid4())}
    first = writer.post("/api/notebook/notes", json=payload).json()["note"]
    assert first["publication_status"] == "private"
    assert writer.post("/api/notebook/notes", json=payload).json()["note"]["id"] == first["id"]
    assert writer.get("/api/notebook/export").status_code == 403
    publication = publication_body(first)
    path = f'/api/notebook/notes/{first["id"]}/publication-requests'
    assert writer.post(path, json=publication).status_code == 403
    stale = {**publication, "content_sha256": "0" * 64}
    assert publisher.post(path, json=stale).status_code == 409
    for _ in range(2):
        result = publisher.post(path, json=publication)
        assert result.status_code == 200, result.text
        assert result.json()["note"]["publication_status"] == "submitted"
    assert count(app, notebook.NotebookPublication) == 1
    assert reader.client.get(f'/api/notebook/notes/{first["id"]}').status_code == 404
    reviewer_agent = agent(app, reviewer)
    for suffix in ("publication-reviews", "publication-withdrawals", "proposal-submissions"):
        assert reviewer_agent.post(f'/api/notebook/notes/{first["id"]}/' + suffix,
                                   json=publication).status_code == 403
    second = revise(owner, first)
    assert publisher.post(path, json=publication_body(first)).status_code == 409
    assert publisher.post(path, json=publication).json()["note"]["revision"] == 1
    assert second["revision"] == 2


def test_agent_import_preserves_history_ownership_and_atomicity(system):
    app, owner, reviewer, *_ = system
    client = agent(app, owner)
    value = workspace()
    payload = {"workspace": value, "idempotency_key": str(uuid4())}
    imported = client.post("/api/notebook/import", json=payload).json()
    identifier = imported["id_map"]["local-note"]
    assert client.post("/api/notebook/import", json=payload).json()["replayed"]
    original = client.get("/api/notebook/imports/" + imported["import_id"]).json()
    assert original["workspace"] == value
    assert agent(app, reviewer).get("/api/notebook/imports/" + imported["import_id"]).status_code == 404
    revised = workspace(revisions=2)
    assert client.post("/api/notebook/import", json={"workspace": revised, "idempotency_key": str(uuid4())}).status_code == 200
    broken = deepcopy(revised)
    broken["notes"][0]["revisions"][0]["body"] = "Rewritten history"
    broken["notes"].insert(0, workspace("would-be-new")["notes"][0])
    assert client.post("/api/notebook/import", json={"workspace": broken, "idempotency_key": str(uuid4())}).status_code == 409
    assert count(app, notebook.NotebookNote) == 1
    assert client.get(f"/api/notebook/notes/{identifier}").json()["note"]["revision"] == 2
    foreign = create(reviewer)
    bad = workspace(foreign["id"])
    assert client.post("/api/notebook/import", json={"workspace": bad, "idempotency_key": str(uuid4())}).status_code == 409


def test_revoked_notebook_token_cannot_borrow_active_browser_session(system):
    app, owner, *_ = system
    client = agent(app, owner)
    client.cookies.update(owner.client.cookies)
    raw = client.headers["Authorization"].removeprefix("Bearer ")
    with app.state.db.transaction(write=True) as session:
        token = session.scalar(select(machine_auth.MachineToken).where(
            machine_auth.MachineToken.token_hash == token_hash(raw)))
        token.revoked_at = app.state.clock()
    assert client.get("/api/notebook/export").status_code == 401
    assert client.post("/api/notebook/notes", json={"content": content(), "idempotency_key": str(uuid4())}).status_code == 401
    assert count(app, notebook.NotebookNote) == 0


def test_reviewer_agent_cannot_discover_foreign_private_requests_on_its_own_note(system):
    original, owner, reviewer, reader, now = system
    settings = replace(original.state.settings, reviewer_ids=(owner.identifier, reviewer.identifier))
    app = create_app(settings, clock=lambda: now[0])
    try:
        author = Actor(app, reviewer.identifier)
        moderator = Actor(app, owner.identifier)
        other = Actor(app, reader.identifier)
        note = create(author)
        request_publication(author, note)
        approve(moderator, note)
        private = submit(other, note, purpose="procedure")
        path = "/api/notebook/notes/" + note["id"]
        # Human reviewer sees the packet label, but their notebook token must
        # not discover it through detail, shared summaries or proposal filters.
        assert private["id"] in author.client.get(path).text
        client = agent(app, author)
        assert private["id"] not in client.get(path).text
        page = client.get("/api/notebook/notes").json()
        assert private["id"] not in str(page)
        assert page["notes"][0]["proposal_revision"] is None
        assert client.get("/api/notebook/notes?proposals_only=true").json()["notes"] == []
    finally:
        app.state.db.engine.dispose()
