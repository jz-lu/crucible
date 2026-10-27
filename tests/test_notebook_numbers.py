"""Stable note aliases across revisions, visibility, catalogue updates and writers."""
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import MetaData, delete as sql_delete, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from crucible import beta_access, notebook, notebook_numbers
from crucible.db import Database, User
from test_notebook import (system, content, create, revise, request_publication,
                           approve, workspace, import_workspace)
from test_notebook_browse import curated, page
from test_notebook_trash import delete, restore
from test_notebook_agents import agent
from test_beta_boundary import private_app
from test_researcher_profiles_migration import migration_config, snapshot


def test_numbers_survive_edits_retries_search_and_deletion(system):
    app, owner, _, reader, _ = system
    key = str(uuid4())
    first = create(owner, key=key)
    second = create(owner)
    assert 0 < first['number'] < second['number']
    assert create(owner, key=key)['number'] == first['number']
    latest = revise(owner, first)
    assert latest['number'] == first['number']
    term = f"#{first['number']:04d}"
    found = page(owner, q=term)['notes']
    assert [(row['id'], row['number'], row['revision']) for row in found] == [(first['id'], first['number'], 2)]
    assert page(reader, q=term)['notes'] == []
    for invalid in ('#0', '#' + '9' * 190):
        assert page(owner, q=invalid)['notes'] == []
    deletion = delete(owner, latest).json()['deletion']
    assert page(owner, q=term)['notes'] == []
    assert page(owner, q=term, scope='trash')['notes'][0]['number'] == first['number']
    third = create(owner)
    assert third['number'] > second['number']
    assert restore(owner, deletion).status_code == 200
    assert page(owner, q=term)['notes'][0]['number'] == first['number']


def test_number_search_uses_the_readers_visible_revision(system):
    _, owner, reviewer, reader, _ = system
    saved = create(owner)
    request_publication(owner, saved)
    approve(reviewer, saved)
    latest = revise(owner, saved, content(title='Private revision'))
    found = page(reader, q=f"#{saved['number']}")['notes']
    assert len(found) == 1 and found[0]['number'] == latest['number']
    assert found[0]['revisions'][0]['content']['title'] != 'Private revision'
    assert found[0]['revision'] == 1


def test_catalogue_reordering_and_new_notes_do_not_renumber_existing_notes(system, curated):
    _, owner, _, reader, _ = system
    path, source = curated
    endpoint = '/api/notebook/curated-note-numbers'
    before = owner.client.get(endpoint).json()['numbers']
    assert before == reader.client.get(endpoint).json()['numbers']
    saved = create(owner)
    assert saved['number'] not in before.values()
    source['notes'].reverse()
    source['notes'].append({'id':'new-curated-note', 'revisions':[dict(content(title='New catalogue note'), revision=1, createdAt=10)]})
    path.write_text(json.dumps(source))
    notebook._curated_collection.cache_clear()
    after = owner.client.get(endpoint).json()['numbers']
    assert all(after[key] == number for key, number in before.items())
    assert after['curated:new-curated-note'] not in {*before.values(), saved['number']}
    identifier, number = next(iter(before.items()))
    found = page(owner, include_curated=True, q=f'#{number:04d}')['notes']
    assert [(row['id'], row['number']) for row in found] == [(identifier, number)]


def test_imports_receive_numbers_and_reimports_keep_them(system):
    _, owner, *_ = system
    value = workspace('imported-reference')
    assert import_workspace(owner, value).status_code == 200
    first = page(owner)['notes'][0]
    assert isinstance(first['number'], int)
    value['notes'][0]['revisions'].append(dict(content(title='Imported update'), revision=2, createdAt=2000))
    assert import_workspace(owner, value).status_code == 200
    assert page(owner)['notes'][0]['number'] == first['number']


def test_concurrent_allocators_share_one_alias_per_identity(system):
    app, *_ = system
    identifiers = [f'curated:concurrent-{index}' for index in range(12)]
    def allocate(_):
        with app.state.db.transaction(write=True) as session:
            return notebook_numbers.ensure(session.connection(), identifiers)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(allocate, range(8)))
    assert all(result == results[0] for result in results)
    assert len(set(results[0].values())) == len(identifiers)
    with app.state.db.transaction() as session:
        rows = session.execute(select(notebook_numbers.NotebookNumber.note_id,
            notebook_numbers.NotebookNumber.number)).all()
    assert len(rows) == len(identifiers)


def test_curated_reservations_survive_removal_and_do_not_leak_into_catalogue_cache(system, curated):
    _, owner, *_ = system
    path, source = curated
    endpoint = '/api/notebook/curated-note-numbers'
    before = owner.client.get(endpoint).json()['numbers']
    assert all('number' not in row for row in notebook.curated_collection())
    removed = source['notes'].pop()
    identifier = 'curated:' + removed['id']
    path.write_text(json.dumps(source))
    assert identifier not in owner.client.get(endpoint).json()['numbers']
    assert page(owner, include_curated=True, q=f'#{before[identifier]}')['notes'] == []
    saved = create(owner)
    assert saved['number'] > max(before.values())
    source['notes'].insert(0, removed)
    path.write_text(json.dumps(source))
    assert owner.client.get(endpoint).json()['numbers'] == before


