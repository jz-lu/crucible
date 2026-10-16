"""Explicit beta membership, recipient-bound invitations, and mailbox proof.

An invitation never changes identity: it grants access to the authenticated
account only after separate mailbox verification. All stored credentials are
hashes; account IDs and existing contribution ownership remain unchanged.
"""
import hashlib
import hmac
import re
import secrets
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Float, ForeignKey, Integer, JSON, String, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, LoginSession, User
from .mail_delivery import DeliveryUnavailable, send_verification
from .security import token_hash

INVITATION_SECONDS = 7 * 86400
VERIFICATION_SECONDS = 600
MAX_CODE_FAILURES = 10
MAX_CODE_SENDS = 10
RESEND_SECONDS = 60
_INVITATION = re.compile(r"^cri_[A-Za-z0-9_-]{43}$")


class BetaMembership(Base):
    __tablename__ = "beta_memberships"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[float] = mapped_column(Float)


class AccountState(Base):
    __tablename__ = "account_states"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    suspended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="")


class BetaInvitation(Base):
    __tablename__ = "beta_invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(254))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    redeemed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    redeemed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    send_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_sent_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class MailboxVerification(Base):
    __tablename__ = "mailbox_verifications"
    invitation_id: Mapped[str] = mapped_column(ForeignKey("beta_invitations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    code_mac: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class VerifiedMailbox(Base):
    __tablename__ = "verified_mailboxes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    verified_at: Mapped[float] = mapped_column(Float)


class AccessEvent(Base):
    __tablename__ = "access_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[float] = mapped_column(Float)
    details: Mapped[dict] = mapped_column(JSON)


def normalize_email(value):
    # A conservative ASCII mailbox subset keeps identity comparison predictable.
    # Do not strip dots, +suffixes, or otherwise apply provider-specific aliases.
    if not isinstance(value, str):
        raise ValueError("Enter a valid email address.")
    value = value.strip()
    if (len(value) > 254 or not value.isascii() or value.count("@") != 1
            or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)):
        raise ValueError("Enter a valid email address.")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value or "." not in domain:
        raise ValueError("Enter a valid email address.")
    # SMTP local parts can be case-sensitive. Domain names are not.
    return local + "@" + domain.lower()


def masked_email(value):
    local, domain = value.rsplit("@", 1)
    return local[:1] + "***@" + domain


def is_admin(settings, user):
    return bool(user and user.id in getattr(settings, "access_admin_ids", ()))


def lock_user(session, user_id):
    return session.scalar(select(User).where(User.id == user_id).with_for_update()
                          .execution_options(populate_existing=True))


def require_not_suspended(session, user, *, mutation=False):
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    if mutation:
        user = lock_user(session, user.id)
        if user is None:
            raise HTTPException(401, "Sign in to continue.")
    state = session.get(AccountState, user.id, populate_existing=True)
    if state and state.suspended_at is not None:
        raise HTTPException(403, "This account is suspended. Contact the operator.")
    return user


def require_access(session, settings, user, now, mutation=False):
    user = require_not_suspended(session, user, mutation=mutation)
    if getattr(settings, "access_mode", "open") == "beta":
        membership = session.get(BetaMembership, user.id, populate_existing=True)
        if membership is None or membership.status != "active":
            message = "Beta access was revoked. Contact the operator." if membership and membership.status == "revoked" else "An approved invitation is required for beta access."
            raise HTTPException(403, message)
    return user


def access_payload(session, settings, user):
    mode = getattr(settings, "access_mode", "open")
    membership = session.get(BetaMembership, user.id) if user else None
    account = session.get(AccountState, user.id) if user else None
    status = membership.status if membership else "pending"
    suspended = bool(account and account.suspended_at is not None)
    allowed = not suspended and (mode == "open" or bool(user and status == "active"))
    return {"mode": mode, "allowed": allowed,
            "status": "suspended" if suspended else status if user else "anonymous"}


def _event(session, user_id, actor_id, action, now, **details):
    session.add(AccessEvent(id=str(uuid4()), user_id=user_id, actor_id=actor_id,
                            action=action, created_at=now, details=details))


def _find_invitation(session, token, *, lock=False):
    if not isinstance(token, str) or not _INVITATION.fullmatch(token):
        raise HTTPException(403, "This invitation is invalid or unavailable.")
    query = select(BetaInvitation).where(BetaInvitation.token_hash == token_hash(token))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    invitation = session.scalar(query)
    if invitation is None:
        raise HTTPException(403, "This invitation is invalid or unavailable.")
    return invitation


def validate_registration_invitation(session, token, now, *, lock=False):
    invitation = _find_invitation(session, token, lock=lock)
    if (invitation.expires_at <= now or invitation.revoked_at is not None
            or invitation.redeemed_at is not None or invitation.failed_attempts >= MAX_CODE_FAILURES):
        raise HTTPException(403, "This invitation is invalid or unavailable.")
    return invitation


def create_invitation(session, settings, email, now, *, actor_id=None, expires_in_days=7):
    email = normalize_email(email)
    if type(expires_in_days) is not int or not 1 <= expires_in_days <= 7:
        raise ValueError("Invitation expiry must be between one and seven days.")
    raw = "cri_" + secrets.token_urlsafe(32)
    invitation = BetaInvitation(id=str(uuid4()), token_hash=token_hash(raw), email=email,
        created_by=actor_id, created_at=now, expires_at=now + expires_in_days * 86400,
        send_count=0, failed_attempts=0)
    session.add(invitation)
    _event(session, None, actor_id, "invitation_created", now, invitation_id=invitation.id)
    return invitation, settings.origins[0] + "/#join?token=" + raw


def invitation_status(invitation, now):
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.redeemed_at is not None:
        return "redeemed"
    if invitation.expires_at <= now:
        return "expired"
    if invitation.failed_attempts >= MAX_CODE_FAILURES:
        return "locked"
    return "available"


def invalidate_invitation(session, invitation_id, now, *, actor_id=None, reason=""):
    invitation = session.scalar(select(BetaInvitation).where(BetaInvitation.id == invitation_id)
                                .with_for_update().execution_options(populate_existing=True))
    if invitation is None:
        raise HTTPException(404, "Invitation not found.")
    if invitation.revoked_at is None:
        invitation.revoked_at = now
        _event(session, None, actor_id, "invitation_revoked", now,
               invitation_id=invitation.id, reason=reason[:500])
    return invitation


def revoke_credentials(session, user_id, now):
    # All callers hold User first. Machine auth must use the same lock order.
    from .machine_auth import DeviceConnection, MachineToken
    session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
    session.execute(update(MachineToken).where(MachineToken.user_id == user_id,
        MachineToken.revoked_at.is_(None)).values(revoked_at=now))
    session.execute(update(DeviceConnection).where(DeviceConnection.approved_user_id == user_id,
        DeviceConnection.denied_at.is_(None)).values(denied_at=now))


def set_membership(session, user_id, status, now, *, actor_id=None, reason=""):
    if status not in {"active", "revoked"}:
        raise ValueError("Unsupported membership status.")
    if lock_user(session, user_id) is None:
        raise HTTPException(404, "Account not found.")
    membership = session.get(BetaMembership, user_id, populate_existing=True)
    if membership is None:
        membership = BetaMembership(user_id=user_id, status=status, updated_at=now)
        session.add(membership)
    else:
        membership.status, membership.updated_at = status, now
    if status == "revoked":
        revoke_credentials(session, user_id, now)
    _event(session, user_id, actor_id, "membership_" + ("granted" if status == "active" else "revoked"), now, reason=reason[:500])


def set_suspension(session, user_id, suspended, now, *, actor_id=None, reason=""):
    if lock_user(session, user_id) is None:
        raise HTTPException(404, "Account not found.")
    state = session.get(AccountState, user_id, populate_existing=True)
    if state is None:
        state = AccountState(user_id=user_id)
        session.add(state)
    state.suspended_at, state.reason = (now if suspended else None), reason[:500]
    if suspended:
        revoke_credentials(session, user_id, now)
    _event(session, user_id, actor_id, "account_suspended" if suspended else "account_restored", now, reason=reason[:500])


def _code_mac(settings, invitation_id, user_id, code):
    message = "crucible/mailbox-v1\0" + invitation_id + "\0" + user_id + "\0" + code
    return hmac.new(settings.secret, message.encode("ascii"), hashlib.sha256).hexdigest()


def _check_regrant(session, user, invitation):
    membership = session.get(BetaMembership, user.id, populate_existing=True)
    if membership and membership.status == "revoked" and invitation.created_at <= membership.updated_at:
        raise HTTPException(403, "Access was revoked after this invitation was created. Request a new invitation.")


class InviteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    expires_in_days: int = Field(default=7, ge=1, le=7, strict=True)
    _email = field_validator("email")(normalize_email)


class TokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^cri_[A-Za-z0-9_-]{43}$", min_length=47, max_length=47)


class RedeemInput(TokenInput):
    code: str = Field(pattern=r"^[0-9]{8}$", min_length=8, max_length=8)


class ReasonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


def register_routes(app, db, settings, clock, account_for, throttle, issue_session=None, session_payload=None):
    @app.get("/api/access/status")
    def status(request: Request):
        with db.transaction() as session:
            user = account_for(request, session)
            mailbox = session.get(VerifiedMailbox, user.id)
            return {"access": access_payload(session, settings, user),
                    "verified_email": mailbox.email if mailbox else None}

    @app.post("/api/access/invitations/verify/start")
    def begin_verification(body: TokenInput, request: Request):
        with db.transaction() as session:
            account_id = account_for(request, session, mutation=True).id
        # Share the quota across this account's sessions without penalizing a
        # campus or office whose participants share a public IP address.
        throttle(request, "invite-send", 10, 3600, account_id=account_id)
        if getattr(settings, "mail_backend", "disabled") == "disabled":
            raise HTTPException(503, "Verification email is not configured. Please contact the operator.")
        with db.transaction(write=True) as session:
            user = account_for(request, session, mutation=True)
            if user.id != account_id:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            now = clock()
            invitation = validate_registration_invitation(session, body.token, now, lock=True)
            _check_regrant(session, user, invitation)
            if invitation.send_count >= MAX_CODE_SENDS:
                raise HTTPException(429, "This invitation has reached its email limit. Contact the operator.")
            if invitation.last_sent_at is not None and now < invitation.last_sent_at + RESEND_SECONDS:
                raise HTTPException(429, "Wait before requesting another verification code.",
                                    headers={"Retry-After": str(max(1, int(invitation.last_sent_at + RESEND_SECONDS - now)))})
            code = f"{secrets.randbelow(100_000_000):08d}"
            verification = session.get(MailboxVerification, invitation.id)
            if verification is None:
                verification = MailboxVerification(invitation_id=invitation.id, user_id=user.id,
                    code_mac="", expires_at=now)
                session.add(verification)
            verification.user_id = user.id
            verification.code_mac = _code_mac(settings, invitation.id, user.id, code)
            verification.expires_at = min(now + VERIFICATION_SECONDS, invitation.expires_at)
            verification.consumed_at = None
            invitation.send_count += 1
            invitation.last_sent_at = now
            recipient, expiry = invitation.email, verification.expires_at
            _event(session, user.id, user.id, "mailbox_code_requested", now, invitation_id=invitation.id)
        # Deliver only after the committed challenge exists. Delivery failures do
        # not leak SMTP diagnostics or undo send/guess budgets.
        try:
            send_verification(settings, recipient, code)
        except DeliveryUnavailable as error:
            raise HTTPException(503, str(error)) from None
        return {"status": "verification_sent", "masked_recipient": masked_email(recipient),
                "expires_at": expiry, "resend_after_seconds": RESEND_SECONDS}

    @app.post("/api/access/invitations/redeem")
    def redeem(body: RedeemInput, request: Request):
        with db.transaction() as session:
            account_id = account_for(request, session, mutation=True).id
        throttle(request, "invite-redeem", 30, 3600, account_id=account_id)
        try:
            return redeem_committed(body, request, account_id)
        except IntegrityError:
            # Simultaneous invitations for the same mailbox may both observe no
            # owner before the unique insert. Roll back the entire losing grant.
            raise HTTPException(409, "This mailbox is already associated with an account. Sign in to that account or contact the operator.") from None

    def redeem_committed(body, request, account_id):
        failure = None
        with db.transaction(write=True) as session:
            user = account_for(request, session, mutation=True)
            if user.id != account_id:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            now = clock()
            invitation = _find_invitation(session, body.token, lock=True)
            verification = session.get(MailboxVerification, invitation.id)
            if invitation.redeemed_at is not None:
                # Recover a lost success response during the original code's
                # lifetime. This only returns the receipt; it cannot regrant,
                # extend or revive revoked access, and creates no second event.
                membership = session.get(BetaMembership, user.id, populate_existing=True)
                if (invitation.redeemed_by == user.id and invitation.revoked_at is None
                        and invitation.expires_at > now and membership and membership.status == "active"
                        and verification and verification.user_id == user.id
                        and verification.consumed_at == invitation.redeemed_at
                        and verification.expires_at > now and hmac.compare_digest(
                            verification.code_mac, _code_mac(settings, invitation.id, user.id, body.code))):
                    return {"status": "accepted", "replayed": True,
                            "access": access_payload(session, settings, user)}
                raise HTTPException(403, "This invitation is invalid or unavailable.")
            invitation = validate_registration_invitation(session, body.token, now, lock=True)
            _check_regrant(session, user, invitation)
            valid = (verification is not None and verification.user_id == user.id
                     and verification.expires_at > now and verification.consumed_at is None
                     and hmac.compare_digest(verification.code_mac, _code_mac(settings, invitation.id, user.id, body.code)))
            if not valid:
                invitation.failed_attempts += 1
                failure = HTTPException(403, "The verification code is invalid or expired. Request a new code if needed.")
            else:
                # The invitation row serializes redemption. A mailbox may belong
                # to only one stable account; possession never merges identities.
                other = session.scalar(select(VerifiedMailbox).where(VerifiedMailbox.email == invitation.email))
                mailbox = session.get(VerifiedMailbox, user.id)
                if (other and other.user_id != user.id) or (mailbox and mailbox.email != invitation.email):
                    failure = HTTPException(409, "This mailbox is already associated with an account. Sign in to that account or contact the operator.")
                else:
                    if mailbox is None:
                        session.add(VerifiedMailbox(user_id=user.id, email=invitation.email, verified_at=now))
                    verification.consumed_at = invitation.redeemed_at = now
                    invitation.redeemed_by = user.id
                    set_membership(session, user.id, "active", now, actor_id=user.id, reason="Verified invitation " + invitation.id)
                    _event(session, user.id, user.id, "invitation_redeemed", now, invitation_id=invitation.id)
                    session.flush()
                    result = {"status": "accepted", "access": access_payload(session, settings, user)}
        # Rejected guesses must commit their counters before returning an error.
        if failure:
            raise failure
        return result

    def admin(session, request, *, mutation=False):
        user = account_for(request, session, mutation=mutation)
        require_access(session, settings, user, clock(), mutation=mutation)
        if not is_admin(settings, user):
            raise HTTPException(403, "Beta administration permission is required.")
        return user

    @app.post("/api/access/invitations")
    def invite(body: InviteInput, request: Request):
        with db.transaction() as session:
            account_id = admin(session, request, mutation=True).id
        throttle(request, "invite-create", 30, 3600, account_id=account_id)
        with db.transaction(write=True) as session:
            actor = admin(session, request, mutation=True)
            if actor.id != account_id:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            invitation, link = create_invitation(session, settings, body.email, clock(), actor_id=actor.id,
                                                  expires_in_days=body.expires_in_days)
            return {"invitation": {"id": invitation.id, "email": invitation.email,
                    "expires_at": invitation.expires_at, "url": link}}

    @app.post("/api/access/invitations/{invitation_id}/revoke")
    def revoke_invitation(invitation_id: str, body: ReasonInput, request: Request):
        with db.transaction(write=True) as session:
            actor = admin(session, request, mutation=True)
            invalidate_invitation(session, invitation_id, clock(), actor_id=actor.id, reason=body.reason)
        return {"status": "revoked"}

    def manage_member(user_id, body, request, action):
        # Establish the actor read-only, then lock all involved User rows in a
        # stable order before rechecking CSRF/admin authority. This avoids the
        # reciprocal administrator-revocation lock inversion.
        with db.transaction() as session:
            actor_id = admin(session, request).id
        with db.transaction(write=True) as session:
            for identifier in sorted({actor_id, user_id}):
                lock_user(session, identifier)
            actor = admin(session, request, mutation=True)
            if actor.id != actor_id:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            if action == "revoke":
                set_membership(session, user_id, "revoked", clock(), actor_id=actor.id, reason=body.reason)
            else:
                set_suspension(session, user_id, action == "suspend", clock(), actor_id=actor.id, reason=body.reason)
        return {"status": action}

    @app.post("/api/access/members/{user_id}/revoke")
    def revoke_member(user_id: str, body: ReasonInput, request: Request):
        return manage_member(user_id, body, request, "revoke")

    @app.post("/api/access/members/{user_id}/suspend")
    def suspend_member(user_id: str, body: ReasonInput, request: Request):
        return manage_member(user_id, body, request, "suspend")

    @app.post("/api/access/members/{user_id}/restore")
    def restore_member(user_id: str, body: ReasonInput, request: Request):
        return manage_member(user_id, body, request, "restore")
