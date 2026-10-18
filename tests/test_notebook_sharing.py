"""Retired private grants are preserved evidence, never a source of access."""
import base64
import json
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, inspect, select, text

from crucible import beta_access, notebook, notebook_sharing as sharing
from crucible.db import Database, LoginSession, User
from test_notebook import system, content, create, revise, revision_body, publication_body, request_publication, approve
from test_notebook_access import notebook_access, token_for
from test_proposal_notes import scheme_content, submit as submit_scheme
from test_research_reviews import request_body


def path(note):
    return '/api/notebook/notes/' + note['id']


def seed_legacy_shares(owner, note, handles, *, state='accepted'):
    """Seed pre-retirement history directly; no active sharing API is available."""
    identifiers = []
    with owner.app.state.db.transaction(write=True) as session:
        for handle in handles:
            recipient = session.scalar(select(User).where(User.handle == handle))
            identifier = str(uuid4())
            session.add(sharing.PrivateNoteShare(id=identifier, note_id=note['id'],
                recipient_id=recipient.id, from_revision=note['revision'], shared_at=owner.app.state.clock(),
                revoked_at=None, state=state, response_version=0))
            identifiers.append(identifier)
    return identifiers


def put(actor, note, body=None):
    return actor.client.put(path(note) + '/private-shares', json=body or {},
        headers={'Origin': 'http://testserver', 'X-CSRF-Token': actor.csrf})


def history_snapshot(app):
    with app.state.db.engine.connect() as connection:
        return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
            for model in (User, LoginSession, notebook.NotebookNote, notebook.NotebookRevision,
                sharing.PrivateNoteShare, sharing.PrivateNoteShareChange, sharing.PrivateNoteShareResponse)}


@pytest.mark.parametrize('state', ['pending', 'accepted', 'dismissed'])
def test_existing_private_grants_never_expose_notes_or_future_edits(system, state):
    app, owner, reviewer, reader, _ = system
    first = create(owner, content(title='PRIVATE_EARLIER', tags=['private-marker']))
    second = revise(owner, first, content(title='PRIVATE_GRANTED', tags=['private-marker']))
    seed_legacy_shares(owner, second, ['reader', 'reviewer'], state=state)
    third = revise(owner, second, content(title='PRIVATE_FUTURE', tags=['private-marker']))
    snapshot = history_snapshot(app)
    for actor in (reader, reviewer):
        assert actor.client.get(path(first)).status_code == 404
        for revision in (1, 2, 3):
            assert actor.client.get(path(first), params={'revision': revision}).status_code == 404
        for params in ({}, {'q': 'PRIVATE_'}, {'tag': 'private-marker'}, {'scope': 'mine'}):
            assert actor.client.get('/api/notebook/notes', params=params).json()['notes'] == []
        assert actor.client.get('/api/notebook/notes?scope=shared').status_code == 410
        assert actor.client.get('/api/notebook/export').json()['notes'] == []
        assert actor.client.get('/api/notebook/review-queue').status_code == (200 if actor is reviewer else 403)
    owned = owner.client.get(path(first)).json()['note']
    assert owned['can_edit'] is True and owned['revision_count'] == 3
    assert [row['revision'] for row in owned['revisions']] == [1, 2, 3]
    assert not {'private_shared', 'private_share', 'private_share_count', 'can_manage_private_sharing'} & owned.keys()
    assert history_snapshot(app) == snapshot


def test_published_exact_revision_remains_readable_without_historical_grant_access(system):
    _, owner, reviewer, reader, _ = system
    first = create(owner, content(title='PRIVATE_FIRST'))
    public = revise(owner, first, content(title='Public snapshot'))
    request_publication(owner, public); approve(reviewer, public)
    third = revise(owner, public, content(title='PRIVATE_LATER'))
    seed_legacy_shares(owner, first, ['reader', 'reviewer'])
    for actor in (reader, reviewer):
        record = actor.client.get(path(first)).json()['note']
        assert record['revision'] == 2 and record['revision_count'] == 1
        assert [row['revision'] for row in record['revisions']] == [2]
        assert record['can_edit'] is False and 'PRIVATE_' not in json.dumps(record)
        for revision in (1, 3):
            assert actor.client.get(path(first), params={'revision': revision}).status_code == 404
        listed = actor.client.get('/api/notebook/notes').json()['notes']
        assert [(row['id'], row['revision']) for row in listed] == [(first['id'], 2)]
        assert actor.client.get('/api/notebook/notes?q=PRIVATE_').json()['notes'] == []
    request_publication(owner, third)
    assert [r['revision'] for r in reviewer.client.get(path(first)).json()['note']['revisions']] == [2, 3]
    assert reader.client.get(path(first)).json()['note']['revision'] == 2
    approve(reviewer, third)
    assert reader.client.get(path(first)).json()['note']['revision'] == 3


