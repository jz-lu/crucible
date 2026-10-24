"""Expected admission throttles stay distinct from genuine generator faults."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from sqlalchemy import func,select
from crucible import dh_v7,generation,rsa_v4
from crucible.app import create_app,current_round
from crucible.config import Settings
from crucible.db import Challenge,LoginSession,ProfileIncident
from test_api import Actor

ISSUE={'scheme_id':'teaching-dh-kem','scheme_version':'7','profile_id':'dh-v7-tier-1'}

@pytest.fixture
def system(tmp_path):
    settings=Settings(f"sqlite:///{tmp_path/'busy.db'}",b'b'*32,origins=('http://testserver',),rate_limits=False,
        generation_concurrency=1,auth_concurrency=1)
    now=3_600_600.0;app=create_app(settings,clock=lambda:now,initialize=True)
    actor=Actor(app);user=actor.register('busy_check')
    yield SimpleNamespace(app=app,actor=actor,user=user,now=now)
    actor.client.close();app.state.db.engine.dispose()


def count(system,model):
    with system.app.state.db.transaction() as session:return session.scalar(select(func.count()).select_from(model))


@pytest.mark.parametrize('kind',['account','global','duplicate'])
def test_expected_admission_returns429_without_generation_or_incident(system,monkeypatch,kind):
    if kind=='duplicate':
        key=(system.app.state.db.engine,system.user['id'],'teaching-dh-kem','7','dh-v7-tier-1',current_round(system.now)['id'])
        held=generation.instance_reservation(key)
    else:held=system.app.state.generation_limiter.slot(system.user['id'] if kind=='account' else 'another_synthetic_account')
    with monkeypatch.context() as local:
        local.setattr(dh_v7,'generate',lambda _:pytest.fail('Rejected work must not draw a challenge'))
        with held:
            response=system.actor.post('/api/challenges',ISSUE)
            assert response.status_code==429
            assert response.json()=={'detail':'Challenge generation is busy. Please retry shortly.'}
            assert response.headers['retry-after']=='3' and 'no-store' in response.headers['cache-control']
            assert system.actor.client.get('/health').status_code==200
    assert count(system,Challenge)==count(system,generation.GenerationAttempt)==count(system,ProfileIncident)==0
    # Admission is immediately usable again once the earlier work has left.
    assert system.actor.post('/api/challenges',ISSUE).status_code==200


def test_password_admission429_does_not_create_login_session(system):
    visitor=Actor(system.app);before=count(system,LoginSession)
    try:
        with system.app.state.auth_limiter.slot('password'):
            response=visitor.post('/api/auth/login',{'handle':'busy_check','password':'correct horse battery staple'})
            assert response.status_code==429
            assert response.json()=={'detail':'Sign-in is busy. Please retry shortly.'}
            assert response.headers['retry-after']=='2' and 'no-store' in response.headers['cache-control']
            assert count(system,LoginSession)==before
            assert visitor.client.get('/api/session').json()['user'] is None
    finally:visitor.client.close()


@pytest.mark.parametrize('kind',['implementation_fault','adapter_throws_busy','resource_abort'])
def test_real_generator_failures_remain503_and_keep_accounting(system,monkeypatch,kind):
    adapter=rsa_v4 if kind=='resource_abort' else dh_v7
    error=(rsa_v4.PrimeGenerationLimit('PRIVATE') if kind=='resource_abort' else
        generation.GenerationBusy('PRIVATE') if kind=='adapter_throws_busy' else RuntimeError('PRIVATE'))
    def fail(_):raise error
    monkeypatch.setattr(adapter,'generate',fail)
    body={'scheme_id':adapter.SCHEME_ID,'scheme_version':adapter.SCHEME_VERSION,
        'profile_id':'rsa-v4-tier-1' if kind=='resource_abort' else 'dh-v7-tier-1'}
    response=system.actor.post('/api/challenges',body)
    assert response.status_code==503 and 'PRIVATE' not in response.text and 'retry-after' not in response.headers
    assert count(system,Challenge)==0 and count(system,generation.GenerationAttempt)==1
    assert count(system,ProfileIncident)==(0 if kind=='resource_abort' else 1)
    with system.app.state.db.transaction() as session:
        attempt=session.scalar(select(generation.GenerationAttempt))
        assert attempt.outcome==('generation_abort' if kind=='resource_abort' else 'implementation_fault')
