"""Device handoff never broadens cookie authority or exposes stored credentials."""
import hashlib
import json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import machine_auth, registry
from crucible.app import create_app
from crucible.db import Challenge, Submission
from crucible.machine_auth import DeviceConnection, MachineToken
from crucible.security import token_hash
from scripts.solve_teaching_frontier import recover
from test_api import Actor
from test_calibrated_api import current_system


def start(app, label="Test agent"):
    response = TestClient(app).post("/api/agent/connect/start", json={"label": label})
    assert response.status_code == 200, response.text
    return response.json()


def poll(app, connection):
    return TestClient(app).post("/api/agent/connect/poll", json={"device_code": connection["device_code"]})


def connected(app, handle="alice"):
    browser = Actor(app)
    browser.register(handle)
    connection = start(app)
    response = browser.post(f'/api/agent/connect/{connection["user_code"]}/approve', {})
    assert response.status_code == 200, response.text
    authorization = poll(app, connection)
    assert authorization.status_code == 200, authorization.text
    credential = authorization.json()
    assert credential["status"] == "authorized"
    agent = TestClient(app, headers={"Authorization": f'Bearer {credential["access_token"]}'})
    return browser, agent, connection, credential


def test_device_handoff_needs_explicit_browser_approval_and_persists_only_hashes(current_system):
    app, clock, _ = current_system
    browser = Actor(app)
    browser.register("alice")
    connection = start(app, "Local solver")
    assert len(connection["device_code"]) == 47
    assert connection["expires_at"] == clock[0] + 600
    assert connection["verification_uri"] == f'http://testserver/#connect?code={connection["user_code"]}'
    assert poll(app, connection).json()["status"] == "pending"
    assert poll(app, connection).status_code == 429
    url = f'/api/agent/connect/{connection["user_code"]}'
    assert TestClient(app).get(url).status_code == 401
    preview = browser.client.get(url.lower()).json()["connection"]
    assert preview["status"] == "pending" and preview["label"] == "Local solver"
    assert preview["scopes"] == list(machine_auth.DEFAULT_SCOPES)
    assert "device_code" not in preview
    approved = browser.post(url + "/approve", {})
    assert approved.status_code == 200 and approved.json()["connection"]["status"] == "approved"
    credential = poll(app, connection).json()
    assert credential["status"] == "authorized" and credential["user"] == {"handle": "alice"}
    assert credential["expires_at"] == clock[0] + machine_auth.TOKEN_SECONDS
    assert poll(app, connection).json() == credential
    assert credential["access_token"] not in approved.text
    token_list = browser.client.get("/api/agent/tokens")
    assert credential["access_token"] not in token_list.text and connection["device_code"] not in token_list.text
    with app.state.db.transaction() as session:
        token = session.scalar(select(MachineToken))
        flow = session.scalar(select(DeviceConnection))
        assert token.token_hash == token_hash(credential["access_token"])
        assert flow.device_hash == token_hash(connection["device_code"])
        for model in (token, flow):
            persisted = repr({column.name: getattr(model, column.name) for column in model.__table__.columns})
            assert credential["access_token"] not in persisted and connection["device_code"] not in persisted
        assert session.scalar(select(func.count()).select_from(MachineToken)) == 1


def test_verification_uri_preserves_an_explicitly_allowed_secondary_origin(current_system):
    app, clock, settings = current_system
    configured = replace(settings, origins=("http://testserver", "http://localhost:8000"))
    alternate = create_app(configured, clock=lambda: clock[0])
    try:
        response = TestClient(alternate, base_url="http://localhost:8000").post(
            "/api/agent/connect/start", json={})
        assert response.status_code == 200
        assert response.json()["verification_uri"].startswith("http://localhost:8000/#connect?")
        # A hostname may be trusted for serving, but arbitrary ports must not be
        # reflected into the login destination without an exact configured origin.
        fallback = TestClient(alternate, base_url="http://localhost:9999").post(
            "/api/agent/connect/start", json={})
        assert fallback.json()["verification_uri"].startswith("http://testserver/#connect?")
        assert TestClient(alternate).post("/api/agent/connect/start", json={},
                                         headers={"Host": "attacker.example"}).status_code == 400
    finally:
        alternate.state.db.engine.dispose()