def test_maximum_curated_identity_includes_its_namespace_prefix(system, curated):
    _, owner, *_ = system
    path, source = curated
    source['notes'][0]['id'] = 'x' * 100
    path.write_text(json.dumps(source))
    mapping = owner.client.get('/api/notebook/curated-note-numbers').json()['numbers']
    identifier = 'curated:' + 'x' * 100
    assert len(identifier) == notebook_numbers.NotebookNumber.__table__.c.note_id.type.length == 108
    assert page(owner, include_curated=True, q=f'#{mapping[identifier]}')['notes'][0]['id'] == identifier


def test_number_reservations_are_not_deleted_with_the_logical_note(system):
    app, owner, *_ = system
    note = create(owner)
    # Simulate a future durable purge. The alias deliberately has no cascading
    # foreign key; removal of a note must not make its number reusable.
    with app.state.db.transaction(write=True) as session:
        session.execute(sql_delete(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == note['id']))
        session.execute(sql_delete(notebook.NotebookNote).where(notebook.NotebookNote.id == note['id']))
    assert page(owner, q=f"#{note['number']}")['notes'] == []
    following = create(owner)
    assert following['number'] > note['number']
    with app.state.db.transaction() as session:
        assert session.scalar(select(notebook_numbers.NotebookNumber.number).where(
            notebook_numbers.NotebookNumber.note_id == note['id'])) == note['number']


def test_curated_allocation_requires_current_member_access_and_never_accepts_agent_authority(private_app):
    app = private_app
    endpoint = '/api/notebook/curated-note-numbers'
    anonymous = TestClient(app)
    assert anonymous.get(endpoint).status_code == 401
    member = app.test_login('participant')
    assert member.client.get('/api/session').json()['access']['allowed'] is True
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, app.test_member, 'revoked', app.test_now[0])
    # Membership revocation invalidates the existing login session as well.
    assert member.client.get(endpoint).status_code == 401
    assert member.client.get('/api/notebook/notes?include_curated=true&q=%230001').status_code == 401
    with app.state.db.transaction() as session:
        assert session.execute(select(notebook_numbers.NotebookNumber)).all() == []


def test_agent_number_search_is_owned_only_and_cannot_allocate_curated_references(system):
    app, owner, reviewer, *_ = system
    foreign, own = create(owner), create(reviewer)
    client = agent(app, reviewer, ['notebook:read'])
    assert client.get('/api/notebook/curated-note-numbers').status_code == 403
    assert client.get('/api/notebook/notes', params={'q': f"#{foreign['number']}"}).json()['notes'] == []
    result = client.get('/api/notebook/notes', params={'q': f"#{own['number']}"}).json()['notes']
    assert [(row['id'], row['number']) for row in result] == [(own['id'], own['number'])]


def test_plain_numeric_text_keeps_text_search_and_alias_never_replaces_exact_citation_identity(system):
    _, owner, *_ = system
    note = create(owner, content(title='Number 12345 is just text'))
    assert page(owner, q='12345')['notes'][0]['id'] == note['id']
    assert page(owner, q='#12345')['notes'] == []
    detail = owner.client.get(f"/api/notebook/notes/{note['id']}?revision=1").json()['note']
    assert (detail['number'], detail['id'], detail['revision'], detail['content_sha256']) == (
        note['number'], note['id'], note['revision'], note['content_sha256'])


def test_postgres_schema_uses_identity_not_serial():
    ddl = str(CreateTable(notebook_numbers.NotebookNumber.__table__).compile(dialect=postgresql.dialect()))
    assert 'GENERATED ALWAYS AS IDENTITY' in ddl and 'SERIAL' not in ddl


def test_empty_alias_migration_can_be_reversed(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0025')
    command.downgrade(config, '0024')
    db = Database(url)
    try:
        assert 'notebook_numbers' not in inspect(db.engine).get_table_names()
    finally:
        db.engine.dispose()


def test_migration_assigns_existing_notes_once_and_new_allocations_continue(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'numbers.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    command.upgrade(config, '0024')
    db = Database(url)
    owner = str(uuid4())
    identifiers = [str(uuid4()) for _ in range(2)]
    try:
        with db.engine.begin() as connection:
            connection.execute(User.__table__.insert(), dict(id=owner, handle='reference-migration', created_at=1.0))
            for created, identifier in enumerate(identifiers, start=1):
                connection.execute(notebook.NotebookNote.__table__.insert(), dict(id=identifier,
                    author_id=owner, created_at=created, latest_revision=1,
                    idempotency_key=str(uuid4()), request_sha256='a' * 64))
        metadata = MetaData()
        metadata.reflect(db.engine)
        metadata.remove(metadata.tables['alembic_version'])
        with db.engine.connect() as connection:
            original = snapshot(connection, metadata)
        command.upgrade(config, 'head')
        command.check(config)
        with db.transaction(write=True) as session:
            before = dict(session.execute(select(notebook_numbers.NotebookNumber.note_id,
                notebook_numbers.NotebookNumber.number)).all())
            new = str(uuid4())
            allocated = notebook_numbers.ensure(session.connection(), [*identifiers, new])
        assert before == dict(zip(identifiers, (1, 2)))
        assert all(allocated[key] == value for key, value in before.items())
        assert allocated[new] == 3
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == original
            assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0025'
            assert not connection.exec_driver_sql('PRAGMA foreign_key_check').all()
        with pytest.raises(RuntimeError, match='Cannot discard allocated'):
            command.downgrade(config, '0024')
    finally:
        db.engine.dispose()
