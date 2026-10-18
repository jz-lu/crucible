"""Scoped agent credentials approved explicitly through an existing browser login.

The private device code stays in the local client. The browser sees only the
short matching code and requested permissions. Neither device secrets nor
bearer credentials are persisted: only their hashes are stored. A short,
idempotent exchange window lets the client recover a lost response safely.
"""
import base64
import hashlib
import hmac
import re
import secrets
from urllib.parse import quote
from uuid import uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Float, ForeignKey, JSON, String, and_, case, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, User
from . import beta_access, agent_guidance
from .security import token_hash


DEVICE_SECONDS = 600
EXCHANGE_SECONDS = 120
TOKEN_SECONDS = 30 * 86400
POLL_INTERVAL = 2
LEGACY_SCOPES = ("challenges:read", "challenges:write", "methods:read", "methods:write",
                 "submissions:read", "submissions:write")
SCOPES = LEGACY_SCOPES + ("proposals:read", "proposals:write", "notes:read", "notes:rate")
ANONYMOUS_POST_PATHS = frozenset({"/api/agent/connect/start", "/api/agent/connect/poll"})
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_TOKEN = re.compile(r"Bearer (cru_[A-Za-z0-9_-]{43})", re.IGNORECASE)
_USER_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class MachineToken(Base):
    __tablename__ = "machine_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    scopes: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class DeviceConnection(Base):
    __tablename__ = "device_connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(9), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    # Freeze exactly what the browser will approve. Future scope additions must
    # never broaden an already pending or approved connection request.
    scopes: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    last_polled_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    denied_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_id: Mapped[str | None] = mapped_column(ForeignKey("machine_tokens.id"), nullable=True, unique=True)


class ConnectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(default="Crucible agent", min_length=1, max_length=80)


class PollInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_code: str = Field(pattern=r"^crd_[A-Za-z0-9_-]{43}$", min_length=47, max_length=47)


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def source_upload_path(path):
    return re.fullmatch(rf"/api/methods/{_UUID}/source", path) is not None


def required_scope(method, path):
    """An explicit route/method allowlist, never a prefix-wide authorization."""
    if agent_guidance.is_guidance_read(method, path):
        # Every existing agent role can learn its rules. This any-of allowance
        # applies only to the fixed documentation paths, never account data.
        return SCOPES
    if method == "GET":
        if path == "/api/agent/notes" or re.fullmatch(r"/api/agent/notes/[^/]{1,108}", path):
            return "notes:read"
        if path in {"/api/schemes", "/api/leaderboard", "/api/challenges",
                    "/api/protocol/source", "/api/protocol/example", "/api/protocol/spec",
                    "/api/protocol/artifacts", "/api/protocol/artifact-source", "/api/protocol/correctness",
                    "/api/protocol/reference-method", "/api/protocol/benchmarks", "/api/protocol/assumptions", "/api/protocol/evidence-note",
                    "/api/agent/client"} or re.fullmatch(rf"/api/challenges/{_UUID}/download", path):
            return "challenges:read"
        if path == "/api/methods" or re.fullmatch(rf"/api/methods/{_UUID}/source(?:/download)?", path):
            return "methods:read"
        if path == "/api/me/submissions" or re.fullmatch(rf"/api/submissions/{_UUID}", path):
            return "submissions:read"
        if path in {"/api/protocol/spec-schema", "/api/protocol/spec-template", "/api/protocol/proposal-guide",
                    "/api/me/protocol-proposals"} or re.fullmatch(
                        rf"/api/protocol/proposals/{_UUID}(?:/revisions)?", path):
            return "proposals:read"
    if method == "POST":
        if re.fullmatch(r"/api/agent/notes/[^/]{1,108}/rating", path):
            return "notes:rate"
        if path == "/api/challenges":
            return "challenges:write"
        if path == "/api/methods" or source_upload_path(path):
            return "methods:write"
        if re.fullmatch(rf"/api/challenges/{_UUID}/submit", path):
            return "submissions:write"
        if path in {"/api/protocol/proposals/validate", "/api/protocol/proposals"} or re.fullmatch(
                rf"/api/protocol/proposals/{_UUID}/revisions", path):
            return "proposals:write"
    return None


def _bearer(header):
    match = _TOKEN.fullmatch(header) if isinstance(header, str) else None
    if match is None or not match.group(1).startswith("cru_"):
        raise HTTPException(401, "Invalid or expired agent credential.")
    return match.group(1)