def test_active_tokens_remain_visible_ahead_of_revoked_tokens_with_pagination(current_system):
    app, clock, _ = current_system
    browser, _, _, credential = connected(app)
    with app.state.db.transaction(write=True) as session:
        owner = session.get(MachineToken, credential["token"]["id"]).user_id
        for index in range(101):
            session.add(MachineToken(id=str(uuid4()), user_id=owner, token_hash=f"{index:064x}",
                                     label=f"Old revoked agent {index}", scopes=list(machine_auth.SCOPES),
                                     created_at=clock[0]+index+1, expires_at=clock[0]+9999,
                                     revoked_at=clock[0]))
    response = browser.client.get("/api/agent/tokens").json()
    assert len(response["tokens"]) == 100 and response["pagination"]["has_more"]
    assert response["tokens"][0]["id"] == credential["token"]["id"]
    last = browser.client.get("/api/agent/tokens?offset=100").json()
    assert len(last["tokens"]) == 2 and not last["pagination"]["has_more"]
    assert browser.client.get("/api/agent/tokens?limit=101").status_code == 422


def test_agent_can_fetch_solve_and_submit_without_cookies_origin_or_csrf(current_system):
    app, clock, _ = current_system
    _, agent, _, credential = connected(app)
    assert not agent.cookies
    assert agent.get("/api/schemes").status_code == 200
    method = agent.post("/api/methods", json={"name": "Agent BSGS", "pseudocode": "Recover x with BSGS; derive transcript KDF."})
    assert method.status_code == 200, method.text
    method_id = method.json()["method"]["id"]
    source_url = f"/api/methods/{method_id}/source"
    source = {"format": "crucible-source-v1", "files": [{"path": "solve.py", "content": "#" + "x"*70000}]}
    attached = agent.post(source_url, json=source)
    assert attached.status_code == 200, attached.text
    assert agent.get(source_url).json() == source
    assert agent.get(source_url+"/download").status_code == 200
    assert TestClient(app).get(source_url).status_code == 404
    challenge = agent.post("/api/challenges", json={"scheme_id": "teaching-dh-kem", "instance_size": 1})
    assert challenge.status_code == 200, challenge.text
    data = challenge.json()["challenge"]
    public = agent.get(data["download_url"])
    assert public.status_code == 200
    clock[0] += 5
    answer = {"candidate": recover(public.json()), "method_id": method.json()["method"]["id"],
              "compute": {"hardware": "Test CPU", "workers": 1, "wall_seconds": 1,
                          "precomputation": "None", "peak_memory": "Not measured", "notes": "Agent fixture"},
              "idempotency_key": str(uuid4())}
    response = agent.post(f'/api/challenges/{data["id"]}/submit', json=answer)
    assert response.status_code == 200 and response.json()["submission"]["status"] == "correct"
    receipt = agent.get('/api/submissions/' + response.json()["submission"]["id"])
    assert receipt.status_code == 200
    assert receipt.json()["submission"]["method"]["source"] == attached.json()["source"]
    assert TestClient(app).get(source_url).status_code == 200
    assert answer["candidate"] not in receipt.text and credential["access_token"] not in receipt.text
    history = agent.get("/api/me/submissions")
    assert history.status_code == 200 and len(history.json()["submissions"]) == 1


