"""The local trial adds no decisions and retains every pre-existing row."""
from uuid import uuid4

from alembic import command
from sqlalchemy import MetaData, inspect, text
import pytest

from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot

TABLES = {'local_statement_updates', 'local_statement_current', 'local_statement_adoptions'}


def test_statement_upgrade_preserves_existing_research_and_challenges(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'statements.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0031')
    db = Database(url)
    try:
        original = MetaData(); original.reflect(bind=db.engine)
        original.remove(original.tables['alembic_version'])
        with db.engine.begin() as connection:
            identities, _ = seed_original(connection, original)
            before = snapshot(connection, original)
        command.upgrade(config, '0032')
        assert set(inspect(db.engine).get_table_names()) == set(original.tables) | TABLES | {'alembic_version'}
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
            for name in TABLES:
                assert connection.scalar(text('SELECT count(*) FROM ' + name)) == 0
        command.check(config)
        command.downgrade(config, '0031')
        command.upgrade(config, '0032')
        with db.engine.begin() as connection:
            connection.execute(text('INSERT INTO local_statement_updates '
                '(id,author_id,created_at,packet,packet_sha256,idempotency_key) '
                'VALUES (:id,:author,1,:packet,:digest,:key)'),
                {'id':str(uuid4()), 'author':identities['user'], 'packet':'{}', 'digest':'a'*64, 'key':str(uuid4())})
        with pytest.raises(RuntimeError, match='history is retained'):
            command.downgrade(config, '0031')
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert connection.scalar(text('SELECT count(*) FROM local_statement_updates')) == 1
    finally:
        db.engine.dispose()
