"""Same-origin API. Clients submit answers; the server owns time and verification."""
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import kem, machine_auth, method_sources, people, protocol_proposals, registry, research
from .config import ROOT, Settings, load_settings
from .db import AdapterVersion, Base, Challenge, Database, LoginSession, Method, ProfileIncident, RateBucket, Submission, User
from .schemas import Credentials, NewChallenge, NewMethod, NewSubmission
from .security import mac, passwords, payload_mac, target_mac, token_hash, verify_password


def current_round(now):
    number = int(now) // 3600
    return {"id": number, "starts_at": number * 3600, "ends_at": (number + 1) * 3600}


class RequestBoundary:
    """Bound complete request bytes before parsing and record their arrival time.

    In production also configure proxy body limits/timeouts; do not log bodies.
    No forwarded IP is trusted here. The ASGI server must trust only its proxy.
    """
    def __init__(self, app, settings, clock, db):
        self.app, self.settings, self.clock, self.db = app, settings, clock, db

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope["headers"]}
        authorization = [value for key, value in scope["headers"] if key.lower() == b"authorization"]
        machine = False
        if authorization:
            if len(authorization) != 1:
                return await JSONResponse({"detail": "Invalid or expired agent credential."}, 401,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
            def authenticate_machine():
                with self.db.transaction() as session:
                    machine_auth.authorize(authorization[0].decode("latin1"), scope["method"], scope["path"],
                                           session, self.settings, self.clock)
            try:
                await run_in_threadpool(authenticate_machine)
            except HTTPException as error:
                return await JSONResponse({"detail": error.detail}, error.status_code,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
            machine = True
        unsafe = scope["method"] not in {"GET", "HEAD", "OPTIONS"}
        body = b""
        if unsafe:
            origin = headers.get(b"origin", b"").decode("latin1")
            device_request = scope["method"] == "POST" and scope["path"] in machine_auth.ANONYMOUS_POST_PATHS
            if not machine and not device_request and origin not in self.settings.origins:
                return await JSONResponse({"detail": "A same-origin request is required."}, 403)(scope, receive, send)
            if headers.get(b"content-type", b"").split(b";")[0].strip().lower() != b"application/json":
                return await JSONResponse({"detail": "Send an application/json request."}, 415)(scope, receive, send)
            try:
                limit = 2097152 if scope["method"] == "POST" and machine_auth.source_upload_path(scope["path"]) else 65536
                if int(headers.get(b"content-length", b"0")) > limit:
                    return await JSONResponse({"detail": "Request is too large."}, 413)(scope, receive, send)
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body += message.get("body", b"")
                        if len(body) > limit:
                            return await JSONResponse({"detail": "Request is too large."}, 413)(scope, receive, send)
                        if not message.get("more_body", False):
                            break
            except (ValueError, TimeoutError):
                return await JSONResponse({"detail": "Incomplete or invalid request."}, 408)(scope, receive, send)
        scope.setdefault("state", {})["received_at"] = self.clock()
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if unsafe and not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        async def secure_send(message):
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ]
                if scope["path"].startswith("/api"):
                    extra.append((b"cache-control", b"no-store"))
                if self.settings.production:
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)
        await self.app(scope, buffered_receive, secure_send)