def test_bearer_identity_wins_over_cookie_and_bad_bearer_never_falls_back(current_system):
    app, _, _ = current_system
    alice_browser, alice, _, _ = connected(app, "alice")
    _, bob, _, bob_token = connected(app, "bob")
    alice.post("/api/methods", json={"name": "Alice only", "pseudocode": "Alice method"})
    bob.post("/api/methods", json={"name": "Bob only", "pseudocode": "Bob method"})
    mixed = TestClient(app, cookies=alice_browser.client.cookies,
                       headers={"Authorization": f'Bearer {bob_token["access_token"]}'})
    assert [row["name"] for row in mixed.get("/api/methods").json()["methods"]] == ["Bob only"]
    for header in ("Bearer cru_" + "x"*43, "Bearer invalid", "Basic anything", "Bearer ", ""):
        response = alice_browser.client.get("/api/methods", headers={"Authorization": header})
        assert response.status_code == 401
        assert alice_browser.client.get("/api/schemes", headers={"Authorization": header}).status_code == 401


def test_challenges_and_methods_remain_owned_with_agent_credentials(current_system):
    app, _, _ = current_system
    _, alice, _, _ = connected(app, "alice")
    _, bob, _, _ = connected(app, "bob")
    challenge = alice.post("/api/challenges", json={"scheme_id": "teaching-dh-kem", "instance_size": 1}).json()["challenge"]
    assert bob.get(challenge["download_url"]).status_code == 404
    method = alice.post("/api/methods", json={"name": "Private method", "pseudocode": "Only Alice"}).json()["method"]
    assert bob.get("/api/methods").json()["methods"] == []
    body = {"candidate": recover(challenge["public"]), "method_id": method["id"],
            "compute": {"hardware": "CPU", "workers": 1, "wall_seconds": 0,
                        "precomputation": "None", "peak_memory": "Unknown", "notes": ""},
            "idempotency_key": str(uuid4())}
    assert bob.post(f'/api/challenges/{challenge["id"]}/submit', json=body).status_code == 404


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/session"), ("POST", "/api/auth/logout"), ("POST", "/api/auth/login"),
    ("POST", "/api/auth/register"), ("GET", "/api/agent/tokens"),
    ("POST", "/api/agent/connect/start"), ("POST", "/api/agent/connect/ABCD-EFGH/approve"),
    ("GET", "/api/protocol/proposals"), ("GET", "/api/protocol/review-queue"),
    ("POST", "/api/protocol/proposals/00000000-0000-4000-8000-000000000000/reviews"),
])
def test_agent_cannot_reuse_cookies_for_account_or_review_actions(current_system, method, path):
    app, _, _ = current_system
    browser, agent, _, _ = connected(app)
    agent.cookies.update(browser.client.cookies)
    response = agent.request(method, path, json={} if method == "POST" else None,
                             headers={"Origin": "http://testserver", "X-CSRF-Token": browser.csrf})
    assert response.status_code == 403


def test_stored_scope_limits_are_enforced(current_system):
    app, _, _ = current_system
    _, agent, _, credential = connected(app)
    with app.state.db.transaction(write=True) as session:
        token = session.get(MachineToken, credential["token"]["id"])
        token.scopes = ["challenges:read"]
    assert agent.get("/api/schemes").status_code == 200
    assert agent.post("/api/challenges", json={"scheme_id": "teaching-dh-kem", "instance_size": 1}).status_code == 403
    assert agent.get("/api/methods").status_code == 403
    assert agent.get("/api/me/submissions").status_code == 403


def test_cookie_mutations_still_require_origin_and_csrf(current_system):
    app, _, _ = current_system
    browser = Actor(app)
    browser.register("alice")
    connection = start(app)
    approve = f'/api/agent/connect/{connection["user_code"]}/approve'
    assert browser.client.post(approve, json={}).status_code == 403
    assert browser.client.post(approve, json={}, headers={"Origin": "http://testserver"}).status_code == 403
    assert browser.client.post(approve, json={}, headers={"Origin": "http://elsewhere", "X-CSRF-Token": browser.csrf}).status_code == 403
    method = {"name": "Cookie method", "pseudocode": "Text"}
    assert browser.client.post("/api/methods", json=method).status_code == 403
    assert browser.client.post("/api/methods", json=method, headers={"Origin": "http://testserver"}).status_code == 403
    assert browser.post(approve, {}).status_code == 200


