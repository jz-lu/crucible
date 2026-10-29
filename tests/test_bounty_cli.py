"""Standalone CLI through actual scoped HTTP routes on a disposable database."""
from copy import deepcopy
from io import BytesIO
import json
import time
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import func, select

from scripts import crucible_cli as cli
from crucible import machine_auth
from crucible.bounty_models import BountyTask, BountySubmission
from test_bounties import system, terms, available, opened, decision, published, boost
from test_notebook_agents import agent


@pytest.fixture
def connected_cli(system,tmp_path,monkeypatch):
    app,poster,reviewer,solver,supporter,*_=system
    server='http://localhost:8000'; config=tmp_path/'credentials.json'
    state={'requests':[],'lose_once':False,'scopes':list(machine_auth.BOUNTY_SCOPES),'actor':poster}
    def connect(actor=poster,scopes=machine_auth.BOUNTY_SCOPES):
        api=agent(app,actor,scopes); state.update(api=api,actor=actor,scopes=list(scopes))
        handle={poster.identifier:'poster',solver.identifier:'solver',supporter.identifier:'supporter'}[actor.identifier]
        cli.save_json(config,{'format':'crucible-client-config-v1','default_server':server,'servers':{server:{
            'access_token':api.headers['Authorization'].removeprefix('Bearer '),'expires_at':time.time()+3600,'handle':handle,'scopes':list(scopes)}}})
    connect()
    class Reply(BytesIO):
        def __init__(self,data,url): super().__init__(data); self.url=url
    class Transport:
        def open(self,request,timeout):
            path=request.full_url.removeprefix(server);state['requests'].append((request.get_method(),path,request.data))
            response=state['api'].request(request.get_method(),path,content=request.data,headers=dict(request.header_items()))
            if response.status_code>=400: raise HTTPError(request.full_url,response.status_code,'fixture',response.headers,BytesIO(response.content))
            if state['lose_once'] and request.get_method()=='POST': state['lose_once']=False;raise URLError('lost after commit')
            return Reply(response.content,request.full_url)
    original=cli.Client.__init__
    def initialize(self,*args,**kwargs): original(self,*args,**kwargs);self.opener=Transport()
    monkeypatch.setattr(cli.Client,'__init__',initialize)
    def invoke(*args): return cli.main(['--config',str(config),'--json','bounty',*map(str,args)])
    return system,invoke,state,tmp_path,config,connect


def output(capsys): return json.loads(capsys.readouterr().out)
def posts(state): return [item for item in state['requests'] if item[0]=='POST']
def count(app,model):
    with app.state.db.transaction() as session:return session.scalar(select(func.count()).select_from(model))

def init_draft(f,capsys):
    system,invoke,state,root,*_=f; path=root/'draft'
    assert invoke('init','--path',path)==0;output(capsys)
    cli.save_json(path/'terms.json',terms())
    return path


def test_init_and_private_save_do_not_spend_and_lost_create_replays_exactly(connected_cli,capsys):
    system,invoke,state,root,*_=connected_cli;app,poster,*_=system
    path=init_draft(connected_cli,capsys)
    assert posts(state)==[];assert available(poster)==10
    state['lose_once']=True
    assert invoke('save','--path',path)==1;assert output(capsys)['error']['code']=='network_error'
    assert count(app,BountyTask)==1
    assert invoke('save','--path',path)==0;receipt=output(capsys)['receipt']
    assert posts(state)[0]==posts(state)[1];assert count(app,BountyTask)==1
    assert receipt['status']=='draft';assert available(poster)==10
    assert (path/'.bounty-request.json').stat().st_mode & 0o777 == 0o600
    assert invoke('save','--path',path)==0;assert output(capsys)['status']=='unchanged';assert len(posts(state))==2


def test_uncertain_changes_account_and_scope_cannot_replace_frozen_input(connected_cli,capsys):
    system,invoke,state,root,config,connect=connected_cli
    path=init_draft(connected_cli,capsys);state['lose_once']=True
    assert invoke('save','--path',path)==1;output(capsys)
    cli.save_json(path/'terms.json',terms(title='Changed after unknown save'))
    before=len(posts(state));assert invoke('save','--path',path)==1;assert output(capsys)['error']['code']=='bounty_retry_changed';assert len(posts(state))==before
    cli.save_json(path/'terms.json',terms());connect(system[1],['bounties:read','bounties:write'])
    assert invoke('save','--path',path)==1;assert output(capsys)['error']['code']=='bounty_retry_changed'
    connect(system[3]);assert invoke('save','--path',path)==1;assert output(capsys)['error']['code']=='bounty_owner_mismatch'
    assert len(posts(state))==before


