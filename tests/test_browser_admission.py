"""Browser admission query budgets without stale permissions or write shortcuts."""
from contextlib import contextmanager
import asyncio
import importlib
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy import delete, event, func, select, update

from crucible import beta_access as beta
from crucible.db import LoginSession, Method, User
from crucible.security import token_hash
from test_beta_boundary import private_app


@pytest.mark.parametrize('method,path,status,binding,custom_policy', [
    ('GET', '/', 200, 'asset', True),
    ('HEAD', '/', 304, 'asset', True),
    ('GET', '/api/session', 200, 'asset', False),
    ('GET', '/', 500, 'asset', False),
    ('GET', '/', 200, 'missing', False),
    ('GET', '/', 200, 'untyped', False),
])
def test_public_asset_policy_is_bound_to_the_selected_response(method, path, status, binding, custom_policy):
    from crucible.app import RequestBoundary
    from crucible.config import Settings
    from crucible.public_assets import Asset, CONTENT_SECURITY_POLICY
    policy = CONTENT_SECURITY_POLICY.replace(b"style-src 'self'", b"style-src 'self' 'sha256-synthetic'")
    asset = Asset(b'synthetic document', 'text/html', policy)
    messages = []

    async def application(scope, receive, send):
        if binding == 'asset':
            scope['_crucible_public_asset'] = asset
        elif binding == 'untyped':
            scope['_crucible_public_asset'] = SimpleNamespace(content_security_policy=b"default-src *")
        await send({'type': 'http.response.start', 'status': status, 'headers': [
            (b'content-security-policy', b"default-src *"),
            (b'Content-Security-Policy', b"script-src 'unsafe-inline'")]})
        await send({'type': 'http.response.body', 'body': b''})

    async def receive():
        return {'type': 'http.request', 'body': b''}

    async def send(message):
        messages.append(message)

    scope = {'type': 'http', 'method': method, 'path': path,
             'headers': [(b'content-security-policy', b"default-src *")],
             # A prior/upstream binding is not authority for this response.
             '_crucible_public_asset': asset}
    boundary = RequestBoundary(application, Settings('sqlite://', b'x' * 32), lambda: 1, db=None)
    asyncio.run(boundary(scope, receive, send))
    headers = messages[0]['headers']
    policies = [value for key, value in headers if key.lower() == b'content-security-policy']
    assert policies == [policy if custom_policy else CONTENT_SECURITY_POLICY]
    assert (b'x-frame-options', b'DENY') in headers


