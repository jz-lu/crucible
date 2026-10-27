"""Old UI/API bridge keeps new schema data and allocates aliases on old writes."""
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import MetaData, inspect, select, text

from crucible import dh_v4, notebook, notebook_numbers, registry
from crucible.db import Database
from crucible.researcher_profiles import ResearcherProfile
from crucible.security import verify_password
from scripts import beta_preflight
from test_notebook import system, content, create, revise, workspace, import_workspace
from test_notebook_trash import delete, restore
from test_researcher_profiles_migration import migration_config, seed_original, snapshot, user_columns


@pytest.fixture(autouse=True)
def portable_registry(monkeypatch):
    monkeypatch.setattr(registry, 'ADAPTERS', {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, 'CURRENT', {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})


def aliases(app):
    with app.state.db.transaction() as session:
        return dict(session.execute(select(notebook_numbers.NotebookNumber.note_id,
            notebook_numbers.NotebookNumber.number)).all())


def test_real_migration_preserves_original_records_and_populated_profiles_and_exact_head(tmp_path, monkeypatch):
    url = f'sqlite:///{tmp_path / "bridge-migration.db"}'
    config = migration_config(url, tmp_path, monkeypatch)
    assert ScriptDirectory.from_config(config).get_heads() == ['0025']
    command.upgrade(config, '0023')
    db = Database(url)
    try:
        original = MetaData(); original.reflect(db.engine)
        original.remove(original.tables['alembic_version'])
        assert len(original.tables) == 44
        with db.engine.begin() as connection:
            ids, password = seed_original(connection, original)
        command.upgrade(config, '0024')
        # Existing forward-feature profile data must survive bridge operation.
        with db.engine.begin() as connection:
            connection.execute(ResearcherProfile.__table__.insert().values(user_id=ids['user'],
                display_name='Existing researcher', affiliation='Preserved affiliation', position='', website='',
                revision=2, updated_at=3_600_610.0, last_idempotency_key=str(uuid4()), last_request_sha256='a'*64))
        metadata = MetaData(); metadata.reflect(db.engine)
        metadata.remove(metadata.tables['alembic_version'])
        assert len(metadata.tables) == 45
        with db.engine.connect() as connection:
            before = snapshot(connection, metadata)
            settings = SimpleNamespace(reviewer_ids=(), access_admin_ids=())
            assert beta_preflight.inspect_database(connection, settings, ['0025'], None)[0]['status'] == 'fail'
        columns = user_columns(db.engine)
        command.upgrade(config, '0025'); command.check(config)
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert user_columns(db.engine) == columns
            assert len(inspect(connection).get_table_names()) == 47
            assert verify_password(connection.scalar(text('SELECT password_hash FROM users')), password)
            assert dict(connection.execute(select(notebook_numbers.NotebookNumber.note_id,
                notebook_numbers.NotebookNumber.number)).all()) == {ids['note']: 1}
            assert beta_preflight.inspect_database(connection, settings, ['0025'], None)[0]['status'] == 'pass'
            assert not connection.exec_driver_sql('PRAGMA foreign_key_check').all()
        with pytest.raises(RuntimeError, match='Cannot discard allocated'):
            command.downgrade(config, '0024')
    finally:
        db.engine.dispose()


def test_old_note_writes_reserve_numbers_across_retries_revisions_trash_and_import(system):
    app, owner, _, reader, _ = system
    with app.state.db.transaction(write=True) as session:
        existing = notebook_numbers.ensure(session.connection(), ['curated:already-reserved'])
    key = str(uuid4()); first = create(owner, key=key)
    assert 'number' not in first  # Legacy response shape is intentional.
    reserved = aliases(app)
    assert set(reserved) == {*existing, first['id']}
    assert reserved[first['id']] > existing['curated:already-reserved']
    assert create(owner, key=key)['id'] == first['id']
    latest = revise(owner, first)
    assert aliases(app) == reserved
    deletion = delete(owner, latest).json()['deletion']
    assert aliases(app) == reserved
    assert restore(owner, deletion).status_code == 200
    imported = import_workspace(owner, workspace()); assert imported.status_code == 200
    imported_id = next(iter(imported.json()['id_map'].values()))
    after = aliases(app)
    assert after[imported_id] > reserved[first['id']]
    assert all(after[key] == number for key, number in reserved.items())
    assert import_workspace(owner, workspace()).status_code == 200
    assert aliases(app) == after
    assert reader.client.get('/api/notebook/notes/' + first['id']).status_code == 404
    routes = {route.path for route in app.routes if hasattr(route, 'path')}
    assert '/api/me/profile' not in routes
    assert '/api/notebook/curated-note-numbers' not in routes
    detail = owner.client.get('/api/notebook/notes/' + first['id']).json()['note']
    assert 'number' not in detail and detail['revision'] == 2


def test_bridge_note_and_alias_insertion_share_one_transaction(system):
    app, owner, *_ = system
    before = aliases(app); identifier = str(uuid4())
    with pytest.raises(RuntimeError, match='synthetic rollback'):
        with app.state.db.transaction(write=True) as session:
            session.add(notebook.NotebookNote(id=identifier, author_id=owner.identifier, created_at=1,
                latest_revision=1, idempotency_key=str(uuid4()), request_sha256='b'*64))
            session.flush()
            assert session.scalar(select(notebook_numbers.NotebookNumber.number)
                .where(notebook_numbers.NotebookNumber.note_id == identifier)) is not None
            raise RuntimeError('synthetic rollback')
    assert aliases(app) == before
    with app.state.db.transaction() as session:
        assert session.get(notebook.NotebookNote, identifier) is None
