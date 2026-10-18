"""Participant proposal automation never grants private review authority."""
import base64
from copy import deepcopy
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, select, text

from crucible import machine_auth
from crucible.app import create_app
from crucible.config import ROOT, Settings
from crucible.db import Database, User
from crucible.machine_auth import DeviceConnection, MachineToken
from crucible.protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview
from crucible.scoring_awards import SchemeAdmissionCredit
from crucible.security import token_hash
from test_machine_auth import start, poll
from test_protocol_proposals import (
    Actor, ORIGIN, SUBMIT, VALIDATE, count, proposal_system, review_system,
    review_body, revision_body, specification, submit,
)


def connect(app, browser):
    connection = start(app, "Proposal agent")
    response = browser.post(f'/api/agent/connect/{connection["user_code"]}/approve', {})
    assert response.status_code == 200, response.text
    credential = poll(app, connection).json()
    assert credential["status"] == "authorized"
    agent = TestClient(app, headers={"Authorization": f'Bearer {credential["access_token"]}'})
    return agent, credential


def test_new_connection_can_validate_submit_revise_and_replay_its_private_proposal(proposal_system):
    app, _, _ = proposal_system
    browser = Actor(app, "author")
    agent, credential = connect(app, browser)
    assert credential["scopes"] == list(machine_auth.SCOPES)
    assert set(credential["scopes"]) - set(machine_auth.LEGACY_SCOPES) == {
        "proposals:read", "proposals:write", "notes:read", "notes:rate",
    }
    assert agent.get("/api/protocol/spec-schema").status_code == 200
    template = agent.get("/api/protocol/spec-template")
    assert template.status_code == 200
    invalid = agent.post(VALIDATE, json={"specification": template.json()})
    assert invalid.status_code == 422 and invalid.json()["errors"]
    body = {"specification": specification(), "idempotency_key": str(uuid4())}
    valid = agent.post(VALIDATE, json={"specification": body["specification"]})
    assert valid.status_code == 200 and valid.json()["validation"]["status"] == "format-valid"
    assert count(app, ProtocolProposal) == 0
    response = agent.post(SUBMIT, json=body)
    assert response.status_code == 200, response.text
    record = response.json()["proposal"]
    assert record["status"] == "submitted" and record["revision"] == 1
    assert agent.post(SUBMIT, json=body).json()["proposal"] == record
    conflicting = deepcopy(body)
    conflicting["specification"]["summary"] = "Changed content under the same request key."
    assert agent.post(SUBMIT, json=conflicting).status_code == 409
    url = f'{SUBMIT}/{record["id"]}'
    assert agent.get(url).json()["proposal"]["id"] == record["id"]
    assert [p["id"] for p in agent.get("/api/me/protocol-proposals").json()["proposals"]] == [record["id"]]
    revision = revision_body(record)
    changed = agent.post(url + "/revisions", json=revision)
    assert changed.status_code == 200, changed.text
    updated = changed.json()["proposal"]
    assert updated["revision"] == 2 and updated["status"] == "submitted"
    assert agent.post(url + "/revisions", json=revision).json()["proposal"] == updated
    conflict = deepcopy(revision)
    conflict["specification"]["summary"] = "Another change with the same revision request key."
    assert agent.post(url + "/revisions", json=conflict).status_code == 409
    stale = {**revision, "idempotency_key": str(uuid4())}
    assert agent.post(url + "/revisions", json=stale).status_code == 409
    history = agent.get(url + "/revisions").json()
    assert [row["revision"] for row in history["revisions"]] == [2, 1]
    # An initial submission retry stays bound to its original historical document.
    assert agent.post(SUBMIT, json=body).json()["proposal"] == record
    assert count(app, ProtocolProposal) == 1
    assert count(app, ProtocolProposalRevision) == 2
    assert count(app, ProtocolProposalReview) == count(app, SchemeAdmissionCredit) == 0