def test_admission_is_explicit_and_reuses_one_spend_receipt(connected_cli,capsys):
    system,invoke,state,*_=connected_cli;app,poster,*_=system
    path=init_draft(connected_cli,capsys)
    assert invoke('save','--path',path)==0;output(capsys)
    before=len(posts(state))
    assert invoke('request','--path',path,'--confirm-allowance','2')==1;assert output(capsys)['error']['code']=='bounty_cost_confirmation';assert len(posts(state))==before
    state['lose_once']=True
    assert invoke('request','--path',path,'--confirm-allowance','1')==1;output(capsys)
    assert available(poster)==9
    assert invoke('request','--path',path,'--confirm-allowance','1')==0;output(capsys)
    assert posts(state)[-1]==posts(state)[-2];assert available(poster)==9
    assert invoke('request','--path',path,'--confirm-allowance','1')==0;assert output(capsys)['replayed_receipt'] is True
    assert available(poster)==9


def test_boost_retries_across_status_refresh_and_requires_new_intent_for_repeat(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli;app,poster,reviewer,solver,supporter,*_=system
    value=opened(system);connect(supporter);path=root/'existing'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    state['lose_once']=True
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1;output(capsys)
    assert invoke('status','--path',path)==0;output(capsys)
    assert invoke('boost','--path',path,'--confirm-allowance','1')==0;assert output(capsys)['receipt']['reward_points']==200
    assert posts(state)[-1]==posts(state)[-2];assert available(supporter)==9
    assert invoke('boost','--path',path,'--confirm-allowance','1')==0;output(capsys);assert available(supporter)==9
    assert invoke('boost','--path',path,'--confirm-allowance','1','--new-request')==0;assert output(capsys)['receipt']['reward_points']==300;assert available(supporter)==8


def test_contribution_pins_a_published_note_without_approving_it(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli;app,poster,reviewer,solver,*_=system
    value=opened(system);pin=published(solver,reviewer);connect(solver);path=root/'solve'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    cli.save_json(path/'contribution.json',{'contribution_note':pin,'claim':'solution','explanation':'The exact note contains the evidence.'})
    assert invoke('submit','--path',path,'--file',path/'contribution.json')==0;output(capsys)
    assert count(app,BountySubmission)==1
    record=solver.client.get('/api/bounties/'+value['bounty']['id']).json()['bounty']
    assert record['status']=='open' and record['award'] is None
    assert record['submissions'][0]['contribution_note']==pin


def test_workspace_origin_is_checked_before_any_request_and_legacy_tokens_gain_no_scope(connected_cli,capsys):
    system,invoke,state,root,config,_=connected_cli
    path=init_draft(connected_cli,capsys);before=len(state['requests'])
    assert invoke('save','--path',path,'--server','https://different.example')==1;output(capsys);assert len(state['requests'])==before
    document=cli.load_json(config);document['servers']['http://localhost:8000']['scopes']=list(machine_auth.DEFAULT_SCOPES);cli.save_json(config,document)
    assert invoke('list')==1;assert output(capsys)['error']['code']=='bounty_scope_required';assert len(state['requests'])==before


def test_known_receipt_recovers_metadata_after_a_local_write_failure(connected_cli,capsys,monkeypatch):
    system,invoke,state,*_=connected_cli;path=init_draft(connected_cli,capsys)
    original=cli.save_json;fail=[True]
    def save(target,value):
        if target.name==cli.BOUNTY_META and value.get("bounty") and fail[0]:
            fail[0]=False;raise OSError("Synthetic metadata write interruption")
        return original(target,value)
    monkeypatch.setattr(cli,"save_json",save)
    assert invoke("save","--path",path)==1;output(capsys)
    assert invoke("save","--path",path)==0;assert output(capsys)["replayed_receipt"] is True
    assert len(posts(state))==1
    assert cli.load_json(path/cli.BOUNTY_META)["bounty"]["id"]


def test_new_request_cannot_abandon_unknown_spend_and_known_receipt_ids_are_validated(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli;value=opened(system);connect(system[4]);path=root/"boost"
    assert invoke("init","--bounty",value["bounty"]["id"],"--path",path)==0;output(capsys)
    state["lose_once"]=True
    assert invoke("boost","--path",path,"--confirm-allowance","1")==1;output(capsys)
    before=len(posts(state))
    assert invoke("boost","--path",path,"--confirm-allowance","1","--new-request")==1
    assert output(capsys)["error"]["code"]=="bounty_retry_changed";assert len(posts(state))==before
    pending=cli.load_json(path/".bounty-request.json");pending["idempotency_key"]="../../outside"
    cli.save_json(path/".bounty-request.json",pending)
    assert invoke("boost","--path",path,"--confirm-allowance","1")==1;output(capsys)
    assert len(posts(state))==before


def test_definite_rejection_can_be_corrected_but_unknown_outcomes_stay_frozen(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli;value=opened(system);connect(system[3]);path=root/"evidence"
    assert invoke("init","--bounty",value["bounty"]["id"],"--path",path)==0;output(capsys)
    pin=published(system[3],system[2]);wrong={**pin,"content_sha256":"0"*64}
    cli.save_json(path/"contribution.json",{"contribution_note":wrong,"claim":"partial","explanation":"A wrong pinned hash."})
    assert invoke("submit","--path",path,"--file",path/"contribution.json")==1;output(capsys)
    assert "rejection" in cli.load_json(path/".bounty-request.json")
    cli.save_json(path/"contribution.json",{"contribution_note":pin,"claim":"partial","explanation":"The corrected exact evidence."})
    assert invoke("submit","--path",path,"--file",path/"contribution.json")==0;output(capsys)
    assert count(system[0],BountySubmission)==1


@pytest.mark.parametrize('change', ['account', 'scopes', 'token'])
def test_actual_request_reread_is_bound_to_account_and_granted_scopes(connected_cli,capsys,monkeypatch,change):
    system,invoke,state,root,_,connect=connected_cli
    app,poster,reviewer,solver,supporter,*_=system
    value=opened(system);connect(supporter);path=root/'race'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    original=cli.save_json;switched=[False]
    def save(target,value):
        original(target,value)
        if target.name=='.bounty-request.json' and not switched[0]:
            switched[0]=True
            connect(solver if change=='account' else supporter,
                    ['bounties:read','bounties:spend'] if change=='scopes' else machine_auth.BOUNTY_SCOPES)
    monkeypatch.setattr(cli,'save_json',save)
    if change=='token':
        assert invoke('boost','--path',path,'--confirm-allowance','1')==0;output(capsys)
        assert available(supporter)==9;assert available(solver)==10
    else:
        assert invoke('boost','--path',path,'--confirm-allowance','1')==1
        assert output(capsys)['error']['code']=='bounty_'+('account' if change=='account' else 'scopes')+'_changed'
        assert not posts(state);assert available(supporter)==10;assert available(solver)==10


@pytest.mark.parametrize('repair_hash', [False,True])
def test_frozen_body_cannot_replace_unchanged_terms(connected_cli,capsys,monkeypatch,repair_hash):
    system,invoke,state,*_=connected_cli;path=init_draft(connected_cli,capsys)
    original=cli.Client.request;fail=[True]
    def request(self,endpoint,body=None,**kwargs):
        if body is not None and fail[0]:
            fail[0]=False;raise cli.ClientError('Synthetic failure before request','network_error')
        return original(self,endpoint,body,**kwargs)
    monkeypatch.setattr(cli.Client,'request',request)
    assert invoke('save','--path',path)==1;output(capsys)
    pending=cli.load_json(path/'.bounty-request.json');pending['body']['terms']['title']='Unconfirmed changed title'
    if repair_hash:pending['payload_sha256']=cli.bounty_commitment(pending)
    cli.save_json(path/'.bounty-request.json',pending)
    assert invoke('save','--path',path)==1
    assert output(capsys)['error']['code']=='bounty_retry_invalid';assert not posts(state)
    assert count(system[0],BountyTask)==0


@pytest.mark.parametrize('field,value', [('units',2),('confirm_cost_units',2),('unexpected',True)])
def test_boost_body_must_match_single_confirmed_allowance(connected_cli,capsys,field,value):
    system,invoke,state,root,_,connect=connected_cli;task=opened(system);connect(system[4]);path=root/'boost-body'
    assert invoke('init','--bounty',task['bounty']['id'],'--path',path)==0;output(capsys)
    state['lose_once']=True
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1;output(capsys)
    pending=cli.load_json(path/'.bounty-request.json');pending['body'][field]=value
    pending['payload_sha256']=cli.bounty_commitment(pending);cli.save_json(path/'.bounty-request.json',pending)
    before=len(posts(state))
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1
    assert output(capsys)['error']['code']=='bounty_retry_invalid';assert len(posts(state))==before
    assert available(system[4])==9


def test_old_completed_receipt_does_not_rewind_a_newer_status(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli;app,poster,reviewer,solver,supporter,*_=system
    value=opened(system);connect(supporter);path=root/'later-status'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    assert invoke('boost','--path',path,'--confirm-allowance','1')==0;output(capsys)
    task=cli.load_json(path/cli.BOUNTY_META)['bounty'];policy=cli.load_json(path/cli.BOUNTY_META)['policy']
    from uuid import uuid4
    reply=solver.post('/api/bounties/'+task['id']+'/boosts',{
        'expected_version':task['version'],'terms_sha256':task['terms_sha256'],'policy_id':policy['id'],
        'units':1,'confirm_cost_units':1,'idempotency_key':str(uuid4())})
    assert reply.status_code==200
    assert invoke('status','--path',path)==0;latest=output(capsys)['bounty']['version']
    before=len(posts(state))
    assert invoke('boost','--path',path,'--confirm-allowance','1')==0;assert output(capsys)['replayed_receipt']
    assert cli.load_json(path/cli.BOUNTY_META)['bounty']['version']==latest;assert len(posts(state))==before
    assert invoke('boost','--path',path,'--confirm-allowance','1','--new-request')==0;output(capsys)
    assert available(supporter)==8


def test_unknown_committed_boost_followed_by_409_stays_frozen_then_replays(connected_cli,capsys,monkeypatch):
    system,invoke,state,root,_,connect=connected_cli
    value=opened(system);connect(system[4]);path=root/'later-rejection'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    state['lose_once']=True
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1;output(capsys)
    before=cli.load_json(path/'.bounty-request.json')
    original=cli.Client.request
    def rejected(self,endpoint,body=None,**kwargs):
        if body is not None: raise cli.ClientError('Synthetic retry rejection','http_409')
        return original(self,endpoint,body,**kwargs)
    monkeypatch.setattr(cli.Client,'request',rejected)
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1;output(capsys)
    after=cli.load_json(path/'.bounty-request.json')
    assert before==after and 'rejection' not in after
    assert invoke('boost','--path',path,'--confirm-allowance','1','--new-request')==1
    assert output(capsys)['error']['code']=='bounty_retry_changed'
    monkeypatch.setattr(cli.Client,'request',original)
    assert invoke('boost','--path',path,'--confirm-allowance','1')==0;output(capsys)
    assert available(system[4])==9

def test_stale_first_boost_can_refresh_and_deliberately_retry_once(connected_cli,capsys):
    system,invoke,state,root,_,connect=connected_cli
    value=opened(system);connect(system[4]);path=root/'stale-first'
    assert invoke('init','--bounty',value['bounty']['id'],'--path',path)==0;output(capsys)
    boost(system[3],value)
    assert invoke('boost','--path',path,'--confirm-allowance','1')==1
    assert output(capsys)['error']['code']=='http_409'
    rejected=cli.load_json(path/'.bounty-request.json')
    assert rejected['rejection']['code']=='http_409'
    assert invoke('status','--path',path)==0;output(capsys)
    assert invoke('boost','--path',path,'--confirm-allowance','1','--new-request')==0;output(capsys)
    accepted=cli.load_json(path/'.bounty-request.json')
    assert accepted['idempotency_key']!=rejected['idempotency_key']
    assert (path/'receipts'/(rejected['idempotency_key']+'.json')).is_file()
    assert available(system[4])==9
