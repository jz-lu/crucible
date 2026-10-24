"""One-use recovery through an already verified mailbox; never grants access."""
import hmac
import json
import secrets

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Float, ForeignKey, String, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from . import beta_access
from .db import Base, LoginSession, User
from .mail_delivery import DeliveryUnavailable, send_password_reset
from .security import mac, passwords, token_hash

RESET_SECONDS = 1800
RESEND_SECONDS = 60
GENERIC_RESULT = {"status": "requested", "message":
    "If this account has a verified email address, a reset link will arrive shortly."}
INVALID = "This reset link is invalid or has expired. Request a new link."


class PasswordReset(Base):
    __tablename__ = "password_resets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    credential_mac: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)
    consumed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ResetStart(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    handle: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")

    @field_validator("handle")
    @classmethod
    def normalize(cls, value):
        return value.lower()


class ResetComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$", min_length=43, max_length=43)
    password: str = Field(min_length=12, max_length=128)


def credential_binding(settings, user, mailbox):
    data = json.dumps([user.id, user.password_hash, mailbox.email], separators=(",", ":")).encode()
    return mac(settings.secret, "password-reset-credentials-v1", data)


def valid_reset(settings, user, mailbox, record, digest, now):
    return bool(user and user.password_hash and mailbox and record and
        record.consumed_at is None and record.expires_at > now and
        hmac.compare_digest(record.token_hash, digest) and
        hmac.compare_digest(record.credential_mac, credential_binding(settings, user, mailbox)))


def deliver_reset(settings, db, recipient, raw, user_id):
    # Runs after the generic response. Mailbox existence/provider latency must
    # not determine the response body/status. A restart before this bounded
    # background delivery can lose the email; the participant can request again.
    try:
        send_password_reset(settings, recipient, settings.origins[0] + "/#reset-password?token=" + raw)
    except DeliveryUnavailable:
        # Invalidate only this failed delivery, never a newer reset request.
        with db.transaction(write=True) as session:
            beta_access.lock_user(session, user_id)
            session.execute(delete(PasswordReset).where(
                PasswordReset.user_id == user_id, PasswordReset.token_hash == token_hash(raw)))


def register_routes(app, db, settings, clock, identity, issue_session,
                    session_payload, password_operation, throttle):
    @app.post("/api/auth/password/reset/start")
    def request_reset(body: ResetStart, request: Request, background: BackgroundTasks):
        throttle(request, "password-reset-ip", 20, 3600)
        throttle(request, "password-reset-handle", 5, 3600, handle=body.handle)
        if settings.mail_backend == "disabled":
            raise HTTPException(503, "Password recovery is not configured. Contact the operator.")
        with db.transaction(write=True) as session:
            identity(request, session, mutation=True)
            user = session.scalar(select(User).where(User.handle == body.handle).with_for_update())
            mailbox = session.get(beta_access.VerifiedMailbox, user.id) if user else None
            if not user or not user.password_hash or not mailbox:
                return GENERIC_RESULT
            account_state = session.get(beta_access.AccountState, user.id)
            if account_state and account_state.suspended_at is not None:
                return GENERIC_RESULT
            now = clock()
            record = session.get(PasswordReset, user.id)
            if record and now < record.created_at + RESEND_SECONDS:
                return GENERIC_RESULT
            raw = secrets.token_urlsafe(32)
            if record is None:
                record = PasswordReset(user_id=user.id)
                session.add(record)
            record.token_hash = token_hash(raw)
            record.credential_mac = credential_binding(settings, user, mailbox)
            record.created_at, record.expires_at, record.consumed_at = now, now + RESET_SECONDS, None
            recipient, identifier = mailbox.email, user.id
        background.add_task(deliver_reset, settings, db, recipient, raw, identifier)
        return GENERIC_RESULT

    @app.post("/api/auth/password/reset/complete")
    def complete_reset(body: ResetComplete, request: Request):
        throttle(request, "password-reset-complete", 20, 900)
        digest = token_hash(body.token)
        with db.transaction() as session:
            identity(request, session, mutation=True)
            record = session.scalar(select(PasswordReset).where(PasswordReset.token_hash == digest))
            user = session.get(User, record.user_id) if record else None
            mailbox = session.get(beta_access.VerifiedMailbox, user.id) if user else None
            if not valid_reset(settings, user, mailbox, record, digest, clock()):
                raise HTTPException(400, INVALID)
            identifier = user.id
        # Argon2 is bounded by the existing authentication concurrency limiter.
        # No database lock is held while computing it. Recheck under the user
        # lock afterwards, in the same order as session/agent revocation.
        encoded = password_operation(passwords.hash, body.password)
        response = JSONResponse({})
        with db.transaction(write=True) as session:
            user = beta_access.lock_user(session, identifier)
            previous = identity(request, session, mutation=True)
            mailbox = session.get(beta_access.VerifiedMailbox, identifier)
            record = session.get(PasswordReset, identifier, populate_existing=True)
            if not valid_reset(settings, user, mailbox, record, digest, clock()):
                raise HTTPException(400, INVALID)
            beta_access.require_not_suspended(session, user)
            now = clock()
            user.password_hash = encoded
            record.consumed_at = now
            beta_access.revoke_credentials(session, identifier, now)
            session.execute(delete(LoginSession).where(LoginSession.token_hash == previous.token_hash))
            raw = issue_session(response, session)
            session.flush()
            response.body = json.dumps({"status": "password_reset", **session_payload(None, raw, session)}).encode()
            response.headers["content-length"] = str(len(response.body))
        return response
