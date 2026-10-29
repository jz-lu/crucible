"""Real bounded HTTP workflow on disposable data; no submitted programs execute."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import beta_access, bounty_allowance as allowance, registry
from crucible.app import create_app
from crucible.bounty_models import (BountyAllowanceEvent as Event, BountyAward, BountyDecision,
                                   BountyPolicy, BountySubmission, BountyTask, BountyWallet)
from crucible.config import Settings
from crucible.db import User
from test_notebook import Actor, create as create_note, request_publication, approve, content

ORIGIN = 'http://testserver'


def make_system(database_url, monkeypatch, *, initialize=True, seed_policy=True):
    monkeypatch.setattr(registry, 'ADAPTERS', {k:v for k,v in registry.ADAPTERS.items() if k[0] in ('teaching-dh-kem', 'rsa-kem')})
    monkeypatch.setattr(registry, 'CURRENT', {'teaching-dh-kem': '7', 'rsa-kem': '4'})
    now = [datetime(2026,10,29,tzinfo=timezone.utc).timestamp()]
    ids = [str(uuid4()) for _ in range(5)]
    settings = Settings(database_url, b'b' * 32, origins=(ORIGIN,),
                        reviewer_ids=(ids[1], ids[4]), rate_limits=False, access_mode='beta')
    app = create_app(settings, clock=lambda:now[0], initialize=initialize)
    with app.state.db.transaction(write=True) as session:
        session.add_all([User(id=i, handle=h, created_at=now[0]) for i,h in zip(ids, ('poster','reviewer','solver','supporter','reviewer2'))])
        session.flush()
        session.add_all([beta_access.BetaMembership(user_id=i,status='active',updated_at=now[0]) for i in ids])
        if seed_policy:
            session.add(BountyPolicy(id=allowance.INITIAL_ID,effective_month=allowance.INITIAL_MONTH,
                                    configuration=deepcopy(allowance.INITIAL_CONFIGURATION),created_at=now[0]))
    actors = [Actor(app,i) for i in ids]
    return app, *actors, now


@pytest.fixture
def system(tmp_path, monkeypatch):
    value=make_system(f"sqlite:///{tmp_path / 'bounties.db'}",monkeypatch)
    yield value
    value[0].state.db.engine.dispose()


def terms(**changes):
    return {'title':'Reproduce a bounded experiment','summary':'Check the recorded finite result.',
            'background_tags':['finite groups'],'scope':4,'completion_criteria':'Reproduce the specified outputs and document discrepancies.',
            'verification_plan':'Independent reproduction from exact source and inputs; record limitations.',
            'source_notes':[],**changes}


def post(actor,path,body,code=200):
    response = actor.post(path,body)
    assert response.status_code == code,response.text
    return response.json()


def create(actor,**changes):
    return post(actor,'/api/bounties',{'terms':terms(**changes),'idempotency_key':str(uuid4())})


def body(value,**changes):
    task=value['bounty']
    return {'expected_version':task['version'],'idempotency_key':str(uuid4()),**changes}


def request(actor,value):
    return post(actor,'/api/bounties/'+value['bounty']['id']+'/admission-requests',body(value,
        terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],confirm_cost_units=1))


def decision(actor,value,choice='approved'):
    return post(actor,'/api/bounties/'+value['bounty']['id']+'/admission-decisions',body(value,
        terms_sha256=value['bounty']['terms_sha256'],decision=choice,reason='The scoped brief and evidence have been reviewed.',
        checks={key:True for key in ('scope','evidence','verification','duplicates','ownership')}))


def opened(system):
    _,owner,reviewer,*_=system
    return decision(reviewer,request(owner,create(owner)))


def boost(actor,value,units=1,key=None):
    return post(actor,'/api/bounties/'+value['bounty']['id']+'/boosts',body(value,
        terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=units,
        confirm_cost_units=units,**({'idempotency_key':key} if key else {})))


def published(actor,reviewer):
    note=create_note(actor,content(title='A reproducible result'))
    request_publication(actor,note)
    approve(reviewer,note)
    return {key:note[key] for key in ('revision','content_sha256')}|{'note_id':note['id']}


def submit(actor,value,note,claim='solution'):
    return post(actor,'/api/bounties/'+value['bounty']['id']+'/submissions',body(value,
        terms_sha256=value['bounty']['terms_sha256'],contribution_note=note,claim=claim,
        explanation='This exact note gives the specified evidence and its limitations.'))


def assess(actor,value,kind,choice='approved',submission_id=None):
    sid=submission_id or value['receipt']['submission_id']
    return post(actor,'/api/bounties/'+value['bounty']['id']+'/submissions/'+sid+'/decisions',body(value,
        kind=kind,decision=choice,reason='Independent evaluation of the exact scoped result.'))


def available(actor):return actor.client.get('/api/me/bounty-allowance').json()['allowance']['available_units']


def test_default_board_empty_allowance_read_is_not_a_grant_and_drafting_is_free(system):
    app,owner,*_=system
    result=owner.client.get('/api/bounties').json()
    assert result['bounties']==[] and result['policy']['monthly_units']==10
    value=create(owner)
    assert value['bounty']['status']=='draft' and available(owner)==10
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Event))==0
        assert session.scalar(select(func.count()).select_from(BountyAward))==0


def test_reserve_admit_boost_and_independent_single_award(system):
    app,owner,reviewer,solver,supporter,_,now=system
    value=request(owner,create(owner))
    assert available(owner)==9 and value['bounty']['reward_points']==0
    value=decision(reviewer,value)
    assert value['bounty']['reward_points']==100 and available(owner)==9
    value=boost(supporter,value)
    assert value['bounty']['reward_points']==200 and available(supporter)==9
    value=submit(solver,value,published(solver,reviewer))
    assert value['bounty']['status']=='open' # A claim is not qualification.
    sid=value['receipt']['submission_id']
    value=assess(reviewer,value,'pipeline',submission_id=sid)
    assert value['bounty']['status']=='frozen' and value['bounty']['award'] is None
    value=assess(owner,value,'poster',submission_id=sid)
    assert value['bounty']['status']=='completed' and value['bounty']['award']['points']==200
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(BountyAward))==1
        points=session.execute(allowance.bounty_points_relation()).one()
        assert points.user_id==solver.identifier and points.bounty_points==200
        assert session.scalar(select(func.sum(Event.available_delta)).where(Event.user_id==owner.identifier))==9


def test_stale_and_changed_idempotent_requests_never_spend_twice(system):
    app,owner,reviewer,solver,supporter,*_=system
    value=opened(system);key=str(uuid4())
    request_body=body(value,terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=1,confirm_cost_units=1,idempotency_key=key)
    path='/api/bounties/'+value['bounty']['id']+'/boosts'
    first=post(supporter,path,request_body)
    retry=post(supporter,path,request_body)
    assert retry['receipt']==first['receipt'] and available(supporter)==9
    post(supporter,path,{**request_body,'units':2,'confirm_cost_units':2},409)
    post(supporter,path,{**request_body,'idempotency_key':str(uuid4())},409)
    assert available(supporter)==9


def test_decline_and_withdraw_return_reservation_once_without_reward(system):
    app,owner,reviewer,*_=system
    value=decision(reviewer,request(owner,create(owner)),'declined')
    assert available(owner)==10 and value['bounty']['status']=='declined'
    value=request(owner,create(owner));path='/api/bounties/'+value['bounty']['id']+'/withdrawals'
    b=body(value,reason='Withdrawing the pending brief.')
    first=post(owner,path,b);post(owner,path,b)
    assert available(owner)==10 and first['bounty']['status']=='withdrawn'
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind=='release'))==2
        assert session.scalar(select(func.count()).select_from(BountyAward))==0


def test_revision_feedback_keeps_one_reservation_and_frozen_terms_do_not_edit(system):
    _,owner,reviewer,*_=system
    value=decision(reviewer,request(owner,create(owner)),'changes_requested')
    assert available(owner)==9
    value=post(owner,'/api/bounties/'+value['bounty']['id']+'/revisions',body(value,terms=terms(title='Clarified finite experiment')))
    value=request(owner,value)
    assert available(owner)==9 and value['bounty']['revision']==2
    value=decision(reviewer,value)
    post(owner,'/api/bounties/'+value['bounty']['id']+'/revisions',body(value,terms=terms()),409)


def test_qualification_freezes_offer_but_only_final_award_refunds_later_boosts(system):
    _,owner,reviewer,solver,supporter,*_=system
    value=boost(supporter,opened(system))
    value=submit(solver,value,published(solver,reviewer));sid=value['receipt']['submission_id']
    value=boost(supporter,value)
    assert value['bounty']['reward_points']==300 and available(supporter)==8
    value=assess(reviewer,value,'pipeline',submission_id=sid)
    assert value['bounty']['reward_points']==200 and available(supporter)==8
    post(supporter,'/api/bounties/'+value['bounty']['id']+'/boosts',body(value,
        terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=1,confirm_cost_units=1),409)
    value=assess(owner,value,'poster',submission_id=sid)
    assert value['bounty']['award']['points']==200 and available(supporter)==9


def test_winner_cannot_convert_their_own_boost_to_score(system):
    _,owner,reviewer,solver,supporter,*_=system
    value=boost(solver,opened(system),2)
    assert available(solver)==8
    value=submit(solver,value,published(solver,reviewer));sid=value['receipt']['submission_id']
    value=assess(owner,value,'poster',submission_id=sid)
    value=assess(reviewer,value,'pipeline',submission_id=sid)
    assert value['bounty']['award']['points']==100 and available(solver)==10


def test_earlier_unresolved_claim_blocks_later_award_until_recorded_rejection(system):
    _,owner,reviewer,solver,supporter,*_=system
    value=submit(solver,opened(system),published(solver,reviewer));first=value['receipt']['submission_id']
    value=submit(supporter,value,published(supporter,reviewer));second=value['receipt']['submission_id']
    value=assess(owner,value,'poster',submission_id=second)
    value=assess(reviewer,value,'pipeline',submission_id=second)
    assert value['bounty']['status']=='frozen' and value['bounty']['award'] is None
    value=assess(reviewer,value,'pipeline','declined',submission_id=first)
    assert value['bounty']['award']['submission_id']==second


def test_only_one_active_claim_and_no_owner_or_unpublished_claim(system):
    _,owner,reviewer,solver,*_=system
    value=opened(system);note=create_note(solver)
    pin={'note_id':note['id'],'revision':note['revision'],'content_sha256':note['content_sha256']}
    path='/api/bounties/'+value['bounty']['id']+'/submissions'
    post(solver,path,body(value,terms_sha256=value['bounty']['terms_sha256'],contribution_note=pin,claim='solution',explanation='A result.'),409)
    value=submit(solver,value,published(solver,reviewer))
    post(solver,path,body(value,terms_sha256=value['bounty']['terms_sha256'],contribution_note=published(solver,reviewer),claim='solution',explanation='Another result.'),409)
    post(owner,path,body(value,terms_sha256=value['bounty']['terms_sha256'],contribution_note=published(owner,reviewer),claim='solution',explanation='Own result.'),403)


def test_proposal_owner_and_solver_cannot_supply_pipeline_approval(system):
    _,owner,reviewer,solver,*_=system
    value=submit(solver,opened(system),published(solver,reviewer));sid=value['receipt']['submission_id']
    path='/api/bounties/'+value['bounty']['id']+'/submissions/'+sid+'/decisions'
    for actor in (owner,solver):post(actor,path,body(value,kind='pipeline',decision='approved',reason='Self review.'),403)
    post(reviewer,path,body(value,kind='poster',decision='approved',reason='Not the poster.'),403)


def test_month_boundary_refunds_stay_expired_and_grants_do_not_accumulate(system):
    app,owner,reviewer,solver,supporter,_,now=system
    pending=request(owner,create(owner))
    value=boost(supporter,opened(system),2)
    assert available(owner)==8 and available(supporter)==8
    now[0]=datetime(2026,11,1,tzinfo=timezone.utc).timestamp()
    # Renew fixture browser sessions; normal sessions also expire independently.
    for actor in (owner,reviewer,supporter):
        fresh=Actor(app,actor.identifier);actor.client,actor.csrf=fresh.client,fresh.csrf
    assert available(owner)==10 and available(supporter)==10
    decision(reviewer,pending,'declined')
    assert available(owner)==10
    value=post(owner,'/api/bounties/'+value['bounty']['id']+'/cancellations',body(value,reason='Close this unclaimed task.'))
    assert value['receipt']['refunds']==[{'units':2,'available_units':0,'original_month':'2026-10'}]
    assert available(supporter)==10
    allowance.grant_current_month(app.state.db,app.state.settings,now=now[0])
    allowance.grant_current_month(app.state.db,app.state.settings,now=now[0])
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind=='grant',Event.month=='2026-11'))==5
        assert session.scalar(select(Event.available_delta).where(Event.kind=='release',Event.original_event_id==pending['receipt']['reservation_id']))==0


def test_concurrent_retries_have_one_effect_and_spending_never_goes_negative(system):
    app,owner,reviewer,solver,supporter,*_=system
    value=opened(system);path='/api/bounties/'+value['bounty']['id']+'/boosts'
    b=body(value,terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=10,confirm_cost_units=10)
    def send(_):return supporter.post(path,b)
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(send,range(4)))
    assert all(r.status_code==200 for r in responses)
    assert len({r.json()['receipt']['boost_id'] for r in responses})==1 and available(supporter)==0
    latest=responses[0].json()
    post(supporter,path,body(latest,terms_sha256=latest['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=1,confirm_cost_units=1),409)
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind=='boost'))==1
        assert session.get(BountyWallet,supporter.identifier).available_units==0


def test_revocation_private_drafts_and_csrf_are_enforced(system):
    app,owner,reviewer,solver,*_=system
    value=create(owner);path='/api/bounties/'+value['bounty']['id']
    assert solver.client.get(path).status_code==404
    assert reviewer.client.get(path).status_code==404
    assert owner.client.post('/api/bounties',json={'terms':terms(),'idempotency_key':str(uuid4())}).status_code==403
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session,solver.identifier,'revoked',app.state.clock())
    assert solver.client.get('/api/bounties').status_code==401
    solver=Actor(app,solver.identifier)
    assert solver.client.get('/api/bounties').status_code==403
    assert solver.post('/api/bounties',{'terms':terms(),'idempotency_key':str(uuid4())}).status_code==403


def test_partial_work_and_declined_claim_never_lock_or_award(system):
    app,owner,reviewer,solver,supporter,*_=system
    value=submit(solver,opened(system),published(solver,reviewer),'partial')
    partial=value['receipt']['submission_id']
    path='/api/bounties/'+value['bounty']['id']+'/submissions/'+partial+'/decisions'
    post(reviewer,path,body(value,kind='pipeline',decision='approved',reason='Not a completion claim.'),409)
    value=submit(supporter,value,published(supporter,reviewer));sid=value['receipt']['submission_id']
    value=assess(reviewer,value,'pipeline','declined',submission_id=sid)
    assert value['bounty']['status']=='open' and value['bounty']['award'] is None
    value=boost(supporter,value)
    assert value['bounty']['reward_points']==200


def test_withdrawn_exact_evidence_prevents_award_without_substituting_latest_revision(system):
    from crucible import notebook,notebook_moderation
    app,owner,reviewer,solver,*_=system
    pin=published(solver,reviewer)
    value=submit(solver,opened(system),pin);sid=value['receipt']['submission_id']
    value=assess(reviewer,value,'pipeline',submission_id=sid)
    post(reviewer,'/api/notebook/notes/'+pin['note_id']+'/publication-withdrawals',{
        'revision':pin['revision'],'content_sha256':pin['content_sha256'],'reason':'The source claim needs correction.',
        'idempotency_key':str(uuid4())})
    path='/api/bounties/'+value['bounty']['id']+'/submissions/'+sid+'/decisions'
    post(owner,path,body(value,kind='poster',decision='approved',reason='Trying to accept withdrawn evidence.'),409)
    detail=owner.client.get('/api/bounties/'+value['bounty']['id']).json()
    assert detail['bounty']['submissions'][0]['contribution_note']['unavailable']
    with app.state.db.transaction() as session:assert session.scalar(select(func.count()).select_from(BountyAward))==0


def test_future_policy_is_versioned_and_existing_offer_keeps_original_value(system):
    app,owner,reviewer,solver,supporter,_,now=system
    value=opened(system)
    next_policy={**allowance.INITIAL_CONFIGURATION,'monthly_units':12,'points_per_boost':150}
    with pytest.raises(ValueError,match='future'):
        allowance.configure_policy(app.state.db,app.state.settings,actor_id=reviewer.identifier,
            effective_month='2026-10',configuration=next_policy,now=now[0])
    configured=allowance.configure_policy(app.state.db,app.state.settings,actor_id=reviewer.identifier,
            effective_month='2026-11',configuration=next_policy,now=now[0])
    assert allowance.configure_policy(app.state.db,app.state.settings,actor_id=reviewer.identifier,
            effective_month='2026-11',configuration=next_policy,now=now[0])==configured
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        allowance.configure_policy(app.state.db,app.state.settings,actor_id=reviewer.identifier,
            effective_month='2026-11',configuration={**next_policy,'monthly_units':13},now=now[0])
    now[0]=datetime(2026,11,2,tzinfo=timezone.utc).timestamp()
    supporter=Actor(app,supporter.identifier)
    latest=supporter.client.get('/api/bounties/'+value['bounty']['id']).json()
    assert latest['policy']['id']==configured['id'] and latest['bounty']['base_reward_points']==100
    assert available(supporter)==12
    post(supporter,'/api/bounties/'+value['bounty']['id']+'/boosts',body(latest,
        terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=1,confirm_cost_units=1),409)
    latest=boost(supporter,latest)
    assert latest['bounty']['reward_points']==250 and available(supporter)==11


def test_owner_and_reviewer_cannot_admit_their_own_task_even_in_development(system):
    _,owner,reviewer,*_=system
    value=request(reviewer,create(reviewer))
    post(reviewer,'/api/bounties/'+value['bounty']['id']+'/admission-decisions',body(value,
        terms_sha256=value['bounty']['terms_sha256'],decision='approved',reason='Own task.',
        checks={key:True for key in ('scope','evidence','verification','duplicates','ownership')}),403)


@pytest.mark.parametrize('units',[0,-1,True,1.5,11])
def test_boost_units_are_bounded_positive_integers(system,units):
    _,_,_,_,supporter,*_=system
    value=opened(system)
    post(supporter,'/api/bounties/'+value['bounty']['id']+'/boosts',body(value,
        terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=units,confirm_cost_units=1),422)
    assert available(supporter)==10


def test_no_same_month_refund_or_expiry_can_increase_original_budget(system):
    _,owner,reviewer,solver,supporter,*_=system
    value=boost(supporter,opened(system),3)
    assert available(supporter)==7
    path='/api/bounties/'+value['bounty']['id']+'/cancellations';b=body(value,reason='Cancel unclaimed task.')
    first=post(owner,path,b);post(owner,path,b)
    assert available(supporter)==10
    assert first['receipt']['refunds']==[{'units':3,'available_units':3,'original_month':'2026-10'}]
    history=supporter.client.get('/api/me/bounty-allowance').json()['ledger']
    assert len([row for row in history if row['kind']=='refund'])==1
    assert sum(row['available_delta'] for row in history)==10


def test_background_tags_reject_duplicates_after_normalization(system):
    _,owner,*_=system
    post(owner,'/api/bounties',{'terms':terms(background_tags=['groups',' groups ']),'idempotency_key':str(uuid4())},422)


def test_personal_activity_includes_contributions_but_not_other_private_tasks(system):
    _,owner,reviewer,solver,*_=system
    private=create(owner)
    value=submit(solver,opened(system),published(solver,reviewer),'partial')
    own=create(solver)
    page=solver.client.get('/api/bounties?status=mine').json()['bounties']
    assert {task['id'] for task in page}=={own['bounty']['id'],value['bounty']['id']}
    assert private['bounty']['id'] not in {task['id'] for task in page}


def test_rejected_earlier_claim_cannot_destroy_later_qualified_offer(system):
    app,owner,reviewer,solver,supporter,reviewer2,_=system
    value=submit(solver,opened(system),published(solver,reviewer));first=value['receipt']['submission_id']
    value=boost(reviewer2,value,3)
    value=submit(supporter,value,published(supporter,reviewer));second=value['receipt']['submission_id']
    assert value['bounty']['submissions'][1]['offered_reward_points']==400
    value=assess(reviewer,value,'pipeline',submission_id=second)
    assert value['bounty']['reward_points']==400 and available(reviewer2)==7
    value=assess(reviewer,value,'pipeline',submission_id=first)
    assert value['bounty']['reward_points']==100 and available(reviewer2)==7
    value=assess(owner,value,'poster','declined',submission_id=first)
    assert value['bounty']['reward_points']==400 and available(reviewer2)==7
    value=assess(owner,value,'poster',submission_id=second)
    assert value['bounty']['award']['points']==400 and available(reviewer2)==7
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Event).where(Event.kind=='refund'))==0