@contextmanager
def selects(app):
    statements = []
    def capture(_, __, statement, ___, ____, _____):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)
    event.listen(app.state.db.engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", capture)


def test_protected_browser_read_uses_two_fresh_joined_checks(private_app):
    member = private_app.test_login("participant")
    with selects(private_app) as queries:
        response = member.client.get("/api/methods")
    assert response.status_code == 200 and response.json() == {"methods": []}
    assert len(queries) == 3  # Admission + handler authorization + method list.
    authentication = [query for query in queries if "FROM login_sessions" in query]
    assert len(authentication) == 2
    assert all("JOIN account_states" in query and "JOIN beta_memberships" in query
               for query in authentication)


def test_protected_static_admission_uses_one_query(private_app):
    member = private_app.test_login("participant")
    with selects(private_app) as queries:
        response = member.client.get("/notebook/seed.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert len(queries) == 1


@pytest.mark.parametrize("raw", ["", "not-ascii-\u00e9", "x" * 129])
def test_invalid_browser_token_does_not_query(private_app, raw):
    with selects(private_app) as queries, private_app.state.db.transaction() as session:
        with pytest.raises(HTTPException) as error:
            beta.browser_account(session, raw, lambda: private_app.test_now[0], require_membership=True)
    assert error.value.status_code == 401 and queries == []


def test_joined_lookup_refreshes_user_and_ignores_cached_membership(private_app):
    member = private_app.test_login("participant")
    raw = member.client.cookies[private_app.state.settings.cookie_name]
    with private_app.state.db.transaction(write=True) as session:
        cached_user = session.get(User, private_app.test_member)
        cached_membership = session.get(beta.BetaMembership, private_app.test_member)
        session.execute(update(User).where(User.id == private_app.test_member)
                        .values(handle="updated_participant").execution_options(synchronize_session=False))
        session.execute(update(beta.BetaMembership).where(beta.BetaMembership.user_id == private_app.test_member)
                        .values(status="revoked").execution_options(synchronize_session=False))
        assert cached_membership.status == "active"
        with pytest.raises(HTTPException) as error:
            beta.browser_account(session, raw, lambda: private_app.test_now[0], require_membership=True)
        assert error.value.status_code == 403 and "revoked" in error.value.detail
        assert cached_user.handle == "updated_participant"


def test_expiry_is_checked_after_database_read(private_app):
    member = private_app.test_login("participant")
    raw = member.client.cookies[private_app.state.settings.cookie_name]
    with private_app.state.db.transaction() as session:
        expiry = session.get(LoginSession, token_hash(raw)).expires_at
        with selects(private_app) as queries:
            def clock_after_query():
                assert len(queries) == 1
                return expiry
            with pytest.raises(HTTPException) as error:
                beta.browser_account(session, raw, clock_after_query, require_membership=True)
        assert error.value.status_code == 401


def test_membership_and_suspension_are_fresh_on_each_request(private_app):
    member = private_app.test_login("participant")
    assert member.client.get("/api/methods").status_code == 200
    with private_app.state.db.transaction(write=True) as session:
        session.execute(delete(beta.BetaMembership).where(beta.BetaMembership.user_id == private_app.test_member))
    # Account settings remain accessible to an invited account awaiting membership.
    assert member.client.get("/api/access/status").json()["access"]["allowed"] is False
    assert member.client.get("/api/methods").status_code == 403
    with private_app.state.db.transaction(write=True) as session:
        session.add(beta.BetaMembership(user_id=private_app.test_member, status="active",
                                        updated_at=private_app.test_now[0]))
    assert member.client.get("/api/methods").status_code == 200
    with private_app.state.db.transaction(write=True) as session:
        # Keep the cookie to exercise the suspension check itself, not just logout.
        session.add(beta.AccountState(user_id=private_app.test_member, suspended_at=private_app.test_now[0]))
    for path in ("/api/methods", "/api/access/status"):
        result = member.client.get(path)
        assert result.status_code == 403 and "suspended" in result.json()["detail"]


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("change", ["revoke", "expire", "suspend", "remove_membership"])
def test_changes_after_outer_admission_are_rechecked(private_app, monkeypatch, method, change):
    member = private_app.test_login("participant")
    module = importlib.import_module("crucible.app")
    original = module.run_in_threadpool
    changed = []
    def change_access():
        with private_app.state.db.transaction(write=True) as session:
            if change == "revoke":
                beta.set_membership(session, private_app.test_member, "revoked", private_app.test_now[0])
            elif change == "expire":
                session.execute(update(LoginSession).where(LoginSession.user_id == private_app.test_member)
                                .values(expires_at=private_app.test_now[0]))
            elif change == "suspend":
                session.add(beta.AccountState(user_id=private_app.test_member, suspended_at=private_app.test_now[0]))
            else:
                session.execute(delete(beta.BetaMembership).where(beta.BetaMembership.user_id == private_app.test_member))
        changed.append(True)
    async def after_admission(function, *args, **kwargs):
        value = await original(function, *args, **kwargs)
        if function.__name__ == "admit_browser":
            await original(change_access)
        return value
    monkeypatch.setattr(module, "run_in_threadpool", after_admission)
    if method == "POST":
        result = member.post("/api/methods", {"name": "Must not be saved", "pseudocode": "A synthetic test method."})
    else:
        result = member.client.get("/api/methods")
    assert changed == [True]
    assert result.status_code == (401 if change in {"revoke", "expire"} else 403)
    with private_app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Method)) == 0
