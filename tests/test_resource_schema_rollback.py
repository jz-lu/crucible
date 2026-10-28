"""Schema-compatible rollback keeps evidence but has no resource feature routes."""
import io
from pathlib import Path
import zipfile

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, inspect, text

import crucible
from crucible import dh_v4, registry, resource_schema
from crucible.app import create_app
from crucible.config import load_settings
from crucible.db import Database
from test_researcher_profiles_migration import migration_config, seed_original, snapshot


TABLES = ('resource_publications', 'resource_activations', 'resource_events')


@pytest.fixture
def preserved(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path / 'rollback.db')
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0025')
    db = Database(url)
    original = MetaData()
    original.reflect(bind=db.engine, only=sorted(set(inspect(db.engine).get_table_names()) - {'alembic_version'}))
    with db.engine.begin() as connection:
        seed_original(connection, original)
    with db.engine.connect() as connection:
        before = snapshot(connection, original)
    command.upgrade(config, '0026')
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        archive.writestr('fixture.json', '{"retained":"synthetic original evidence"}')
    with db.engine.begin() as connection:
        connection.execute(resource_schema.ResourcePublication.__table__.insert(), {
            'id': 'a' * 64, 'scheme_id': 'synthetic-only', 'version': '1',
            'repository_id': '1', 'run_id': 1, 'run_attempt': 1, 'created_at': 1.25,
            'publication': {'format': 'synthetic fixture'},
            'resources': {'configurations': [], 'untouched': [3, 1, 2]}, 'bundle': raw.getvalue()})
    command.upgrade(config, 'head')
    command.check(config)
    with db.engine.begin() as connection:
        connection.execute(resource_schema.ResourceActivation.__table__.insert(), {
            'id': 'b' * 64, 'scheme_id': 'synthetic-only', 'version': '1',
            'fingerprint': 'c' * 64, 'producer_sha256': 'd' * 64,
            'binding': {'fixture': [1, 3, 2]}, 'source': {'synthetic': True}, 'created_at': 2.25})
        connection.execute(resource_schema.ResourceEvent.__table__.insert(), {
            'id': 'e' * 64, 'repository_id': '1', 'run_id': 2, 'run_attempt': 1,
            'kind': 'failed', 'scheme_id': 'synthetic-only', 'version': '1',
            'activation_id': 'b' * 64, 'received_at': 3.25,
            'payload': {'phase': 'failed', 'preserved': ['exact', None, 0]}})
    resources = MetaData()
    resources.reflect(bind=db.engine, only=TABLES)
    with db.engine.connect() as connection:
        assert snapshot(connection, original) == before
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0027'
        retained = snapshot(connection, resources)
    # Real artifact checks still execute, against this explicit portable fixture.
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})
    yield config, db, resources, retained, original, before
    db.engine.dispose()


@pytest.mark.parametrize('publishing_flag', ['false', 'true'])
def test_rollback_serves_old_app_without_resource_routes_and_preserves_evidence(preserved, monkeypatch, publishing_flag):
    config, db, resources, retained, original, before = preserved
    monkeypatch.setenv('CRUCIBLE_RESOURCE_PUBLISHING', publishing_flag)
    settings = load_settings()
    app = create_app(settings)
    try:
        assert not any('/resource' in getattr(route, 'path', '') for route in app.routes)
        with TestClient(app, base_url=settings.origins[0]) as client:
            assert client.get('/health').status_code == 200
            with db.engine.connect() as connection:
                assert snapshot(connection, original) == before
            for path in ('/api/resource-publications', '/api/resource-state'):
                assert client.post(path, json={'synthetic': True}).status_code in (403, 404)
            assert client.get('/api/protocol/resources').status_code in (403, 404)
        with db.engine.connect() as connection:
            assert snapshot(connection, resources) == retained
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0027'
        command.check(config)
        root = Path(crucible.__file__).resolve().parents[1]
        assert not (root / 'web/resource-metrics.js').exists()
        assert 'resource-metrics.js' not in (root / 'web/index.html').read_text()
    finally:
        app.state.db.engine.dispose()


def test_populated_lifecycle_evidence_refuses_destructive_downgrade(preserved):
    config, db, resources, retained, _, _ = preserved
    with pytest.raises(RuntimeError, match='Preserve resource lifecycle evidence'):
        command.downgrade(config, '0026')
    with db.engine.connect() as connection:
        assert snapshot(connection, resources) == retained
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0027'
