"""An unreleased task board stays unavailable without changing retained data."""
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import registry
from crucible.app import create_app
from crucible.bounty_models import BountyTask, BountyWallet
from test_beta_boundary import private_app


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    # Availability is independent of native worker implementation; exercise the
    # ordinary routes with the existing portable DH/RSA adapters only.
    monkeypatch.setattr(registry, "ADAPTERS", {k:v for k,v in registry.ADAPTERS.items()
                                             if k[0] in ("teaching-dh-kem", "rsa-kem")})
    monkeypatch.setattr(registry, "CURRENT", {"teaching-dh-kem":"7", "rsa-kem":"4"})


def test_default_release_hides_bounties_and_keeps_other_workflows(private_app):
    assert private_app.state.settings.bounties_enabled is False
    actor = private_app.test_login("participant")
    assert actor.client.get("/api/session").json()["capabilities"]["bounties"] is False
    html = actor.client.get("/").text
    assert 'data-bounties-enabled="false"' in html
    paths = ["/api/bounties", "/api/bounties/review-queue", "/api/me/bounty-allowance",
             "/api/bounties/" + str(uuid4()), "/notebook/bounties.js",
             "/notebook/bounties-api.js", "/notebook/bounties.css", "/api/agent/guidance/bounties.txt"]
    for path in paths:
        assert actor.client.get(path).status_code == 404, path
    assert actor.post("/api/bounties", {"idempotency_key": str(uuid4())}).status_code == 404
    assert actor.client.get("/api/notebook/notes").status_code == 200
    pipeline = actor.client.get("/api/agent/pipeline").json()
    assert pipeline["features"] == {"bounties": False}
    assert "bounties.txt" not in {r["id"] for r in pipeline["resources"]}
    assert "research_bounty" not in {r["task"] for r in pipeline["reading_plan"]}
    assert "agent-research-bounties" not in {r["id"] for r in pipeline["capabilities"]["current"]}
    assert "Research bounties" not in actor.client.get("/api/agent/guidance/agent-workflow.txt").text
    for resource in ["scoring-policy.txt", "scoring-base.txt"]:
        text = actor.client.get("/api/agent/guidance/" + resource).text
        assert "100 points" in text and "bounty" not in text.lower()
    with private_app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyTask)) == 0
        assert session.scalar(select(func.count()).select_from(BountyWallet)) == 0


def test_disabled_release_cannot_request_bounty_agent_permissions(private_app):
    with TestClient(private_app) as client:
        for scope in ["bounties:read", "bounties:write", "bounties:spend"]:
            response = client.post("/api/agent/connect/start", json={"label": "Test", "scopes": [scope]}, headers={"Origin": "http://testserver"})
            assert response.status_code == 400
            assert "not available" in response.json()["detail"]


def test_enabled_fixture_keeps_task_feature_explicit(private_app):
    app = create_app(replace(private_app.state.settings, bounties_enabled=True), initialize=False)
    try:
        with TestClient(app) as client:
            assert 'data-bounties-enabled="true"' in client.get("/").text
        assert any(getattr(route, "path", None) == "/api/bounties" for route in app.routes)
    finally:
        app.state.db.engine.dispose()


def test_pending_bounty_connection_cannot_be_issued_after_feature_is_disabled(private_app):
    actor = private_app.test_login("participant")
    enabled = create_app(replace(private_app.state.settings, bounties_enabled=True), clock=lambda:private_app.test_now[0])
    try:
        with TestClient(enabled) as client:
            started = client.post("/api/agent/connect/start", json={"label":"Before release change", "scopes":["bounties:read"]}, headers={"Origin":"http://testserver"})
            assert started.status_code == 200
            request = started.json()
        code = request["user_code"]
        assert actor.client.get("/api/agent/connect/" + code).status_code == 400
        assert actor.post("/api/agent/connect/" + code + "/approve", {}).status_code == 400
        assert actor.client.post("/api/agent/connect/poll", json={"device_code":request["device_code"]}, headers={"Origin":"http://testserver"}).status_code == 400
        assert actor.post("/api/agent/connect/" + code + "/deny", {}).status_code == 200
    finally:
        enabled.state.db.engine.dispose()


def test_guidance_feature_sections_are_explicit_and_complete():
    from crucible import agent_guidance
    from crucible.config import ROOT
    for name in ["agent-workflow.txt", "scoring-policy.txt", "scoring-base.txt"]:
        path = ROOT / agent_guidance.RESOURCES[name][0]
        raw = path.read_bytes()
        assert raw.count(b"<!-- feature:bounties -->") == raw.count(b"<!-- endfeature:bounties -->") == 1
        for enabled in [False, True]:
            body, _ = agent_guidance.guidance(ROOT, name, bounties_enabled=enabled)
            assert b"<!-- feature:" not in body and b"<!-- endfeature:" not in body
            assert (b"bounties" in body.lower()) is enabled
