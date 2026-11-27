"""Visibility expansion preserves accounts, content and past publication decisions."""
from uuid import uuid4

from alembic import command
from sqlalchemy import MetaData, inspect, text
import pytest

from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def test_visibility_upgrade_preserves_records_and_protects_history(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'visibility.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0034')
    db = Database(url)
    try:
        original = MetaData(); original.reflect(bind=db.engine)
        original.remove(original.tables['alembic_version'])
        with db.engine.begin() as connection:
            ids, _ = seed_original(connection, original)
            before = snapshot(connection, original)
        command.upgrade(config, '0035')
        assert set(inspect(db.engine).get_table_names()) == set(original.tables) | {
            'notebook_revision_visibility', 'notebook_visibility_changes', 'alembic_version'}
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert connection.scalar(text('SELECT count(*) FROM notebook_revision_visibility')) == 0
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
        command.upgrade(config, 'head'); command.check(config); command.downgrade(config, '0035')
        command.downgrade(config, '0034'); command.upgrade(config, '0035')
        with db.engine.begin() as connection:
            event_id = str(uuid4())
            connection.execute(text('INSERT INTO notebook_visibility_changes '
                '(id,note_id,author_id,created_at,private,revision_ids,previous_publications,idempotency_key,request_sha256) '
                'VALUES (:id,:note,:author,1,1,\'[]\',\'[]\',:key,:hash)'),
                {'id':event_id,'note':ids['note'],'author':ids['user'],'key':str(uuid4()),'hash':'a'*64})
            connection.execute(text('INSERT INTO notebook_revision_visibility '
                '(revision_id,private,change_id) VALUES (:revision,1,:id)'),
                {'revision':ids['revision'],'id':event_id})
        with pytest.raises(RuntimeError, match='Visibility history is retained'):
            command.downgrade(config, '0034')
        with db.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM notebook_revision_visibility')) == 1
            assert snapshot(connection, original) == before
    finally:
        db.engine.dispose()