def test_revoke_is_owned_idempotent_and_invalidates_exchange_retries(current_system):
    app, _, _ = current_system
    alice, agent, connection, credential = connected(app)
    bob = Actor(app)
    bob.register("bob")
    url = f'/api/agent/tokens/{credential["token"]["id"]}/revoke'
    assert bob.client.get("/api/agent/tokens").json()["tokens"] == []
    assert bob.post(url, {}).status_code == 404
    assert alice.client.post(url, json={}, headers={"Origin": "http://testserver"}).status_code == 403
    revoked = alice.post(url, {})
    assert revoked.status_code == 200 and not revoked.json()["token"]["active"]
    assert alice.post(url, {}).json() == revoked.json()
    assert agent.get("/api/methods").status_code == 401
    assert poll(app, connection).json()["status"] == "denied"
    assert len(alice.client.get("/api/agent/tokens").json()["tokens"]) == 1


def test_exchange_window_expires_without_invalidating_delivered_token(current_system):
    app, clock, _ = current_system
    _, agent, connection, credential = connected(app)
    clock[0] += machine_auth.EXCHANGE_SECONDS
    assert poll(app, connection).json()["status"] == "expired"
    assert agent.get("/api/methods").status_code == 200
    clock[0] = credential["expires_at"]
    assert agent.get("/api/methods").status_code == 401


def test_cleanup_preserves_active_handoffs_accounts_and_delivered_credentials(current_system):
    app, clock, _ = current_system
    from crucible.manage import cleanup_expired
    browser, agent, exchanged, credential = connected(app)
    late_approval = start(app, "Approved near deadline")
    stale = start(app, "Expired pending")
    clock[0] += machine_auth.DEVICE_SECONDS - 1
    browser.post(f'/api/agent/connect/{late_approval["user_code"]}/approve', {})
    fresh = start(app, "Still pending")
    clock[0] += 2
    cleanup_expired(app.state.db, now=clock[0])
    with app.state.db.transaction() as session:
        remaining = set(session.scalars(select(DeviceConnection.user_code)))
        assert remaining == {late_approval["user_code"], fresh["user_code"]}
        assert session.get(MachineToken, credential["token"]["id"]) is not None
    assert poll(app, exchanged).json()["status"] == "expired"
    assert poll(app, stale).json()["status"] == "expired"
    assert poll(app, late_approval).json()["status"] == "authorized"
    assert agent.get("/api/methods").status_code == 200
    assert browser.client.get("/api/session").json()["user"]["handle"] == "alice"


def test_expired_or_denied_device_request_never_yields_a_credential(current_system):
    app, clock, _ = current_system
    browser = Actor(app)
    browser.register("alice")
    connection = start(app)
    clock[0] += machine_auth.DEVICE_SECONDS
    assert poll(app, connection).json()["status"] == "expired"
    assert browser.post(f'/api/agent/connect/{connection["user_code"]}/approve', {}).status_code == 410
    denied = start(app)
    assert browser.post(f'/api/agent/connect/{denied["user_code"]}/deny', {}).json()["connection"]["status"] == "denied"
    assert poll(app, denied).json()["status"] == "denied"
    assert browser.post(f'/api/agent/connect/{denied["user_code"]}/approve', {}).status_code == 410
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(MachineToken)) == 0


def test_device_approval_cannot_be_transferred_to_another_account(current_system):
    app, _, _ = current_system
    alice, agent, connection, _ = connected(app)
    bob = Actor(app)
    bob.register("bob")
    url = f'/api/agent/connect/{connection["user_code"]}'
    assert bob.client.get(url).status_code == 404
    assert bob.post(url+"/approve", {}).status_code == 404
    assert bob.post(url+"/deny", {}).status_code == 404
    assert alice.post(url+"/approve", {}).status_code == 200
    assert agent.get("/api/methods").status_code == 200


