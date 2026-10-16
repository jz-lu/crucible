"""Isolated invitation/access tests; no real mailbox or existing account is used."""
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import beta_access as beta
from crucible.db import Base, Database, LoginSession, User
from crucible.machine_auth import DeviceConnection, MachineToken


@pytest.fixture
def beta_system(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'beta.db'}")
    Base.metadata.create_all(db.engine)
    users = {name: str(uuid4()) for name in ("admin", "alice", "bob")}
    now = [1_000_000.0]
    settings = SimpleNamespace(access_mode="beta", access_admin_ids=(users["admin"],),
        secret=b"b" * 32, origins=("http://testserver",), production=False,
        mail_backend="local", mail_outbox_dir=str(tmp_path / "outbox"), mail_from="beta@example.org")
    with db.transaction(write=True) as session:
        for name, identifier in users.items():
            session.add(User(id=identifier, handle=name, password_hash="fixture", created_at=now[0]))
        session.flush()
        beta.set_membership(session, users["admin"], "active", now[0])
    app = FastAPI()

    def account_for(request, session, mutation=False):
        user = session.get(User, request.headers.get("x-user", ""))
        if not user:
            raise HTTPException(401, "Sign in.")
        if mutation and request.headers.get("x-csrf-token") != "fixture":
            raise HTTPException(403, "Invalid CSRF token.")
        return beta.require_not_suspended(session, user, mutation=mutation)

    quota_calls, counters = [], {}
    def throttle(request, name, limit, seconds, extra="", *, account_id=None):
        quota_calls.append({"name": name, "account_id": account_id, "peer": request.client.host})
        if system.enforce_quotas:
            subject = ("account", account_id) if account_id else ("ip", request.client.host)
            key = name, subject, int(now[0]) // seconds
            counters[key] = counters.get(key, 0) + 1
            if counters[key] > limit:
                raise HTTPException(429, "Fixture quota reached.")
    beta.register_routes(app, db, settings, lambda: now[0], account_for, throttle)
    client = TestClient(app)
    def post(path, body, who="alice"):
        return client.post(path, json=body, headers={"x-user": users[who], "x-csrf-token": "fixture"})
    system = SimpleNamespace(db=db, users=users, now=now, settings=settings, client=client, post=post,
                             quota_calls=quota_calls, enforce_quotas=False)
    yield system
    client.close()
    db.engine.dispose()


def invitation(system, email="alice@example.org"):
    response = system.post("/api/access/invitations", {"email": email}, "admin")
    assert response.status_code == 200, response.text
    record = response.json()["invitation"]
    return record, record["url"].split("token=", 1)[1]


def start(system, token, who="alice"):
    response = system.post("/api/access/invitations/verify/start", {"token": token}, who)
    assert response.status_code == 200, response.text
    files = list(Path(system.settings.mail_outbox_dir).glob("*.eml"))
    file = max(files, key=lambda path: path.stat().st_mtime_ns)
    mail = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
    code = re.search(r"code is: ([0-9]{8})", mail.get_content()).group(1)
    return response, code, file, mail


def redeem(system, token, code, who="alice"):
    return system.post("/api/access/invitations/redeem", {"token": token, "code": code}, who)


def test_recipient_proof_grants_same_account_and_consumes_once(beta_system):
    s = beta_system
    record, token = invitation(s)
    response, code, file, mail = start(s, token)
    assert mail["To"] == "alice@example.org"
    assert response.json()["masked_recipient"] == "a***@example.org"
    assert code not in response.text and "alice@example.org" not in response.text
    assert file.stat().st_mode & 0o777 == 0o600
    accepted = redeem(s, token, code)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["access"] == {"mode": "beta", "allowed": True, "status": "active"}
    replay = redeem(s, token, code)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert redeem(s, token, code, "bob").status_code == 403
    with s.db.transaction() as session:
        row = session.get(beta.BetaInvitation, record["id"])
        assert row.redeemed_by == s.users["alice"] and row.token_hash != token
        verified = session.get(beta.VerifiedMailbox, s.users["alice"])
        assert verified.email == "alice@example.org"
        stored = session.get(beta.MailboxVerification, record["id"])
        assert code not in stored.code_mac
        assert session.scalar(select(func.count()).select_from(User)) == 3


def test_registration_validation_is_read_only_and_bound_to_expiry(beta_system):
    s = beta_system
    record, token = invitation(s)
    with s.db.transaction() as session:
        for _ in range(2):
            assert beta.validate_registration_invitation(session, token, s.now[0]).id == record["id"]
        assert session.get(beta.BetaInvitation, record["id"]).redeemed_at is None
        with pytest.raises(HTTPException):
            beta.validate_registration_invitation(session, token, record["expires_at"])
        with pytest.raises(HTTPException):
            beta.validate_registration_invitation(session, "bad", s.now[0])


