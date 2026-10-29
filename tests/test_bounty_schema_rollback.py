"""Old application at exact schema0029 retains all newer evidence without routes."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, inspect, text

import crucible
from crucible import dh_v4, registry, scoring
from crucible.app import create_app
from crucible.config import load_settings
from crucible.db import Database
from scripts.beta_preflight import inspect_database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot

BOUNTY_TABLES = {'bounty_policies', 'bounty_wallets', 'bounty_tasks', 'bounty_terms',
                'bounty_allowance_events', 'bounty_submissions', 'bounty_decisions',
                'bounty_awards', 'bounty_write_receipts'}
NEW_TABLES = BOUNTY_TABLES | {'scheme_admission_retirements'}
POLICY = 'c392e762f76b1dd9e1db2e3db06e33ef238e0eda4977618ae53592ef6753a0b7'


def insert(metadata, connection, table, **values):
    connection.execute(metadata.tables[table].insert().values(**values))


def seed_admission(metadata, connection, original):
    """Synthetic storage evidence; no live approval or award is created."""
    proposal, review = str(uuid4()), str(uuid4())
    reviewer, solver = str(uuid4()), str(uuid4())
    for identifier, handle in ((reviewer, 'reviewer'), (solver, 'solver')):
        insert(metadata, connection, 'users', id=identifier, handle=handle, created_at=1)
    insert(metadata, connection, 'protocol_proposals', id=proposal, author_id=original['user'],
           idempotency_key=str(uuid4()), created_at=2, status='approved', revision=1,
           content_sha256='a'*64, specification={'synthetic': 'retained exact specification'})
    insert(metadata, connection, 'protocol_proposal_reviews', id=review, proposal_id=proposal,
           revision=1, reviewer_id=reviewer, created_at=3, decision='approved', content_sha256='a'*64,
           implementation_sha256='b'*64, checks={'synthetic': True}, notes='Retained review α',
           evidence='Synthetic preservation fixture', idempotency_key=str(uuid4()), request_sha256='c'*64)
    insert(metadata, connection, 'scheme_admission_credits', family_id='synthetic-family',
           user_id=original['user'], scheme_id='synthetic-frozen', scheme_version='1',
           proposal_id=proposal, review_id=review, revision=1, specification_sha256='a'*64,
           source_sha256='b'*64, reviewer_id=reviewer, created_at=4, evidence='Immutable fixture evidence')
    return reviewer, solver


def seed_new_evidence(metadata, connection, original, reviewer, solver):
    task, terms, submission, poster_decision, pipeline_decision = [str(uuid4()) for _ in range(5)]
    insert(metadata, connection, 'bounty_wallets', user_id=original['user'], available_units=9, version=1, last_month='2026-10')
    insert(metadata, connection, 'bounty_tasks', id=task, owner_id=original['user'], status='completed',
           version=4, revision=1, terms_sha256='d'*64, title='Synthetic preserved bounty', summary='Storage fixture only',
           background_tags=['fixture'], scope=3, reward_points=100, base_reward_points=100, sequence=4,
           policy_id=POLICY, created_at=5, updated_at=9)
    insert(metadata, connection, 'bounty_terms', id=terms, task_id=task, revision=1,
           terms={'synthetic': ['exact', None, 'α  ']}, content_sha256='d'*64, created_by=original['user'], created_at=5)
    insert(metadata, connection, 'bounty_allowance_events', id=str(uuid4()), user_id=original['user'],
           policy_id=POLICY, month='2026-10', kind='reservation', event_key='fixture-reservation',
           wallet_version=1, available_delta=-1, units=1, reward_points=100, task_id=task,
           task_sequence=1, terms_sha256='d'*64, created_at=6)
    insert(metadata, connection, 'bounty_submissions', id=submission, task_id=task, terms_id=terms,
           author_id=solver, note_id=original['note'], note_revision=1, note_sha256='f'*64,
           claim='solution', explanation='Synthetic storage fixture, not scientific evidence', status='awarded',
           receipt_order=2, reward_boundary=1, offered_reward_points=100, created_at=7)
    for identifier, actor, kind in ((poster_decision, original['user'], 'poster'), (pipeline_decision, reviewer, 'pipeline')):
        insert(metadata, connection, 'bounty_decisions', id=identifier, task_id=task, submission_id=submission,
               terms_id=terms, evidence_sha256='f'*64, actor_id=actor, kind=kind, decision='approved',
               reason='Synthetic exact storage record', checks={}, task_version=4, created_at=8)
    insert(metadata, connection, 'bounty_awards', task_id=task, submission_id=submission, user_id=solver,
           policy_id=POLICY, terms_id=terms, poster_decision_id=poster_decision, pipeline_decision_id=pipeline_decision,
           reward_boundary=1, points=100, created_at=9)
    insert(metadata, connection, 'bounty_write_receipts', user_id=original['user'], idempotency_key=str(uuid4()),
           request_sha256='e'*64, task_id=task, effect={'synthetic': [1, 3, None]}, created_at=6)
    insert(metadata, connection, 'scheme_admission_retirements', family_id='synthetic-family', reviewer_id=reviewer,
           created_at=10, reason='Retained terminal cutoff; old scoring ignores this without deleting it')


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    url='sqlite:///'+str(tmp_path/'fallback.db')
    config=migration_config(url,tmp_path,monkeypatch)
    command.upgrade(config,'0027')
    assert ScriptDirectory.from_config(config).get_heads()==['0029']
    db=Database(url)
    original=MetaData()
    original.reflect(bind=db.engine,only=sorted(set(inspect(db.engine).get_table_names())-{'alembic_version'}))
    with db.engine.begin() as connection:
        ids,_=seed_original(connection,original)
        reviewer,solver=seed_admission(original,connection,ids)
    with db.engine.connect() as connection:
        before=snapshot(connection,original)
        assert inspect_database(connection,load_settings(),['0029'],None)[0]['status']=='fail'
    command.upgrade(config,'0029')
    command.check(config)
    all_tables=MetaData();all_tables.reflect(bind=db.engine)
    with db.engine.connect() as connection:
        assert snapshot(connection,original)==before
        assert set(inspect(db.engine).get_table_names())==set(original.tables)|NEW_TABLES|{'alembic_version'}
        for name in NEW_TABLES-{'bounty_policies'}:
            assert connection.scalar(text('SELECT count(*) FROM '+name))==0
        policy=connection.execute(all_tables.tables['bounty_policies'].select()).mappings().one()
        encoded=json.dumps({'effective_month':policy['effective_month'],'configuration':policy['configuration']},sort_keys=True,separators=(',',':')).encode()
        assert policy['id']==POLICY==hashlib.sha256(encoded).hexdigest()
        assert policy['created_by'] is None
    with db.engine.begin() as connection:
        seed_new_evidence(all_tables,connection,ids,reviewer,solver)
    all_tables.remove(all_tables.tables['alembic_version'])
    with db.engine.connect() as connection:
        retained=snapshot(connection,all_tables)
        assert not connection.execute(text('PRAGMA foreign_key_check')).all()
    monkeypatch.setattr(registry,'ADAPTERS',{(dh_v4.SCHEME_ID,dh_v4.SCHEME_VERSION):dh_v4})
    monkeypatch.setattr(registry,'CURRENT',{dh_v4.SCHEME_ID:dh_v4.SCHEME_VERSION})
    yield config,db,all_tables,retained,original,before
    db.engine.dispose()


def test_populated_upgrade_and_old_application_preserve_all_evidence(migrated):
    config,db,metadata,retained,original,before=migrated
    settings=load_settings()
    with db.engine.connect() as connection:
        assert inspect_database(connection,settings,['0029'],None)[0]['status']=='pass'
        assert inspect_database(connection,settings,['0027'],None)[0]['status']=='fail'
    app=create_app(settings)
    try:
        assert scoring.POLICY_ID=='crucible-points-v10'
        assert not any('/bount' in getattr(route,'path','') for route in app.routes)
        with TestClient(app,base_url=settings.origins[0]) as client:
            assert client.get('/health').status_code==200
            for path in ('/api/bounties','/api/bounties/review-queue','/api/me/bounty-allowance'):
                assert client.get(path).status_code in (403,404)
            assert client.post('/api/bounties',json={'synthetic':True}).status_code in (403,404)
        with db.engine.connect() as connection:
            assert snapshot(connection,metadata)==retained
            assert snapshot(connection,original)==before
            assert connection.scalar(text('SELECT version_num FROM alembic_version'))=='0029'
        command.check(config)
        root=Path(crucible.__file__).resolve().parents[1]
        assert not (root/'web/notebook/bounties.js').exists()
        assert 'bounties.js' not in (root/'web/index.html').read_text()
    finally:app.state.db.engine.dispose()


def test_populated_retirement_refuses_downgrade_without_changing_rows(migrated):
    config,db,metadata,retained,*_=migrated
    with pytest.raises(RuntimeError,match='Preserve admission retirement evidence'):
        command.downgrade(config,'0028')
    with db.engine.connect() as connection:
        assert snapshot(connection,metadata)==retained
        assert connection.scalar(text('SELECT version_num FROM alembic_version'))=='0029'


def test_populated_bounty_journal_refuses_0028_downgrade(tmp_path,monkeypatch):
    url='sqlite:///'+str(tmp_path/'bounty-only.db');config=migration_config(url,tmp_path,monkeypatch)
    command.upgrade(config,'0028');db=Database(url)
    try:
        metadata=MetaData();metadata.reflect(bind=db.engine)
        with db.engine.begin() as connection:
            user=str(uuid4())
            insert(metadata,connection,'users',id=user,handle='allowance-fixture',created_at=1)
            insert(metadata,connection,'bounty_wallets',user_id=user,available_units=9,version=1,last_month='2026-10')
        with db.engine.connect() as connection:before=snapshot(connection,metadata)
        with pytest.raises(RuntimeError,match='Preserve bounty research and ledger evidence'):
            command.downgrade(config,'0027')
        with db.engine.connect() as connection:assert snapshot(connection,metadata)==before
    finally:db.engine.dispose()


def test_bridge_only_models_are_migration_only_and_source_overlays_replace_them():
    root=Path(crucible.__file__).resolve().parents[1]
    imports=[path.relative_to(root).as_posix() for parent in ('crucible','scripts','migrations')
             for path in (root/parent).rglob('*.py')
             if path.name!='admission_lifecycle_schema.py' and 'import' in path.read_text()
             and 'admission_lifecycle_schema' in path.read_text()]
    assert imports==['migrations/env.py']
    source=(root/'deploy/release.py').read_text()
    assert 'rm -rf /opt/crucible/crucible' in source
    assert 'COPY --chown=0:0 source/ /opt/crucible/' in source