def test_bearer_owner_only_even_for_configured_reviewer_with_browser_cookies(review_system):
    app, _, _, author, reviewer, bystander = review_system
    private = submit(author)
    own = submit(reviewer)
    reviewer_agent, _ = connect(app, reviewer)
    bystander_agent, _ = connect(app, bystander)
    reviewer_agent.cookies.update(reviewer.client.cookies)
    for agent in (reviewer_agent, bystander_agent):
        url = f'{SUBMIT}/{private["id"]}'
        for suffix in ("", "/revisions"):
            denied = agent.get(url + suffix)
            assert denied.status_code == 404
            assert denied.json() == {"detail": "Proposal not found."}
        assert agent.post(url + "/revisions", json=revision_body(private)).status_code == 404
    assert reviewer_agent.get(f'{SUBMIT}/{own["id"]}').status_code == 200
    assert [p["id"] for p in reviewer_agent.get("/api/me/protocol-proposals").json()["proposals"]] == [own["id"]]
    # Exactly the same reviewer remains able to perform manual browser review.
    assert reviewer.client.get(f'{SUBMIT}/{private["id"]}').status_code == 200
    assert reviewer.client.get(f'{SUBMIT}/{private["id"]}/revisions').status_code == 200
    assert reviewer.client.get("/api/protocol/review-queue").status_code == 200
    for method, path, body in (
        ("GET", "/api/protocol/review-queue", None),
        ("POST", f'{SUBMIT}/{private["id"]}/reviews', review_body(private)),
        ("POST", f'{SUBMIT}/{private["id"]}/activate', {}),
        ("GET", "/api/agent/tokens", None),
    ):
        response = reviewer_agent.request(method, path, json=body,
            headers={"Origin": ORIGIN, "X-CSRF-Token": reviewer.csrf})
        assert response.status_code == 403, response.text
    assert count(app, ProtocolProposalReview) == count(app, SchemeAdmissionCredit) == 0


def test_old_token_requires_reconnect_and_read_only_scopes_cannot_write(proposal_system):
    app, _, _ = proposal_system
    browser = Actor(app, "legacy")
    agent, credential = connect(app, browser)
    record = submit(browser)
    with app.state.db.transaction(write=True) as session:
        session.get(MachineToken, credential["token"]["id"]).scopes = list(machine_auth.LEGACY_SCOPES)
    assert agent.get("/api/schemes").status_code == 200
    paths = ["/api/me/protocol-proposals", "/api/protocol/spec-schema", "/api/protocol/spec-template",
             f'{SUBMIT}/{record["id"]}', f'{SUBMIT}/{record["id"]}/revisions']
    for path in paths:
        assert agent.get(path).status_code == 403
    for path, body in ((VALIDATE, {"specification": specification()}),
                       (SUBMIT, {"specification": specification(), "idempotency_key": str(uuid4())}),
                       (f'{SUBMIT}/{record["id"]}/revisions', revision_body(record))):
        assert agent.post(path, json=body).status_code == 403
    fresh, _ = connect(app, browser)
    assert fresh.get("/api/me/protocol-proposals").status_code == 200
    with app.state.db.transaction(write=True) as session:
        old = session.get(MachineToken, credential["token"]["id"])
        assert old.scopes == list(machine_auth.LEGACY_SCOPES)
        old.scopes = ["proposals:read"]
    assert agent.get(f'{SUBMIT}/{record["id"]}').status_code == 200
    assert agent.post(VALIDATE, json={"specification": specification()}).status_code == 403
    assert agent.post(f'{SUBMIT}/{record["id"]}/revisions', json=revision_body(record)).status_code == 403


@pytest.mark.parametrize("state", ["revoked", "expired", "unknown", "malformed"])
def test_invalid_credential_cannot_fall_back_to_browser_proposal_authority(proposal_system, state):
    app, clock, _ = proposal_system
    browser = Actor(app, "author")
    agent, credential = connect(app, browser)
    agent.cookies.update(browser.client.cookies)
    if state in {"revoked", "expired"}:
        with app.state.db.transaction(write=True) as session:
            token = session.get(MachineToken, credential["token"]["id"])
            if state == "revoked":
                token.revoked_at = clock[0]
            else:
                token.expires_at = clock[0]
    else:
        agent.headers["Authorization"] = "Bearer " + ("cru_" + "x" * 43 if state == "unknown" else "not-a-token")
    assert agent.get("/api/me/protocol-proposals").status_code == 401
    assert agent.post(SUBMIT, json={"specification": specification(), "idempotency_key": str(uuid4())},
        headers={"Origin": ORIGIN, "X-CSRF-Token": browser.csrf}).status_code == 401
    assert count(app, ProtocolProposal) == 0


def test_anonymous_validation_preserved_but_submission_needs_login(proposal_system):
    app, _, _ = proposal_system
    public = TestClient(app)
    assert public.post(VALIDATE, json={"specification": specification()}, headers={"Origin": ORIGIN}).status_code == 200
    assert public.get("/api/me/protocol-proposals").status_code == 401
    assert public.post(SUBMIT, json={"specification": specification(), "idempotency_key": str(uuid4())},
                       headers={"Origin": ORIGIN}).status_code == 401
    assert count(app, ProtocolProposal) == 0


