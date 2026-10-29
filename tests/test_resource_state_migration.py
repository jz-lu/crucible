"""Schema 0027 adds lifecycle evidence without rewriting retained ZIPs."""
from alembic import command
import pytest
from sqlalchemy import create_engine, inspect, text
from crucible import resource_publications as rp
from test_researcher_profiles_migration import migration_config


def test_0027_preserves_0026_publication_and_matches_metadata(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'resource-migration.db')
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0026')
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(rp.ResourcePublication.__table__.insert(), dict(id='a'*64, scheme_id='test-only', version='1',
            repository_id='1', run_id=1, run_attempt=1, created_at=1,
            publication={'format':'legacy fixture'}, resources={'configurations':[]}, bundle=b'untouched evidence'))
    command.upgrade(config, '0027')
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT bundle FROM resource_publications')) == b'untouched evidence'
        assert connection.scalar(text('SELECT count(*) FROM resource_activations')) == 0
        assert connection.scalar(text('SELECT count(*) FROM resource_events')) == 0
    # Metadata describes the current application head, not historical 0027.
    # Check this exact transition's preservation first, then current agreement.
    command.upgrade(config, 'head')
    command.check(config)
    # Empty additive tables can be removed; populated publication remains.
    command.downgrade(config, '0026')
    assert 'resource_events' not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT bundle FROM resource_publications')) == b'untouched evidence'
    engine.dispose()
