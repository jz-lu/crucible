"""Explicit bounty scopes never inherit human review or default-token powers."""
from uuid import uuid4

from crucible import machine_auth
from test_bounties import system, create, request, opened, body, terms
from test_notebook_agents import agent


def test_bounty_routes_are_explicit_and_absent_from_default_scopes(system):
    app, owner, *_ = system
    assert not set(machine_auth.BOUNTY_SCOPES) & set(machine_auth.DEFAULT_SCOPES)
    default = agent(app, owner, machine_auth.DEFAULT_SCOPES)
    for path in ['/api/bounties', '/api/me/bounty-allowance']:
        assert default.get(path).status_code == 403
    assert default.post('/api/bounties', json={'terms': terms(), 'idempotency_key': str(uuid4())}).status_code == 403
    assert machine_auth.required_scope('GET', '/api/bounties/review-queue') is None
    assert machine_auth.required_scope('POST', '/api/bounties/not-a-uuid/boosts') is None


def test_read_and_write_scopes_cannot_allocate_monthly_allowance(system):
    app, owner, *_ = system
    value = create(owner)
    for scopes in [('bounties:read',), ('bounties:write',)]:
        client = agent(app, owner, scopes)
        path = '/api/bounties/' + value['bounty']['id']
        assert client.get(path).status_code == (200 if scopes[0].endswith('read') else 403)
        assert client.post(path + '/admission-requests', json=body(value,
            terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],confirm_cost_units=1)).status_code == 403
    assert owner.client.get('/api/me/bounty-allowance').json()['allowance']['available_units'] == 10


def test_spend_scope_authorizes_only_exact_allowance_operations(system):
    app, owner, *_ = system
    value = create(owner)
    client = agent(app, owner, ['bounties:spend'])
    assert client.get('/api/bounties').status_code == 403
    assert client.post('/api/bounties',json={'terms':terms(),'idempotency_key':str(uuid4())}).status_code == 403
    payload = body(value,terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],confirm_cost_units=1)
    path='/api/bounties/'+value['bounty']['id']+'/admission-requests'
    assert client.post(path,json=payload).status_code == 200
    assert client.post(path,json=payload).status_code == 200
    assert owner.client.get('/api/me/bounty-allowance').json()['allowance']['available_units'] == 9


def test_even_reviewer_agents_cannot_decide_or_discover_private_review_queue(system):
    app, owner, reviewer, *_ = system
    value=request(owner,create(owner))
    client=agent(app,reviewer,machine_auth.BOUNTY_SCOPES)
    client.cookies.update(reviewer.client.cookies)
    assert client.get('/api/bounties/review-queue').status_code == 403
    assert client.get('/api/bounties/'+value['bounty']['id']).status_code == 404
    identifier=value['bounty']['id']
    for suffix in ('/admission-decisions','/submissions/'+str(uuid4())+'/decisions'):
        assert machine_auth.required_scope('POST','/api/bounties/'+identifier+suffix) is None


def test_guidance_is_available_without_granting_other_private_resources(system):
    app,owner,*_=system
    client=agent(app,owner,['bounties:read'])
    assert client.get('/api/agent/pipeline').status_code == 200
    assert client.get('/api/notebook/export').status_code == 403
    assert client.get('/api/me/submissions').status_code == 403
