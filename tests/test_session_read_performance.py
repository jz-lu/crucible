"""Fresh browser session display in one query, including missing joined rows."""
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, update

from crucible import beta_access as beta, github_auth
from crucible.db import LoginSession
from crucible.security import token_hash
from test_browser_admission import selects
from test_beta_boundary import private_app
from test_github_auth import system as github_system, local_user, login


def test_session_joins_account_and_github_state_without_changing_response(github_system):
    identifier = local_user(github_system, linked_id="123456")
    actor = login(github_system)
    with selects(github_system.app) as queries:
        response = actor.client.get('/api/session')
    assert response.status_code == 200 and len(queries) == 1
    assert float(response.headers['server-timing'].removeprefix('app;dur=')) >= 0
    result = response.json()
    assert result['user'] == {'id': identifier, 'handle': 'existing'}
    assert result['access'] == {'mode': 'beta', 'status': 'active', 'allowed': True}
    assert result['csrf_token'] == actor.csrf
    assert result['auth'] == {'github_enabled': True, 'github_linked': True,
        'github_login': 'old-login', 'password_enabled': True, 'password_recovery': True}
    assert 'JOIN github_identities' in queries[0] and 'JOIN account_states' in queries[0]
    with github_system.db.transaction(write=True) as session:
        session.execute(update(github_auth.GitHubIdentity).where(
            github_auth.GitHubIdentity.user_id == identifier).values(github_login='changed-login'))
        session.add(beta.AccountState(user_id=identifier, suspended_at=github_system.now[0]))
    with selects(github_system.app) as queries:
        fresh = actor.client.get('/api/session').json()
    assert len(queries) == 1 and fresh['auth']['github_login'] == 'changed-login'
    assert fresh['access'] == {'mode': 'beta', 'status': 'suspended', 'allowed': False}
    assert not any(fresh['capabilities'].values())
    with github_system.db.transaction(write=True) as session:
        session.execute(delete(github_auth.GitHubIdentity))
        session.execute(delete(beta.AccountState))
        session.execute(delete(beta.BetaMembership).where(beta.BetaMembership.user_id == identifier))
    with selects(github_system.app) as queries:
        missing = actor.client.get('/api/session').json()
    assert len(queries) == 1
    assert missing['access'] == {'mode': 'beta', 'status': 'pending', 'allowed': False}
    assert missing['auth']['github_linked'] is False and missing['auth']['github_login'] is None


def test_anonymous_session_creation_and_reuse_keep_csrf_without_extra_reads(private_app):
    client = TestClient(private_app)
    with selects(private_app) as queries:
        first = client.get('/api/session')
    assert first.status_code == 200 and queries == []
    with selects(private_app) as queries:
        second = client.get('/api/session')
    assert len(queries) == 1 and first.json() == second.json()
    assert second.json()['user'] is None and second.json()['access']['allowed'] is False
    assert 'set-cookie' not in second.headers
    client.close()


def test_deleted_session_loses_identity_on_next_request(private_app):
    actor = private_app.test_login('participant')
    with private_app.state.db.transaction(write=True) as session:
        session.execute(delete(LoginSession).where(LoginSession.user_id == private_app.test_member))
    with selects(private_app) as queries:
        response = actor.client.get('/api/session')
    assert len(queries) == 1 and response.json()['user'] is None
    assert response.json()['csrf_token'] != actor.csrf and 'set-cookie' in response.headers


def test_session_expiry_is_checked_after_joined_read(private_app):
    actor = private_app.test_login('participant')
    raw = actor.client.cookies[private_app.state.settings.cookie_name]
    with private_app.state.db.transaction() as session:
        expiry = session.get(LoginSession, token_hash(raw)).expires_at
    def advance(_, __, statement, ___, ____, _____):
        if statement.lstrip().lower().startswith('select') and 'FROM login_sessions' in statement:
            private_app.test_now[0] = expiry
    event.listen(private_app.state.db.engine, 'after_cursor_execute', advance)
    try:
        response = actor.client.get('/api/session')
    finally:
        event.remove(private_app.state.db.engine, 'after_cursor_execute', advance)
    assert response.status_code == 200 and response.json()['user'] is None
    assert response.json()['access']['allowed'] is False
    assert response.json()['csrf_token'] != actor.csrf