def create_app(settings: Settings | None = None, *, clock=time.time, initialize=False):
    registry.validate_registry()
    settings = settings or load_settings()
    db = Database(settings.database_url)
    if initialize:  # Explicit test fixture only. Normal startup uses Alembic.
        Base.metadata.create_all(db.engine)
    app = FastAPI(title="Crucible recovery API", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.db, app.state.settings, app.state.clock = db, settings, clock
    app.add_middleware(RequestBoundary, settings=settings, clock=clock, db=db)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, error):
        if request.url.path.startswith("/api/protocol/proposals"):
            return protocol_proposals.validation_error_response(error)
        # Pydantic's default errors echo input values, which can include secrets.
        fields = sorted({str(part) for issue in error.errors() for part in issue["loc"] if part not in {"body", "query"}})
        detail = "Check the format and required values for: " + ", ".join(fields) + "." if fields else "Check the request format and required values."
        return JSONResponse({"detail": detail}, 422)

    def envelope(**values):
        now = clock()
        return {**values, "server_time": now, "round": current_round(now)}

    def session_for(request, session, required=False):
        if "authorization" in request.headers:
            return machine_auth.request_identity(request, session, settings, clock)
        raw = request.cookies.get(settings.cookie_name, "")
        record = None
        if raw and raw.isascii() and len(raw) <= 128:
            record = session.get(LoginSession, token_hash(raw))
        if record is None or record.expires_at <= clock():
            if required:
                raise HTTPException(401, "Sign in to continue.")
            return None
        return record

    def identity(request, session, *, mutation=False):
        if "authorization" in request.headers:
            raise HTTPException(403, "Agent credentials cannot manage browser accounts.")
        record = session_for(request, session, required=True)
        if mutation:
            supplied = request.headers.get("x-csrf-token", "")
            expected = mac(settings.secret, "csrf-v1", request.cookies[settings.cookie_name].encode("ascii"))
            if not supplied.isascii() or not hmac.compare_digest(supplied, expected):
                raise HTTPException(403, "Your session changed. Refresh and try again.")
        return record

    def user_for(request, session, *, mutation=False):
        record = (machine_auth.request_identity(request, session, settings, clock, mutation=mutation)
                  if "authorization" in request.headers else identity(request, session, mutation=mutation))
        if not record.user_id:
            raise HTTPException(401, "Sign in to continue.")
        return session.get(User, record.user_id)

    def issue_session(response, session, user_id=None):
        raw = secrets.token_urlsafe(32)
        session.add(LoginSession(token_hash=token_hash(raw), user_id=user_id, expires_at=clock() + settings.session_seconds))
        response.set_cookie(settings.cookie_name, raw, max_age=settings.session_seconds, httponly=True,
                            secure=settings.production, samesite="strict", path="/")
        return raw

    def session_payload(user, raw):
        return envelope(user={"id": user.id, "handle": user.handle} if user else None,
                        capabilities={"review_protocol_proposals": protocol_proposals.is_reviewer(settings, user)},
                        csrf_token=mac(settings.secret, "csrf-v1", raw.encode("ascii")))

    def throttle(request, name, limit, seconds, extra=""):
        if not settings.rate_limits:
            return
        # Persist counters in their own transaction so rejected operations count.
        peer = request.client.host if request.client else "unknown"
        key = mac(settings.secret, "rate-v1", f"{name}:{peer}:{extra}".encode())
        now = clock()
        bucket = int(now) // seconds
        with db.transaction(write=True) as session:
            if db.sqlite:
                from sqlalchemy.dialects.sqlite import insert
            else:
                from sqlalchemy.dialects.postgresql import insert
            statement = insert(RateBucket).values(key=key, bucket=bucket, count=1, expires_at=(bucket + 2) * seconds)
            count = session.execute(statement.on_conflict_do_update(index_elements=["key", "bucket"],
                                     set_={"count": RateBucket.count + 1}).returning(RateBucket.count)).scalar_one()
        if count > limit:
            raise HTTPException(429, "Too many requests. Please try again later.", headers={"Retry-After": str(max(1, int((bucket + 1) * seconds - now)))})

    def adapter_for(scheme_version=None, scheme_id=kem.SCHEME_ID):
        try:
            return registry.adapter_for(scheme_id, scheme_version)
        except ValueError:
            raise HTTPException(404, "Unknown scheme or version.") from None

    def manifest_for(profile_id, scheme_version=None, scheme_id=None):
        try:
            return registry.manifest_for(profile_id, scheme_id, scheme_version)
        except ValueError:
            raise HTTPException(404, "Unknown parameter profile for this scheme and version.") from None

    def challenge_manifest(body):
        if body.profile_id is not None:
            manifest = manifest_for(body.profile_id, body.scheme_version, body.scheme_id)
        else:
            adapter = adapter_for(body.scheme_version, body.scheme_id)
            manifest = next((item for item in adapter.manifests()
                             if item.get("instance_size", item["parameter_bits"]) == body.instance_size), None)
            if manifest is None:
                raise HTTPException(404, "Unknown instance size for this scheme.")
        if manifest["version"] != registry.CURRENT[manifest["scheme_id"]]:
            raise HTTPException(409, "New challenges use the current registered scheme version.")
        return manifest

    def pin_adapter(session, manifest):
        identity_ = (manifest["scheme_id"], manifest["version"])
        current_hash = manifest["implementation"]["source_sha256"]
        existing_pin = session.get(AdapterVersion, identity_)
        if existing_pin is None:
            # Upgrading a pre-pin database must respect its existing releases.
            # Missing or conflicting historical provenance is a stop, not an
            # invitation to bless the currently running source as authoritative.
            legacy = session.scalars(select(Challenge.manifest).where(
                Challenge.scheme_id == identity_[0], Challenge.scheme_version == identity_[1])).yield_per(100)
            for previous in legacy:
                if previous.get("implementation", {}).get("source_sha256") != current_hash:
                    raise HTTPException(409, "Historical instances use a different or unrecorded implementation. Review the release and use a new scheme version.")
        if db.sqlite:
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(AdapterVersion).values(scheme_id=manifest["scheme_id"], version=manifest["version"],
            source_sha256=current_hash, created_at=clock()).on_conflict_do_nothing())
        pinned = session.get(AdapterVersion, identity_)
        if pinned.source_sha256 != current_hash:
            raise HTTPException(409, "The implementation changed without a new scheme version. New challenges are paused.")

    def owned_challenge(session, challenge_id, user_id, *, lock=False):
        query = select(Challenge).where(Challenge.id == challenge_id, Challenge.user_id == user_id)
        if lock:
            query = query.with_for_update()
        result = session.scalar(query)
        if not result:
            raise HTTPException(404, "Challenge not found.")
        return result

    def submission_data(submission):
        return {"id": submission.id, "status": "correct" if submission.correct else "incorrect",
                "received_at": submission.received_at, "method_id": submission.method_id}

    def challenge_data(session, challenge):
        submission = session.scalar(select(Submission).where(Submission.challenge_id == challenge.id))
        status = "open" if clock() < challenge.ends_at else "expired"
        if submission:
            status = "solved" if submission.correct else "incorrect"
        return {"id": challenge.id, "profile_id": challenge.profile_id, "round_id": challenge.round_id,
                "scheme_id": challenge.scheme_id, "scheme_version": challenge.scheme_version,
                "issued_at": challenge.issued_at, "starts_at": challenge.starts_at, "ends_at": challenge.ends_at,
                "public": challenge.public_data, "status": status,
                "submission": submission_data(submission) if submission else None,
                "download_url": f"/api/challenges/{challenge.id}/download"}

    @app.get("/api/session")
    def get_session(request: Request):
        throttle(request, "session", 90, 60)
        response = JSONResponse({})
        with db.transaction(write=True) as session:
            record = session_for(request, session)
            raw = request.cookies.get(settings.cookie_name)
            if not record:
                raw = issue_session(response, session)
            user = session.get(User, record.user_id) if record and record.user_id else None
            response.body = json.dumps(session_payload(user, raw)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.post("/api/auth/register")
    def register(body: Credentials, request: Request):
        throttle(request, "register", 5, 3600)
        with db.transaction() as session:
            identity(request, session, mutation=True)
        encoded = passwords.hash(body.password)
        response = JSONResponse({})
        try:
            with db.transaction(write=True) as session:
                previous = identity(request, session, mutation=True)
                user = User(id=str(uuid4()), handle=body.handle, password_hash=encoded, created_at=clock())
                session.add(user)
                session.flush()
                session.delete(previous)
                raw = issue_session(response, session, user.id)
                response.body = json.dumps(session_payload(user, raw)).encode()
                response.headers["content-length"] = str(len(response.body))
        except IntegrityError:
            raise HTTPException(409, "That handle is unavailable.")
        return response

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request):
        throttle(request, "login-ip", 30, 900)
        throttle(request, "login-handle", 10, 900, body.handle)
        with db.transaction() as session:
            identity(request, session, mutation=True)
            user = session.scalar(select(User).where(User.handle == body.handle))
            valid = verify_password(user.password_hash if user else None, body.password)
            if not user or not valid:
                raise HTTPException(401, "Handle or password is incorrect.")
            user_id = user.id
        response = JSONResponse({})
        with db.transaction(write=True) as session:
            previous = identity(request, session, mutation=True)
            session.delete(previous)
            user = session.get(User, user_id)
            if passwords.check_needs_rehash(user.password_hash):
                user.password_hash = passwords.hash(body.password)
            raw = issue_session(response, session, user_id)
            response.body = json.dumps(session_payload(user, raw)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        with db.transaction(write=True) as session:
            previous = identity(request, session, mutation=True)
            session.delete(previous)
            response = JSONResponse({})
            raw = issue_session(response, session)
            response.body = json.dumps(session_payload(None, raw)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.get("/api/schemes")
    def schemes():
        catalogue = registry.catalogue()
        releases = [release for scheme in catalogue for release in [scheme, *scheme["previous_releases"]]]
        # One bounded-result query for the whole catalogue: first successful
        # receipt per exact scheme/version/profile, using the leaderboard's tie
        # rule. Do not mistake a first recovery at a smaller size for the solver
        # who first reached the largest displayed size.
        current_releases = [and_(Challenge.scheme_id == scheme["id"],
                                 Challenge.scheme_version == scheme["version"]) for scheme in releases]
        first_receipts = select(
            Challenge.scheme_id.label("scheme_id"), Challenge.scheme_version.label("scheme_version"),
            Submission.profile_id.label("profile_id"), Submission.user_id.label("user_id"),
            Submission.received_at.label("received_at"), Submission.id.label("receipt_id"),
            func.row_number().over(
                partition_by=(Challenge.scheme_id, Challenge.scheme_version, Submission.profile_id),
                order_by=(Submission.received_at, Submission.id)).label("position"),
        ).join(Challenge, Challenge.id == Submission.challenge_id).join(
            Method, Method.id == Submission.method_id).where(
            Submission.correct.is_(True), Challenge.correctness_passed.is_(True),
            Submission.profile_id == Challenge.profile_id,
            Submission.scheme_version == Challenge.scheme_version,
            Submission.user_id == Challenge.user_id, Submission.user_id == Method.user_id,
            Submission.round_id == Challenge.round_id,
            Submission.received_at >= Challenge.issued_at,
            Submission.received_at < Challenge.ends_at,
            or_(*current_releases)).subquery()
        with db.transaction() as session:
            records = session.execute(select(first_receipts, User.handle)
                .join(User, User.id == first_receipts.c.user_id).where(first_receipts.c.position == 1)).all()
            participants = registry.participant_counts(session, releases)
        recovered = {(row.scheme_id, row.scheme_version, row.profile_id): row for row in records}
        for scheme in releases:
            scheme["participant_count"] = participants.get((scheme["id"], scheme["version"]), 0)
            profiles = scheme["profiles"]
            first_by_profile = {}
            for profile in profiles:
                receipts = [recovered[(scheme["id"], version, profile["id"])]
                    for version in registry.comparison_versions(scheme["id"], scheme["version"], profile["id"])
                    if (scheme["id"], version, profile["id"]) in recovered]
                if receipts:
                    first_by_profile[profile["id"]] = min(receipts, key=lambda row: (row.received_at, row.receipt_id))
            scheme["recovered_profile_ids"] = [profile["id"] for profile in profiles if profile["id"] in first_by_profile]
            largest = max((index for index, profile in enumerate(profiles) if profile["id"] in first_by_profile), default=-1)
            largest_id = profiles[largest]["id"] if largest >= 0 else None
            scheme["largest_recovered_profile_id"] = largest_id
            scheme["next_profile_id"] = profiles[largest + 1]["id"] if largest + 1 < len(profiles) else None
            first = first_by_profile.get(largest_id)
            scheme["first_recovery"] = {"handle": first.handle, "received_at": first.received_at,
                "profile_id": first.profile_id, "scheme_version": first.scheme_version,
                "submission_id": first.receipt_id} if first else None
        return envelope(schemes=catalogue)

    @app.get("/api/challenges")
    def current_challenge(profile_id: str, request: Request, scheme_id: str | None = None,
                          scheme_version: str | None = None):
        manifest = manifest_for(profile_id, scheme_version, scheme_id)
        with db.transaction() as session:
            user = user_for(request, session)
            observed_at = clock()
            round_ = current_round(observed_at)
            challenge = session.scalar(select(Challenge).where(Challenge.user_id == user.id, Challenge.profile_id == profile_id,
                      Challenge.scheme_id == manifest["scheme_id"], Challenge.scheme_version == manifest["version"],
                      Challenge.round_id == round_["id"]))
            started = manifest["version"] == registry.CURRENT[manifest["scheme_id"]] and session.scalar(
                select(Challenge.id).where(Challenge.user_id == user.id,
                    Challenge.scheme_id == manifest["scheme_id"], Challenge.scheme_version == manifest["version"],
                    Challenge.round_id == round_["id"]).limit(1)) is not None
            # Describe the exact queried round, even if the hour turns while
            # reading. A later guarded POST must still match the server's round.
            return {"challenge": challenge_data(session, challenge) if challenge else None,
                    "round_started": started, "round_started_round_id": round_["id"],
                    "server_time": observed_at, "round": round_}

    @app.post("/api/challenges")
    def start_challenge(body: NewChallenge, request: Request):
        throttle(request, "issue", 30, 60)
        manifest = challenge_manifest(body)
        profile_id = manifest["id"]
        adapter = adapter_for(manifest["version"], manifest["scheme_id"])
        # Adapters are an explicit code registry; proposals never execute uploads.
        if not manifest.get("educational"):
            raise HTTPException(409, "This profile has not been admitted to this challenge track.")

        def preflight(session, *, expected_user=None, expected_round=None):
            user = user_for(request, session, mutation=True)
            if expected_user is not None and user.id != expected_user:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            # This short lock serializes final insertion for one account on
            # PostgreSQL. SQLite serializes only these brief write phases.
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
            round_ = current_round(clock())
            if expected_round is not None and round_["id"] != expected_round:
                raise HTTPException(409, "The round just changed. Request the new round.")
            # Generation may have outlived a release/configuration change. Do
            # not attach its result to a replacement source or parameter set.
            if challenge_manifest(body) != manifest:
                raise HTTPException(409, "The registered challenge changed. Refresh and try again.")
            if body.active_round_id is not None:
                if body.active_round_id != round_["id"]:
                    raise HTTPException(409, "The active round changed. Request the new round manually.")
                started = session.scalar(select(Challenge.id).where(Challenge.user_id == user.id,
                    Challenge.scheme_id == manifest["scheme_id"], Challenge.scheme_version == manifest["version"],
                    Challenge.round_id == round_["id"]).limit(1))
                if started is None:
                    raise HTTPException(409, "Request this scheme's current round before opening another tier automatically.")
            pin_adapter(session, manifest)
            existing = session.scalar(select(Challenge).where(Challenge.user_id == user.id, Challenge.profile_id == profile_id,
                      Challenge.scheme_id == manifest["scheme_id"],
                      Challenge.scheme_version == manifest["version"], Challenge.round_id == round_["id"]))
            if existing:
                return user.id, round_, existing
            held = session.scalar(select(ProfileIncident.id).where(ProfileIncident.profile_id == profile_id,
                 ProfileIncident.scheme_version == manifest["version"], ProfileIncident.resolved.is_(False)).limit(1))
            if held:
                raise HTTPException(503, "This profile is paused for an implementation review.")
            return user.id, round_, None

        def existing_response(session, existing, round_):
            return envelope(challenge=challenge_data(session, existing), round_started=True,
                            round_started_round_id=round_["id"])

        with db.transaction(write=True) as session:
            user_id, round_, existing = preflight(session)
            if existing:
                return existing_response(session, existing, round_)

        def record_incident(diagnostics):
            # A real adapter failure remains a fault even if the request's
            # session or round expires meanwhile. Persist fixed diagnostics,
            # never exception text or generated private values.
            with db.transaction(write=True) as session:
                session.add(ProfileIncident(id=str(uuid4()), profile_id=profile_id,
                    scheme_version=manifest["version"], created_at=clock(), diagnostics={
                        **diagnostics, "implementation_sha256": manifest["implementation"]["source_sha256"]}))

        # Expensive fresh-key generation holds no DB session, transaction, or
        # account lock. Concurrent duplicate requests may compute candidates;
        # the final phase returns the one durable instance to every caller.
        try:
            generated = adapter.generate(profile_id)
        except Exception as error:
            limit_type = getattr(adapter, "PrimeGenerationLimit", None)
            if isinstance(limit_type, type) and isinstance(error, limit_type):
                raise HTTPException(503, "Parameter generation exhausted its bounded search. Please try again.") from None
            record_incident({"reason": "adapter_execution_failed"})
            raise HTTPException(503, "This profile encountered an implementation fault and is paused for review.") from None
        if not generated.diagnostics.get("shared_secret_matches", False):
            # Never hide a failed round trip by resampling.
            record_incident({"shared_secret_matches": False})
            raise HTTPException(503, "This profile failed its implementation check and is paused for review.")

        with db.transaction(write=True) as session:
            _, final_round, existing = preflight(session, expected_user=user_id, expected_round=round_["id"])
            if existing:
                return existing_response(session, existing, final_round)
            issued = clock()
            if issued >= round_["ends_at"]:
                raise HTTPException(409, "The round just changed. Request the new round.")
            challenge_id = str(uuid4())
            challenge = Challenge(id=challenge_id, user_id=user_id, scheme_id=manifest["scheme_id"],
                scheme_version=manifest["version"], profile_id=profile_id, round_id=round_["id"],
                issued_at=issued, starts_at=round_["starts_at"], ends_at=round_["ends_at"],
                public_data=generated.public, manifest=manifest,
                target_mac=target_mac(settings.secret, challenge_id, generated.target), correctness_passed=True)
            session.add(challenge)
            session.flush()
            return envelope(challenge=challenge_data(session, challenge), round_started=True,
                            round_started_round_id=round_["id"])

    @app.get("/api/challenges/{challenge_id}/download")
    def download(challenge_id: str, request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            challenge = owned_challenge(session, challenge_id, user.id)
            value = {"format": "crucible-challenge-v1", "objective": "Recover the complete encapsulated shared secret.",
                     "challenge": challenge_data(session, challenge), "scheme_manifest": challenge.manifest,
                     "deadline_rule": "Complete submission body must reach this server before ends_at (exclusive)."}
            return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="crucible-{challenge.id}.json"'})

    @app.get("/api/methods")
    def methods(request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            records = session.scalars(select(Method).where(Method.user_id == user.id).order_by(Method.created_at.desc()).limit(200)).all()
            return {"methods": [{"id": method.id, "name": method.name, "pseudocode": method.pseudocode, "created_at": method.created_at} for method in records]}

    @app.post("/api/methods")
    def save_method(body: NewMethod, request: Request):
        throttle(request, "methods", 30, 3600)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            method = Method(id=str(uuid4()), user_id=user.id, name=body.name, pseudocode=body.pseudocode, created_at=clock())
            session.add(method)
            return {"method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode, "created_at": method.created_at}}

    @app.post("/api/challenges/{challenge_id}/submit")
    def submit(challenge_id: str, body: NewSubmission, request: Request):
        throttle(request, "submit", 30, 60)
        received = request.state.received_at
        payload = body.model_dump(mode="json")
        commitment = payload_mac(settings.secret, {"challenge_id": challenge_id, **payload})
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            # Serialize all this user's answer writes, including cross-challenge
            # reuse of an idempotency key. SQLite serializes write transactions.
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
            challenge = owned_challenge(session, challenge_id, user.id, lock=True)
            expected_bytes = challenge.manifest.get("shared_secret_bytes") if isinstance(challenge.manifest, dict) else None
            if type(expected_bytes) is not int or not 1 <= expected_bytes <= 4096:
                raise HTTPException(503, "This challenge has an invalid shared-secret encoding declaration and needs review.")
            if len(body.candidate) != expected_bytes * 2:
                raise HTTPException(422, "The candidate must match this challenge's declared shared-secret byte length.")
            replay = session.scalar(select(Submission).where(Submission.user_id == user.id, Submission.idempotency_key == str(body.idempotency_key)))
            if replay:
                if not hmac.compare_digest(replay.payload_mac, commitment):
                    raise HTTPException(409, "That retry identifier belongs to a different submission.")
                return envelope(submission=submission_data(replay), challenge=challenge_data(session, challenge))
            if session.scalar(select(Submission.id).where(Submission.challenge_id == challenge.id)):
                raise HTTPException(409, "This challenge already has a final answer.")
            if received < challenge.issued_at or received >= challenge.ends_at:
                raise HTTPException(409, "This round has closed. Answers must arrive before the hour ends.")
            method = session.get(Method, str(body.method_id))
            if not method or method.user_id != user.id:
                raise HTTPException(404, "Method not found.")
            correct = hmac.compare_digest(challenge.target_mac, target_mac(settings.secret, challenge.id, bytes.fromhex(body.candidate)))
            record = Submission(id=str(uuid4()), challenge_id=challenge.id, user_id=user.id, method_id=method.id,
                profile_id=challenge.profile_id, scheme_version=challenge.scheme_version, round_id=challenge.round_id,
                idempotency_key=str(body.idempotency_key), payload_mac=commitment, correct=correct,
                received_at=received, elapsed_seconds=received - challenge.issued_at, compute=body.compute.model_dump())
            session.add(record)
            session.flush()
            return envelope(submission=submission_data(record), challenge=challenge_data(session, challenge))

    @app.get("/api/leaderboard")
    def leaderboard(profile_id: str, scheme_version: str | None = None, scheme_id: str | None = None):
        manifest = manifest_for(profile_id, scheme_version, scheme_id)
        target_size = manifest.get("instance_size", manifest["parameter_bits"])
        source_profiles, source_sizes = {}, {}
        adapter = adapter_for(manifest["version"], manifest["scheme_id"])
        # Credit is derived from actual registered sizes, never lexical profile
        # order or synthetic submissions. Compatibility belongs to each source
        # profile: an archived release cannot acquire unsupported larger sizes.
        for profile in adapter.manifests():
            if profile.get("instance_size", profile["parameter_bits"]) < target_size:
                continue
            for version in registry.comparison_versions(manifest["scheme_id"], manifest["version"], profile["id"]):
                source = registry.manifest_for(profile["id"], manifest["scheme_id"], version)
                size = source.get("instance_size", source["parameter_bits"])
                if size >= target_size:
                    source_profiles.setdefault(version, []).append(source["id"])
                    source_sizes[(version, source["id"])] = size
        eligible_sources = [and_(Challenge.scheme_version == version, Challenge.profile_id.in_(profiles))
                            for version, profiles in source_profiles.items()]
        with db.transaction() as session:
            # Window ranking avoids fetching every historical solve into memory.
            first = select(Submission.id.label("id"), func.row_number().over(partition_by=Submission.user_id,
                     order_by=(Submission.received_at, Submission.id)).label("position")).join(
                     Challenge, Challenge.id == Submission.challenge_id).join(
                     Method, Method.id == Submission.method_id).where(
                     Submission.correct.is_(True), Challenge.correctness_passed.is_(True),
                     Submission.profile_id == Challenge.profile_id,
                     Submission.user_id == Challenge.user_id, Challenge.scheme_id == manifest["scheme_id"],
                     Submission.user_id == Method.user_id,
                     Submission.scheme_version == Challenge.scheme_version,
                     Submission.round_id == Challenge.round_id,
                     Submission.received_at >= Challenge.issued_at,
                     Submission.received_at < Challenge.ends_at,
                     or_(*eligible_sources)).subquery()
            records = session.execute(select(Submission, User, Method).join(first, first.c.id == Submission.id)
                .join(User, Submission.user_id == User.id).join(Method, Submission.method_id == Method.id)
                .where(first.c.position == 1).order_by(Submission.received_at, Submission.id).limit(10)).all()
            entries = [{"rank": index + 1, "submission_id": result.id,
                "handle": user.handle, "profile_id": result.profile_id,
                "scheme_id": manifest["scheme_id"], "scheme_version": result.scheme_version,
                "instance_size": source_sizes[(result.scheme_version, result.profile_id)],
                "is_inherited": result.profile_id != profile_id,
                "received_at": result.received_at, "elapsed_seconds": result.elapsed_seconds,
                "method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode},
                "compute": result.compute, "challenge_id": result.challenge_id} for index, (result, user, method) in enumerate(records)]
            return envelope(entries=entries, scheme_id=manifest["scheme_id"], scheme_version=manifest["version"],
                            includes_compatible_versions=[version for version in source_profiles if version != manifest["version"]],
                            profile_id=profile_id)

    @app.get("/api/submissions/{submission_id}")
    def submission_detail(submission_id: str, request: Request):
        with db.transaction() as session:
            record = session.execute(select(Submission, Challenge, User, Method)
                .join(Challenge, Challenge.id == Submission.challenge_id)
                .join(User, User.id == Submission.user_id)
                .join(Method, Method.id == Submission.method_id)
                .where(Submission.id == submission_id,
                       Submission.user_id == Challenge.user_id,
                       Submission.user_id == Method.user_id,
                       Submission.profile_id == Challenge.profile_id,
                       Submission.scheme_version == Challenge.scheme_version)).first()
            if record is None:
                raise HTTPException(404, "Submission not found.")
            result, challenge, user, method = record
            if not result.correct:
                login = session_for(request, session)
                if login is None or login.user_id != result.user_id:
                    raise HTTPException(404, "Submission not found.")
            # Receipt details always describe their original frozen instance,
            # independent of compatible-release or inherited leaderboard credit.
            manifest = challenge.manifest if isinstance(challenge.manifest, dict) else {}
            size = manifest.get("instance_size", manifest.get("parameter_bits"))
            size = size if type(size) is int and size > 0 else None
            compute = result.compute if isinstance(result.compute, dict) else {}
            return envelope(submission={
                "id": result.id, "challenge_id": challenge.id, "handle": user.handle,
                "status": "correct" if result.correct else "incorrect",
                "scheme_id": challenge.scheme_id, "scheme_version": challenge.scheme_version,
                "profile_id": challenge.profile_id, "instance_size": size,
                "received_at": result.received_at, "elapsed_seconds": result.elapsed_seconds,
                "method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode,
                           "source": method_sources.source_descriptor(session, method.id)},
                "compute": {key: compute[key] for key in ("hardware", "workers", "wall_seconds",
                    "precomputation", "peak_memory", "notes") if key in compute},
            })

    @app.get("/api/me/submissions")
    def history(request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            records = session.execute(select(Submission, Method.name, Challenge.scheme_id).join(Method, Submission.method_id == Method.id)
                      .join(Challenge, Submission.challenge_id == Challenge.id)
                      .where(Submission.user_id == user.id).order_by(Submission.received_at.desc()).limit(50)).all()
            return {"submissions": [{**submission_data(record), "profile_id": record.profile_id,
                    "scheme_id": scheme_id, "scheme_version": record.scheme_version, "round_id": record.round_id, "method_name": name}
                    for record, name, scheme_id in records]}

    @app.get("/health")
    def health():
        with db.transaction() as session:
            session.execute(select(User.id).limit(1))
        return {"status": "ok"}

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/styles.css")
    def styles():
        return FileResponse(ROOT / "web" / "styles.css", headers={"Cache-Control": "no-cache"})

    @app.get("/app.js")
    def javascript():
        return FileResponse(ROOT / "web" / "app.js", headers={"Cache-Control": "no-cache"})

    @app.get("/math.js")
    def math_javascript():
        return FileResponse(ROOT / "web" / "math.js", headers={"Cache-Control": "no-cache"})

    @app.get("/assets/crucible-logo.png")
    def logo():
        return FileResponse(ROOT / "web" / "assets" / "crucible-logo.png", media_type="image/png",
                            headers={"Cache-Control": "no-cache"})

    # Only the vendored math distribution is mounted, never the repository or
    # general web directory. Real-path containment also rejects escaping symlinks.
    app.mount("/vendor/katex", StaticFiles(directory=ROOT / "web" / "vendor" / "katex",
                                          follow_symlink=False), name="katex")

    @app.get("/api/protocol/source")
    def protocol_source(version: str | None = None, scheme_id: str = kem.SCHEME_ID):
        adapter = adapter_for(version, scheme_id)
        # Paths come exclusively from the trusted registry, never request text.
        source = adapter.__file__
        return FileResponse(source, media_type="text/plain", filename=f"{scheme_id}-v{adapter.SCHEME_VERSION}.py")

    @app.get("/api/protocol/example")
    def example_solver(scheme_id: str = kem.SCHEME_ID, version: str | None = None):
        adapter_for(version, scheme_id)
        source = ROOT / registry.DETAILS[scheme_id]["example"]
        return FileResponse(source, media_type="text/plain", filename=source.name)

    @app.get("/api/agent/client")
    def agent_client():
        source = ROOT / "scripts" / "crucible_cli.py"
        return FileResponse(source, media_type="text/plain", filename="crucible_cli.py",
                            headers={"X-Content-SHA256": hashlib.sha256(source.read_bytes()).hexdigest()})

    machine_auth.register_routes(app, db, settings, clock, user_for, throttle)
    method_sources.register_routes(app, db, user_for, clock, throttle)
    people.register_routes(app, db, envelope)
    research.register_routes(app, db, settings, clock, user_for, throttle)
    protocol_proposals.register_routes(app, db, settings, clock, user_for, throttle)
    return app