def test_code_binds_account_and_persists_rejected_guess_budget(beta_system):
    s = beta_system
    record, token = invitation(s)
    _, code, _, _ = start(s, token)
    assert redeem(s, token, code, "bob").status_code == 403
    wrong = "00000000" if code != "00000000" else "00000001"
    for _ in range(9):
        assert redeem(s, token, wrong).status_code == 403
    assert redeem(s, token, code).status_code == 403
    with s.db.transaction() as session:
        assert session.get(beta.BetaInvitation, record["id"]).failed_attempts == 10
        assert session.get(beta.BetaMembership, s.users["alice"]) is None


def test_expired_and_replaced_code_cannot_grant(beta_system):
    s = beta_system
    _, token = invitation(s)
    _, old, _, _ = start(s, token)
    s.now[0] += 600
    assert redeem(s, token, old).status_code == 403
    _, new, _, _ = start(s, token)
    if old != new:
        assert redeem(s, token, old).status_code == 403
    assert redeem(s, token, new).status_code == 200


def test_send_budget_and_cooldown_survive_new_requests(beta_system):
    s = beta_system
    record, token = invitation(s)
    start(s, token)
    denied = s.post("/api/access/invitations/verify/start", {"token": token})
    assert denied.status_code == 429 and denied.headers["retry-after"] == "60"
    for _ in range(9):
        s.now[0] += 60
        start(s, token)
    s.now[0] += 60
    assert s.post("/api/access/invitations/verify/start", {"token": token}).status_code == 429
    with s.db.transaction() as session:
        assert session.get(beta.BetaInvitation, record["id"]).send_count == 10


def test_mailbox_never_merges_accounts(beta_system):
    s = beta_system
    _, token = invitation(s)
    _, code, _, _ = start(s, token)
    assert redeem(s, token, code).status_code == 200
    second, token2 = invitation(s)
    _, code2, _, _ = start(s, token2, "bob")
    assert redeem(s, token2, code2, "bob").status_code == 409
    with s.db.transaction() as session:
        assert session.get(beta.BetaInvitation, second["id"]).redeemed_at is None
        assert session.get(beta.BetaMembership, s.users["bob"]) is None


def test_concurrent_redemption_has_exactly_one_grant(beta_system):
    s = beta_system
    _, token = invitation(s)
    _, code, _, _ = start(s, token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: redeem(s, token, code), range(2)))
    assert sorted(response.status_code for response in results) == [200, 200]
    assert sum(bool(response.json().get("replayed")) for response in results) == 1
    with s.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(beta.AccessEvent).where(
            beta.AccessEvent.user_id == s.users["alice"], beta.AccessEvent.action == "membership_granted")) == 1


def test_revoke_destroys_credentials_and_regrant_does_not_restore_them(beta_system):
    s = beta_system
    token_id, device_id = str(uuid4()), str(uuid4())
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "active", s.now[0])
        session.add(LoginSession(token_hash="s" * 64, user_id=s.users["alice"], expires_at=s.now[0] + 1000))
        session.add(MachineToken(id=token_id, user_id=s.users["alice"], token_hash="t" * 64, label="fixture",
            scopes=["challenges:read"], created_at=s.now[0], expires_at=s.now[0] + 1000))
        session.flush()
        session.add(DeviceConnection(id=device_id, device_hash="d" * 64, user_code="ABCD-EFGH", label="fixture",
            scopes=["challenges:read"], created_at=s.now[0], expires_at=s.now[0] + 1000,
            approved_user_id=s.users["alice"], approved_at=s.now[0], token_id=token_id))
    result = s.post(f'/api/access/members/{s.users["alice"]}/revoke', {"reason": "test"}, "admin")
    assert result.status_code == 200, result.text
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "active", s.now[0] + 1)
    with s.db.transaction() as session:
        assert session.get(LoginSession, "s" * 64) is None
        assert session.get(MachineToken, token_id).revoked_at == s.now[0]
        assert session.get(DeviceConnection, device_id).denied_at == s.now[0]


def test_old_invitation_cannot_undo_later_revocation(beta_system):
    s = beta_system
    _, token = invitation(s)
    _, code, _, _ = start(s, token)
    s.now[0] += 1
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "revoked", s.now[0])
    assert redeem(s, token, code).status_code == 403
    s.now[0] += 1
    _, token2 = invitation(s)
    _, code2, _, _ = start(s, token2)
    assert redeem(s, token2, code2).status_code == 200


