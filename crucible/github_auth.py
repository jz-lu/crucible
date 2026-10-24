"""Browser-only, identity-only GitHub OAuth with explicit account linking.

Protocol: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
State, browser binding and PKCE protect the redirect; a GitHub numeric user ID
identifies an account. Email addresses and names never merge existing accounts.
No GitHub token, authorization code, private email or repository data is stored.
The only retained invitation secret is encrypted in a ten-minute browser flow.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Literal
from urllib.parse import urlencode
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
import httpx
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import Float, ForeignKey, String, Text, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from . import beta_access
from .db import Base, LoginSession, User
from .schemas import Input
from .security import mac, token_hash, verify_password

FLOW_SECONDS = 600
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_RETURN_PATHS = {"#challenge", "#scheme", "#assumptions", "#person", "#people", "#notebook",
                 "#propose", "#reviews", "#about", "#connect"}


class GitHubIdentity(Base):
    __tablename__ = "github_identities"
    github_user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    github_login: Mapped[str] = mapped_column(String(39))
    created_at: Mapped[float] = mapped_column(Float)
    last_login_at: Mapped[float] = mapped_column(Float)


class GitHubFlow(Base):
    __tablename__ = "github_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_hash: Mapped[str] = mapped_column(String(64))
    original_session_hash: Mapped[str] = mapped_column(String(64), index=True)
    original_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    consumed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    github_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    github_login: Mapped[str | None] = mapped_column(String(39), nullable=True)
    invitation_id: Mapped[str | None] = mapped_column(ForeignKey("beta_invitations.id"), nullable=True)
    invitation_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    return_route: Mapped[str] = mapped_column(String(2048))
    result_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Start(Input):
    mode: Literal["login", "link"] = "login"
    invitation_token: str | None = Field(default=None, min_length=32, max_length=128)
    return_route: str = Field(default="#challenge", max_length=2048)
    password: SecretStr | None = Field(default=None, max_length=128)

    @field_validator("return_route")
    @classmethod
    def internal_route(cls, value):
        path = value.split("?", 1)[0].split("/", 1)[0]
        if path not in _RETURN_PATHS or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
            raise ValueError("Choose a local Crucible page.")
        return value


class Complete(Input):
    handle: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_]+$")

    @field_validator("handle")
    @classmethod
    def normalized(cls, value):
        return value.lower()


class ProviderFailure(Exception):
    """A fixed failure category; no provider payload or credentials in errors."""


def _b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _verifier(settings, state):
    return _b64(bytes.fromhex(mac(settings.secret, "github-pkce-v1", state.encode("ascii"))))


def _cipher(settings):
    return AESGCM(bytes.fromhex(mac(settings.secret, "github-invitation-key-v1", b"")))


def _encrypt_invitation(settings, identifier, token):
    nonce = secrets.token_bytes(12)
    encrypted = _cipher(settings).encrypt(nonce, token.encode("ascii"), ("github-invite-v1:" + identifier).encode())
    return (nonce + encrypted).hex()


def _decrypt_invitation(settings, flow):
    if not flow.invitation_encrypted:
        return None
    try:
        value = bytes.fromhex(flow.invitation_encrypted)
        return _cipher(settings).decrypt(value[:12], value[12:],
            ("github-invite-v1:" + flow.state_hash).encode()).decode("ascii")
    except Exception:
        raise HTTPException(409, "Restart GitHub sign-in.") from None


def auth_payload(session, settings, user):
    identity = session.scalar(select(GitHubIdentity).where(GitHubIdentity.user_id == user.id)) if (
        settings.github_login_enabled and user) else None
    return auth_summary(settings, user, identity)


def auth_summary(settings, user, identity):
    """Public account options from the current request's freshly read identity."""
    if not settings.github_login_enabled or not user:
        identity = None
    return {"github_enabled": settings.github_login_enabled, "github_linked": identity is not None,
            "github_login": identity.github_login if identity else None,
            "password_enabled": bool(user and user.password_hash),
            "password_recovery": settings.mail_backend != "disabled"}


