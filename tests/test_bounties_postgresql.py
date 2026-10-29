"""Real locks, runtime grants and backup restoration on the fixed CI PG17 service.

No ambient DSN or live account is accepted. Fixture roles/schema are synthetic
and the shared fixture removes only its own objects. Local absence is explicit.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event as ThreadEvent
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import MetaData, event, func, select, text

from crucible import beta_access, bounty_allowance as allowance
from crucible.bounty_models import BountyAllowanceEvent, BountyAward, BountyWallet
from test_bounties import make_system, opened, boost, submit, published, assess, body
from test_bounties_migration import TABLES
from test_notebook_numbers_postgresql import pg_tool, preserve_service_identifier
from test_researcher_profiles_migration import snapshot
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged


@pytest.fixture
def pg_system(preserve_service_identifier, postgres, monkeypatch):
    postgres.service_container=preserve_service_identifier
    command.upgrade(postgres.config,'head')
    command.check(postgres.config)
    assert_original_unchanged(postgres)
    value=make_system(postgres.runtime_url,monkeypatch,initialize=False,seed_policy=False)
    try:yield postgres,value
    finally:value[0].state.db.engine.dispose()


def test_different_tasks_cannot_double_spend_one_account(pg_system):
    fixture,system=pg_system
    app,owner,reviewer,solver,supporter,*_=system
    tasks=[opened(system),opened(system)]
    barrier=Barrier(2,timeout=5)
    def spend(value):
        payload=body(value,terms_sha256=value['bounty']['terms_sha256'],policy_id=value['policy']['id'],units=6,confirm_cost_units=6)
        barrier.wait()
        return supporter.post('/api/bounties/'+value['bounty']['id']+'/boosts',payload)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(spend,tasks))
    assert sorted(r.status_code for r in results)==[200,409]
    with app.state.db.transaction() as session:
        assert session.get(BountyWallet,supporter.identifier).available_units==4
        assert session.scalar(select(func.count()).select_from(BountyAllowanceEvent).where(
            BountyAllowanceEvent.user_id==supporter.identifier,BountyAllowanceEvent.kind=='grant'))==1
        assert session.scalar(select(func.sum(BountyAllowanceEvent.available_delta)).where(
            BountyAllowanceEvent.user_id==supporter.identifier))==4


def test_revocation_serializes_before_award_when_account_lock_is_held(pg_system):
    _,system=pg_system
    app,owner,reviewer,solver,*_=system
    value=submit(solver,opened(system),published(solver,reviewer));sid=value['receipt']['submission_id']
    value=assess(owner,value,'poster',submission_id=sid)
    attempting=ThreadEvent()
    def before(connection,cursor,statement,parameters,context,many):
        if 'FROM users' in statement and 'FOR UPDATE' in statement and solver.identifier in repr(parameters):
            attempting.set()
    event.listen(app.state.db.engine,'before_cursor_execute',before)
    path='/api/bounties/'+value['bounty']['id']+'/submissions/'+sid+'/decisions'
    payload=body(value,kind='pipeline',decision='approved',reason='Exact independent review.')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with app.state.db.transaction(write=True) as session:
                beta_access.lock_user(session,solver.identifier)
                attempting.clear()
                future=pool.submit(reviewer.post,path,payload)
                assert attempting.wait(5),'The settlement must attempt the participant lock.'
                assert not future.done()
                beta_access.set_membership(session,solver.identifier,'revoked',app.state.clock(),reason='Synthetic concurrency check.')
            result=future.result(timeout=15)
        assert result.status_code==409,result.text
        with app.state.db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(BountyAward))==0
    finally:event.remove(app.state.db.engine,'before_cursor_execute',before)


def test_exact_award_and_allowance_survive_readonly_backup_restore(pg_system):
    fixture,system=pg_system
    app,owner,reviewer,solver,supporter,*_=system
    value=boost(supporter,opened(system))
    value=submit(solver,value,published(solver,reviewer));sid=value['receipt']['submission_id']
    value=assess(owner,value,'poster',submission_id=sid)
    value=assess(reviewer,value,'pipeline',submission_id=sid)
    assert value['bounty']['award']['points']==200
    metadata=MetaData();metadata.reflect(bind=fixture.owner_engine,only=sorted(TABLES))
    with fixture.backup_engine.connect() as connection:
        before=snapshot(connection,metadata)
        for name in TABLES:
            assert connection.scalar(text('SELECT has_table_privilege(current_user,:t,:p)'),{'t':name,'p':'SELECT'})
            for privilege in ('INSERT','UPDATE','DELETE'):
                assert not connection.scalar(text('SELECT has_table_privilege(current_user,:t,:p)'),{'t':name,'p':privilege})
    with pytest.raises(RuntimeError,match='Preserve bounty'):
        command.downgrade(fixture.config,'0027')
    dumped=pg_tool(fixture.service_container,'pg_dump',fixture.backup,'--format=custom','--no-owner','--no-privileges',
                   *[f'--table={fixture.schema}.{table}' for table in sorted(TABLES)],'crucible_metadata_test')
    assert dumped.returncode==0,dumped.stderr.decode()
    assert dumped.stdout.startswith(b'PGDMP') and len(dumped.stdout)<4*1024*1024
    with fixture.owner_engine.begin() as connection:connection.exec_driver_sql('DROP TABLE '+','.join(sorted(TABLES)))
    restored=pg_tool(fixture.service_container,'pg_restore',fixture.owner,'--no-owner','--no-privileges',
                    '--single-transaction','--exit-on-error','--dbname=crucible_metadata_test',payload=dumped.stdout)
    assert restored.returncode==0,restored.stderr.decode()
    with fixture.runtime_engine.connect() as connection:assert snapshot(connection,metadata)==before
    command.check(fixture.config)
    assert solver.client.get('/api/bounties/'+value['bounty']['id']).json()['bounty']['award']==value['bounty']['award']


def test_competing_qualifications_preserve_later_offer_until_final_winner(pg_system):
    from test_bounties import test_rejected_earlier_claim_cannot_destroy_later_qualified_offer
    _,system=pg_system
    test_rejected_earlier_claim_cannot_destroy_later_qualified_offer(system)