def authorize(header, method, path, session, settings, clock, *, mutation=False):
    raw = _bearer(header)
    scope = required_scope(method, path)
    if scope is None:
        raise HTTPException(403, "Agent credentials cannot access this action. Use your browser account.")
    query = select(MachineToken).where(MachineToken.token_hash == token_hash(raw))
    token = session.scalar(query)
    if token is None or token.revoked_at is not None or token.expires_at <= clock():
        raise HTTPException(401, "Invalid or expired agent credential.")
    accepted_scopes = scope if isinstance(scope, tuple) else (scope,)
    if not isinstance(token.scopes, list) or not any(value in token.scopes for value in accepted_scopes):
        raise HTTPException(403, "This agent credential does not allow that action.")
    user = session.get(User, token.user_id)
    beta_access.require_access(session, settings, user, clock(), mutation=mutation)
    if mutation:
        # Membership revocation locks User before credentials. Match that order
        # and re-read the token after waiting, so a stale approval cannot win.
        token = session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if token is None or token.revoked_at is not None or token.expires_at <= clock():
            raise HTTPException(401, "Invalid or expired agent credential.")
        if not isinstance(token.scopes, list) or not any(value in token.scopes for value in accepted_scopes):
            raise HTTPException(403, "This agent credential does not allow that action.")
    return token


def request_identity(request, session, settings, clock, *, mutation=False):
    return authorize(request.headers.get("authorization"), request.method, request.url.path,
                     session, settings, clock, mutation=mutation)


def _derived_token(settings, device_code, connection_id):
    message = b"crucible/agent-credential/v1\x00" + device_code.encode("ascii") + b"\x00" + connection_id.encode("ascii")
    digest = hmac.new(settings.secret, message, hashlib.sha256).digest()
    return "cru_" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _user_code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z2-9]{4}-?[A-Za-z2-9]{4}", value):
        raise HTTPException(404, "Connection request not found.")
    code = value.replace("-", "").upper()
    return code[:4] + "-" + code[4:]


def _status(connection, now):
    if connection.denied_at is not None:
        return "denied"
    deadline = connection.approved_at + EXCHANGE_SECONDS if connection.approved_at is not None else connection.expires_at
    if now >= deadline:
        return "expired"
    return "approved" if connection.approved_at is not None else "pending"


def _connection_data(connection, now):
    return {"user_code": connection.user_code, "label": connection.label,
            "scopes": list(connection.scopes), "created_at": connection.created_at,
            "expires_at": connection.approved_at + EXCHANGE_SECONDS if connection.approved_at is not None else connection.expires_at,
            "status": _status(connection, now)}


def _token_data(token, now):
    return {"id": token.id, "label": token.label, "scopes": list(token.scopes),
            "created_at": token.created_at, "expires_at": token.expires_at,
            "revoked_at": token.revoked_at,
            "active": token.revoked_at is None and token.expires_at > now}