def test_retired_endpoints_check_identity_and_never_mutate_history(system):
    app, owner, reviewer, reader, _ = system
    original = create(owner)
    grant_id = seed_legacy_shares(owner, original, ['reader'])[0]
    with app.state.db.transaction(write=True) as session:
        session.add(sharing.PrivateNoteShareChange(id=str(uuid4()), note_id=original['id'], author_id=owner.identifier,
            sequence=1, created_at=app.state.clock(), revision=1, content_sha256=original['content_sha256'],
            before_recipients=[], after_recipients=[{'handle':'reader'}], before_sharing_sha256='0'*64,
            after_sharing_sha256='1'*64, idempotency_key=str(uuid4()), request_sha256='2'*64))
        session.add(sharing.PrivateNoteShareResponse(id=str(uuid4()), share_id=grant_id, recipient_id=reader.identifier,
            created_at=app.state.clock(), before_state='pending', state='accepted', response_version=1,
            idempotency_key=str(uuid4()), request_sha256='3'*64))
    before = history_snapshot(app)
    audience = path(original) + '/private-shares'
    response = '/api/notebook/private-shares/' + grant_id + '/response'
    # Stale or malformed payloads cannot regrant access or change old receipts.
    for body in ({}, {'handles':['reviewer']}, {'decision':'dismissed'}, None):
        assert put(owner, original, body).status_code == 410
        assert reader.post(response, body or {}).status_code == 410
    assert owner.client.get(audience).status_code == 410
    for actor in (owner, reviewer, reader):
        assert actor.client.get('/api/notebook/private-shares').status_code == 410
        assert actor.client.get('/api/notebook/notes?scope=shared').status_code == 410
    for actor in (reader, reviewer):
        assert actor.client.get(audience).status_code == 404
        assert put(actor, original).status_code == 404
    for actor in (owner, reviewer):
        assert actor.post(response, {}).status_code == 404
    assert reader.post('/api/notebook/private-shares/' + str(uuid4()) + '/response', {}).status_code == 404
    assert owner.client.put(audience, json={}).status_code == 403
    assert reader.client.post(response, json={}).status_code == 403
    assert history_snapshot(app) == before


def test_retired_endpoints_keep_browser_authentication_and_beta_gate(notebook_access):
    app, now, ids, actors = notebook_access
    owner, reader = actors['author'], actors['reader']
    original = create(owner)
    # This fixture's actors do not retain app; seed pre-retirement records explicitly.
    grant_id = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        session.add(sharing.PrivateNoteShare(id=grant_id, note_id=original['id'], recipient_id=ids['reader'],
            from_revision=1, shared_at=now[0], revoked_at=None, state='accepted'))
    audience = path(original) + '/private-shares'
    response = '/api/notebook/private-shares/' + grant_id + '/response'
    for target in (audience, '/api/notebook/private-shares', '/api/notebook/notes?scope=shared'):
        with TestClient(app) as anonymous:
            assert anonymous.get(target).status_code == 401
        assert owner.client.get(target, headers={'Authorization':'Bearer invalid'}).status_code in (401, 403)
    token = token_for(app, owner)
    assert owner.client.put(audience, json={}, headers={'Authorization':'Bearer '+token}).status_code == 403
    with TestClient(app) as anonymous:
        assert anonymous.put(audience, json={}).status_code == 401
        assert anonymous.post(response, json={}).status_code == 401
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, ids['reader'], 'revoked', now[0])
        beta_access.set_membership(session, ids['author'], 'revoked', now[0])
    for target in (audience, '/api/notebook/private-shares', '/api/notebook/notes?scope=shared'):
        assert owner.client.get(target).status_code == 401
    assert put(owner, original).status_code == 401
    assert reader.post(response, {}).status_code == 401


def test_legacy_recipients_cannot_edit_export_publish_or_forward_private_content(system):
    _, owner, reviewer, reader, _ = system
    original = create(owner, scheme_content())
    seed_legacy_shares(owner, original, ['reader', 'reviewer'])
    for actor in (reader, reviewer):
        assert actor.post(path(original) + '/revisions', revision_body(original)).status_code == 404
        assert actor.client.get('/api/notebook/export', params={'note_id':original['id']}).status_code == 404
        for suffix in ('/publication-requests', '/proposal-submissions'):
            assert actor.post(path(original) + suffix, publication_body(original)).status_code == 404
        assert actor.post('/api/notebook/research-requests', request_body(original)).status_code == 404
    assert submit_scheme(owner, original).status_code == 200
    assert reviewer.client.get(path(original)).status_code == 200  # Explicit review packet, independent of grant.
    assert reader.client.get(path(original)).status_code == 404