def test_malformed_payloads_and_duplicate_authorization_headers_are_safe(current_system):
    app, _, _ = current_system
    _, agent, connection, credential = connected(app)
    invalid = TestClient(app).post("/api/agent/connect/poll", json={"device_code": "private-malformed-device-secret"})
    assert invalid.status_code == 422 and "private-malformed-device-secret" not in invalid.text
    assert TestClient(app).post("/api/agent/connect/start", json={"label": "Agent", "scopes": ["review:write"]}).status_code == 422
    assert TestClient(app).post("/api/agent/connect/start", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    response = TestClient(app).get("/api/methods", headers=[("Authorization", "Bearer "+credential["access_token"]),
                                                          ("Authorization", "Bearer invalid")])
    assert response.status_code == 401
    too_large = agent.post("/api/methods", json={"name": "large", "pseudocode": "x"*65536})
    assert too_large.status_code == 413
    assert agent.post("/api/agent/connect/start", json={}).status_code == 403


def test_anonymous_device_start_is_rate_limited(current_system):
    _, clock, settings = current_system
    limited = create_app(replace(settings, rate_limits=True), clock=lambda: clock[0])
    try:
        client = TestClient(limited)
        for _ in range(10):
            assert client.post("/api/agent/connect/start", json={}).status_code == 200
        response = client.post("/api/agent/connect/start", json={})
        assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
        clock[0] += 600
        assert client.post("/api/agent/connect/start", json={}).status_code == 200
    finally:
        limited.state.db.engine.dispose()


def test_revocation_during_generation_prevents_final_challenge_issue(current_system, monkeypatch):
    app, _, _ = current_system
    browser, agent, _, credential = connected(app)
    adapter = registry.adapter_for("teaching-dh-kem")
    generate = adapter.generate
    entered, release = Event(), Event()
    def paused(profile_id):
        entered.set()
        assert release.wait(10)
        return generate(profile_id)
    monkeypatch.setattr(adapter, "generate", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(agent.post, "/api/challenges", json={"scheme_id": "teaching-dh-kem", "instance_size": 1})
        assert entered.wait(10)
        try:
            response = browser.post(f'/api/agent/tokens/{credential["token"]["id"]}/revoke', {})
            assert response.status_code == 200
        finally:
            release.set()
        assert future.result(10).status_code == 401
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0


def test_agent_submission_still_obeys_the_shared_deadline(current_system):
    app, clock, _ = current_system
    _, agent, _, _ = connected(app)
    challenge = agent.post("/api/challenges", json={"scheme_id": "teaching-dh-kem", "instance_size": 1}).json()["challenge"]
    method = agent.post("/api/methods", json={"name": "Test", "pseudocode": "BSGS"}).json()["method"]
    clock[0] = challenge["ends_at"]
    body = {"candidate": recover(challenge["public"]), "method_id": method["id"],
            "compute": {"hardware": "CPU", "workers": 1, "wall_seconds": 1,
                        "precomputation": "None", "peak_memory": "Unknown", "notes": ""},
            "idempotency_key": str(uuid4())}
    response = agent.post(f'/api/challenges/{challenge["id"]}/submit', json=body)
    assert response.status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Submission)) == 0


def test_bootstrap_download_is_exact_first_party_client(current_system):
    app, _, _ = current_system
    from crucible.config import ROOT
    response = TestClient(app).get("/api/agent/client")
    assert response.status_code == 200
    assert response.content == (ROOT / "scripts" / "crucible_cli.py").read_bytes()
    assert response.headers["x-content-sha256"] == hashlib.sha256(response.content).hexdigest()
    assert "crucible_cli.py" in response.headers["content-disposition"]