def register_routes(app, db, settings, clock, user_for, throttle):
    @app.post("/api/agent/connect/start")
    def start(body: ConnectInput, request: Request):
        throttle(request, "agent-connect-start", 300 if settings.access_mode == "beta" else 10, 600)
        for _ in range(3):
            raw = "crd_" + secrets.token_urlsafe(32)
            code = "".join(secrets.choice(_USER_ALPHABET) for _ in range(8))
            code = code[:4] + "-" + code[4:]
            now = clock()
            connection = DeviceConnection(id=str(uuid4()), device_hash=token_hash(raw), user_code=code,
                                          label=body.label, scopes=list(SCOPES),
                                          created_at=now, expires_at=now+DEVICE_SECONDS)
            try:
                with db.transaction(write=True) as session:
                    session.add(connection)
                break
            except IntegrityError:
                continue
        else:
            raise HTTPException(503, "Could not start a connection. Try again.")
        request_origin = f"{request.url.scheme}://{request.url.netloc}"
        origin = request_origin if request_origin in settings.origins else settings.origins[0]
        return {"device_code": raw, "user_code": code,
                "verification_uri": origin + "/#connect?code=" + quote(code),
                "expires_at": now+DEVICE_SECONDS, "expires_in": DEVICE_SECONDS,
                "interval": POLL_INTERVAL, "scopes": list(connection.scopes)}

    @app.post("/api/agent/connect/poll")
    def poll(body: PollInput, request: Request):
        digest = token_hash(body.device_code)
        # A cohort may share one institutional egress address. Per-device
        # polling intervals and counters remain authoritative within this cap.
        throttle(request, "agent-connect-poll-ip", 5000 if settings.access_mode == "beta" else 120, 60)
        throttle(request, "agent-connect-poll-device", 60, 60, digest)
        with db.transaction(write=True) as session:
            query = select(DeviceConnection).where(DeviceConnection.device_hash == digest)
            connection = session.scalar(query)
            observed_owner = connection.approved_user_id if connection else None
            if observed_owner:
                beta_access.lock_user(session, observed_owner)
            connection = session.scalar(query.with_for_update().execution_options(populate_existing=True))
            now = clock()
            status = _status(connection, now) if connection else "expired"
            if status in {"expired", "denied"}:
                return {"status": status, "interval": POLL_INTERVAL}
            if connection.approved_user_id != observed_owner:
                # Approval arrived while acquiring the connection lock. Retry
                # in User->Connection order instead of inverting revocation.
                return {"status": "pending", "interval": POLL_INTERVAL}
            if status == "pending":
                if connection.last_polled_at is not None and now < connection.last_polled_at + POLL_INTERVAL:
                    raise HTTPException(429, "Wait before checking this connection again.", headers={"Retry-After": str(POLL_INTERVAL)})
                connection.last_polled_at = now
                return {"status": "pending", "interval": POLL_INTERVAL}
            user = session.get(User, connection.approved_user_id)
            if user is None:
                return {"status": "expired", "interval": POLL_INTERVAL}
            try:
                beta_access.require_access(session, settings, user, now)
            except HTTPException:
                return {"status": "denied", "interval": POLL_INTERVAL}
            raw = _derived_token(settings, body.device_code, connection.id)
            if connection.token_id is None:
                token = MachineToken(id=str(uuid4()), user_id=user.id, token_hash=token_hash(raw),
                                     label=connection.label, scopes=list(connection.scopes), created_at=now,
                                     expires_at=now+TOKEN_SECONDS)
                session.add(token)
                session.flush()
                connection.token_id = token.id
            else:
                token = session.scalar(select(MachineToken).where(
                    MachineToken.id == connection.token_id).with_for_update())
                if token is None or token.revoked_at is not None or token.expires_at <= now:
                    return {"status": "denied", "interval": POLL_INTERVAL}
                if not hmac.compare_digest(token.token_hash, token_hash(raw)):
                    return {"status": "expired", "interval": POLL_INTERVAL}
            return {"status": "authorized", "access_token": raw, "token_type": "Bearer",
                    "expires_at": token.expires_at, "scopes": list(token.scopes),
                    "user": {"handle": user.handle}, "token": {"id": token.id, "label": token.label}}

    def owned_connection(session, code, user, *, write=False):
        query = select(DeviceConnection).where(DeviceConnection.user_code == _user_code(code))
        connection = session.scalar(query.with_for_update() if write else query)
        if connection is None or connection.approved_user_id not in {None, user.id}:
            raise HTTPException(404, "Connection request not found.")
        return connection

    @app.get("/api/agent/connect/{code}")
    def preview(code: str, request: Request):
        throttle(request, "agent-connect-preview", 600 if settings.access_mode == "beta" else 60, 60)
        with db.transaction() as session:
            user = user_for(request, session)
            connection = owned_connection(session, code, user)
            return {"connection": _connection_data(connection, clock())}

    @app.post("/api/agent/connect/{code}/approve")
    def approve(code: str, body: EmptyInput, request: Request):
        throttle(request, "agent-connect-approve", 600 if settings.access_mode == "beta" else 20, 600)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            connection = owned_connection(session, code, user, write=True)
            now = clock()
            status = _status(connection, now)
            if status in {"expired", "denied"}:
                raise HTTPException(410, "This connection request is no longer available. Start a new one from your agent.")
            if status == "pending":
                connection.approved_user_id, connection.approved_at = user.id, now
            return {"connection": _connection_data(connection, now)}

    @app.post("/api/agent/connect/{code}/deny")
    def deny(code: str, body: EmptyInput, request: Request):
        throttle(request, "agent-connect-deny", 600 if settings.access_mode == "beta" else 20, 600)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            connection = owned_connection(session, code, user, write=True)
            now = clock()
            connection.denied_at = now
            if connection.token_id is not None:
                token = session.scalar(select(MachineToken).where(
                    MachineToken.id == connection.token_id).with_for_update())
                if token is not None and token.revoked_at is None:
                    token.revoked_at = now
            return {"connection": _connection_data(connection, now)}

    @app.get("/api/agent/tokens")
    def tokens(request: Request, limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        with db.transaction() as session:
            user = user_for(request, session)
            now = clock()
            active_first = case((and_(MachineToken.revoked_at.is_(None), MachineToken.expires_at > now), 0), else_=1)
            rows = session.scalars(select(MachineToken).where(MachineToken.user_id == user.id)
                                   .order_by(active_first, MachineToken.created_at.desc(), MachineToken.id)
                                   .offset(offset).limit(limit+1)).all()
            return {"tokens": [_token_data(token, now) for token in rows[:limit]],
                    "pagination": {"limit": limit, "offset": offset, "has_more": len(rows) > limit}}

    @app.post("/api/agent/tokens/{token_id}/revoke")
    def revoke(token_id: str, body: EmptyInput, request: Request):
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            token = session.scalar(select(MachineToken).where(
                MachineToken.id == token_id, MachineToken.user_id == user.id).with_for_update())
            if token is None:
                raise HTTPException(404, "Agent credential not found.")
            if token.revoked_at is None:
                token.revoked_at = clock()
            return {"token": _token_data(token, clock())}
