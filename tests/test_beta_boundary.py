"""Whole-application beta boundaries, including agent and generation revocation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import re
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import beta_access as beta, dh_v7, machine_auth
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge, LoginSession, User
from crucible.security import passwords, token_hash
from test_api import Actor

PASSWORD = "a separate beta testing password"

@pytest.fixture
def private_app(tmp_path):
    now = [3_600_600.0]
    admin_id, member_id = str(uuid4()), str(uuid4())
    settings = Settings(f"sqlite:///{tmp_path / 'private.db'}", b'b'*32,
        origins=("http://testserver",), access_mode="beta", access_admin_ids=(admin_id,),
        mail_backend="local", mail_outbox_dir=str(tmp_path / "outbox"),
        mail_from="beta@example.org", rate_limits=False)
    app = create_app(settings, clock=lambda:now[0], initialize=True)
    encoded = passwords.hash(PASSWORD)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in ((admin_id,"operator"),(member_id,"participant")):
            session.add(User(id=identifier,handle=handle,password_hash=encoded,created_at=now[0]))
        session.flush()
        beta.set_membership(session,admin_id,"active",now[0])
        beta.set_membership(session,member_id,"active",now[0])
    def login(handle):
        actor=Actor(app)
        result=actor.post('/api/auth/login',{'handle':handle,'password':PASSWORD})
        assert result.status_code == 200, result.text
        actor.csrf=result.json()['csrf_token']
        return actor
    app.test_login, app.test_member, app.test_admin, app.test_now = login, member_id, admin_id, now
    yield app
    app.state.db.engine.dispose()

@pytest.mark.parametrize('path',[
 '/api/schemes','/api/people','/api/people/participant','/api/leaderboard',
 '/api/protocol/source','/api/protocol/artifacts','/api/protocol/artifact-source',
 '/api/protocol/spec','/api/protocol/spec-schema','/api/protocol/spec-template',
 '/api/protocol/proposal-guide',
 '/api/protocol/correctness','/api/protocol/example',
 '/api/submissions/00000000-0000-0000-0000-000000000000',
 '/api/methods/00000000-0000-0000-0000-000000000000/source/download',
 '/notebook/seed.js','/notebook/store.js','/openapi.json',
])
def test_private_reads_and_head_are_not_anonymous(private_app,path):
    with TestClient(private_app) as client:
        for method in ('GET','HEAD'):
            response=client.request(method,path)
            assert response.status_code==401
            assert response.headers['cache-control']=='no-store'
            assert response.headers['referrer-policy']=='no-referrer'
            assert 'frame-ancestors' in response.headers['content-security-policy']


def test_unknown_routes_fail_closed_even_for_members(private_app):
    @private_app.get('/accidentally-public')
    def accidentally_public():
        return {'private':'never'}
    member=private_app.test_login('participant')
    assert member.client.get('/accidentally-public').status_code==404
    with pytest.raises(RuntimeError,match='Unclassified route'):
        private_app.state.access_policy.register(private_app.routes)


def test_invited_signup_verifies_fixed_mailbox_before_access(private_app):
    admin=private_app.test_login('operator')
    visitor=Actor(private_app)
    credentials={'handle':'new_researcher','password':PASSWORD}
    assert visitor.post('/api/auth/register',credentials).status_code==403
    response=admin.post('/api/access/invitations',{'email':'Researcher@example.org'})
    assert response.status_code==200
    invitation=response.json()['invitation']
    token=invitation['url'].split('token=',1)[1]
    response=visitor.post('/api/auth/register',{**credentials,'invitation_token':token})
    assert response.status_code==200,response.text
    visitor.csrf=response.json()['csrf_token']
    assert response.json()['access']=={'mode':'beta','allowed':False,'status':'pending'}
    assert visitor.client.get('/api/schemes').status_code==403
    assert visitor.post('/api/access/invitations/verify/start',{'token':token}).status_code==200
    messages=list(Path(private_app.state.settings.mail_outbox_dir).glob('*.eml'))
    assert len(messages)==1
    content=messages[0].read_text()
    assert 'To: Researcher@example.org' in content
    code=re.search(r'code is: ([0-9]{8})',content).group(1)
    assert visitor.post('/api/access/invitations/redeem',{'token':token,'code':code}).status_code==200
    assert visitor.client.get('/api/schemes').status_code==200
    session=visitor.client.get('/api/session').json()
    assert session['access']['allowed'] and not session['capabilities']['manage_beta_access']


def connect(member):
    start=member.client.post('/api/agent/connect/start',json={'label':'Isolated beta test'}).json()
    assert member.post('/api/agent/connect/'+start['user_code']+'/approve',{}).status_code==200
    return start


def test_revocation_blocks_existing_browser_bearer_and_device_replay(private_app):
    member=private_app.test_login('participant')
    connection=connect(member)
    token=member.client.post('/api/agent/connect/poll',json={'device_code':connection['device_code']}).json()['access_token']
    header={'Authorization':'Bearer '+token}
    assert member.client.get('/api/schemes',headers=header).status_code==200
    with private_app.state.db.transaction(write=True) as session:
        beta.set_membership(session,private_app.test_member,'revoked',private_app.test_now[0])
    assert member.client.get('/api/schemes').status_code==401
    assert member.client.get('/api/schemes',headers=header).status_code==401
    assert member.client.post('/api/agent/connect/poll',json={'device_code':connection['device_code']}).json()['status']=='denied'
    with private_app.state.db.transaction(write=True) as session:
        beta.set_membership(session,private_app.test_member,'active',private_app.test_now[0]+1)
    assert member.client.get('/api/schemes',headers=header).status_code==401
    assert member.client.get('/api/schemes').status_code==401


def test_revoked_approval_cannot_mint_first_agent_token(private_app):
    member=private_app.test_login('participant')
    connection=connect(member)
    with private_app.state.db.transaction(write=True) as session:
        beta.set_membership(session,private_app.test_member,'revoked',private_app.test_now[0])
    response=member.client.post('/api/agent/connect/poll',json={'device_code':connection['device_code']})
    assert response.json()['status']=='denied'
    with private_app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(machine_auth.MachineToken))==0


def test_revoke_during_generation_prevents_publication(private_app,monkeypatch):
    member=private_app.test_login('participant')
    entered,release=Event(),Event()
    actual=dh_v7.generate
    def blocked(profile):
        entered.set()
        assert release.wait(5)
        return actual(profile)
    monkeypatch.setattr(dh_v7,'generate',blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(member.post,'/api/challenges',{'scheme_id':dh_v7.SCHEME_ID,'scheme_version':dh_v7.SCHEME_VERSION,'profile_id':'dh-v7-tier-1'})
        assert entered.wait(3)
        try:
            with private_app.state.db.transaction(write=True) as session:
                beta.set_membership(session,private_app.test_member,'revoked',private_app.test_now[0])
        finally:
            release.set()
        assert future.result(timeout=5).status_code==401
    with private_app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge))==0
    assert private_app.state.generation_limiter.snapshot()['inflight']==0


def test_password_capacity_does_not_block_reads_or_create_sessions(private_app):
    visitor=Actor(private_app)
    with private_app.state.auth_limiter.slot('password'),private_app.state.auth_limiter.slot('password'):
        response=visitor.post('/api/auth/login',{'handle':'participant','password':PASSWORD})
        assert response.status_code==429 and response.headers['retry-after']=='2'
        assert visitor.client.get('/health').status_code==200
        assert visitor.client.get('/api/session').json()['user'] is None


def test_login_handle_quota_is_shared_across_peer_ips(private_app):
    limited = create_app(replace(private_app.state.settings, rate_limits=True),
                         clock=lambda: private_app.test_now[0])
    clients = [TestClient(limited, client=(f'203.0.113.{index}', 50000)) for index in (1, 2, 3)]
    try:
        csrf = [client.get('/api/session').json()['csrf_token'] for client in clients]
        def login(index, handle, password):
            return clients[index].post('/api/auth/login', json={'handle': handle, 'password': password},
                headers={'Origin': 'http://testserver', 'X-CSRF-Token': csrf[index]})
        for attempt in range(10):
            # Alternating peers and case variants must share one normalized
            # handle budget, independently of each coarse IP allowance.
            response = login(attempt % 2, 'Participant' if attempt % 2 else 'PARTICIPANT',
                             'incorrect password of valid length')
            assert response.status_code == 401
        blocked = login(2, 'participant', PASSWORD)
        assert blocked.status_code == 429
        assert int(blocked.headers['retry-after']) > 0
        unaffected = login(2, 'operator', PASSWORD)
        assert unaffected.status_code == 200, unaffected.text
        csrf[2] = unaffected.json()['csrf_token']
        private_app.test_now[0] += 901
        assert login(2, 'participant', PASSWORD).status_code == 200
    finally:
        for client in clients:
            client.close()
        limited.state.db.engine.dispose()


def test_production_is_beta_only_until_publication_policy_exists():
    with pytest.raises(RuntimeError,match='public publication'):
        Settings('sqlite://',b'b'*32,production=True)
    with pytest.raises(RuntimeError,match='development only'):
        Settings('sqlite://',b'b'*32,production=True,access_mode='beta',mail_backend='local',mail_outbox_dir='/tmp/outbox')


def test_production_release_allowlist_is_separate_from_membership(private_app):
    settings=replace(private_app.state.settings,production=True,mail_backend='disabled',
                     beta_releases=(dh_v7.SCHEME_ID+'@'+dh_v7.SCHEME_VERSION,))
    hosted=create_app(settings,clock=lambda:private_app.test_now[0])
    client=TestClient(hosted,base_url='https://testserver')
    # Synthetic credential only, avoiding HTTP-origin differences from the
    # cookie/TLS behavior under test. Real deployment still requires PostgreSQL.
    raw='test-hosted-session'
    with hosted.state.db.transaction(write=True) as session:
        session.add(LoginSession(token_hash=token_hash(raw),user_id=private_app.test_member,expires_at=private_app.test_now[0]+100))
    client.cookies.set(settings.cookie_name,raw)
    from crucible.security import mac
    headers={'Origin':'http://testserver','X-CSRF-Token':mac(settings.secret,'csrf-v1',raw.encode())}
    allowed=client.post('/api/challenges',json={'profile_id':'dh-v7-tier-1'},headers=headers)
    assert allowed.status_code==200,allowed.text
    denied=client.post('/api/challenges',json={'profile_id':'rsa-v4-tier-1'},headers=headers)
    assert denied.status_code==409
    client.close()
    hosted.state.db.engine.dispose()