def _provider_json(client, method, url, deadline, **kwargs):
    try:
        with client.stream(method, url, **kwargs) as response:
            if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                raise ProviderFailure()
            data = bytearray()
            for chunk in response.iter_raw(chunk_size=16384):
                if time.monotonic() > deadline or len(data) + len(chunk) > 65536:
                    raise ProviderFailure()
                data.extend(chunk)
            value = json.loads(bytes(data))
            if type(value) is not dict:
                raise ProviderFailure()
            return value
    except (httpx.HTTPError, httpx.StreamError, ValueError, UnicodeError, RecursionError):
        raise ProviderFailure() from None


def fetch_identity(settings, code, verifier):
    """Fixed HTTPS endpoints, bounded IO, no redirects/proxies/token storage."""
    deadline = time.monotonic() + 15
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False, trust_env=False,
                      headers={"Accept-Encoding": "identity"}) as client:
        token = _provider_json(client, "POST", "https://github.com/login/oauth/access_token", deadline,
            headers={"Accept": "application/json", "User-Agent": "Crucible-sign-in"},
            data={"client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                  "code": code, "code_verifier": verifier, "redirect_uri": settings.github_callback_url})
        access, kind = token.get("access_token"), token.get("token_type")
        if ("error" in token or type(kind) is not str or kind.lower() != "bearer" or token.get("scope") != ""
                or type(access) is not str or not 20 <= len(access) <= 512
                or not re.fullmatch(r"[A-Za-z0-9_-]+", access)):
            raise ProviderFailure()
        profile = _provider_json(client, "GET", "https://api.github.com/user", deadline,
            headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + access,
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Crucible-sign-in"})
    number, login = profile.get("id"), profile.get("login")
    if (type(number) is not int or not 1 <= number <= 2**63 - 1 or profile.get("type") != "User"
            or type(login) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login)):
        raise ProviderFailure()
    return str(number), login


def _binding(request, settings):
    value = request.cookies.get(settings.github_cookie_name, "")
    parts = value.split(".")
    if len(parts) != 2 or not all(_TOKEN.fullmatch(item) for item in parts):
        raise HTTPException(409, "Restart GitHub sign-in.")
    return parts


def _flow(session, request, settings, now, *, lock=False):
    state, binding = _binding(request, settings)
    query = select(GitHubFlow).where(GitHubFlow.state_hash == token_hash(state))
    if lock:
        query = query.with_for_update()
    flow = session.scalar(query.execution_options(populate_existing=True))
    if flow is None or flow.expires_at <= now or not hmac.compare_digest(flow.binding_hash, token_hash(binding)):
        raise HTTPException(409, "Restart GitHub sign-in.")
    return flow


def _original_session(session, flow, now, *, lock=False):
    query = select(LoginSession).where(LoginSession.token_hash == flow.original_session_hash)
    if lock:
        query = query.with_for_update()
    previous = session.scalar(query.execution_options(populate_existing=True))
    if previous is None or previous.expires_at <= now or previous.user_id != flow.original_user_id:
        raise HTTPException(409, "Your session changed. Restart GitHub sign-in.")
    if previous.user_id:
        beta_access.require_not_suspended(session, session.get(User, previous.user_id))
    return previous


def _invitation_available(session, settings, flow, now, *, lock=False):
    if settings.access_mode != "beta":
        return
    query = select(beta_access.BetaInvitation).where(beta_access.BetaInvitation.id == flow.invitation_id)
    if lock:
        query = query.with_for_update()
    invitation = session.scalar(query.execution_options(populate_existing=True))
    if invitation is None or beta_access.invitation_status(invitation, now) != "available":
        raise HTTPException(403, "An active beta invitation is required.")


def register_routes(app, db, settings, clock, identity, issue_session, session_payload, password_operation, throttle):
    def enabled(request):
        if not settings.github_login_enabled:
            raise HTTPException(404, "Not found.")
        if "authorization" in request.headers:
            raise HTTPException(403, "Use a browser to manage sign-in.")
        if str(request.base_url).rstrip("/") != settings.origins[0]:
            raise HTTPException(400, "Use the configured Crucible address.")

    def redirect(status):
        return RedirectResponse("/#auth?github=" + status, status_code=303,
                                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.post("/api/auth/github/start")
    def start(body: Start, request: Request):
        enabled(request)
        throttle(request, "github-start", 20, 900)
        expected_user_id, expected_password = None, None
        if body.mode == "link":
            with db.transaction() as session:
                existing = identity(request, session, mutation=True)
                user = session.get(User, existing.user_id) if existing.user_id else None
                if user is None or not user.password_hash or body.password is None:
                    raise HTTPException(403, "Confirm your existing password to link GitHub.")
                beta_access.require_not_suspended(session, user)
                expected_user_id, expected_password = user.id, user.password_hash
            throttle(request, "github-link-password", 10, 900, account_id=expected_user_id)
            if not password_operation(verify_password, expected_password, body.password.get_secret_value()):
                raise HTTPException(403, "Password confirmation failed.")
        state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db.transaction(write=True) as session:
            previous = identity(request, session, mutation=True)
            if (body.mode == "link") != bool(previous.user_id):
                raise HTTPException(409, "Sign in before linking GitHub, or sign out before choosing another account.")
            if previous.user_id:
                user = beta_access.require_not_suspended(session, beta_access.lock_user(session, previous.user_id))
                if user.id != expected_user_id or user.password_hash != expected_password:
                    raise HTTPException(409, "Your credentials changed. Restart GitHub linking.")
                previous = identity(request, session, mutation=True)
            invitation = beta_access.validate_registration_invitation(session, body.invitation_token, clock()) if body.invitation_token else None
            # One active authorization attempt per original browser session.
            # A second start invalidates stale tabs without storing raw state.
            session.execute(delete(GitHubFlow).where(GitHubFlow.original_session_hash == previous.token_hash))
            identifier = token_hash(state)
            session.add(GitHubFlow(state_hash=identifier, binding_hash=token_hash(binding),
                original_session_hash=previous.token_hash, original_user_id=previous.user_id,
                mode=body.mode, created_at=clock(), expires_at=clock() + FLOW_SECONDS,
                consumed_at=None, status="started", github_user_id=None, github_login=None,
                invitation_id=invitation.id if invitation else None,
                invitation_encrypted=_encrypt_invitation(settings, identifier, body.invitation_token) if invitation else None,
                return_route=body.return_route, result_session_hash=None))
        query = {"client_id": settings.github_client_id, "redirect_uri": settings.github_callback_url,
                 "scope": "", "state": state, "code_challenge": _b64(hashlib.sha256(_verifier(settings, state).encode()).digest()),
                 "code_challenge_method": "S256", "allow_signup": "false", "prompt": "select_account"}
        response = JSONResponse({"authorization_url": "https://github.com/login/oauth/authorize?" + urlencode(query)})
        response.set_cookie(settings.github_cookie_name, state + "." + binding, max_age=FLOW_SECONDS,
                            httponly=True, secure=settings.production, samesite="lax", path="/")
        return response

    @app.get("/api/auth/github/callback")
    def callback(request: Request):
        enabled(request)
        throttle(request, "github-callback", 40, 900)
        # Query parameters are untrusted. Reject duplicates and oversized codes;
        # provider error strings are never reflected into output or exceptions.
        try:
            state, _ = _binding(request, settings)
            if request.query_params.getlist("state") != [state]:
                return redirect("failed")
            with db.transaction(write=True) as session:
                flow = _flow(session, request, settings, clock(), lock=True)
                if flow.consumed_at is not None:
                    return redirect("expired")
                _original_session(session, flow, clock())
                flow.consumed_at, flow.status = clock(), "exchanging"
            if request.query_params.get("error"):
                return redirect("denied")
            codes = request.query_params.getlist("code")
            if len(codes) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", codes[0]):
                return redirect("failed")
            try:
                github_id, github_login = fetch_identity(settings, codes[0], _verifier(settings, state))
            except ProviderFailure:
                return redirect("failed")
            response = redirect("complete")
            with db.transaction(write=True) as session:
                flow = _flow(session, request, settings, clock())
                linked = session.get(GitHubIdentity, github_id)
                user_id = flow.original_user_id if flow.mode == "link" else linked.user_id if linked else None
                user = beta_access.lock_user(session, user_id) if user_id else None
                if user:
                    beta_access.require_not_suspended(session, user)
                # Match start/link's User -> Flow ordering; never hold a flow
                # lock while waiting for User held by a fresh linking request.
                expected_origin = flow.original_user_id, flow.original_session_hash, flow.mode
                flow = _flow(session, request, settings, clock(), lock=True)
                if (flow.original_user_id, flow.original_session_hash, flow.mode) != expected_origin:
                    return redirect("session-changed")
                if flow.status != "exchanging":
                    return redirect("expired")
                previous = _original_session(session, flow, clock(), lock=True)
                if flow.mode == "link":
                    other = session.scalar(select(GitHubIdentity).where(GitHubIdentity.user_id == user.id))
                    if (linked and linked.user_id != user.id) or (other and other.github_user_id != github_id):
                        flow.status = "failed"
                        return redirect("identity-in-use")
                    if linked is None:
                        linked = GitHubIdentity(github_user_id=github_id, user_id=user.id,
                            github_login=github_login, created_at=clock(), last_login_at=clock())
                        session.add(linked)
                    flow.status = "linked"
                    response = redirect("linked")
                elif linked is None:
                    try:
                        _invitation_available(session, settings, flow, clock())
                    except HTTPException:
                        flow.status = "failed"
                        return redirect("invite-required")
                    flow.github_user_id, flow.github_login, flow.status = github_id, github_login, "pending"
                    return redirect("choose-handle")
                else:
                    flow.status = "complete"
                linked.github_login, linked.last_login_at = github_login, clock()
                flow.github_user_id, flow.github_login = github_id, github_login
                if flow.invitation_id is None and flow.invitation_encrypted is None:
                    # Successful existing-account sign-in can enter its page
                    # immediately. Invitation flows still use the continuation
                    # endpoint to recover their browser-bound invitation token.
                    try:
                        destination = Start(return_route=flow.return_route).return_route
                    except ValueError:
                        destination = "#challenge"
                    response = RedirectResponse("/" + destination, status_code=303,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
                session.delete(previous)
                raw = issue_session(response, session, user.id)
                flow.result_session_hash = token_hash(raw)
            return response
        except IntegrityError:
            return redirect("identity-in-use")
        except HTTPException:
            return redirect("session-changed")

    @app.get("/api/auth/github/pending")
    def pending(request: Request):
        enabled(request)
        with db.transaction() as session:
            current = identity(request, session)
            try:
                flow = _flow(session, request, settings, clock())
            except HTTPException:
                return {"pending": False}
            expected = flow.result_session_hash if flow.status in {"complete", "linked"} else flow.original_session_hash
            if current.token_hash != expected:
                return {"pending": False}
            if current.user_id:
                beta_access.require_not_suspended(session, session.get(User, current.user_id))
            result = {"pending": flow.status == "pending", "return_route": flow.return_route}
            if flow.status in {"pending", "complete", "linked"}:
                result.update(github_login=flow.github_login, invitation_token=_decrypt_invitation(settings, flow))
            if flow.status == "pending":
                suggested = flow.github_login.lower().replace("-", "_")[:32]
                result["suggested_handle"] = suggested if len(suggested) >= 3 else "user_" + suggested
            return result

    @app.post("/api/auth/github/complete")
    def complete(body: Complete, request: Request):
        enabled(request)
        throttle(request, "github-complete", 20, 900)
        response = JSONResponse({})
        try:
            with db.transaction(write=True) as session:
                previous = identity(request, session, mutation=True)
                flow = _flow(session, request, settings, clock(), lock=True)
                if (flow.status != "pending" or flow.original_user_id is not None or previous.user_id is not None
                        or previous.token_hash != flow.original_session_hash):
                    raise HTTPException(409, "Restart GitHub sign-in.")
                _original_session(session, flow, clock(), lock=True)
                _invitation_available(session, settings, flow, clock(), lock=True)
                if session.get(GitHubIdentity, flow.github_user_id):
                    raise HTTPException(409, "This GitHub account was connected elsewhere. Sign in again.")
                user = User(id=str(uuid4()), handle=body.handle, password_hash=None, created_at=clock())
                session.add(user)
                session.flush()
                session.add(GitHubIdentity(github_user_id=flow.github_user_id, user_id=user.id,
                    github_login=flow.github_login, created_at=clock(), last_login_at=clock()))
                session.delete(previous)
                raw = issue_session(response, session, user.id)
                flow.status, flow.result_session_hash = "complete", token_hash(raw)
                session.flush()
                response.body = json.dumps(session_payload(user, raw, session)).encode()
                response.headers["content-length"] = str(len(response.body))
        except IntegrityError:
            raise HTTPException(409, "That handle or GitHub account is already in use.") from None
        return response
