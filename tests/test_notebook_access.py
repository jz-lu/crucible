"""Notebook ownership, exact-revision sharing and beta/agent boundaries."""
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import beta_access
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import User
from crucible.notebook import NotebookNote, NotebookRevision
from crucible.security import passwords
from test_api import Actor


@pytest.fixture
def notebook_access(tmp_path):
    now = [3_600_600.0]
    ids = {name: str(uuid4()) for name in ('author', 'reviewer', 'reader')}
    settings = Settings(f"sqlite:///{tmp_path / 'notebook-access.db'}", b'n' * 32,
                        origins=('http://testserver',), access_mode='beta',
                        reviewer_ids=(ids['reviewer'],), rate_limits=False)
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    password = 'a separate notebook access fixture password'
    encoded = passwords.hash(password)
    with app.state.db.transaction(write=True) as session:
        for name, identifier in ids.items():
            session.add(User(id=identifier, handle=name, password_hash=encoded, created_at=now[0]))
        session.flush()
        for identifier in ids.values():
            beta_access.set_membership(session, identifier, 'active', now[0])
    actors = {}
    for name in ids:
        actor = Actor(app)
        response = actor.post('/api/auth/login', {'handle': name, 'password': password})
        assert response.status_code == 200, response.text
        actor.csrf = response.json()['csrf_token']
        actors[name] = actor
    yield app, now, ids, actors
    app.state.db.engine.dispose()


def content(label):
    return {'title': label, 'claim': label + ' claim', 'conditions': 'Fixture hypotheses only.',
            'body': label + ' supporting evidence', 'tags': ['access-fixture']}