def test_normal_notebook_reads_do_not_query_retired_grants(system):
    app, owner, reviewer, reader, _ = system
    original = create(owner)
    request_publication(owner, original); approve(reviewer, original)
    seed_legacy_shares(owner, original, ['reader'])
    statements = []
    def sql(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(app.state.db.engine, 'before_cursor_execute', sql)
    try:
        for actor in (owner, reader):
            assert actor.client.get(path(original)).status_code == 200
            assert actor.client.get('/api/notebook/notes?include_curated=true').status_code == 200
            assert actor.client.get('/api/notebook/timeline').status_code == 200
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', sql)
    assert not any('notebook_private_' in statement for statement in statements)


def test_sharing_migration_preserves_history_and_creates_no_grants(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key == 'DATABASE_URL' or key.startswith('CRUCIBLE_'):
            monkeypatch.delenv(key)
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CRUCIBLE_SECRET', base64.b64encode(b'p'*32).decode())
    monkeypatch.setenv('CRUCIBLE_ENV', 'development')
    monkeypatch.setenv('CRUCIBLE_ACCESS_MODE', 'open')
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    command.upgrade(config, '0013')
    db = Database(url)
    identifier, note_id, revision_id = [str(uuid4()) for _ in range(3)]
    try:
        with db.transaction(write=True) as session:
            session.add(User(id=identifier, handle='preserved_author', password_hash='unchanged', created_at=1.0))
            session.flush()
            session.add(LoginSession(token_hash='f'*64, user_id=identifier, expires_at=1000.0))
            session.add(notebook.NotebookNote(id=note_id, author_id=identifier, created_at=2.0,
                latest_revision=1, idempotency_key=str(uuid4()), request_sha256='a'*64))
            session.flush()
            session.add(notebook.NotebookRevision(id=revision_id, note_id=note_id, revision=1, created_at=2.0,
                content=content(), content_sha256=notebook._hash(content()), idempotency_key=str(uuid4()), request_sha256='b'*64))
        def snapshot():
            with db.engine.connect() as connection:
                return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
                        for model in (User, LoginSession, notebook.NotebookNote, notebook.NotebookRevision)}
        before = snapshot()
        command.upgrade(config, 'head')
        command.check(config)
        assert snapshot() == before
        with db.transaction() as session:
            for model in (sharing.PrivateNoteShare, sharing.PrivateNoteShareChange):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        assert 'notebook_private_shares' in inspect(db.engine).get_table_names()
        command.downgrade(config, '0013')
        assert snapshot() == before
    finally:
        db.engine.dispose()


def test_recipient_migration_preserves_existing_grants_as_accepted(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key == 'DATABASE_URL' or key.startswith('CRUCIBLE_'):
            monkeypatch.delenv(key)
    url = f"sqlite:///{tmp_path / 'recipient-migration.db'}"
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CRUCIBLE_SECRET', base64.b64encode(b'p'*32).decode())
    monkeypatch.setenv('CRUCIBLE_ENV', 'development')
    monkeypatch.setenv('CRUCIBLE_ACCESS_MODE', 'open')
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    command.upgrade(config, '0015')
    db = Database(url)
    author_id, recipient_id, note_id = [str(uuid4()) for _ in range(3)]
    active_id, revoked_id = str(uuid4()), str(uuid4())
    try:
        with db.transaction(write=True) as session:
            session.add_all([User(id=identifier, handle=handle, password_hash='preserved', created_at=1.0)
                for identifier, handle in ((author_id, 'old_author'), (recipient_id, 'old_recipient'))])
            session.flush()
            session.add(LoginSession(token_hash='e'*64, user_id=recipient_id, expires_at=1000.0))
            session.add(notebook.NotebookNote(id=note_id, author_id=author_id, created_at=2.0,
                latest_revision=2, idempotency_key=str(uuid4()), request_sha256='a'*64))
            session.flush()
            for number in (1, 2):
                session.add(notebook.NotebookRevision(id=str(uuid4()), note_id=note_id, revision=number,
                    created_at=number + 1.0, content=content(title=f'Preserved {number}'),
                    content_sha256=notebook._hash(content(title=f'Preserved {number}')),
                    idempotency_key=str(uuid4()), request_sha256='b'*64))
            for identifier, floor, revoked in ((active_id, 2, None), (revoked_id, 1, 3.0)):
                session.execute(text('INSERT INTO notebook_private_shares '
                    '(id, note_id, recipient_id, from_revision, shared_at, revoked_at) '
                    'VALUES (:id, :note_id, :recipient_id, :floor, :shared_at, :revoked)'),
                    {'id': identifier, 'note_id': note_id, 'recipient_id': recipient_id,
                     'floor': floor, 'shared_at': float(floor), 'revoked': revoked})
        tables = (User.__tablename__, LoginSession.__tablename__, notebook.NotebookNote.__tablename__,
                  notebook.NotebookRevision.__tablename__, 'notebook_private_shares')
        def snapshot():
            with db.engine.connect() as connection:
                return {table: [dict(row) for row in connection.execute(text('SELECT * FROM ' + table)).mappings()]
                        for table in tables}
        before = snapshot()
        command.upgrade(config, 'head')
        command.check(config)
        after = snapshot()
        assert all(after[table] == before[table] for table in tables[:-1])
        assert [{key: value for key, value in row.items() if key not in ('state', 'response_version')}
                for row in after['notebook_private_shares']] == before['notebook_private_shares']
        assert all(row['state'] == 'accepted' and row['response_version'] == 0
                   for row in after['notebook_private_shares'])
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(sharing.PrivateNoteShareResponse)) == 0
        command.downgrade(config, '0015')
        assert snapshot() == before
    finally:
        db.engine.dispose()
