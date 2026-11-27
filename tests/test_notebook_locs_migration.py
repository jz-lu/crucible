"""Additive loc reservation migration preserves research and prevents reuse."""
from alembic import command
import pytest
from sqlalchemy import MetaData, inspect, text

from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def test_locs_upgrade_is_empty_preserves_research_and_blocks_forgetting_reservations(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'locs.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0035')
    db = Database(url)
    try:
        original = MetaData(); original.reflect(bind=db.engine); original.remove(original.tables['alembic_version'])
        with db.engine.begin() as connection:
            ids, _ = seed_original(connection, original)
            before = snapshot(connection, original)
        command.upgrade(config, '0036')
        assert set(inspect(db.engine).get_table_names()) == set(original.tables) | {'notebook_locs','alembic_version'}
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert connection.scalar(text('SELECT count(*) FROM notebook_locs')) == 0
        command.downgrade(config, '0035'); command.upgrade(config, '0036')
        with db.engine.begin() as connection:
            for passage in ('p1-b-1-2','p1-b-3-4'):
                connection.execute(text('INSERT INTO notebook_locs (note_id,revision,content_sha256,passage) VALUES (:note,1,:hash,:passage)'),
                    {'note':ids['note'],'hash':'a'*64,'passage':passage})
            connection.execute(text('DELETE FROM notebook_locs WHERE number=2'))
            connection.execute(text('INSERT INTO notebook_locs (note_id,revision,content_sha256,passage) VALUES (:note,1,:hash,\'p1-b-5-6\')'),
                {'note':ids['note'],'hash':'a'*64})
            assert connection.scalar(text('SELECT max(number) FROM notebook_locs')) == 3
        with pytest.raises(RuntimeError, match='cannot be forgotten'):
            command.downgrade(config, '0035')
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
    finally:
        db.engine.dispose()
