"""Real migration retains old award evidence and permits reviewer-only awards."""
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import MetaData, inspect, text

from crucible.bounty_allowance import INITIAL_ID
from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def seed_historical_award(connection, metadata, original):
    ids = {name: str(uuid4()) for name in ('task', 'terms', 'claim', 'poster', 'reviewer')}
    def insert(name, **values):
        connection.execute(metadata.tables[name].insert().values(**values))
    insert('bounty_tasks', id=ids['task'], owner_id=original['user'], status='completed',
           version=5, revision=1, terms_sha256='a'*64, title='Historical task', summary='Preserve exactly',
           background_tags=['fixture'], scope=3, reward_points=100, base_reward_points=100,
           sequence=4, policy_id=INITIAL_ID, created_at=1, updated_at=5)
    insert('bounty_terms', id=ids['terms'], task_id=ids['task'], revision=1,
           terms={'title': 'Historical task', 'source_notes': []}, content_sha256='a'*64,
           created_by=original['user'], created_at=1)
    insert('bounty_submissions', id=ids['claim'], task_id=ids['task'], terms_id=ids['terms'],
           author_id=original['user'], note_id=original['note'], note_revision=1,
           note_sha256='f'*64, claim='solution', explanation='Historical evidence', status='awarded',
           receipt_order=2, reward_boundary=1, offered_reward_points=100, created_at=2)
    # A schema-preservation fixture, not a newly authorized award. The API's
    # distinct-participant checks are covered by the HTTP workflow tests.
    for version, kind in enumerate(('poster', 'reviewer'), 3):
        insert('bounty_decisions', id=ids[kind], task_id=ids['task'], submission_id=ids['claim'],
               terms_id=ids['terms'], evidence_sha256='f'*64, actor_id=original['user'],
               kind='pipeline' if kind == 'reviewer' else kind, decision='approved',
               reason='Historical signoff', checks={}, task_version=version, created_at=version)
    insert('bounty_awards', task_id=ids['task'], submission_id=ids['claim'], user_id=original['user'],
           policy_id=INITIAL_ID, terms_id=ids['terms'], poster_decision_id=ids['poster'],
           pipeline_decision_id=ids['reviewer'], reward_boundary=1, points=100, created_at=5)
    return ids


@pytest.mark.parametrize('new_independent_award', [False, True])
def test_upgrade_preserves_history_and_downgrade_never_fabricates_signoff(tmp_path, monkeypatch, new_independent_award):
    url = f"sqlite:///{tmp_path / 'independent-awards.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0029')
    db = Database(url)
    try:
        metadata = MetaData()
        metadata.reflect(bind=db.engine)
        metadata.remove(metadata.tables['alembic_version'])
        with db.engine.begin() as connection:
            original, _ = seed_original(connection, metadata)
            ids = seed_historical_award(connection, metadata, original)
        with db.engine.connect() as connection:
            before = snapshot(connection, metadata)
        command.upgrade(config, '0030')
        assert next(c for c in inspect(db.engine).get_columns('bounty_awards')
                    if c['name'] == 'poster_decision_id')['nullable'] is True
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0030'
        if new_independent_award:
            # Verify actual nullable storage and that rollback refuses to invent
            # a historical poster signoff merely to satisfy the old constraint.
            with db.engine.begin() as connection:
                connection.execute(text('UPDATE bounty_awards SET poster_decision_id=NULL WHERE task_id=:id'),
                                   {'id': ids['task']})
            with pytest.raises(RuntimeError, match='Preserve independent-reviewer bounty awards'):
                command.downgrade(config, '0029')
            with db.engine.connect() as connection:
                assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0030'
                assert connection.scalar(text('SELECT count(*) FROM bounty_awards WHERE poster_decision_id IS NULL')) == 1
                assert connection.scalar(text('SELECT count(*) FROM bounty_decisions')) == 2
        else:
            command.downgrade(config, '0029')
            with db.engine.connect() as connection:
                assert snapshot(connection, metadata) == before
                assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            assert next(c for c in inspect(db.engine).get_columns('bounty_awards')
                        if c['name'] == 'poster_decision_id')['nullable'] is False
    finally:
        db.engine.dispose()
