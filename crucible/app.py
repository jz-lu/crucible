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
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError, TimeoutError as DatabasePoolTimeout
from sqlalchemy.orm import defer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from . import bounties, beta_access, generation, generation_policy, implementation_builds, standard_schemes, kem, machine_auth, method_sources, notebook, people, protocol_proposals, registry, research, research_reviews
from .config import ROOT, Settings, load_settings
from .db import AdapterVersion, Base, Challenge, Database, LoginSession, Method, ProfileIncident, RateBucket, Submission, User
from .schemas import Credentials, NewChallenge, NewMethod, NewSubmission
from .security import mac, passwords, payload_mac, target_mac, token_hash, verify_password
from .public_json import catalogue_progress, catalogue_response
from .public_assets import Asset, CONTENT_SECURITY_POLICY, ProtectedAssets, PublicAssets, PublicMathFiles, is_public_ui
from .access_policy import AccessPolicy
from . import benchmark_reports, assumption_sheets, agent_guidance
from .reference_policy import METHOD as REFERENCE_METHOD
from . import github_auth, password_recovery, researcher_profiles
from . import resource_publications


SOURCE_REVISION_FILE = ROOT / "SOURCE_REVISION"


def packaged_source_revision():
    """Read only the bounded, optional image marker; never infer a local revision."""
    try:
        with SOURCE_REVISION_FILE.open("rb") as marker:
            value = marker.read(42).removesuffix(b"\n")
    except OSError:
        return None
    if len(value) != 40 or any(byte not in b"0123456789abcdef" for byte in value):
        return None
    return value.decode("ascii")


def current_round(now):
    number = int(now) // 3600
    return {"id": number, "starts_at": number * 3600, "ends_at": (number + 1) * 3600}


def database_busy_response():
    return JSONResponse({"detail": "The service is busy. Please retry shortly."}, 503,
                        headers={"Retry-After": "2", "Cache-Control": "no-store"})


def generation_admission(settings, manifest, *, current_version):
    """Describe settings-level issuance permission, not capacity or build health."""
    if manifest["version"] != current_version:
        return {"admitted": False, "reason": "archived_release"}
    # Keep this predicate shared with POST: a catalogue entry alone never admits
    # its implementation, and availability does not replace mutation preflight.
    local_beta = (not settings.production and manifest["scheme_id"] in standard_schemes.SCHEMES
                  and manifest.get("profile_kind") in {"standard-parameter-set", "research-tier"})
    hosted_beta = (settings.production and settings.access_mode == "beta" and
                   f'{manifest["scheme_id"]}@{manifest["version"]}' in settings.beta_releases)
    eligible = hosted_beta if settings.production else manifest.get("educational") or local_beta
    return {"admitted": bool(eligible), "reason": None if eligible else "release_not_admitted"}


def release_generation_admission(settings, scheme_id, version):
    """Fresh settings summary; mixed development profiles defer to exact GET."""
    manifest = {"scheme_id": scheme_id, "version": version}
    current = registry.CURRENT[scheme_id]
    if settings.production or version != current:
        return generation_admission(settings, manifest, current_version=current)
    outcomes = [generation_admission(settings, {**manifest, "educational": educational, "profile_kind": kind},
                                     current_version=current)
                for educational, kind in registry.presentation_issuance_profiles(registry.adapter_for(scheme_id, version))]
    if outcomes and all(outcome == outcomes[0] for outcome in outcomes):
        return outcomes[0]
    return {"admitted": None, "reason": "profile_specific"}


def release_recovery_summary(scheme_id, version, recovered):
    """Rank-one evidence for every registered target, without receipt bodies.

    The first valid receipt per exact source profile is sufficient for the
    earliest qualifying receipt overall. Eligibility deliberately follows the
    leaderboard's source-profile compatibility and actual-size rules, including
    exact named sets and releases that do not inherit larger-profile records.
    Only immutable scalar registry metadata is reused; winners are fresh for
    this request and are never cached across participants or requests.
    """
    profiles = registry.presentation_record_profiles(registry.adapter_for(scheme_id, version))
    source_sizes = {}
    sources = []
    for profile_id, size, *_ in profiles:
        versions = []
        for source_version in registry.comparison_versions(scheme_id, version, profile_id):
            if source_version not in source_sizes:
                source_sizes[source_version] = {row[0]: row[1] for row in
                    registry.presentation_record_profiles(registry.adapter_for(scheme_id, source_version))}
            versions.append((source_version, source_sizes[source_version][profile_id]))
        sources.append((profile_id, size, versions))
    summaries = {}
    for target_id, target_size, kind, _, inherits in profiles:
        exact = kind == "standard-parameter-set" or not inherits
        compatible = {}
        winner, winner_size = None, None
        for source_id, declared_size, versions in sources:
            if (exact and source_id != target_id) or declared_size < target_size:
                continue
            for source_version, size in versions:
                if size < target_size:
                    continue
                if source_version != version:
                    compatible[source_version] = None
                row = recovered.get((scheme_id, source_version, source_id))
                if row is not None and (winner is None or
                        (row.received_at, row.receipt_id) < (winner.received_at, winner.receipt_id)):
                    winner, winner_size = row, size
        summaries[target_id] = {
            "winner": {"rank": 1, "submission_id": winner.receipt_id, "handle": winner.handle,
                "profile_id": winner.profile_id, "scheme_id": scheme_id,
                "scheme_version": winner.scheme_version, "instance_size": winner_size,
                "is_inherited": winner.profile_id != target_id, "received_at": winner.received_at,
                "method": {"name": winner.method_name}} if winner is not None else None,
            "includes_compatible_versions": list(compatible),
        }
    return {"format": "crucible-recovery-summary-v1", "complete": True,
            "scheme_id": scheme_id, "scheme_version": version, "profiles": summaries}