def create(actor, label='private-first-revision'):
    response = actor.post('/api/notebook/notes', {'content': content(label), 'idempotency_key': str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()['note']


def revise(actor, note, label):
    response = actor.post(f"/api/notebook/notes/{note['id']}/revisions", {
        'content': content(label), 'expected_revision': note['revision'],
        'content_sha256': note['content_sha256'], 'idempotency_key': str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()['note']


def publication_body(note):
    return {'revision': note['revision'], 'content_sha256': note['content_sha256'],
            'idempotency_key': str(uuid4())}


def request_sharing(actor, note):
    response = actor.post(f"/api/notebook/notes/{note['id']}/publication-requests", publication_body(note))
    assert response.status_code == 200, response.text


def approve(actor, note):
    response = actor.post(f"/api/notebook/notes/{note['id']}/publication-reviews", {
        **publication_body(note), 'decision': 'approved', 'reason': 'Approve sharing this fixture revision only.'})
    assert response.status_code == 200, response.text


def token_for(app, actor):
    with TestClient(app) as client:
        started = client.post('/api/agent/connect/start', json={'label': 'Notebook boundary fixture'}).json()
        approved = actor.post('/api/agent/connect/' + started['user_code'] + '/approve', {})
        assert approved.status_code == 200, approved.text
        response = client.post('/api/agent/connect/poll', json={'device_code': started['device_code']})
        assert response.status_code == 200, response.text
        return response.json()['access_token']


def test_private_note_is_owner_only_even_for_configured_reviewer(notebook_access):
    app, _, _, actors = notebook_access
    author, reviewer, reader = (actors[name] for name in ('author', 'reviewer', 'reader'))
    note = create(author)
    path = '/api/notebook/notes/' + note['id']
    assert author.client.get(path).status_code == 200
    for actor in (reviewer, reader):
        assert actor.client.get(path).status_code == 404
        assert actor.client.get(path + '?revision=1').status_code == 404
        assert actor.client.get('/api/notebook/notes').json()['notes'] == []
        assert actor.client.get('/api/notebook/export').json()['notes'] == []
        response = actor.post(path + '/revisions', {
            'content': content('unauthorized'), 'expected_revision': 1,
            'content_sha256': note['content_sha256'], 'idempotency_key': str(uuid4())})
        assert response.status_code == 404
    assert reviewer.client.get('/api/notebook/review-queue').json()['notes'] == []
    assert reader.client.get('/api/notebook/review-queue').status_code == 403
    with TestClient(app) as anonymous:
        for target in (path, '/api/notebook/notes', '/api/notebook/export'):
            response = anonymous.get(target)
            assert response.status_code == 401
            assert 'private-first-revision' not in response.text
            assert response.headers['cache-control'] == 'no-store'


def test_only_approved_revision_is_shared_and_private_history_stays_hidden(notebook_access):
    _, _, _, actors = notebook_access
    author, reviewer, reader = (actors[name] for name in ('author', 'reviewer', 'reader'))
    first = create(author)
    second = revise(author, first, 'approved-second-revision')
    path = '/api/notebook/notes/' + first['id']
    request_sharing(author, second)
    assert reader.client.get(path).status_code == 404
    reviewing = reviewer.client.get(path).json()['note']
    assert [item['revision'] for item in reviewing['revisions']] == [2]
    assert reviewer.client.get(path + '?revision=1').status_code == 404
    unauthorized = reader.post(path + '/publication-reviews', {
        **publication_body(second), 'decision': 'approved', 'reason': 'No reviewer authority.'})
    assert unauthorized.status_code == 403
    approve(reviewer, second)
    revise(author, second, 'private-third-revision')
    for actor in (reader, reviewer):
        response = actor.client.get(path)
        visible = response.json()['note']
        assert visible['revision'] == 2
        assert [item['revision'] for item in visible['revisions']] == [2]
        assert visible['revision_count'] == 1
        assert 'private-first-revision' not in response.text and 'private-third-revision' not in response.text
        assert actor.client.get(path + '?revision=2').status_code == 200
        for number in (1, 3):
            assert actor.client.get(path + f'?revision={number}').status_code == 404
        listing = actor.client.get('/api/notebook/notes').json()['notes']
        assert len(listing) == 1 and listing[0]['revision'] == 2
        assert actor.client.get('/api/notebook/export').json()['notes'] == []
    assert [item['revision'] for item in author.client.get(path).json()['note']['revisions']] == [1, 2, 3]


def test_revoked_membership_removes_notebook_reads_and_writes(notebook_access):
    app, now, ids, actors = notebook_access
    author, reviewer, reader = (actors[name] for name in ('author', 'reviewer', 'reader'))
    note = create(author)
    request_sharing(author, note)
    approve(reviewer, note)
    path = '/api/notebook/notes/' + note['id']
    assert reader.client.get(path).status_code == 200
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, ids['reader'], 'revoked', now[0])
        beta_access.set_membership(session, ids['author'], 'revoked', now[0])
    for actor in (author, reader):
        for target in (path, '/api/notebook/notes', '/api/notebook/export'):
            response = actor.client.get(target)
            assert response.status_code == 401
            assert 'private-first-revision' not in response.text
    response = author.post(path + '/revisions', {
        'content': content('revoked-write'), 'expected_revision': 1,
        'content_sha256': note['content_sha256'], 'idempotency_key': str(uuid4())})
    assert response.status_code == 401
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(NotebookRevision)) == 1


@pytest.mark.parametrize('with_cookie', [False, True])
def test_existing_agent_scopes_never_inherit_notebook_authority(notebook_access, with_cookie):
    app, _, _, actors = notebook_access
    author = actors['author']
    note = create(author)
    token = token_for(app, author)
    client = author.client if with_cookie else TestClient(app)
    headers = {'Authorization': 'Bearer ' + token, 'Origin': 'http://testserver', 'X-CSRF-Token': author.csrf}
    assert client.get('/api/schemes', headers=headers).status_code == 200
    path = '/api/notebook/notes/' + note['id']
    for target in ('/api/notebook/notes', path, '/api/notebook/export', '/api/notebook/review-queue'):
        response = client.get(target, headers=headers)
        assert response.status_code == 403
        assert 'private-first-revision' not in response.text
    writes = [('/api/notebook/notes', {'content': content('agent-created'), 'idempotency_key': str(uuid4())}),
              (path + '/revisions', {'content': content('agent-revised'), 'expected_revision': 1,
                                    'content_sha256': note['content_sha256'], 'idempotency_key': str(uuid4())}),
              (path + '/publication-requests', publication_body(note)),
              ('/api/notebook/import', {'workspace': {'format': 'crucible-theory-local-v1', 'notes': [], 'views': []},
                                       'idempotency_key': str(uuid4())})]
    for endpoint, body in writes:
        assert client.post(endpoint, json=body, headers=headers).status_code == 403
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(NotebookNote)) == 1
        assert session.scalar(select(func.count()).select_from(NotebookRevision)) == 1
    if not with_cookie:
        client.close()