@pytest.mark.parametrize("approve_before_update", [False, True])
def test_pending_and_approved_legacy_connections_keep_the_scopes_they_requested(proposal_system, approve_before_update):
    app, _, _ = proposal_system
    browser = Actor(app, "legacy")
    connection = start(app)
    # Model a connection written by the previous application, before migration.
    with app.state.db.transaction(write=True) as session:
        row = session.scalar(select(DeviceConnection).where(DeviceConnection.user_code == connection["user_code"]))
        row.scopes = list(machine_auth.LEGACY_SCOPES)
    url = f'/api/agent/connect/{connection["user_code"]}'
    if approve_before_update:
        assert browser.post(url + "/approve", {}).status_code == 200
    assert browser.client.get(url).json()["connection"]["scopes"] == list(machine_auth.LEGACY_SCOPES)
    assert browser.post(url + "/approve", {}).json()["connection"]["scopes"] == list(machine_auth.LEGACY_SCOPES)
    credential = poll(app, connection).json()
    assert credential["scopes"] == list(machine_auth.LEGACY_SCOPES)
    assert poll(app, connection).json() == credential
    agent = TestClient(app, headers={"Authorization": f'Bearer {credential["access_token"]}'})
    assert agent.get("/api/schemes").status_code == 200
    assert agent.get("/api/me/protocol-proposals").status_code == 403


def test_later_scope_additions_cannot_change_a_started_connection(proposal_system, monkeypatch):
    app, _, _ = proposal_system
    browser = Actor(app, "author")
    connection = start(app)
    approved_scopes = connection["scopes"]
    monkeypatch.setattr(machine_auth, "SCOPES", machine_auth.SCOPES + ("future:permission",))
    url = f'/api/agent/connect/{connection["user_code"]}'
    assert browser.client.get(url).json()["connection"]["scopes"] == approved_scopes
    assert browser.post(url + "/approve", {}).json()["connection"]["scopes"] == approved_scopes
    assert poll(app, connection).json()["scopes"] == approved_scopes


def test_migration_backfills_legacy_requests_without_changing_tokens_or_accounts(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "0007")
    db = Database(url)
    now = 3_600_600.0
    user_id, token_id = str(uuid4()), str(uuid4())
    pending_id, approved_id, exchanged_id = [str(uuid4()) for _ in range(3)]
    old_scopes = ["challenges:read"]
    private_code = "crd_" + "a" * 43
    with db.transaction(write=True) as session:
        session.add(User(id=user_id, handle="preserved", password_hash="unchanged", created_at=1))
        session.flush()
        session.add(MachineToken(id=token_id, user_id=user_id, token_hash="f" * 64, label="Existing token",
                                 scopes=old_scopes, created_at=now, expires_at=now + 3600))
        session.flush()
        for identifier, code, digest, approved, existing_token in (
            (pending_id, "ABCD-EFGH", "b" * 64, None, None),
            (approved_id, "JKLM-NPQR", token_hash(private_code), now, None),
            (exchanged_id, "STUV-WXYZ", "c" * 64, now, token_id),
        ):
            session.execute(text("INSERT INTO device_connections "
                "(id,device_hash,user_code,label,created_at,expires_at,approved_user_id,approved_at,token_id) "
                "VALUES (:id,:digest,:code,'Legacy request',:now,:expires,:user,:approved,:token)"),
                {"id": identifier, "digest": digest, "code": code, "now": now, "expires": now + 600,
                 "user": user_id if approved else None, "approved": approved, "token": existing_token})
    command.upgrade(config, "head")
    command.check(config)
    with db.transaction() as session:
        assert session.get(User, user_id).password_hash == "unchanged"
        assert session.get(MachineToken, token_id).scopes == old_scopes
        for identifier in (pending_id, approved_id, exchanged_id):
            assert session.get(DeviceConnection, identifier).scopes == list(machine_auth.LEGACY_SCOPES)
        assert session.get(DeviceConnection, exchanged_id).token_id == token_id
    settings = Settings(url, b"m" * 32, origins=(ORIGIN,), rate_limits=False)
    app = create_app(settings, clock=lambda: now)
    try:
        old_credential = poll(app, {"device_code": private_code}).json()
        assert old_credential["status"] == "authorized"
        assert old_credential["scopes"] == list(machine_auth.LEGACY_SCOPES)
        assert start(app)["scopes"] == list(machine_auth.SCOPES)
    finally:
        app.state.db.engine.dispose()
    command.downgrade(config, "0007")
    assert "scopes" not in {row["name"] for row in inspect(db.engine).get_columns("device_connections")}
    with db.transaction() as session:
        assert session.get(User, user_id).password_hash == "unchanged"
        assert session.get(MachineToken, token_id).scopes == old_scopes
    db.engine.dispose()