class RequestBoundary:
    """Bound complete request bytes before parsing and record their arrival time.

    In production also configure proxy body limits/timeouts; do not log bodies.
    No forwarded IP is trusted here. The ASGI server must trust only its proxy.
    """
    def __init__(self, app, settings, clock, db, policy=None):
        self.app, self.settings, self.clock, self.db = app, settings, clock, db
        self.policy = policy or AccessPolicy()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        # Only the selected immutable public representation may supply an
        # asset-specific policy. Neither request nor response headers select it.
        scope.pop("_crucible_public_asset", None)
        original_send = send
        async def secure_send(message):
            if message["type"] == "http.response.start":
                cacheable_ui = (scope["method"] in {"GET", "HEAD"}
                                and is_public_ui(scope["path"])
                                and message["status"] in {200, 304})
                public_asset = scope.get("_crucible_public_asset")
                content_policy = (public_asset.content_security_policy
                                  if cacheable_ui and isinstance(public_asset, Asset)
                                  else CONTENT_SECURITY_POLICY)
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", content_policy),
                ]
                if scope["path"].startswith("/api") or (self.settings.access_mode == "beta" and not cacheable_ui):
                    extra.append((b"cache-control", b"no-store"))
                if scope["path"].startswith("/api/"):
                    # Includes admission, pool wait, SQL and handler work until
                    # headers; excludes the proxy/network and response transfer.
                    duration = max(0.0, (time.perf_counter() - started) * 1000)
                    extra.append((b"server-timing", f"app;dur={duration:.2f}".encode("ascii")))
                if self.settings.production:
                    extra.append((b"strict-transport-security", b"max-age=31536000"))
                replaced = {key for key, _ in extra}
                message["headers"] = [(key, value) for key, value in message.get("headers", []) if key.lower() not in replaced] + extra
            await original_send(message)
        send = secure_send
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
            except DatabasePoolTimeout:
                return await database_busy_response()(scope, receive, send)
            except HTTPException as error:
                return await JSONResponse({"detail": error.detail}, error.status_code,
                                          headers={"Cache-Control": "no-store"})(scope, receive, send)
            machine = True
        if self.settings.access_mode == "beta":
            audience = self.policy.audience(scope["method"], scope["path"])
            if audience is None:
                return await JSONResponse({"detail": "Not found."}, 404)(scope, receive, send)
            if audience == "member" and not machine:
                def admit_browser():
                    raw = Request(scope).cookies.get(self.settings.cookie_name, "")
                    with self.db.transaction() as session:
                        beta_access.browser_account(session, raw, self.clock, require_membership=True)
                try:
                    await run_in_threadpool(admit_browser)
                except DatabasePoolTimeout:
                    return await database_busy_response()(scope, receive, send)
                except HTTPException as error:
                    code = "authentication_required" if error.status_code == 401 else "membership_required"
                    return await JSONResponse({"detail": error.detail, "code": code}, error.status_code)(scope, receive, send)
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
                if scope["method"] == "POST":
                    if scope["path"] == "/api/notebook/import":
                        limit = notebook.MAX_WORKSPACE_BYTES + 1024
                    elif scope["path"] == "/api/notebook/notes" or (
                            scope["path"].startswith("/api/notebook/notes/") and
                            scope["path"].endswith("/revisions") and
                            len(scope["path"].split("/")) == 6):
                        limit = 512 * 1024
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

        await self.app(scope, buffered_receive, send)


