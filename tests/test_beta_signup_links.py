"""Reusable admission links reuse mailbox proof and never change account identity."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

from crucible import beta_access as beta
from crucible.db import User
from test_beta_invitations import beta_system, redeem, start


def create(s, **changes):
    body = {"request_id": str(uuid4()), "label": "November research preview", "expires_in_days": 7, **changes}
    with s.db.transaction(write=True) as session:
        record = beta.create_signup_link(session, s.settings, body["label"], s.now[0],
            actor_id=s.users["admin"], expires_in_days=body["expires_in_days"], request_id=body["request_id"])
        link = beta.signup_link_data(s.settings, record, s.now[0])
    return link, link["url"].split("token=", 1)[1], body


def claim(s, token, email="alice@example.org", who="alice"):
    response = s.post("/api/access/signup-links/invitations", {"token": token, "email": email}, who)
    assert response.status_code == 200, response.text
    return response.json()["invitation"]["url"].split("token=", 1)[1]


def test_shared_link_admits_distinct_verified_accounts_and_expiry_keeps_members(beta_system):
    s = beta_system
    link, token, _ = create(s)
    assert link["expires_at"] == s.now[0] + 7 * 86400
    children = []
    for actor in ("alice", "bob"):
        child = claim(s, token, actor + "@example.org", actor)
        children.append(child)
        with s.db.transaction() as session:
            assert session.get(beta.BetaMembership, s.users[actor]) is None
        _, code, _, _ = start(s, child, actor)
        assert redeem(s, child, code, actor).status_code == 200
    assert children[0] != children[1] and token not in children
    s.now[0] = link["expires_at"]
    assert s.post("/api/access/signup-links/inspect", {"token": token}).status_code == 403
    assert s.post("/api/access/signup-links/invitations", {"token": token, "email": "next@example.org"}).status_code == 403
    with s.db.transaction() as session:
        for actor in ("alice", "bob"):
            assert beta.access_payload(session, s.settings, session.get(User, s.users[actor]))["allowed"]


def test_inspection_and_claim_send_no_mail_create_no_account_or_grant(beta_system):
    s = beta_system
    link, token, _ = create(s)
    response = s.post("/api/access/signup-links/inspect", {"token": token})
    assert response.json() == {"status": "available", "label": link["label"], "expires_at": link["expires_at"]}
    child = claim(s, token)
    assert not list(Path(s.settings.mail_outbox_dir).glob("*.eml"))
    with s.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 3
        assert session.get(beta.BetaMembership, s.users["alice"]) is None
        invitation = beta.validate_registration_invitation(session, child, s.now[0])
        assert invitation.expires_at == link["expires_at"]
        assert invitation.token_hash != child
        assert session.get(beta.BetaSignupLink, link["id"]).token_hash != token


def test_retries_keep_same_invitation_deadline_and_send_guess_budgets(beta_system):
    s = beta_system
    link, token, _ = create(s)
    child = claim(s, token, "alice@EXAMPLE.org")
    _, code, _, _ = start(s, child)
    wrong = "00000000" if code != "00000000" else "00000001"
    assert redeem(s, child, wrong).status_code == 403
    s.now[0] += 30
    assert claim(s, token, "alice@example.org") == child
    with s.db.transaction() as session:
        invitation = beta.validate_registration_invitation(session, child, s.now[0])
        assert invitation.failed_attempts == 1 and invitation.send_count == 1
        assert invitation.expires_at == link["expires_at"]
        assert session.scalar(select(func.count()).select_from(beta.BetaSignupInvitation)) == 1
    assert s.post("/api/access/invitations/verify/start", {"token": child}).status_code == 429


@pytest.mark.parametrize("close", ["expire", "revoke"])
def test_closed_parent_invalidates_pending_registration_and_verification(beta_system, close):
    s = beta_system
    link, token, _ = create(s)
    child = claim(s, token)
    _, code, _, _ = start(s, child)
    if close == "expire":
        s.now[0] = link["expires_at"]
    else:
        with s.db.transaction(write=True) as session:
            beta.revoke_signup_link(session, link["id"], s.now[0], reason="Closed trial")
    assert s.post("/api/access/signup-links/inspect", {"token": token}).status_code == 403
    assert s.post("/api/access/signup-links/invitations", {"token": token, "email": "bob@example.org"}).status_code == 403
    assert s.post("/api/access/invitations/inspect", {"token": child}).status_code == 403
    assert s.post("/api/access/invitations/verify/start", {"token": child}).status_code == 403
    assert redeem(s, child, code).status_code == 403
    with s.db.transaction() as session:
        with pytest.raises(HTTPException):
            beta.validate_registration_invitation(session, child, s.now[0])
        assert session.get(beta.BetaMembership, s.users["alice"]) is None


def test_revocation_is_idempotent_and_does_not_revoke_joined_accounts(beta_system):
    s = beta_system
    link, token, _ = create(s)
    child = claim(s, token)
    _, code, _, _ = start(s, child)
    assert redeem(s, child, code).status_code == 200
    for _ in range(2):
        with s.db.transaction(write=True) as session:
            beta.revoke_signup_link(session, link["id"], s.now[0])
    with s.db.transaction() as session:
        assert session.get(beta.BetaMembership, s.users["alice"]).status == "active"
        assert session.scalar(select(func.count()).select_from(beta.AccessEvent).where(
            beta.AccessEvent.action == "signup_link_revoked")) == 1


def test_shared_link_cannot_restore_an_individually_revoked_membership(beta_system):
    s = beta_system
    with s.db.transaction(write=True) as session:
        beta.set_membership(session, s.users["alice"], "revoked", s.now[0])
    s.now[0] += 1
    _, token, _ = create(s)
    child = claim(s, token)
    assert s.post("/api/access/invitations/verify/start", {"token": child}).status_code == 403
    assert redeem(s, child, "12345678").status_code == 403


def test_web_has_no_campaign_management_and_recipient_claim_requires_csrf(beta_system):
    s = beta_system
    link, token, _ = create(s)
    for actor in ("alice", "admin"):
        assert s.post("/api/access/signup-links", {"label": "Another campaign"}, actor).status_code == 404
        assert s.client.get("/api/access/signup-links", headers={"x-user": s.users[actor]}).status_code == 404
        assert s.post(f'/api/access/signup-links/{link["id"]}/revoke', {}, actor).status_code == 404
    assert s.client.post("/api/access/signup-links/invitations", json={"token": token, "email": "a@example.org"},
                         headers={"x-user": s.users["alice"]}).status_code == 403


def test_concurrent_recipient_claims_reuse_one_invitation(beta_system):
    s = beta_system
    _, token, _ = create(s)
    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(lambda _: claim(s, token), range(2)))
    assert children[0] == children[1]
    with s.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(beta.BetaSignupInvitation)) == 1
        assert session.scalar(select(func.count()).select_from(beta.BetaInvitation)) == 1


def test_secret_rotation_never_returns_a_broken_recovered_url(beta_system):
    s = beta_system
    link, token, _ = create(s)
    s.settings.secret = b"c" * 32
    with s.db.transaction() as session:
        record = beta.find_signup_link(session, token, s.now[0])
        assert beta.signup_link_data(s.settings, record, s.now[0])["url"] is None
