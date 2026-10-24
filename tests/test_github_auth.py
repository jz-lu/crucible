"""Real browser/DB boundaries with fake, bounded GitHub HTTP responses only."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from threading import Event, local
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import delete, inspect, select, text

from crucible import beta_access as beta, github_auth as github
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Database, LoginSession, User
from crucible.security import passwords, token_hash
from test_api import Actor
from test_beta_transfer import postgres  # Explicit local disposable PostgreSQL cluster only.

PASSWORD = "Synthetic local test password"
ACCESS = "gho_synthetic_not_a_real_provider_token"


@pytest.fixture
def system(tmp_path, monkeypatch):
    from crucible import dh_v4, registry
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})
    settings = Settings(f"sqlite:///{tmp_path / 'github.db'}", b"g" * 32,
        origins=("http://testserver",), access_mode="beta", rate_limits=False,
        github_login_enabled=True, github_client_id="synthetic_client",
        github_client_secret="synthetic-not-a-real-client-secret",
        mail_backend="local", mail_outbox_dir=str(tmp_path / "outbox"), mail_from="test@example.org")
    now = [3_600_600.0]
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    state = SimpleNamespace(app=app, db=app.state.db, settings=settings, now=now, calls=[],
        profile={"id": 123456, "login": "github-person", "type": "User", "email": "ignored@example.org"},
        token={"access_token": ACCESS, "token_type": "bearer", "scope": ""}, response_override=None)
    actual_client = httpx.Client

    def provider(request):
        state.calls.append(request)
        if state.response_override:
            return state.response_override(request)
        if str(request.url) == "https://github.com/login/oauth/access_token":
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(state.token).encode()))
        assert str(request.url) == "https://api.github.com/user"
        assert request.headers["authorization"] == "Bearer " + ACCESS
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(state.profile).encode()))

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        assert kwargs["timeout"].connect == 3 and kwargs["timeout"].read == 5
        return actual_client(transport=httpx.MockTransport(provider), **kwargs)

    monkeypatch.setattr(github.httpx, "Client", client)
    yield state
    app.state.db.engine.dispose()


def invitation(system):
    with system.db.transaction(write=True) as session:
        record, url = beta.create_invitation(session, system.settings, "new@example.org", system.now[0])
        return record.id, url.split("token=", 1)[1]


def local_user(system, handle="existing", *, linked_id=None):
    identifier = str(uuid4())
    with system.db.transaction(write=True) as session:
        session.add(User(id=identifier, handle=handle, password_hash=passwords.hash(PASSWORD), created_at=system.now[0]))
        session.flush()
        beta.set_membership(session, identifier, "active", system.now[0])
        if linked_id:
            session.add(github.GitHubIdentity(github_user_id=linked_id, user_id=identifier,
                github_login="old-login", created_at=system.now[0], last_login_at=system.now[0]))
    return identifier


def login(system, handle="existing"):
    actor = Actor(system.app)
    response = actor.post("/api/auth/login", {"handle": handle, "password": PASSWORD})
    assert response.status_code == 200, response.text
    actor.csrf = response.json()["csrf_token"]
    return actor


def start(system, actor, **body):
    if body.get("mode") == "link":
        body.setdefault("password", PASSWORD)
    response = actor.post("/api/auth/github/start", body)
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query, keep_blank_values=True)
    assert query["scope"] == [""] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [system.settings.github_callback_url]
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    return query


def callback(system, actor, query, *, code="synthetic-code", **parameters):
    # Real cross-site top-level callback omits the Strict login session cookie.
    # Supply only the dedicated Lax binding cookie to exercise that boundary.
    cookie = actor.client.cookies.get(system.settings.github_cookie_name)
    return actor.client.get("/api/auth/github/callback?" + urlencode({"state": query["state"][0], "code": code, **parameters}),
        headers={"Cookie": system.settings.github_cookie_name + "=" + cookie}, follow_redirects=False)


def status(response):
    assert response.status_code == 303
    assert response.headers["location"].startswith("/#auth?github=")
    return response.headers["location"].split("=", 1)[1]


def test_invited_provider_signup_chooses_handle_then_still_requires_mailbox_redemption(system):
    actor = Actor(system.app)
    invite_id, invite_token = invitation(system)
    old_session = actor.client.cookies[system.settings.cookie_name]
    query = start(system, actor, invitation_token=invite_token, return_route="#connect?code=ABCD-EFGH")
    flow_cookie = actor.client.cookies[system.settings.github_cookie_name]
    with system.db.transaction() as session:
        flow = session.get(github.GitHubFlow, token_hash(query["state"][0]))
        assert flow.original_session_hash == token_hash(old_session)
        assert flow.binding_hash == token_hash(flow_cookie.split(".")[1])
        assert invite_token not in flow.invitation_encrypted
        assert flow.invitation_id == invite_id
    assert status(callback(system, actor, query)) == "choose-handle"
    exchange = parse_qs(system.calls[0].content.decode())
    verifier = exchange["code_verifier"][0]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=") == query["code_challenge"][0]
    pending = actor.client.get("/api/auth/github/pending").json()
    assert pending == {"pending": True, "github_login": "github-person", "suggested_handle": "github_person",
                       "return_route": "#connect?code=ABCD-EFGH", "invitation_token": invite_token}
    response = actor.post("/api/auth/github/complete", {"handle": "Chosen_Name"})
    assert response.status_code == 200, response.text
    actor.csrf = response.json()["csrf_token"]
    payload = response.json()
    assert payload["user"]["handle"] == "chosen_name" and payload["access"]["status"] == "pending"
    assert payload["auth"]["github_linked"] and not payload["auth"]["password_enabled"]
    assert actor.client.cookies[system.settings.cookie_name] != old_session
    with system.db.transaction() as session:
        user = session.get(User, payload["user"]["id"])
        assert user.password_hash is None
        assert session.get(github.GitHubIdentity, "123456").user_id == user.id
        assert session.get(LoginSession, token_hash(old_session)) is None
        assert session.get(beta.BetaMembership, user.id) is None
        assert session.get(beta.VerifiedMailbox, user.id) is None
        assert ACCESS not in json.dumps([dict(row) for row in session.execute(select(github.GitHubFlow.__table__)).mappings()])
    assert actor.client.get("/api/schemes").status_code == 403
    assert actor.post("/api/access/invitations/verify/start", {"token": invite_token}).status_code == 200
    import re
    code = re.search(r"code is: ([0-9]{8})", next(Path(system.settings.mail_outbox_dir).glob("*.eml")).read_text())[1]
    assert actor.post("/api/access/invitations/redeem", {"token": invite_token, "code": code}).status_code == 200
    assert actor.client.get("/api/session").json()["access"]["allowed"]
    assert status(callback(system, actor, query)) == "expired"
    assert len(system.calls) == 2


def test_existing_linked_account_uses_numeric_id_despite_provider_rename(system):
    user_id = local_user(system, linked_id="123456")
    actor = Actor(system.app)
    query = start(system, actor)
    system.profile["login"] = "renamed-person"
    assert status(callback(system, actor, query)) == "complete"
    payload = actor.client.get("/api/session").json()
    assert payload["user"] == {"id": user_id, "handle": "existing"}
    assert payload["auth"]["github_login"] == "renamed-person"
    assert actor.client.get("/api/auth/github/pending").json()["pending"] is False


def test_no_email_or_handle_merge_and_no_new_account_without_invitation(system):
    existing_id = local_user(system, "github_person")
    actor = Actor(system.app)
    query = start(system, actor)
    assert status(callback(system, actor, query)) == "invite-required"
    with system.db.transaction() as session:
        assert session.scalars(select(User.id)).all() == [existing_id]
        assert session.get(github.GitHubIdentity, "123456") is None
    _, token = invitation(system)
    query = start(system, actor, invitation_token=token)
    assert status(callback(system, actor, query)) == "choose-handle"
    assert actor.post("/api/auth/github/complete", {"handle": "github_person"}).status_code == 409
    result = actor.post("/api/auth/github/complete", {"handle": "separate_account"})
    assert result.status_code == 200 and result.json()["user"]["id"] != existing_id


def test_existing_account_links_explicitly_without_changing_password_or_account(system):
    user_id = local_user(system)
    actor = login(system)
    assert actor.post("/api/auth/github/start", {"mode": "login"}).status_code == 409
    with system.db.transaction() as session:
        before = session.get(User, user_id).password_hash
    query = start(system, actor, mode="link", return_route="#person/existing")
    assert status(callback(system, actor, query)) == "linked"
    payload = actor.client.get("/api/session").json()
    assert payload["user"]["id"] == user_id and payload["auth"]["github_linked"]
    with system.db.transaction() as session:
        assert session.get(User, user_id).password_hash == before
        assert session.get(github.GitHubIdentity, "123456").user_id == user_id


def test_cannot_link_provider_already_linked_to_other_account(system):
    first = local_user(system, "first", linked_id="123456")
    local_user(system, "second")
    actor = login(system, "second")
    query = start(system, actor, mode="link")
    assert status(callback(system, actor, query)) == "identity-in-use"
    with system.db.transaction() as session:
        assert session.get(github.GitHubIdentity, "123456").user_id == first


def test_linking_requires_fresh_local_password_and_rejects_provider_only(system):
    user_id = local_user(system)
    actor = login(system)
    assert actor.post("/api/auth/github/start", {"mode": "link"}).status_code == 403
    assert actor.post("/api/auth/github/start", {"mode": "link", "password": "wrong password"}).status_code == 403
    with system.db.transaction(write=True) as session:
        assert session.scalars(select(github.GitHubFlow)).all() == []
        session.get(User, user_id).password_hash = None
    assert actor.post("/api/auth/github/start", {"mode": "link", "password": PASSWORD}).status_code == 403
    assert not system.calls


def test_password_changed_during_link_confirmation_cannot_issue_flow(system, monkeypatch):
    user_id = local_user(system)
    actor = login(system)
    actual = github.verify_password
    def verify(encoded, candidate):
        result = actual(encoded, candidate)
        with system.db.transaction(write=True) as session:
            session.get(User, user_id).password_hash = "changed-by-other-request"
        return result
    monkeypatch.setattr(github, "verify_password", verify)
    response = actor.post("/api/auth/github/start", {"mode": "link", "password": PASSWORD})
    assert response.status_code == 409
    with system.db.transaction() as session:
        assert session.scalars(select(github.GitHubFlow)).all() == []


def test_provider_only_user_cannot_enter_password_path_even_if_dummy_check_matches(system, monkeypatch):
    from crucible import app as app_module
    user_id = local_user(system)
    with system.db.transaction(write=True) as session:
        session.get(User, user_id).password_hash = None
    monkeypatch.setattr(app_module, "verify_password", lambda *_: True)
    actor = Actor(system.app)
    assert actor.post("/api/auth/login", {"handle": "existing", "password": PASSWORD}).status_code == 401
    assert actor.client.get("/api/session").json()["user"] is None


def test_state_browser_binding_csrf_origin_and_machine_boundary(system):
    actor = Actor(system.app)
    assert actor.client.get("/api/auth/github/start").status_code == 404
    assert actor.client.post("/api/auth/github/start", json={}, headers={"Origin": "http://testserver"}).status_code == 403
    assert actor.client.post("/api/auth/github/start", json={}, headers={"Origin": "https://evil.invalid", "X-CSRF-Token": actor.csrf}).status_code == 403
    assert actor.post("/api/auth/github/start", {"return_route": "https://evil.invalid"}).status_code == 422
    assert actor.post("/api/auth/github/start", {"return_route": "#join?token=unexpected"}).status_code == 422
    query = start(system, actor)
    attacker = Actor(system.app)
    response = attacker.client.get("/api/auth/github/callback?" + urlencode({"state": query["state"][0], "code": "code"}), follow_redirects=False)
    assert status(response) == "session-changed"
    assert not system.calls
    bad = {"state": ["A" * 43]}
    assert status(callback(system, actor, bad)) == "failed"
    assert not system.calls
    duplicate = actor.client.get("/api/auth/github/callback?" + urlencode({"state": query["state"][0], "code": "code"}) + "&state=duplicate", follow_redirects=False)
    assert status(duplicate) == "failed" and not system.calls
    assert actor.client.post("/api/auth/github/start", json={}, headers={"Authorization": "Bearer cru_" + "a" * 43}).status_code in {401, 403}


@pytest.mark.parametrize("change", ["logout", "suspend", "revoke"])
def test_session_revocation_during_provider_roundtrip_cannot_link(system, monkeypatch, change):
    user_id = local_user(system)
    actor = login(system)
    query = start(system, actor, mode="link")
    entered, release = Event(), Event()
    original = github.fetch_identity
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(github, "fetch_identity", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(callback, system, actor, query)
        assert entered.wait(3)
        try:
            with system.db.transaction(write=True) as session:
                if change == "logout":
                    session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
                elif change == "suspend":
                    beta.set_suspension(session, user_id, True, system.now[0])
                else:
                    beta.set_membership(session, user_id, "revoked", system.now[0])
        finally:
            release.set()
        assert status(future.result(timeout=5)) == "session-changed"
    with system.db.transaction() as session:
        assert session.get(github.GitHubIdentity, "123456") is None


def test_callback_one_use_before_outbound_http_and_expiry(system, monkeypatch):
    local_user(system, linked_id="123456")
    actor = Actor(system.app)
    query = start(system, actor)
    entered, release = Event(), Event()
    original = github.fetch_identity
    def delayed(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(github, "fetch_identity", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(callback, system, actor, query)
        assert entered.wait(3)
        try:
            assert status(callback(system, actor, query)) == "expired"
        finally:
            release.set()
        assert status(future.result(timeout=5)) == "complete"
    assert len(system.calls) == 2
    actor = Actor(system.app)
    query = start(system, actor)
    system.now[0] += 601
    assert status(callback(system, actor, query)) == "session-changed"
    assert len(system.calls) == 2


def test_pending_signup_rechecks_invitation_and_original_session(system):
    actor = Actor(system.app)
    invite_id, token = invitation(system)
    query = start(system, actor, invitation_token=token)
    assert status(callback(system, actor, query)) == "choose-handle"
    with system.db.transaction(write=True) as session:
        beta.invalidate_invitation(session, invite_id, system.now[0])
    assert actor.post("/api/auth/github/complete", {"handle": "new_person"}).status_code == 403
    with system.db.transaction() as session:
        assert session.scalars(select(User)).all() == []
    actor.post("/api/auth/logout", {})
    assert actor.client.get("/api/auth/github/pending").json() == {"pending": False}


@pytest.mark.parametrize("kind", ["scope", "bad_token_type", "invalid_id", "bot", "oversized", "compressed", "deep_json", "redirect", "timeout"])
def test_provider_failures_use_fixed_errors_without_credentials(system, kind):
    actor = Actor(system.app)
    query = start(system, actor)
    if kind == "scope":
        system.token["scope"] = "repo"
    elif kind == "bad_token_type":
        system.token["token_type"] = {"sensitive": ACCESS}
    elif kind == "invalid_id":
        system.profile["id"] = True
    elif kind == "bot":
        system.profile["type"] = "Bot"
    elif kind == "oversized":
        system.response_override = lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 65537))
    elif kind == "compressed":
        system.response_override = lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"unused"))
    elif kind == "deep_json":
        system.response_override = lambda request: httpx.Response(200, stream=httpx.ByteStream(b"[" * 2000 + b"0" + b"]" * 2000))
    elif kind == "redirect":
        system.response_override = lambda request: httpx.Response(302, headers={"Location": "https://evil.invalid"})
    else:
        def fail(request):
            raise httpx.ReadTimeout(ACCESS, request=request)
        system.response_override = fail
    response = callback(system, actor, query)
    assert status(response) == "failed"
    assert ACCESS not in response.text and ACCESS not in str(response.headers)
    with system.db.transaction() as session:
        assert session.scalars(select(github.GitHubIdentity)).all() == []


def test_disabled_default_does_not_advertise_provider_or_break_password_login(system):
    local_user(system)
    app = create_app(replace(system.settings, github_login_enabled=False), clock=lambda: system.now[0])
    try:
        actor = Actor(app)
        assert actor.client.get("/api/session").json()["auth"]["github_enabled"] is False
        assert actor.post("/api/auth/github/start", {}).status_code == 404
        assert actor.post("/api/auth/login", {"handle": "existing", "password": PASSWORD}).status_code == 200
    finally:
        app.state.db.engine.dispose()


def test_configuration_requires_explicit_enable_credentials_and_one_canonical_origin(system):
    for changes in ({"github_client_id": ""}, {"github_client_secret": ""},
                    {"origins": ("http://localhost:8000", "http://127.0.0.1:8000")},
                    {"origins": ("http://testserver/path",)}, {"github_login_enabled": "true"}):
        with pytest.raises(RuntimeError):
            replace(system.settings, **changes)


@pytest.mark.parametrize("route", ["#challenge", "#challenge/teaching-dh-kem/7?profile=dh-v7-tier-1",
    "#scheme/ml-kem/compressed-v4/assumptions", "#assumptions", "#person/countableclouds", "#people",
    "#notebook?note=fixture", "#propose", "#reviews?section=schemes", "#about", "#connect?code=ABCD-EFGH"])
def test_actual_frontend_routes_can_be_preserved_through_oauth(system, route):
    actor = Actor(system.app)
    start(system, actor, return_route=route)
    assert actor.client.get("/api/auth/github/pending").json()["return_route"] == route


@pytest.fixture
def postgres_system(system, postgres):
    settings = replace(system.settings, database_url=postgres.url.render_as_string(hide_password=False))
    app = create_app(settings, clock=lambda: system.now[0])
    system.app, system.db, system.settings = app, app.state.db, settings
    try:
        yield system
    finally:
        app.state.db.engine.dispose()


def test_real_pg_link_restart_during_callback_does_not_invert_user_flow_locks(postgres_system, monkeypatch):
    system = postgres_system
    local_user(system)
    actor = login(system)
    query = start(system, actor, mode="link")
    callback_at_user, restart_has_user, release_callback = Event(), Event(), Event()
    context = local()
    original_fetch, original_lock = github.fetch_identity, beta.lock_user

    def fetch(*args):
        result = original_fetch(*args)
        context.callback = True
        return result

    def lock(session, user_id):
        if getattr(context, "callback", False):
            callback_at_user.set()
            assert release_callback.wait(5)
            return original_lock(session, user_id)
        user = original_lock(session, user_id)
        if callback_at_user.is_set():
            restart_has_user.set()
        return user

    monkeypatch.setattr(github, "fetch_identity", fetch)
    monkeypatch.setattr(beta, "lock_user", lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback_result = pool.submit(callback, system, actor, query)
        assert callback_at_user.wait(3)
        restart_result = pool.submit(actor.post, "/api/auth/github/start", {"mode": "link", "password": PASSWORD})
        try:
            assert restart_has_user.wait(3)
        finally:
            release_callback.set()
        assert restart_result.result(timeout=8).status_code == 200
        assert status(callback_result.result(timeout=8)) == "session-changed"
    with system.db.transaction() as session:
        assert session.scalars(select(github.GitHubIdentity)).all() == []
        flows = session.scalars(select(github.GitHubFlow)).all()
        assert len(flows) == 1 and flows[0].status == "started"


def test_real_pg_concurrent_signup_for_same_provider_cannot_create_duplicate_accounts(postgres_system):
    system = postgres_system
    actors = [Actor(system.app), Actor(system.app)]
    for actor in actors:
        _, token = invitation(system)
        query = start(system, actor, invitation_token=token)
        assert status(callback(system, actor, query)) == "choose-handle"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(actor.post, "/api/auth/github/complete", {"handle": "concurrent_" + str(index)})
                   for index, actor in enumerate(actors)]
        assert sorted(future.result(timeout=8).status_code for future in futures) == [200, 409]
    with system.db.transaction() as session:
        assert len(session.scalars(select(User)).all()) == 1
        assert len(session.scalars(select(github.GitHubIdentity)).all()) == 1


def test_github_migration_preserves_existing_identity_and_rejects_destructive_downgrade(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("CRUCIBLE_") or name == "DATABASE_URL":
            monkeypatch.delenv(name)
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0020")
    db = Database(url)
    try:
        with db.transaction(write=True) as session:
            session.add(User(id="retained", handle="retained", password_hash="original-password-hash", created_at=1))
            session.flush()
            session.add(LoginSession(token_hash="a" * 64, user_id="retained", expires_at=12345))
        def snapshot():
            with db.engine.connect() as connection:
                return [tuple(row) for row in connection.execute(text("SELECT * FROM users"))], [tuple(row) for row in connection.execute(text("SELECT * FROM login_sessions"))]
        before = snapshot()
        command.upgrade(config, "head")
        command.check(config)
        assert snapshot() == before
        assert next(c for c in inspect(db.engine).get_columns("users") if c["name"] == "password_hash")["nullable"]
        with db.transaction(write=True) as session:
            session.add(User(id="provider-only", handle="provider_only", password_hash=None, created_at=2))
        with pytest.raises(RuntimeError, match="provider-only"):
            command.downgrade(config, "0020")
        with db.transaction(write=True) as session:
            assert session.get(User, "provider-only").password_hash is None
            session.delete(session.get(User, "provider-only"))
        command.downgrade(config, "0020")
        assert snapshot() == before
    finally:
        db.engine.dispose()
