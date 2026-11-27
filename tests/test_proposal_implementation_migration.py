"""0034 only adds progress history and never rewrites existing evidence."""
from uuid import uuid4

from alembic import command
from sqlalchemy import MetaData, inspect, text
import pytest

from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def test_implementation_upgrade_preserves_all_existing_records(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'implementation.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0033')
    db = Database(url)
    try:
        original = MetaData(); original.reflect(bind=db.engine)
        original.remove(original.tables['alembic_version'])
        with db.engine.begin() as connection:
            identities, _ = seed_original(connection, original)
            before = snapshot(connection, original)
        command.upgrade(config, '0034')
        assert set(inspect(db.engine).get_table_names()) == set(original.tables) | {'proposal_implementation_events','alembic_version'}
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
        command.upgrade(config, 'head'); command.check(config); command.downgrade(config, '0034')
        command.downgrade(config, '0033'); command.upgrade(config, '0034')
        with db.engine.begin() as connection:
            request_id = str(uuid4())
            connection.execute(text('INSERT INTO research_review_requests '
                '(id,author_id,created_at,purpose,title,question,sources,idempotency_key,request_sha256) '
                'VALUES (:id,:author,1,\'procedure\',\'Fixture\',\'Fixture\',\'[]\',:key,:digest)'),
                {'id':request_id,'author':identities['user'],'key':str(uuid4()),'digest':'a'*64})
            connection.execute(text('INSERT INTO research_assessments '
                '(request_id,reviewer_id,created_at,outcome,checks,findings,limitations,test_only_self_review,idempotency_key,request_sha256) '
                'VALUES (:id,:author,1,\'recommend\',\'{}\',\'Fixture\',\'Fixture\',0,:key,:digest)'),
                {'id':request_id,'author':identities['user'],'key':str(uuid4()),'digest':'a'*64})
            connection.execute(text('INSERT INTO proposal_implementation_events '
                '(id,research_request_id,sequence,decision_sha256,actor_id,created_at,status,summary,evidence,idempotency_key,request_sha256) '
                'VALUES (:id,:target,1,:digest,:author,1,\'in_progress\',\'Fixture\',\'[]\',:key,:digest)'),
                {'id':str(uuid4()),'target':request_id,'author':identities['user'],'key':str(uuid4()),'digest':'a'*64})
        with pytest.raises(RuntimeError, match='history is retained'):
            command.downgrade(config, '0033')
        with db.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM proposal_implementation_events')) == 1
    finally:
        db.engine.dispose()