def create_app(settings: Settings | None = None, *, clock=time.time, initialize=False):
    source_revision = packaged_source_revision()
    registry.validate_registry()
    registry.warm_navigation_metadata()
    settings = settings or load_settings()
    db = Database(settings.database_url)
    if initialize:  # Explicit test fixture only. Normal startup uses Alembic.
        Base.metadata.create_all(db.engine)
    app = FastAPI(title="Crucible recovery API", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.db, app.state.settings, app.state.clock = db, settings, clock
    app.state.generation_limiter = generation.GenerationLimiter(settings.generation_concurrency,
                                                               max_per_account=settings.generation_per_account)
    app.state.auth_limiter = generation.GenerationLimiter(settings.auth_concurrency,
                                                         max_per_account=settings.auth_concurrency)
    policy = AccessPolicy()
    app.state.access_policy = policy
    public_assets = PublicAssets(ROOT / "web", frozen=settings.production)
    protected_assets = ProtectedAssets(ROOT / "web")
    app.add_middleware(RequestBoundary, settings=settings, clock=clock, db=db, policy=policy)
    app.add_middleware(resource_publications.ResourceIngestBoundary, db=db, settings=settings, clock=clock)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.hosts)

    @app.exception_handler(DatabasePoolTimeout)
    async def database_busy(request, error):
        return database_busy_response()

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
            record = session.get(LoginSession, token_hash(raw), populate_existing=True)
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

    def account_for(request, session, *, mutation=False):
        if not mutation and request.method in {"GET", "HEAD"} and "authorization" not in request.headers:
            return beta_access.browser_account(session, request.cookies.get(settings.cookie_name, ""), clock)
        record = (machine_auth.request_identity(request, session, settings, clock, mutation=mutation)
                  if "authorization" in request.headers else identity(request, session, mutation=mutation))
        if not record.user_id:
            raise HTTPException(401, "Sign in to continue.")
        user = session.get(User, record.user_id)
        if mutation:
            user = beta_access.lock_user(session, record.user_id)
            # Recheck credentials after the account lock; revocation takes it first.
            record = (machine_auth.request_identity(request, session, settings, clock, mutation=True)
                      if "authorization" in request.headers else identity(request, session, mutation=True))
            if not record.user_id or user is None or record.user_id != user.id:
                raise HTTPException(401, "Sign in to continue.")
        return beta_access.require_not_suspended(session, user)

    def user_for(request, session, *, mutation=False):
        if not mutation and request.method in {"GET", "HEAD"} and "authorization" not in request.headers:
            return beta_access.browser_account(session, request.cookies.get(settings.cookie_name, ""), clock,
                                               require_membership=settings.access_mode == "beta")
        user = account_for(request, session, mutation=mutation)
        return beta_access.require_access(session, settings, user, clock(), mutation=mutation)

    def issue_session(response, session, user_id=None):
        raw = secrets.token_urlsafe(32)
        session.add(LoginSession(token_hash=token_hash(raw), user_id=user_id, expires_at=clock() + settings.session_seconds))
        response.set_cookie(settings.cookie_name, raw, max_age=settings.session_seconds, httponly=True,
                            secure=settings.production, samesite="strict", path="/")
        return raw

    def session_payload(user, raw, session, *, access=None, auth=None):
        access = beta_access.access_payload(session, settings, user) if access is None else access
        return envelope(user={"id": user.id, "handle": user.handle} if user else None, access=access,
                        auth=github_auth.auth_payload(session, settings, user) if auth is None else auth,
                        capabilities={"manage_beta_access": beta_access.is_admin(settings, user) and access["allowed"],
                                      "review_protocol_proposals": protocol_proposals.is_reviewer(settings, user) and access["allowed"],
                                      "self_review_protocol_proposals": protocol_proposals.can_self_review(settings, user) and access["allowed"]},
                        csrf_token=mac(settings.secret, "csrf-v1", raw.encode("ascii")))

    def password_operation(operation, *arguments):
        # Argon2 uses 64 MiB per operation. Bound memory independently of native
        # challenge work; do not fill the request threadpool waiting for a slot.
        try:
            with app.state.auth_limiter.slot("password"):
                return operation(*arguments)
        except generation.GenerationBusy:
            raise HTTPException(429, "Sign-in is busy. Please retry shortly.", headers={"Retry-After": "2"}) from None

    def throttle(request, name, limit, seconds, extra="", *, account_id=None, handle=None):
        if not settings.rate_limits:
            return
        # Persist counters in their own transaction so rejected operations count.
        peer = request.client.host if request.client else "unknown"
        subject = (f"{name}:account:{account_id}" if account_id is not None else
                   f"{name}:handle:{handle}" if handle is not None else f"{name}:{peer}:{extra}")
        key = mac(settings.secret, "rate-v1", subject.encode())
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
        if manifest.get("profile_kind") == "standard-parameter-set" and body.profile_id is None:
            raise HTTPException(422, "Choose a published parameter set by profile_id; numeric stages do not apply to this scheme.")
        return manifest

    def pin_adapter(session, manifest):
        try:
            additional_platform = implementation_builds.additional_platform_admission(manifest)
        except ValueError:
            raise HTTPException(409, "The implementation has an unsupported platform admission declaration.") from None
        identity_ = (manifest["scheme_id"], manifest["version"])
        current_hash = manifest["implementation"]["source_sha256"]
        artifact_hash = manifest["implementation"].get("artifact_sha256")
        existing_pin = session.get(AdapterVersion, identity_)
        if additional_platform is not None:
            # Additional platforms must never establish their own baseline,
            # including on an empty database or when claiming the original pair.
            if existing_pin is None or not implementation_builds.implementation_is_compatible(session, manifest, existing_pin):
                raise HTTPException(409, "This additional platform requires an existing original release baseline and an active review of its exact build.")
            return
        if existing_pin is None:
            # Upgrading a pre-pin database must respect its existing releases.
            # Missing or conflicting historical provenance is a stop, not an
            # invitation to bless the currently running source as authoritative.
            legacy = session.scalars(select(Challenge.manifest).where(
                Challenge.scheme_id == identity_[0], Challenge.scheme_version == identity_[1])).yield_per(100)
            for previous in legacy:
                if (previous.get("implementation", {}).get("source_sha256") != current_hash
                        or previous.get("implementation", {}).get("artifact_sha256") != artifact_hash):
                    raise HTTPException(409, "Historical instances use a different or unrecorded implementation. Review the release and use a new scheme version.")
        if db.sqlite:
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        session.execute(insert(AdapterVersion).values(scheme_id=manifest["scheme_id"], version=manifest["version"],
            source_sha256=current_hash, artifact_sha256=artifact_hash, created_at=clock()).on_conflict_do_nothing())
        pinned = session.get(AdapterVersion, identity_)
        if (pinned.source_sha256 != current_hash or pinned.artifact_sha256 != artifact_hash) and not (
                implementation_builds.implementation_is_compatible(session, manifest, pinned)):
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
                "received_at": submission.received_at, "method_id": submission.method_id,
                "ai_model": submission.ai_model or ""}

    def serialize_challenge(challenge, submission, *, summary=False):
        status = "open" if clock() < challenge.ends_at else "expired"
        if submission:
            status = "solved" if submission.correct else "incorrect"
        data = {**standard_schemes.profile_presentation(challenge.manifest),
                "id": challenge.id, "profile_id": challenge.profile_id, "round_id": challenge.round_id,
                "scheme_id": challenge.scheme_id, "scheme_version": challenge.scheme_version,
                "issued_at": challenge.issued_at, "starts_at": challenge.starts_at, "ends_at": challenge.ends_at,
                **({} if summary else {"public": challenge.public_data}),
                "status": status,
                "submission": submission_data(submission) if submission else None,
                "download_url": f"/api/challenges/{challenge.id}/download"}
        if summary:
            # Use the frozen declaration enforced by answer submission, never
            # load a large public key just to display the shared-secret length.
            # Invalid historical declarations stay readable with length unknown;
            # submission still rejects them and full inputs remain downloadable.
            expected = challenge.manifest.get("shared_secret_bytes")
            data.update(projection="summary", shared_secret_bytes=expected
                        if type(expected) is int and 1 <= expected <= 4096 else None)
        return data

    def challenge_data(session, challenge, *, summary=False):
        submission = session.scalar(select(Submission).where(Submission.challenge_id == challenge.id))
        return serialize_challenge(challenge, submission, summary=summary)

    @app.get("/api/session")
    def get_session(request: Request):
        throttle(request, "session", 300, 60)
        response = JSONResponse({})
        with db.transaction(write=True) as session:
            raw = request.cookies.get(settings.cookie_name)
            # One fresh snapshot for the browser's session display. Member
            # routes retain their separate admission and handler checks; writes
            # retain account locks and credential/CSRF rechecks.
            row = None
            if "authorization" in request.headers:
                # This browser-only route rejects machine credentials even if
                # it is called outside the normal request boundary.
                machine_auth.request_identity(request, session, settings, clock)
            if raw and raw.isascii() and len(raw) <= 128:
                row = session.execute(select(LoginSession, User, beta_access.BetaMembership,
                        beta_access.AccountState, github_auth.GitHubIdentity)
                    .select_from(LoginSession)
                    .outerjoin(User, User.id == LoginSession.user_id)
                    .outerjoin(beta_access.BetaMembership, beta_access.BetaMembership.user_id == User.id)
                    .outerjoin(beta_access.AccountState, beta_access.AccountState.user_id == User.id)
                    .outerjoin(github_auth.GitHubIdentity, github_auth.GitHubIdentity.user_id == User.id)
                    .where(LoginSession.token_hash == token_hash(raw))).one_or_none()
            # Expiry is evaluated after a possibly slow checkout/read, exactly
            # as for a normal session lookup. Expired rows grant no identity.
            record = row.LoginSession if row and row.LoginSession.expires_at > clock() else None
            if not record:
                raw = issue_session(response, session)
            user = row.User if record else None
            access = beta_access.access_summary(settings, user,
                row.BetaMembership if user else None, row.AccountState if user else None)
            auth = github_auth.auth_summary(settings, user, row.GitHubIdentity if user else None)
            response.body = json.dumps(session_payload(user, raw, session, access=access, auth=auth)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.post("/api/auth/register")
    def register(body: Credentials, request: Request):
        throttle(request, "register", 200 if settings.access_mode == "beta" else 5, 3600)
        with db.transaction() as session:
            identity(request, session, mutation=True)
            if settings.access_mode == "beta":
                beta_access.validate_registration_invitation(session, body.invitation_token, clock())
        encoded = password_operation(passwords.hash, body.password)
        response = JSONResponse({})
        try:
            with db.transaction(write=True) as session:
                previous = identity(request, session, mutation=True)
                if settings.access_mode == "beta":
                    beta_access.validate_registration_invitation(session, body.invitation_token, clock(), lock=True)
                user = User(id=str(uuid4()), handle=body.handle, password_hash=encoded, created_at=clock())
                session.add(user)
                session.flush()
                session.delete(previous)
                raw = issue_session(response, session, user.id)
                response.body = json.dumps(session_payload(user, raw, session)).encode()
                response.headers["content-length"] = str(len(response.body))
        except IntegrityError:
            raise HTTPException(409, "That handle is unavailable.")
        return response

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request):
        throttle(request, "login-ip", 600 if settings.access_mode == "beta" else 30, 900)
        throttle(request, "login-handle", 10, 900, handle=body.handle)
        with db.transaction() as session:
            identity(request, session, mutation=True)
            user = session.scalar(select(User).where(User.handle == body.handle))
            user_id, encoded = (user.id, user.password_hash) if user else (None, None)
        valid = password_operation(verify_password, encoded, body.password)
        if not user_id or not encoded or not valid:
            raise HTTPException(401, "Handle or password is incorrect.")
        replacement = password_operation(passwords.hash, body.password) if passwords.check_needs_rehash(encoded) else None
        response = JSONResponse({})
        with db.transaction(write=True) as session:
            user = beta_access.lock_user(session, user_id)
            beta_access.require_not_suspended(session, user)
            if user.password_hash != encoded:
                raise HTTPException(401, "Your credentials changed. Please sign in again.")
            previous = identity(request, session, mutation=True)
            session.delete(previous)
            if replacement:
                user.password_hash = replacement
            raw = issue_session(response, session, user_id)
            response.body = json.dumps(session_payload(user, raw, session)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        with db.transaction(write=True) as session:
            previous = identity(request, session, mutation=True)
            session.delete(previous)
            response = JSONResponse({})
            raw = issue_session(response, session)
            response.body = json.dumps(session_payload(None, raw, session)).encode()
            response.headers["content-length"] = str(len(response.body))
        return response

    @app.get("/api/schemes")
    def schemes(request: Request, progress_only: bool = False, projection: str | None = None,
                scheme_id: str | None = None, version: str | None = None):
        if (projection not in {None, "navigation", "release", "map"}
                or projection is not None and "progress_only" in request.query_params
                or projection != "release" and (scheme_id is not None or version is not None)
                or projection == "release" and (not scheme_id or not version)):
            raise HTTPException(422, "Choose a navigation projection, an exact release, or the existing catalogue/progress view.")
        if projection == "map":
            # Graph layout is bundled separately. Its action links need only
            # current release identities, never all profile labels/live scores.
            return catalogue_response(envelope(projection="map", schemes=[
                {"id": identifier, "version": version} for identifier, version in registry.CURRENT.items()],
                catalogue_revision=registry.catalogue_revision()), request.headers.get("accept-encoding"))
        if projection == "release":
            releases = [registry.descriptor(adapter_for(version, scheme_id))]
            catalogue = None
        else:
            catalogue = (registry.navigation_catalogue() if projection == "navigation" else
                         registry.progress_catalogue() if progress_only else registry.catalogue())
            releases = [release for scheme in catalogue for release in [scheme, *scheme["previous_releases"]]]
        # One bounded-result query for the whole catalogue: first successful
        # receipt per exact scheme/version/profile, using the leaderboard's tie
        # rule. Do not mistake a first recovery at a smaller size for the solver
        # who first reached the largest displayed size.
        source_releases = {(scheme["id"], scheme["version"]) for scheme in releases}
        if projection == "release":
            # An exact detail still inherits only explicitly compatible receipts,
            # including when those source releases were not requested themselves.
            source_releases.update((scheme["id"], source_version)
                for scheme in releases for profile in scheme["profiles"]
                for source_version in registry.comparison_versions(scheme["id"], scheme["version"], profile["id"]))
        current_releases = [and_(Challenge.scheme_id == identifier, Challenge.scheme_version == release_version)
                            for identifier, release_version in sorted(source_releases)]
        first_receipts = select(
            Challenge.scheme_id.label("scheme_id"), Challenge.scheme_version.label("scheme_version"),
            Submission.profile_id.label("profile_id"), Submission.user_id.label("user_id"),
            Submission.received_at.label("received_at"), Submission.id.label("receipt_id"),
            Method.name.label("method_name"),
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
            scheme["generation_admission"] = release_generation_admission(settings, scheme["id"], scheme["version"])
            scheme["participant_count"] = participants.get((scheme["id"], scheme["version"]), 0)
            # Some frozen research manifests contain the policy flag from their
            # initial release. Catalogue descriptors use today's explicit score
            # policy; challenge downloads still preserve the original manifest.
            scheme["profiles"] = [({**profile, **standard_schemes.scoring_presentation(profile)}
                                   if "scoring_eligible" in profile else profile)
                                  for profile in scheme["profiles"]]
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
            navigation = [p for p in profiles if not scheme.get("default_progression") or p.get("stage_number") is not None]
            later_ids = {p["id"] for p in profiles[largest + 1:]}
            scheme["next_profile_id"] = next((p["id"] for p in navigation if p["id"] in later_ids), None)
            if scheme.get("record_inheritance") is False:
                scheme["next_profile_id"] = next((p["id"] for p in navigation if p["id"] not in first_by_profile), None)
                scheme["profile_recoveries"] = [{"profile_id": p["id"], "profile_label": p.get("stage_label", p.get("profile_label", f'Stage {p["instance_size"]}')),
                    "handle": first_by_profile[p["id"]].handle,
                    "submission_id": first_by_profile[p["id"]].receipt_id,
                    "received_at": first_by_profile[p["id"]].received_at}
                    for p in profiles if p["id"] in first_by_profile]
            first = first_by_profile.get(largest_id)
            scheme["first_recovery"] = {"handle": first.handle, "received_at": first.received_at,
                "profile_id": first.profile_id, "scheme_version": first.scheme_version,
                "submission_id": first.receipt_id} if first else None
            scheme["recovery_summary"] = release_recovery_summary(scheme["id"], scheme["version"], recovered)
        document = {"catalogue_revision": registry.catalogue_revision()}
        if projection == "release":
            document.update(projection="release", scheme=releases[0], current_version=registry.CURRENT[scheme_id])
        elif projection == "navigation":
            document.update(projection="navigation", schemes=catalogue)
        elif progress_only:
            document.update(projection="progress", releases=catalogue_progress(catalogue))
        else:
            document["schemes"] = catalogue
        return catalogue_response(envelope(**document), request.headers.get("accept-encoding"))

    @app.get("/api/challenges")
    def current_challenge(profile_id: str, request: Request, scheme_id: str | None = None,
                          scheme_version: str | None = None, summary: bool = False):
        manifest = manifest_for(profile_id, scheme_version, scheme_id)
        with db.transaction() as session:
            user = user_for(request, session)
            observed_at = clock()
            round_ = current_round(observed_at)
            query = select(Challenge, Submission).outerjoin(Submission, Submission.challenge_id == Challenge.id)
            if summary:
                query = query.options(defer(Challenge.public_data, raiseload=True))
            row = session.execute(query.where(Challenge.user_id == user.id, Challenge.profile_id == profile_id,
                      Challenge.scheme_id == manifest["scheme_id"], Challenge.scheme_version == manifest["version"],
                      Challenge.round_id == round_["id"])).one_or_none()
            challenge, submission = row if row is not None else (None, None)
            started = manifest["version"] == registry.CURRENT[manifest["scheme_id"]] and (challenge is not None or session.scalar(
                select(Challenge.id).where(Challenge.user_id == user.id,
                    Challenge.scheme_id == manifest["scheme_id"], Challenge.scheme_version == manifest["version"],
                    Challenge.round_id == round_["id"]).limit(1)) is not None)
            # Describe the exact queried round, even if the hour turns while
            # reading. A later guarded POST must still match the server's round.
            return {"challenge": serialize_challenge(challenge, submission, summary=summary) if challenge else None,
                    "generation_admission": generation_admission(settings, manifest,
                        current_version=registry.CURRENT[manifest["scheme_id"]]),
                    "round_started": started, "round_started_round_id": round_["id"],
                    "server_time": observed_at, "round": round_}

    @app.post("/api/challenges")
    def start_challenge(body: NewChallenge, request: Request, summary: bool = False):
        throttle(request, "issue", 1200, 60)
        manifest = challenge_manifest(body)
        profile_id = manifest["id"]
        adapter = adapter_for(manifest["version"], manifest["scheme_id"])
        # Adapters are an explicit code registry; proposals never execute uploads.
        if not generation_admission(settings, manifest,
                current_version=registry.CURRENT[manifest["scheme_id"]])["admitted"]:
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
                    raise HTTPException(409, "Request this scheme's current round before opening another stage automatically.")
            pin_adapter(session, manifest)
            query = select(Challenge)
            if summary:
                query = query.options(defer(Challenge.public_data, raiseload=True))
            existing = session.scalar(query.where(Challenge.user_id == user.id, Challenge.profile_id == profile_id,
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
            return envelope(challenge=challenge_data(session, existing, summary=summary), round_started=True,
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

        def generate_and_commit():
            # Another request may have completed between the first preflight
            # and acquiring this process-local reservation. Reuse that instance.
            with db.transaction(write=True) as session:
                _, checked_round, existing = preflight(session, expected_user=user_id, expected_round=round_["id"])
                if existing:
                    return existing_response(session, existing, checked_round)

            # Fresh-key generation holds no DB session, transaction, or account
            # lock. Cross-process duplicates still converge in the final phase.
            with app.state.generation_limiter.slot(user_id):
                # Quota/ledger failures are infrastructure failures, not evidence
                # of a faulty cryptographic profile. Only classify adapter errors.
                # Revisits and saturation retries do not consume this allowance.
                throttle(request, "generation-account", 30, 60, account_id=user_id)
                # Every actual draw has the same accounting, including DH/RSA.
                # Busy/reuse/quota rejection never creates an attempt row.
                attempt_id = generation.start_attempt(db, manifest, user_id, round_["id"], clock())
                try:
                    generated = adapter.generate(profile_id)
                    generation_policy.validate_result(manifest, generated)
                except Exception as error:
                    outcome = generation_policy.failure_outcome(adapter, error)
                    generation.finish_attempt(db, attempt_id, clock(), outcome, "unissued")
                    if outcome == "generation_abort":
                        raise HTTPException(503, "Parameter generation exceeded its resource allowance. Please retry later.") from None
                    record_incident({"reason": "adapter_execution_failed"})
                    raise HTTPException(503, "This profile encountered an implementation fault and is paused for review.") from None
            if not generated.diagnostics.get("shared_secret_matches", False):
                # Hold all mismatches without retrying or calling a mathematical
                # failure an implementation bug. No secrets enter this ledger.
                generation.finish_attempt(db, attempt_id, clock(), "agreement_failure_under_review", "quarantined")
                record_incident({"shared_secret_matches": False, "reason": "agreement_failure_under_review"})
                raise HTTPException(503, "This profile failed its agreement check and is paused for review.")
            generation.finish_attempt(db, attempt_id, clock(), "agreement", "generated_unissued")

            with db.transaction(write=True) as session:
                _, final_round, existing = preflight(session, expected_user=user_id, expected_round=round_["id"])
                if existing:
                    session.get(generation.GenerationAttempt, attempt_id).disposition = "duplicate_discarded"
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
                session.get(generation.GenerationAttempt, attempt_id).disposition = "issued"
                session.flush()
                return envelope(challenge=challenge_data(session, challenge, summary=summary), round_started=True,
                                round_started_round_id=round_["id"])

        key = (db.engine, user_id, manifest["scheme_id"], manifest["version"], profile_id, round_["id"])
        try:
            with generation.instance_reservation(key):
                return generate_and_commit()
        except generation.GenerationBusy:
            raise HTTPException(429, "Challenge generation is busy. Please retry shortly.", headers={"Retry-After": "3"}) from None

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
        throttle(request, "methods-ip", 1000, 3600)
        with db.transaction() as session:
            account_id = user_for(request, session, mutation=True).id
        # Account quotas remain shared across browser/agent credentials and IPs.
        # Charge outside the mutation transaction: the limiter writes separately.
        throttle(request, "methods-account", 100, 3600, account_id=account_id)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            if user.id != account_id:
                raise HTTPException(403, "Your session changed. Refresh and try again.")
            method = Method(id=str(uuid4()), user_id=user.id, name=body.name, pseudocode=body.pseudocode, created_at=clock())
            session.add(method)
            return {"method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode, "created_at": method.created_at}}

    @app.post("/api/challenges/{challenge_id}/submit")
    def submit(challenge_id: str, body: NewSubmission, request: Request):
        throttle(request, "submit", 30, 60)
        received = request.state.received_at
        payload = body.model_dump(mode="json")
        # Preserve pre-annotation retry commitments. A supplied annotation is
        # immutable and part of the same commitment as the answer and compute.
        if not body.ai_model:
            payload.pop("ai_model")
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
                received_at=received, elapsed_seconds=received - challenge.issued_at, compute=body.compute.model_dump(),
                ai_model=body.ai_model or None)
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
            if (manifest.get("profile_kind") == "standard-parameter-set" or manifest.get("record_inheritance") is False) and profile["id"] != profile_id:
                continue
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
            entries = [{**standard_schemes.profile_presentation(manifest), "rank": index + 1, "submission_id": result.id,
                "handle": user.handle, "profile_id": result.profile_id,
                "scheme_id": manifest["scheme_id"], "scheme_version": result.scheme_version,
                "instance_size": source_sizes[(result.scheme_version, result.profile_id)],
                "is_inherited": result.profile_id != profile_id,
                "received_at": result.received_at, "elapsed_seconds": result.elapsed_seconds,
                "method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode},
                "compute": result.compute, "ai_model": result.ai_model or "",
                "challenge_id": result.challenge_id} for index, (result, user, method) in enumerate(records)]
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
                **standard_schemes.profile_presentation(manifest),
                "id": result.id, "challenge_id": challenge.id, "handle": user.handle,
                "status": "correct" if result.correct else "incorrect",
                "scheme_id": challenge.scheme_id, "scheme_version": challenge.scheme_version,
                "profile_id": challenge.profile_id, "instance_size": size,
                "received_at": result.received_at, "elapsed_seconds": result.elapsed_seconds,
                "ai_model": result.ai_model or "",
                "method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode,
                           "source": method_sources.source_descriptor(session, method.id)},
                "compute": {key: compute[key] for key in ("hardware", "workers", "wall_seconds",
                    "precomputation", "peak_memory", "notes") if key in compute},
            })

    @app.get("/api/me/submissions")
    def history(request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            records = session.execute(select(Submission, Method.name, Challenge.scheme_id, Challenge.manifest).join(Method, Submission.method_id == Method.id)
                      .join(Challenge, Submission.challenge_id == Challenge.id)
                      .where(Submission.user_id == user.id).order_by(Submission.received_at.desc()).limit(50)).all()
            return {"submissions": [{**submission_data(record), **standard_schemes.profile_presentation(manifest), "profile_id": record.profile_id,
                    "scheme_id": scheme_id, "scheme_version": record.scheme_version, "round_id": record.round_id, "method_name": name}
                    for record, name, scheme_id, manifest in records]}

    @app.get("/health")
    def health():
        with db.transaction() as session:
            session.execute(select(User.id).limit(1))
        return {"status": "ok", **({"source_revision": source_revision} if source_revision else {})}

    @app.get("/")
    def index(request: Request):
        return public_assets.response(request)

    @app.get("/styles.css")
    def styles(request: Request):
        return public_assets.response(request)

    @app.get("/app.js")
    def javascript(request: Request):
        return public_assets.response(request)

    @app.get("/resource-metrics.js")
    def resource_metrics_javascript(request: Request):
        return public_assets.response(request)

    @app.get("/bootstrap.js")
    def bootstrap_javascript(request: Request):
        return public_assets.response(request)

    @app.get("/assumptions.js")
    def assumptions_javascript(request: Request):
        return public_assets.response(request)

    @app.get("/proposal-retractions.js")
    def proposal_retractions_script(request: Request):
        return public_assets.response(request)

    @app.get("/math.js")
    def math_javascript(request: Request):
        return public_assets.response(request)

    @app.get("/map/{asset_name}")
    def research_map_asset(asset_name: str, request: Request):
        # Curated, read-only research notes; never a directory mount or a view
        # into account-owned notebook data. Beta membership is enforced above.
        return protected_assets.response(request)

    @app.get("/notebook/{asset_name}")
    def notebook_asset(asset_name: str, request: Request):
        # Only these fixed UI assets are exposed. Saved notebook data uses
        # authenticated routes with per-note ownership/publication checks.
        return protected_assets.response(request)

    @app.get("/assets/crucible-logo.png")
    def logo(request: Request):
        return public_assets.response(request)

    # Only the vendored math distribution is mounted, never the repository or
    # general web directory. Real-path containment also rejects escaping symlinks.
    app.mount("/vendor/katex", PublicMathFiles(public_assets), name="katex")

    @app.get("/api/protocol/reference-method")
    def reference_method():
        return envelope(method=REFERENCE_METHOD)

    @app.get(agent_guidance.PIPELINE_PATH)
    def agent_pipeline():
        return FileResponse(ROOT / "web" / "agent" / "pipeline.json", media_type="application/json",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/agent/guidance/{resource_name}")
    def agent_guidance_resource(resource_name: str):
        resource = agent_guidance.RESOURCES.get(resource_name)
        if resource is None:
            raise HTTPException(404, "Agent guidance not found.")
        path, media_type = resource
        return FileResponse(ROOT / path, media_type=media_type, headers={"Cache-Control": "no-store"})

    @app.get("/api/protocol/proposal-guide")
    def proposal_guide(note: bool = False, data: bool = False, evidence_sha256: str | None = None):
        from . import display_notes
        binding = display_notes.proposal_guide()
        if data:
            evidence = display_notes.evidence(binding, evidence_sha256=evidence_sha256)
            if evidence is None:
                raise HTTPException(404, "No matching supporting evidence is available.")
            return envelope(**evidence)
        if note:
            document = display_notes.document(binding)
            if document is None:
                raise HTTPException(404, "No supporting note is available.")
            return envelope(note=document)
        return FileResponse(ROOT / "docs" / "proposal-guide.txt", media_type="text/plain",
                            headers={"Cache-Control": "no-store", **({"Link": '</' + binding["url"] + '>; rel="describedby"'} if binding else {})})

    @app.get("/api/protocol/evidence-note")
    def protocol_evidence_note(scheme_id: str, version: str | None = None,
                               revision: int | None = None, sha256: str | None = None,
                               data: bool = False, evidence_sha256: str | None = None):
        from . import display_notes
        adapter = adapter_for(version, scheme_id)
        binding = display_notes.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION)
        if data:
            evidence = display_notes.evidence(binding, evidence_sha256=evidence_sha256, revision=revision, sha256=sha256)
            if evidence is None:
                raise HTTPException(404, "No matching supporting evidence is available.")
            return envelope(**evidence)
        document = display_notes.document(binding, revision=revision, sha256=sha256)
        if document is None:
            raise HTTPException(404, "No matching supporting note is available.")
        return envelope(note=document)

    @app.get("/api/protocol/assumptions")
    def protocol_assumptions(scheme_id: str, version: str | None = None):
        from . import display_notes
        adapter = adapter_for(version, scheme_id)
        return envelope(sheet=assumption_sheets.sheet_for(adapter.SCHEME_ID, adapter.SCHEME_VERSION),
                        supporting_note=display_notes.for_release(adapter.SCHEME_ID, adapter.SCHEME_VERSION))

    @app.get("/api/protocol/benchmarks")
    def reference_benchmarks(scheme_id: str, version: str | None = None):
        from . import display_notes
        adapter = adapter_for(version, scheme_id)
        # Read only explicitly selected evidence. Timing never runs in a request.
        report = benchmark_reports.report_for(adapter)
        return envelope(method=REFERENCE_METHOD, report=report,
                        supporting_note=display_notes.for_report(adapter.SCHEME_ID, adapter.SCHEME_VERSION, report))

    @app.get("/api/protocol/source")
    def protocol_source(version: str | None = None, scheme_id: str = kem.SCHEME_ID):
        adapter = adapter_for(version, scheme_id)
        # Paths come exclusively from the trusted registry, never request text.
        source = adapter.__file__
        return FileResponse(source, media_type="text/plain", filename=f"{scheme_id}-v{adapter.SCHEME_VERSION}.py")

    @app.get("/api/protocol/artifacts")
    def protocol_artifacts(version: str | None = None, scheme_id: str = kem.SCHEME_ID):
        adapter = adapter_for(version, scheme_id)
        provider = getattr(adapter, "artifact_manifest", None)
        if provider is None:
            raise HTTPException(404, "This release is a single-file source artifact.")
        return provider()

    @app.get("/api/protocol/correctness")
    def protocol_correctness(scheme_id: str, version: str | None = None):
        adapter = adapter_for(version, scheme_id)
        with db.transaction() as session:
            rows = session.execute(select(generation.GenerationAttempt.profile_id,
                generation.GenerationAttempt.outcome, generation.GenerationAttempt.disposition,
                func.count().label("count")).where(
                    generation.GenerationAttempt.scheme_id == scheme_id,
                    generation.GenerationAttempt.scheme_version == adapter.SCHEME_VERSION).group_by(
                    generation.GenerationAttempt.profile_id, generation.GenerationAttempt.outcome,
                    generation.GenerationAttempt.disposition)).all()
        return {"scheme_id": scheme_id, "version": adapter.SCHEME_VERSION,
                "policy": "hold-on-disagreement-v1",
                "interpretation": "Counts of recorded generation attempts, including discarded and unissued draws. Historical unrecorded draws are not reconstructed. Started rows may be in flight or interrupted. These observations do not validate analytical failure bounds. Issued samples are conditioned on agreement.",
                "outcomes": [dict(row._mapping) for row in rows]}

    @app.get("/api/protocol/artifact-source")
    def protocol_artifact_source(path: str, scheme_id: str, version: str | None = None):
        from . import artifact_sources
        adapter = adapter_for(version, scheme_id)
        provider = getattr(adapter, "artifact_manifest", None)
        if provider is None:
            raise HTTPException(404, "This release has no multifile source closure.")
        try:
            content = artifact_sources.read_source(provider(), path)
        except KeyError:
            raise HTTPException(404, "Not a registered public source artifact.") from None
        except (OSError, ValueError, TypeError):
            raise HTTPException(409, "The registered public source artifact is unavailable or changed.") from None
        return Response(content, media_type="text/plain", headers={"X-Content-SHA256": hashlib.sha256(content).hexdigest()})

    @app.get("/api/protocol/example")
    def example_solver(scheme_id: str = kem.SCHEME_ID, version: str | None = None):
        adapter = adapter_for(version, scheme_id)
        source = ROOT / registry.example_path(adapter)
        return FileResponse(source, media_type="text/plain", filename=source.name)

    @app.get("/api/agent/client")
    def agent_client():
        source = ROOT / "scripts" / "crucible_cli.py"
        return FileResponse(source, media_type="text/plain", filename="crucible_cli.py",
                            headers={"X-Content-SHA256": hashlib.sha256(source.read_bytes()).hexdigest()})

    machine_auth.register_routes(app, db, settings, clock, user_for, throttle)
    method_sources.register_routes(app, db, user_for, clock, throttle)
    people.register_routes(app, db, envelope)
    researcher_profiles.register_routes(app, db, clock, user_for, envelope, throttle)
    research.register_routes(app, db, settings, clock, user_for, throttle)
    protocol_proposals.register_routes(app, db, settings, clock, user_for, throttle)
    notebook.register_routes(app, db, settings, clock, user_for, throttle)
    bounties.register_routes(app, db, settings, clock, user_for, throttle)
    research_reviews.register_routes(app, db, settings, clock, user_for, throttle)
    beta_access.register_routes(app, db, settings, clock, account_for, throttle)
    github_auth.register_routes(app, db, settings, clock, identity, issue_session, session_payload, password_operation, throttle)
    password_recovery.register_routes(app, db, settings, clock, identity, issue_session,
                                      session_payload, password_operation, throttle)
    resource_publications.register_routes(app, db)
    policy.register(app.routes)
    return app
