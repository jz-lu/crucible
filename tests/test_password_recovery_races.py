"""Recovery integration races on synthetic accounts only.

The PostgreSQL test reuses the explicit local disposable-cluster fixture. It
never consults DATABASE_URL or the operator's private beta database.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event, Lock
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from sqlalchemy import select

from crucible import beta_access, github_auth, password_recovery as recovery
from crucible.app import create_app
from crucible.db import LoginSession, User
from crucible.security import token_hash, verify_password
from test_api import Actor
from test_beta_transfer import postgres  # Explicit opt-in DSN; one random test DB.
from test_password_recovery import system, request_link, complete


def test_password_reset_invalidates_pending_github_link_before_provider_exchange(system, monkeypatch):
    original_app, _, _, user_id, clock, original_settings = system
    settings = replace(original_settings, access_mode="beta", github_login_enabled=True,
        github_client_id="synthetic_client", github_client_secret="synthetic-client-secret-only")
    with original_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, user_id, "active", clock[0], reason="Synthetic beta participant")
    app = create_app(settings, clock=lambda: clock[0])
    actor = Actor(app)
    provider_calls = []

    def unexpected_provider_call(*args):
        provider_calls.append(True)
        raise AssertionError("An invalidated linking session must not contact GitHub")

    monkeypatch.setattr(github_auth, "fetch_identity", unexpected_provider_call)
    try:
        signed_in = actor.post("/api/auth/login", {"handle": "existing_researcher",
            "password": "correct horse battery staple"})
        assert signed_in.status_code == 200
        actor.csrf = signed_in.json()["csrf_token"]
        original_cookie = actor.client.cookies[settings.cookie_name]
        started = actor.post("/api/auth/github/start", {"mode": "link", "return_route": "#person/existing_researcher",
            "password": "correct horse battery staple"})
        assert started.status_code == 200
        query = parse_qs(urlsplit(started.json()["authorization_url"]).query)
        binding_cookie = actor.client.cookies[settings.github_cookie_name]

        reset_token = request_link(system)
        assert complete(system, reset_token).status_code == 200
        response = actor.client.get("/api/auth/github/callback?" + urlencode({
            "state": query["state"][0], "code": "unused-synthetic-authorization-code"}),
            # Match a cross-site callback: only the dedicated Lax cookie arrives.
            headers={"Cookie": settings.github_cookie_name + "=" + binding_cookie},
            follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/#auth?github=session-changed"
        assert provider_calls == []
        with app.state.db.transaction() as session:
            assert session.get(LoginSession, token_hash(original_cookie)) is None
            assert session.scalars(select(github_auth.GitHubIdentity)).all() == []
            assert session.get(beta_access.BetaMembership, user_id).status == "active"
            assert verify_password(session.get(User, user_id).password_hash, "new synthetic password only")
    finally:
        actor.client.close()
        app.state.db.engine.dispose()


def test_delayed_delivery_failure_cannot_delete_a_newer_reset_link(system, monkeypatch):
    app, _, visitor, identifier, clock, settings = system
    other_visitor = Actor(app)
    first_started, release_first = Event(), Event()
    recording_lock = Lock()
    delivered_tokens = []
    actual_send = recovery.send_password_reset

    def delayed_send(configuration, recipient, url):
        raw = parse_qs(urlsplit(url).fragment.split("?", 1)[1])["token"][0]
        with recording_lock:
            index = len(delivered_tokens)
            delivered_tokens.append(raw)
        if index == 0:
            first_started.set()
            assert release_first.wait(10)
            raise recovery.DeliveryUnavailable("Synthetic delayed first-delivery failure")
        return actual_send(configuration, recipient, url)

    monkeypatch.setattr(recovery, "send_password_reset", delayed_send)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            earlier = pool.submit(visitor.post, "/api/auth/password/reset/start", {"handle": "existing_researcher"})
            try:
                assert first_started.wait(5)
                clock[0] += recovery.RESEND_SECONDS
                later = other_visitor.post("/api/auth/password/reset/start", {"handle": "existing_researcher"})
                assert later.status_code == 200 and later.json() == recovery.GENERIC_RESULT
                assert len(delivered_tokens) == 2 and delivered_tokens[0] != delivered_tokens[1]
                with app.state.db.transaction() as session:
                    assert session.get(recovery.PasswordReset, identifier).token_hash == token_hash(delivered_tokens[1])
            finally:
                release_first.set()
            first_result = earlier.result(timeout=10)
            assert first_result.status_code == 200 and first_result.json() == recovery.GENERIC_RESULT
        with app.state.db.transaction() as session:
            record = session.get(recovery.PasswordReset, identifier)
            assert record.token_hash == token_hash(delivered_tokens[1]) and record.consumed_at is None
        assert complete(system, delivered_tokens[0]).status_code == 400
        assert complete(system, delivered_tokens[1]).status_code == 200
    finally:
        release_first.set()
        other_visitor.client.close()


def test_real_pg_concurrent_reset_completion_has_one_winner(system, postgres, monkeypatch):
    _, _, _, _, clock, original_settings = system
    settings = replace(original_settings, database_url=postgres.url.render_as_string(hide_password=False))
    app = create_app(settings, clock=lambda: clock[0])
    owner, first, second = Actor(app), Actor(app), Actor(app)
    try:
        user = owner.register("existing_researcher")
        with app.state.db.transaction(write=True) as session:
            session.add(beta_access.VerifiedMailbox(user_id=user["id"], email="verified@example.org", verified_at=clock[0]))
        # Use the ordinary reset route and local-mail reader on the isolated DB.
        fixture = (app, owner, first, user["id"], clock, settings)
        token = request_link(fixture)
        both_hashed = Barrier(2)
        actual_hash = recovery.passwords.hash

        def synchronized_hash(value):
            encoded = actual_hash(value)
            # Both requests passed the unlocked token check before either can
            # enter the final User-locked transaction and consume the record.
            both_hashed.wait(timeout=10)
            return encoded

        monkeypatch.setattr(recovery, "passwords", SimpleNamespace(hash=synchronized_hash))
        passwords = ["first synthetic new password", "second synthetic new password"]
        actors = [first, second]
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(actor.post, "/api/auth/password/reset/complete", {"token": token, "password": password})
                       for actor, password in zip(actors, passwords)]
            responses = [future.result(timeout=20) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 400]
        winner = next(index for index, response in enumerate(responses) if response.status_code == 200)
        assert responses[winner].json()["user"] is None
        assert responses[1 - winner].json() == {"detail": recovery.INVALID}
        with app.state.db.transaction() as session:
            saved = session.get(User, user["id"])
            assert verify_password(saved.password_hash, passwords[winner])
            assert not verify_password(saved.password_hash, passwords[1 - winner])
            assert session.get(recovery.PasswordReset, user["id"]).consumed_at == clock[0]
            assert session.scalar(select(LoginSession).where(LoginSession.user_id == user["id"])) is None
            assert session.get(beta_access.BetaMembership, user["id"]) is None
    finally:
        for actor in (owner, first, second):
            actor.client.close()
        app.state.db.engine.dispose()
