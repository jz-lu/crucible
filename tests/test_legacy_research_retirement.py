"""Retired findings have no API; historical databases keep their exact records."""
import base64
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from crucible import dh_v7, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Database, User
from crucible.research import Contribution, ContributionRevision


@pytest.mark.parametrize('access_mode', ['open', 'beta'])
def test_retired_routes_are_unavailable_and_current_research_routes_remain(tmp_path, monkeypatch, access_mode):
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v7.SCHEME_ID, dh_v7.SCHEME_VERSION): dh_v7})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v7.SCHEME_ID: dh_v7.SCHEME_VERSION})
    settings = Settings(f"sqlite:///{tmp_path / 'retired.db'}", b'r' * 32,
                        origins=('http://testserver',), access_mode=access_mode, rate_limits=False)
    app = create_app(settings, initialize=True)
    try:
        paths = app.openapi()['paths']
        assert '/api/protocol/assumptions' in paths
        assert '/api/notebook/notes' in paths
        assert '/api/notebook/research-requests' in paths
        assert '/api/protocol/proposals' in paths
        assert not any(path == '/api/targets' or path.startswith('/api/contributions') for path in paths)
        with TestClient(app) as client:
            csrf = client.get('/api/session').json()['csrf_token']
            for method, path in (
                ('GET', '/api/targets'), ('HEAD', '/api/targets'),
                ('GET', '/api/contributions'), ('POST', '/api/contributions'),
                ('GET', '/api/contributions/' + str(uuid4())),
                ('POST', '/api/contributions/' + str(uuid4()) + '/revisions'),
            ):
                response = client.request(method, path, headers={
                    'Origin': 'http://testserver', 'X-CSRF-Token': csrf,
                }, **({'json': {}} if method == 'POST' else {}))
                assert response.status_code == 404, (method, path, response.text)
    finally:
        app.state.db.engine.dispose()


def test_historical_findings_survive_upgrade_and_remain_in_schema_metadata(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name == 'DATABASE_URL' or name.startswith('CRUCIBLE_'):
            monkeypatch.delenv(name)
    url = f"sqlite:///{tmp_path / 'historical.db'}"
    for name, value in {
        'DATABASE_URL': url, 'CRUCIBLE_DATA_DIR': str(tmp_path),
        'CRUCIBLE_SECRET': base64.b64encode(b'h' * 32).decode(),
        'CRUCIBLE_ENV': 'development', 'CRUCIBLE_ACCESS_MODE': 'open',
        'CRUCIBLE_MAIL_BACKEND': 'disabled',
    }.items():
        monkeypatch.setenv(name, value)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    command.upgrade(config, '0002')
    db = Database(url)
    author_id, finding_id = str(uuid4()), str(uuid4())
    try:
        with db.transaction(write=True) as session:
            session.add(User(id=author_id, handle='historical_author',
                             password_hash='synthetic-unused-password-hash', created_at=100.0))
            session.flush()
            session.add(Contribution(id=finding_id, author_id=author_id, created_at=101.0))
            session.flush()
            for revision in (1, 2):
                session.add(ContributionRevision(id=str(uuid4()), contribution_id=finding_id,
                    revision=revision, created_at=101.0 + revision, target_id='finite-field-dlog',
                    target_version='1', profile_ids=['dh-20'], title='Historical finding',
                    claim=f'Exact claim revision {revision}', conditions='Synthetic historical scope',
                    body=f'Preserve this original body {revision}', model='classical',
                    evidence_kind='argument', time_cost='O(sqrt(q))', memory_cost='O(sqrt(q))',
                    success_probability='Conditional', artifact_url='https://example.org/artifact'))

        def snapshots():
            with db.engine.connect() as connection:
                return {model.__tablename__: [dict(row) for row in connection.execute(
                    select(model.__table__).order_by(model.id)).mappings()]
                    for model in (User, Contribution, ContributionRevision)}

        before = snapshots()
        command.upgrade(config, 'head')
        command.check(config)
        assert snapshots() == before
    finally:
        db.engine.dispose()
