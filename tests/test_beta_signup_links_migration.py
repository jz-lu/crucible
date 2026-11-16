"""Signup-link tables preserve existing accounts, research and admission evidence."""
from types import SimpleNamespace

from alembic import command
import pytest
from sqlalchemy import MetaData, inspect, text

from crucible import beta_access as beta
from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


def test_signup_migration_is_additive_and_downgrade_preserves_admission_provenance(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'signup-migration.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0031')
    db = Database(url)
    try:
        original = MetaData()
        original.reflect(bind=db.engine)
        original.remove(original.tables['alembic_version'])
        with db.engine.begin() as connection:
            ids, _ = seed_original(connection, original)
            before = snapshot(connection, original)
        command.upgrade(config, '0032')
        command.check(config)
        with db.engine.connect() as connection:
            assert snapshot(connection, original) == before
            assert not connection.execute(text('PRAGMA foreign_key_check')).all()
        assert set(inspect(db.engine).get_table_names()) - {'alembic_version'} == set(original.tables) | {
            'beta_signup_links', 'beta_signup_invitations'}
        command.downgrade(config, '0031')  # Empty additive tables are safely reversible.
        command.upgrade(config, '0032')
        settings = SimpleNamespace(secret=b's' * 32, origins=('http://testserver',))
        with db.transaction(write=True) as session:
            link = beta.create_signup_link(session, settings, 'Trial', 4_000_000, actor_id=ids['user'])
            session.flush()
            token = beta.signup_link_data(settings, link, 4_000_000)['url'].split('token=')[1]
            beta.signup_invitation(session, settings, token, 'synthetic@example.org', 4_000_001)
        current = MetaData()
        current.reflect(bind=db.engine)
        with db.engine.connect() as connection:
            durable = snapshot(connection, current)
        with pytest.raises(RuntimeError, match='Preserve signup links'):
            command.downgrade(config, '0031')
        with db.engine.connect() as connection:
            assert snapshot(connection, current) == durable
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0032'
    finally:
        db.engine.dispose()
