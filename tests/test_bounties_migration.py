"""Additive 0027→0028 migration preserves existing accounts and research."""
from copy import deepcopy

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, inspect, select, text

from crucible.bounty_models import BountyPolicy
from crucible import bounty_allowance as allowance
from crucible.db import Base, Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot

TABLES = {'bounty_policies','bounty_wallets','bounty_tasks','bounty_terms','bounty_allowance_events',
          'bounty_submissions','bounty_decisions','bounty_awards','bounty_write_receipts'}


def test_populated_upgrade_is_additive_and_seeds_only_the_reviewed_policy(tmp_path,monkeypatch):
    url=f"sqlite:///{tmp_path/'bounty-migration.db'}"
    config=migration_config(url,tmp_path,monkeypatch)
    command.upgrade(config,'0027')
    db=Database(url)
    try:
        metadata=MetaData();metadata.reflect(bind=db.engine)
        metadata.remove(metadata.tables['alembic_version'])
        with db.engine.begin() as connection:seed_original(connection,metadata)
        with db.engine.connect() as connection:before=snapshot(connection,metadata)
        command.upgrade(config,'0028')
        assert set(inspect(db.engine).get_table_names())==set(metadata.tables)|TABLES|{'alembic_version'}
        # Check this migration's exact schema independently of later heads.
        expected=MetaData()
        for table in metadata.sorted_tables:table.to_metadata(expected)
        for name in TABLES:Base.metadata.tables[name].to_metadata(expected)
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection),expected)==[]
        with db.engine.connect() as connection:
            assert snapshot(connection,metadata)==before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            for name in TABLES-{'bounty_policies'}:
                assert connection.scalar(text('SELECT count(*) FROM '+name))==0
        with db.transaction() as session:
            policy=session.get(BountyPolicy,allowance.INITIAL_ID)
            assert policy.configuration==allowance.INITIAL_CONFIGURATION
            assert policy.effective_month=='2026-10' and policy.created_by is None
            assert allowance.digest({'effective_month':policy.effective_month,'configuration':policy.configuration})==policy.id
        command.downgrade(config,'0027')
        with db.engine.connect() as connection:assert snapshot(connection,metadata)==before
    finally:db.engine.dispose()


def test_new_policy_history_cannot_be_dropped_by_downgrade(tmp_path,monkeypatch):
    import pytest
    url=f"sqlite:///{tmp_path/'bounty-preservation.db'}"
    config=migration_config(url,tmp_path,monkeypatch)
    command.upgrade(config,'0028')
    db=Database(url)
    try:
        with db.transaction(write=True) as session:
            session.add(BountyPolicy(id='f'*64,effective_month='2027-01',configuration=deepcopy(allowance.INITIAL_CONFIGURATION),created_at=1))
        with pytest.raises(RuntimeError,match='Preserve bounty'):
            command.downgrade(config,'0027')
        assert TABLES<=set(inspect(db.engine).get_table_names())
    finally:db.engine.dispose()
