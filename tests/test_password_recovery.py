"""Real session/CSRF and recovery behavior, using only isolated local mail."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from crucible import beta_access, password_recovery as recovery
from crucible.config import Settings
from crucible.db import LoginSession, User
from crucible.machine_auth import DeviceConnection, MachineToken
from crucible.security import token_hash, verify_password


@pytest.fixture
def system(tmp_path, monkeypatch):
    from crucible import dh_v4, registry
    from crucible.app import create_app
    from test_api import Actor
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})
    settings = Settings(f"sqlite:///{tmp_path / 'recovery.db'}", b"r" * 32,
        origins=("http://testserver",), rate_limits=False, mail_backend="local",
        mail_outbox_dir=str(tmp_path / "mail"), mail_from="beta@example.org")
    clock = [3_600_600.0]
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    owner = Actor(app)
    user = owner.register("existing_researcher")
    with app.state.db.transaction(write=True) as session:
        session.add(beta_access.VerifiedMailbox(user_id=user["id"], email="verified@example.org", verified_at=clock[0]))
    visitor = Actor(app)
    yield app, owner, visitor, user["id"], clock, settings
    owner.client.close()
    visitor.client.close()
    app.state.db.engine.dispose()


def request_link(system):
    app, owner, visitor, identifier, clock, settings = system
    result = visitor.post("/api/auth/password/reset/start", {"handle": "Existing_Researcher"})
    assert result.status_code == 200 and result.json() == recovery.GENERIC_RESULT
    path = max(Path(settings.mail_outbox_dir).glob("*.eml"), key=lambda p: p.stat().st_mtime_ns)
    mail = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    assert mail["To"] == "verified@example.org" and path.stat().st_mode & 0o777 == 0o600
    token = re.search(r"http://testserver/#reset-password\?token=([A-Za-z0-9_-]{43})", mail.get_content()).group(1)
    assert token not in result.text
    return token


def complete(system, token):
    return system[2].post("/api/auth/password/reset/complete", {
        "token": token, "password": "new synthetic password only"})


def test_one_use_reset_preserves_account_revokes_browser_and_agents_without_admitting(system):
    app, owner, visitor, identifier, clock, settings = system
    token_id = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        session.add(MachineToken(id=token_id, user_id=identifier, token_hash=token_hash("cru_" + "a" * 43),
            label="Fixture", scopes=["challenges:read"], created_at=clock[0], expires_at=clock[0]+3600, revoked_at=None))
        session.flush()
        session.add(DeviceConnection(id=str(uuid4()), device_hash=token_hash("fixture-device"), user_code="ABCD-EFGH",
            label="Fixture", scopes=["challenges:read"], created_at=clock[0], expires_at=clock[0]+600,
            approved_user_id=identifier, approved_at=clock[0], token_id=token_id, denied_at=None))
    token = request_link(system)
    with app.state.db.transaction() as session:
        record = session.get(recovery.PasswordReset, identifier)
        assert record.token_hash == token_hash(token) and token not in str(record.__dict__)
    response = complete(system, token)
    assert response.status_code == 200 and response.json()["status"] == "password_reset"
    assert response.json()["user"] is None
    visitor.csrf = response.json()["csrf_token"]
    assert complete(system, token).status_code == 400
    assert owner.client.get("/api/session").json()["user"] is None
    with app.state.db.transaction() as session:
        user = session.get(User, identifier)
        assert user.handle == "existing_researcher" and verify_password(user.password_hash, "new synthetic password only")
        assert session.scalar(select(LoginSession).where(LoginSession.user_id == identifier)) is None
        assert session.get(MachineToken, token_id).revoked_at == clock[0]
        assert session.scalar(select(DeviceConnection)).denied_at == clock[0]
        assert session.get(beta_access.BetaMembership, identifier) is None
    assert visitor.post("/api/auth/login", {"handle": "existing_researcher", "password": "correct horse battery staple"}).status_code == 401
    assert visitor.post("/api/auth/login", {"handle": "existing_researcher", "password": "new synthetic password only"}).status_code == 200


@pytest.mark.parametrize("reason", ["expired", "changed_password", "changed_mailbox", "suspended"])
def test_outstanding_link_cannot_override_changed_security_state(system, reason):
    token = request_link(system)
    app, _, _, identifier, clock, settings = system
    with app.state.db.transaction(write=True) as session:
        if reason == "expired":
            clock[0] += recovery.RESET_SECONDS
        elif reason == "changed_password":
            session.get(User, identifier).password_hash = "newer credential state"
        elif reason == "changed_mailbox":
            session.get(beta_access.VerifiedMailbox, identifier).email = "changed@example.org"
        else:
            beta_access.set_suspension(session, identifier, True, clock[0], reason="Fixture")
    assert complete(system, token).status_code in (400, 403)


def test_missing_unverified_and_provider_only_accounts_get_same_response(system):
    app, _, visitor, identifier, clock, settings = system
    for handle in ("unknown_researcher", "existing_researcher"):
        if handle == "existing_researcher":
            with app.state.db.transaction(write=True) as session:
                session.delete(session.get(beta_access.VerifiedMailbox, identifier))
        response = visitor.post("/api/auth/password/reset/start", {"handle": handle})
        assert response.status_code == 200 and response.json() == recovery.GENERIC_RESULT
    with app.state.db.transaction(write=True) as session:
        session.add(beta_access.VerifiedMailbox(user_id=identifier, email="verified@example.org", verified_at=clock[0]))
        session.get(User, identifier).password_hash = None
    assert visitor.post("/api/auth/password/reset/start", {"handle": "existing_researcher"}).json() == recovery.GENERIC_RESULT
    assert not list(Path(settings.mail_outbox_dir).glob("*.eml"))


def test_resend_cooldown_and_replacement_invalidate_prior_link(system):
    first = request_link(system)
    assert request_link(system) == first
    system[4][0] += recovery.RESEND_SECONDS
    second = request_link(system)
    assert second != first and complete(system, first).status_code == 400
    assert complete(system, second).status_code == 200


def test_delivery_failure_is_generic_and_invalidates_only_failed_link(system, monkeypatch):
    def failed(*args):
        raise recovery.DeliveryUnavailable("Sensitive SMTP diagnostic must never be returned")
    monkeypatch.setattr(recovery, "send_password_reset", failed)
    response = system[2].post("/api/auth/password/reset/start", {"handle": "existing_researcher"})
    assert response.status_code == 200 and response.json() == recovery.GENERIC_RESULT
    with system[0].state.db.transaction() as session:
        assert session.get(recovery.PasswordReset, system[3]) is None


def test_origin_csrf_and_validation_do_not_expose_reset_secrets(system):
    token = request_link(system)
    visitor = system[2]
    for headers in ({}, {"Origin": "https://attacker.example", "X-CSRF-Token": visitor.csrf}):
        response = visitor.client.post("/api/auth/password/reset/complete",
            json={"token": token, "password": "new synthetic password only"}, headers=headers)
        assert response.status_code == 403 and token not in response.text
    response = visitor.post("/api/auth/password/reset/complete", {"token": token, "password": "short"})
    assert response.status_code == 422 and token not in response.text and "short" not in response.text


def test_recheck_after_argon2_does_not_resurrect_used_link(system, monkeypatch):
    token = request_link(system)
    original = recovery.passwords.hash
    def racing_hash(value):
        encoded = original(value)
        with system[0].state.db.transaction(write=True) as session:
            session.get(recovery.PasswordReset, system[3]).consumed_at = system[4][0]
        return encoded
    monkeypatch.setattr(recovery, "passwords", SimpleNamespace(hash=racing_hash))
    assert complete(system, token).status_code == 400
