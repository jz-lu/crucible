"""Operator policy migration preserves exact publication history and authority."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import event, func, select

from crucible import beta_access, notebook, notebook_moderation
from crucible import publication_screening_adoption as adoption, publication_screening_jobs as jobs
from crucible.publication_screening_models import ScreeningAttempt, ScreeningBudget, ScreeningControl, ScreeningJob
from crucible.publication_screening_worker import policy_for
from test_notebook import create, revise, publication_body
from test_notebook_publication_cancellation import cancel
from test_notebook_trash import delete
from test_publication_screening_integration import screened, configure, run, SyntheticProvider


@pytest.fixture(autouse=True)
def enabled_control(screened):
    configure(screened)


def settings(system):
    return replace(system.app.state.settings,access_admin_ids=(system.reviewer.identifier,system.reviewer2.identifier))


def manual_request(system, actor=None, note=None, **changes):
    """Synthetic old DB state: original human-only request, no screening job."""
    actor = actor or system.owner
    note = note or create(actor)
    with system.app.state.db.transaction(write=True) as session:
        revision = session.scalar(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id==note['id'],notebook.NotebookRevision.revision==note['revision']))
        values = dict(id=str(uuid4()),revision_id=revision.id,author_id=actor.identifier,
            requested_at=system.now[0]-3600,idempotency_key=str(uuid4()),
            request_sha256=notebook._hash({'note_id':note['id'],'revision':note['revision'],
                'content_sha256':note['content_sha256']}),test_only_self_review=False)
        row = notebook.NotebookPublication(**{**values,**changes})
        session.add(row); session.flush()
        original = {column.name:getattr(row,column.name) for column in row.__table__.columns}
    return note,original


def preview(system, **changes):
    return adoption.preview(system.app.state.db,settings(system),actor_id=system.reviewer.identifier,
        clock=lambda:system.now[0],**changes)


def apply(system, plan, **changes):
    args = dict(actor_id=system.reviewer.identifier,authorization_reference='authorized-platform-change-test',
        reason='Move pending publication requests to the current automatic publication process.',clock=lambda:system.now[0])
    return adoption.apply_plan(system.app.state.db,settings(system),plan,**{**args,**changes})


def rows(system, model):
    with system.app.state.db.transaction() as session:
        return [{column.name:getattr(row,column.name) for column in row.__table__.columns}
            for row in session.scalars(select(model))]


def audit_rows(system):
    return [row for row in rows(system,beta_access.AccessEvent) if row['action']==adoption.ACTION]


def rehash(plan):
    plan['plan_sha256'] = jobs.digest({key:value for key,value in plan.items() if key!='plan_sha256'})
    return plan


def test_all_authors_exact_requests_receive_honest_operator_audit_once(screened):
    first,original = manual_request(screened)
    other,_ = manual_request(screened,screened.reader)
    before = rows(screened,notebook.NotebookPublication)
    plan = preview(screened)
    assert len(plan['requests'])==2
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]
    original_content = first['revisions'][0]['content']
    assert original_content['body'] not in str(plan) and original_content['title'] not in str(plan)
    result = apply(screened,plan)
    assert result['queued']==2
    assert rows(screened,notebook.NotebookPublication)==before
    assert rows(screened,ScreeningAttempt)==[] and rows(screened,ScreeningBudget)==[]
    audits = audit_rows(screened)
    assert len(audits)==2
    assert {row['user_id'] for row in audits}=={screened.owner.identifier,screened.reader.identifier}
    for row in audits:
        assert row['actor_id']==screened.reviewer.identifier
        assert row['details']['authority_basis']=='platform_policy_migration'
        assert row['details']['publication_basis']=='original_publication_request'
        assert row['details']['fresh_author_consent_recorded'] is False
        assert row['details']['plan_sha256']==plan['plan_sha256']
        assert row['details']['authorization_reference']=='authorized-platform-change-test'
        assert row['details']['policy_id']==plan['policy_id']
        assert row['details']['job_id'] in {job['id'] for job in rows(screened,ScreeningJob)}
        assert not {'body','title','email','consented_at'} & row['details'].keys()
    previous_jobs = rows(screened,ScreeningJob)
    screened.now[0]+=20
    replay = apply(screened,plan)
    assert replay['queued']==0 and {item['status'] for item in replay['outcomes']}=={'already_screened'}
    assert rows(screened,ScreeningJob)==previous_jobs and audit_rows(screened)==audits
    assert preview(screened)['requests']==[]


def test_adoption_and_worker_publish_only_original_submitted_revision(screened):
    first,original = manual_request(screened)
    second = revise(screened.owner,first)
    plan = preview(screened)
    assert len(plan['requests'])==1 and plan['requests'][0]['revision']==1
    assert apply(screened,plan)['queued']==1
    with screened.app.state.db.transaction() as session:
        row = session.get(notebook.NotebookPublication,original['id'])
        assert {column.name:getattr(row,column.name) for column in row.__table__.columns}==original
    configure(screened)
    provider = SyntheticProvider()
    assert run(screened,provider)['status']=='published'
    assert len(provider.inputs)==1 and provider.inputs[0].content_sha256==first['content_sha256']
    assert screened.reader.client.get(f'/api/notebook/notes/{first["id"]}?revision=1').status_code==200
    assert screened.reader.client.get(f'/api/notebook/notes/{first["id"]}?revision=2').status_code==404
    assert rows(screened,ScreeningJob)[0]['revision_id']==original['revision_id']
    assert len(rows(screened,ScreeningJob))==1
    assert apply(screened,plan)['queued']==0


@pytest.mark.parametrize('state', ['approved','changes_requested','cancelled','withdrawn','deleted','revoked','suspended'])
def test_plan_does_not_override_later_terminal_or_access_changes(screened,state):
    note,original = manual_request(screened)
    plan = preview(screened)
    if state in {'approved','changes_requested'}:
        response = screened.reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
            publication_body(note,decision=state,reason='Independent synthetic review.'))
        assert response.status_code==200,response.text
    elif state=='cancelled':
        assert cancel(screened.owner,note).status_code==200
    elif state=='deleted':
        assert delete(screened.owner,note).status_code==200
    else:
        with screened.app.state.db.transaction(write=True) as session:
            if state=='withdrawn':
                session.add(notebook_moderation.PublicationWithdrawal(revision_id=original['revision_id'],
                    moderator_id=screened.reviewer.identifier,created_at=screened.now[0],
                    reason='Synthetic withdrawal',idempotency_key=str(uuid4()),request_sha256='f'*64))
            elif state=='revoked':
                beta_access.set_membership(session,screened.owner.identifier,'revoked',screened.now[0])
            else:
                beta_access.set_suspension(session,screened.owner.identifier,True,screened.now[0],reason='Synthetic')
    assert preview(screened)['requests']==[]
    result = apply(screened,plan)
    assert result['queued']==0
    assert result['outcomes'][0]['status'] in {'request_ineligible','author_ineligible'}
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]


@pytest.mark.parametrize('mode,status', [('automatic','queued'),('automatic','running'),
    ('automatic','human_review'),('automatic','cancelled'),('shadow','completed'),('shadow','queued')])
def test_any_existing_job_is_excluded_and_never_reset(screened,mode,status):
    note,original = manual_request(screened)
    plan = preview(screened)
    policy = policy_for(replace(screened.app.state.settings,publication_screening_mode=mode))
    with screened.app.state.db.transaction(write=True) as session:
        job = jobs.enqueue(session,session.get(notebook.NotebookPublication,original['id']),
            policy=policy,now=screened.now[0]-100)
        job.status,job.attempt_count,job.error_code = status,2,'provider_timeout'
    before = rows(screened,ScreeningJob)
    assert preview(screened)['requests']==[]
    assert apply(screened,plan)['outcomes'][0]['status']=='already_screened'
    assert rows(screened,ScreeningJob)==before and audit_rows(screened)==[]


def test_pagination_uses_stable_cutoff_and_does_not_starve_after_skipped_rows(screened):
    originals = []
    for index in range(5):
        _,original = manual_request(screened,id=f'00000000-0000-0000-0000-{index+1:012d}')
        originals.append(original)
    first = preview(screened,limit=2)
    assert [item['publication_id'] for item in first['requests']]==[row['id'] for row in originals[:2]]
    screened.now[0]+=1
    manual_request(screened,id='00000000-0000-0000-0000-000000000006',requested_at=screened.now[0])
    # The first planned page becomes ineligible; its cursor still advances.
    with screened.app.state.db.transaction(write=True) as session:
        for row in originals[:2]:
            session.get(notebook.NotebookPublication,row['id']).decision='cancelled'
    assert apply(screened,first)['queued']==0
    second = preview(screened,cutoff=first['cutoff'],after=first['next_after'],limit=2)
    third = preview(screened,cutoff=first['cutoff'],after=second['next_after'],limit=2)
    found = [item['publication_id'] for item in second['requests']+third['requests']]
    assert found==[row['id'] for row in originals[2:]]
    assert third['next_after'] is None


def test_only_active_configured_operator_may_plan_or_apply(screened):
    manual_request(screened)
    plan = preview(screened)
    for actor_id in (screened.owner.identifier,screened.reader.identifier):
        with pytest.raises(HTTPException) as error:
            adoption.preview(screened.app.state.db,settings(screened),actor_id=actor_id,clock=lambda:screened.now[0])
        assert error.value.status_code==403
    with screened.app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session,screened.reviewer.identifier,'revoked',screened.now[0])
    with pytest.raises(HTTPException) as error:
        apply(screened,plan)
    assert error.value.status_code==403
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]


@pytest.mark.parametrize('change', ['digest','policy','operator','future-cutoff','duplicate','identity'])
def test_changed_plan_or_identity_never_creates_unreviewed_work(screened,change):
    manual_request(screened)
    plan = preview(screened)
    if change=='digest': plan['plan_sha256']='a'*64
    if change=='policy': plan['policy_id']='a'*64; rehash(plan)
    if change=='operator': plan['actor_id']=screened.reviewer2.identifier; rehash(plan)
    if change=='future-cutoff': plan['cutoff']+=10; rehash(plan)
    if change=='duplicate': plan['requests']*=2; rehash(plan)
    if change=='identity':
        plan['requests'][0]['content_sha256']='f'*64; rehash(plan)
        assert apply(screened,plan)['outcomes'][0]['status']=='identity_changed'
    else:
        with pytest.raises((ValueError,HTTPException)):
            apply(screened,plan)
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]


def test_failed_audit_rolls_back_the_whole_batch_including_jobs(screened):
    manual_request(screened)
    manual_request(screened,screened.reader)
    plan = preview(screened)
    def fail_audit(mapper,connection,target):
        if target.action==adoption.ACTION:
            raise RuntimeError('Synthetic audit failure')
    event.listen(beta_access.AccessEvent,'before_insert',fail_audit)
    try:
        with pytest.raises(RuntimeError,match='Synthetic audit failure'):
            apply(screened,plan)
    finally:
        event.remove(beta_access.AccessEvent,'before_insert',fail_audit)
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]


def test_disabled_or_shadow_settings_do_not_adopt_and_batch_is_bounded(screened):
    manual_request(screened)
    for mode in ('disabled','shadow'):
        with pytest.raises(HTTPException) as error:
            adoption.preview(screened.app.state.db,replace(settings(screened),publication_screening_mode=mode),
                actor_id=screened.reviewer.identifier,clock=lambda:screened.now[0])
        assert error.value.status_code==409
    for limit in (0,26,True):
        with pytest.raises(ValueError): preview(screened,limit=limit)
    assert rows(screened,ScreeningJob)==[]


@pytest.mark.parametrize('changed', ['disabled','policy','missing'])
def test_apply_rechecks_locked_live_control_after_preview(screened,changed):
    manual_request(screened)
    plan = preview(screened)
    with screened.app.state.db.transaction(write=True) as session:
        control = session.get(ScreeningControl,1)
        if changed=='disabled': control.mode='disabled'
        elif changed=='policy': control.policy_id='f'*64
        else: session.delete(control)
    with pytest.raises(HTTPException) as error:
        apply(screened,plan)
    assert error.value.status_code==409
    assert rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]


def test_cli_defaults_to_read_only_preview_and_apply_requires_explicit_flag(screened,monkeypatch,capsys,tmp_path):
    from crucible import config, publication_screening_worker as worker
    manual_request(screened)
    monkeypatch.setattr(config,'load_settings',lambda:settings(screened))
    monkeypatch.setattr(worker,'worker_database',lambda url:screened.app.state.db)
    assert adoption.main(['--actor-id',screened.reviewer.identifier,'--cutoff',str(screened.now[0])])==0
    plan = __import__('json').loads(capsys.readouterr().out)
    assert len(plan['requests'])==1 and rows(screened,ScreeningJob)==[] and audit_rows(screened)==[]
    path = tmp_path/'plan.json'; path.write_text(__import__('json').dumps(plan))
    with pytest.raises(SystemExit) as error:
        adoption.main(['--actor-id',screened.reviewer.identifier,'--apply-plan',str(path)])
    assert error.value.code==2
    capsys.readouterr()
    assert rows(screened,ScreeningJob)==[]
    assert adoption.main(['--actor-id',screened.reviewer.identifier,'--apply-plan',str(path),
        '--execute-reviewed','--authorization-reference','synthetic-cli-review',
        '--reason','Authorized synthetic policy migration'])==0
    assert __import__('json').loads(capsys.readouterr().out)['queued']==1