def test_success_retry_cannot_revive_revoked_access_or_extend_code_lifetime(beta_system):
    s = beta_system
    _, token = invitation(s)
    _, code, _, _ = start(s, token)
    assert redeem(s, token, code).status_code == 200
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "revoked", s.now[0] + 1)
    assert redeem(s, token, code).status_code == 403
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "active", s.now[0] + 2)
    s.now[0] += 600
    assert redeem(s, token, code).status_code == 403


def test_access_payload_and_suspension_in_open_mode(beta_system):
    s = beta_system
    with s.db.transaction() as session:
        alice = session.get(User, s.users["alice"])
        assert beta.access_payload(session, s.settings, None) == {"mode": "beta", "allowed": False, "status": "anonymous"}
        with pytest.raises(HTTPException):
            beta.require_access(session, s.settings, alice, s.now[0])
    s.settings.access_mode = "open"
    with s.db.transaction(write=True) as session:
        assert beta.access_payload(session, s.settings, None)["allowed"] is True
        beta.set_suspension(session, s.users["alice"], True, s.now[0], reason="test")
    with s.db.transaction() as session:
        alice = session.get(User, s.users["alice"])
        assert beta.access_payload(session, s.settings, alice)["status"] == "suspended"
        with pytest.raises(HTTPException):
            beta.require_access(session, s.settings, alice, s.now[0], mutation=True)


def test_invitation_admin_role_does_not_bypass_membership(beta_system):
    s = beta_system
    assert s.post("/api/access/invitations", {"email": "bob@example.org"}).status_code == 403
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["admin"], "revoked", s.now[0])
    assert s.post("/api/access/invitations", {"email": "bob@example.org"}, "admin").status_code == 403


def test_invitation_revoke_and_missing_csrf_fail_closed(beta_system):
    s = beta_system
    record, token = invitation(s)
    response = s.client.post("/api/access/invitations/verify/start", json={"token": token}, headers={"x-user": s.users["alice"]})
    assert response.status_code == 403
    assert s.post(f'/api/access/invitations/{record["id"]}/revoke', {}, "admin").status_code == 200
    assert s.post("/api/access/invitations/verify/start", {"token": token}).status_code == 403


def test_disabled_mail_and_delivery_failure_do_not_expose_secrets(beta_system, monkeypatch):
    s = beta_system
    record, token = invitation(s)
    s.settings.mail_backend = "disabled"
    response = s.post("/api/access/invitations/verify/start", {"token": token})
    assert response.status_code == 503
    s.settings.mail_backend = "local"
    def unavailable(*_):
        raise beta.DeliveryUnavailable("Verification email could not be delivered. Please contact the operator.")
    monkeypatch.setattr(beta, "send_verification", unavailable)
    response = s.post("/api/access/invitations/verify/start", {"token": token})
    assert response.status_code == 503 and token not in response.text
    with s.db.transaction() as session:
        assert session.get(beta.BetaInvitation, record["id"]).send_count == 1


@pytest.mark.parametrize("email", ["a\nBcc:evil@example.org", "no-at.example.org", "a@localhost", ".a@example.org", "a..b@example.org"])
def test_mailbox_rejects_header_injection_and_invalid_addresses(email):
    with pytest.raises(ValueError):
        beta.normalize_email(email)


def test_email_case_preserves_recipient_localpart(beta_system):
    s = beta_system
    record, token = invitation(s, "Alice@EXAMPLE.ORG")
    assert record["email"] == "Alice@example.org"
    _, code, _, mail = start(s, token)
    assert mail["To"] == "Alice@example.org"
    assert redeem(s, token, code).status_code == 200


def test_shared_peer_does_not_share_participant_invitation_quota(beta_system):
    s = beta_system
    s.enforce_quotas = True
    s.now[0] = 3_600_000.0
    _, alice_token = invitation(s)
    _, bob_token = invitation(s, "bob@example.org")
    for _ in range(10):
        start(s, alice_token)
        s.now[0] += 60
    _, bob_code, _, _ = start(s, bob_token, "bob")
    assert redeem(s, bob_token, bob_code, "bob").status_code == 200
    sends = [call for call in s.quota_calls if call["name"] == "invite-send"]
    assert len(sends) == 11 and len({call["peer"] for call in sends}) == 1
    assert {call["account_id"] for call in sends} == {s.users["alice"], s.users["bob"]}
    assert all(call["account_id"] == s.users["admin"] for call in s.quota_calls if call["name"] == "invite-create")
    assert [call["account_id"] for call in s.quota_calls if call["name"] == "invite-redeem"] == [s.users["bob"]]


def test_admin_authority_is_checked_before_charging_invite_quota(beta_system):
    s = beta_system
    assert s.post("/api/access/invitations", {"email": "bob@example.org"}).status_code == 403
    assert not s.quota_calls
