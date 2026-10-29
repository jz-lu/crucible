"""Agent-only support, exact evidence, one account vote and live author credit."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, inspect, MetaData, select, text
from sqlalchemy.dialects import postgresql

from crucible import beta_access, machine_auth, notebook, note_ratings, scoring
from crucible.security import token_hash
from test_notebook import system, create, content, revise, request_publication, approve, publication_body, count
from test_notebook_browse import curated, seed_notes


def agent(actor, scopes=("notes:read", "notes:rate"), *, expired=False, revoked=False):
    raw = "cru_" + secrets.token_urlsafe(32)
    now = actor.app.state.clock()
    with actor.app.state.db.transaction(write=True) as session:
        session.add(machine_auth.MachineToken(id=str(uuid4()), user_id=actor.identifier, token_hash=token_hash(raw),
            label="Synthetic note rater", scopes=list(scopes), created_at=now,
            expires_at=now - 1 if expired else now + 3600, revoked_at=now if revoked else None))
    return TestClient(actor.app, headers={"Authorization": "Bearer " + raw})


def publish(owner, reviewer, saved):
    request_publication(owner, saved)
    approve(reviewer, saved)
    return saved


def vote_body(saved, value=1, version=0):
    return {"value": value, "revision": saved["revision"], "content_sha256": saved["content_sha256"], "expected_version": version}


def vote(client, saved, value=1, version=0):
    return client.post(f'/api/agent/notes/{saved["id"]}/rating', json=vote_body(saved, value, version))


def read(client, saved, **extra):
    response = client.get(f'/api/agent/notes/{saved["id"]}', params=extra)
    assert response.status_code == 200, response.text
    return response.json()["note"]


def points(actor):
    return actor.client.get('/api/people/author?include_milestones=false').json()['person']


def test_one_positive_rating_per_account_changes_and_removes_author_points(system):
    app, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner, content(kind="Review and synthesis")))
    a, same_account, b = agent(reader), agent(reader), agent(reviewer)
    assert read(a, saved)["rating"]["your_rating"] == {"value": 0, "version": 0}
    first = vote(a, saved)
    assert first.status_code == 200, first.text
    assert first.json()["rating"]["score"] == 1
    assert vote(same_account, saved).json() == first.json()
    assert count(app, note_ratings.NoteRating) == 1
    assert points(owner)["points"] == 1
    assert points(owner)["score_breakdown"]["note_contributions"] == 1
    assert points(owner)["accepted_proposal_count"] == 0
    assert vote(a, saved, 2, 1).json()["rating"]["score"] == 2
    assert vote(b, saved, 2).json()["rating"]["score"] == 4
    state = read(a, saved)["rating"]
    assert (state["raters"], state["upvotes"], state["double_upvotes"]) == (2, 0, 2)
    assert points(owner)["points"] == 4
    assert vote(same_account, saved, 1).status_code == 409
    assert vote(a, saved, 0, 2).json()["rating"]["score"] == 2
    assert points(owner)["points"] == 2
    assert vote(a, saved, 0, 2).status_code == 200  # lost-response retry
    assert vote(a, saved, 2, 1).status_code == 409  # old retry cannot restore support
    assert vote(a, saved, 1, 3).json()["rating"]["score"] == 3
    assert count(app, note_ratings.NoteRating) == 2
    assert vote(agent(owner), saved, 2).status_code == 403
    exported = owner.client.get('/api/notebook/export').json()
    assert "rating" not in json.dumps(exported) and "score" not in json.dumps(exported)


def test_revisions_do_not_multiply_votes_or_inherit_exact_revision_endorsements(system):
    _, owner, reviewer, reader, _ = system
    first = publish(owner, reviewer, create(owner))
    a = agent(reader)
    assert vote(a, first, 2).status_code == 200
    second = revise(owner, first, content(title="A changed argument"))
    assert read(a, first)["revision"] == 1
    assert a.get(f'/api/agent/notes/{first["id"]}', params={"revision": 2}).status_code == 404
    assert vote(a, second, 1, 1).status_code == 404
    publish(owner, reviewer, second)
    current = read(a, first)
    assert current["revision"] == 2
    assert current["rating"]["score"] == 2
    assert current["rating"]["selected_revision"]["score"] == 0
    assert current["rating"]["your_rating"]["revision"] == 1
    assert vote(a, second, 1, 1).json()["rating"]["score"] == 1
    assert read(a, first, revision=1)["rating"]["selected_revision"]["score"] == 0
    assert points(owner)["points"] == 1
    assert vote(a, {**first, "content_sha256": "f" * 64}, 2, 2).status_code == 404


def test_private_pending_and_withdrawn_notes_cannot_be_rated_or_earn_credit(system):
    _, owner, reviewer, reader, _ = system
    saved = create(owner, content(title="PRIVATE_NOTE"))
    a = agent(reader)
    for phase in range(2):
        for client in (a, agent(owner), agent(reviewer)):
            assert client.get(f'/api/agent/notes/{saved["id"]}').status_code == 404
            assert vote(client, saved).status_code == 404
            listing = client.get('/api/agent/notes').json()
            assert saved["id"] not in json.dumps(listing) and "PRIVATE_NOTE" not in json.dumps(listing)
        if phase == 0:
            request_publication(owner, saved)
    approve(reviewer, saved)
    assert vote(a, saved, 2).status_code == 200
    withdrawal = reviewer.post(f'/api/notebook/notes/{saved["id"]}/publication-withdrawals',
        publication_body(saved, reason="Restricted privacy explanation"))
    assert withdrawal.status_code == 200, withdrawal.text
    assert points(owner)["points"] == 0
    assert a.get(f'/api/agent/notes/{saved["id"]}').status_code == 404
    assert vote(a, saved, 1, 1).status_code == 404
    assert saved["id"] not in a.get('/api/agent/notes').text
    cleared = vote(a, saved, 0, 1)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['rating']['score'] == 0
    assert 'Restricted privacy explanation' not in cleared.text
    assert vote(a, saved, 0, 1).status_code == 200


@pytest.mark.parametrize('value', [-1, 3, True, 1.0, "2", None])
def test_rating_values_are_strict_positive_levels_or_clear(system, value):
    _, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner))
    assert vote(agent(reader), saved, value).status_code == 422


def test_cookies_old_tokens_readonly_tokens_and_revoked_tokens_cannot_vote(system):
    _, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner))
    url = f'/api/agent/notes/{saved["id"]}/rating'
    assert reader.post(url, vote_body(saved)).status_code == 403
    assert reader.client.get('/api/agent/notes').status_code == 403
    for scopes in (machine_auth.LEGACY_SCOPES, ("proposals:read", "proposals:write"), ("notes:read",)):
        client = agent(reader, scopes)
        assert client.post(url, json=vote_body(saved)).status_code == 403
    for options in ({"revoked": True}, {"expired": True}):
        client = agent(reader, **options)
        assert client.get('/api/agent/notes').status_code == 401
        assert vote(client, saved).status_code == 401
    client = agent(reader)
    for path in ('/api/notebook/notes', '/api/notebook/export', '/map/notes.json'):
        assert client.get(path).status_code == 403
    assert client.post('/api/notebook/notes', json={}).status_code == 403
    assert client.post(f'/api/notebook/notes/{saved["id"]}/publication-reviews', json={}).status_code == 403


def test_suspended_voters_lose_influence_and_write_access(system):
    app, owner, reviewer, reader, now = system
    saved = publish(owner, reviewer, create(owner))
    client = agent(reader)
    assert vote(client, saved, 2).status_code == 200
    with app.state.db.transaction(write=True) as session:
        session.add(beta_access.AccountState(user_id=reader.identifier, suspended_at=now[0], reason="Fixture"))
    assert points(owner)["points"] == 0
    assert read(agent(reviewer), saved)["rating"]["score"] == 0
    assert vote(client, saved, 1, 1).status_code == 403


def test_concurrent_agents_of_one_account_cannot_stack_votes(system):
    app, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner))
    a, b = agent(reader), agent(reader)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: vote(pair[0], saved, pair[1]), [(a, 1), (b, 2)]))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert count(app, note_ratings.NoteRating) == 1
    assert read(a, saved)["rating"]["raters"] == 1
    assert points(owner)["points"] in (1, 2)


def test_curated_ratings_have_no_invented_author_and_validate_bundled_history(system, curated):
    _, owner, _, reader, _ = system
    client = agent(reader)
    saved = {"id": "curated:curated-example-000"}
    record = read(client, saved)
    assert record["author"] is None
    assert vote(client, record, 2).json()["rating"]["score"] == 2
    assert points(owner)["points"] == 0
    assert vote(client, {**record, "content_sha256": "f" * 64}, 1, 1).status_code == 404
    assert vote(client, {**record, "id": "curated:missing"}, 1, 1).status_code == 404
    assert client.get('/api/agent/notes').json()["notes"][0]["id"] == saved["id"]


def test_global_ranking_paginates_account_and_curated_notes_before_hydration(system, curated):
    app, owner, reviewer, reader, _ = system
    records = seed_notes(owner, 62)
    with app.state.db.transaction(write=True) as session:
        for row in session.scalars(select(notebook.NotebookRevision)):
            session.add(notebook.NotebookPublication(id=str(uuid4()), revision_id=row.id, author_id=owner.identifier,
                requested_at=1, idempotency_key=str(uuid4()), request_sha256="a"*64, decision="approved",
                reviewer_id=reviewer.identifier, reviewed_at=2, reason="Fixture", test_only_self_review=False))
    client = agent(reader)
    assert vote(client, records[0], 2).status_code == 200
    curated_note = read(client, {"id": "curated:curated-example-000"})
    assert vote(client, curated_note).status_code == 200
    statements = []
    def observe(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(app.state.db.engine, "before_cursor_execute", observe)
    try:
        found, after = [], None
        for _ in range(20):
            response = client.get('/api/agent/notes', params={"limit": 7, **({"after": after} if after else {})})
            assert response.status_code == 200, response.text
            page = response.json()
            found.extend(page["notes"])
            after = page["next_after"]
            if after is None:
                break
        assert len(found) == len({row['id'] for row in found}) == 69
        assert [row['id'] for row in found[:2]] == [records[0]['id'], curated_note['id']]
        assert all(set(row['content']) <= {'title', 'claim', 'tags', 'kind'} for row in found)
        assert all(' notebook_revisions.content,' not in statement for statement in statements)
        assert any('LIMIT' in statement and 'score' in statement for statement in statements)
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", observe)
    recent = client.get('/api/agent/notes', params={"order": "recent", "limit": 1}).json()
    assert recent['notes'][0]['id'] == records[-1]['id']
    assert client.get('/api/agent/notes', params={"after": recent['next_after']}).status_code == 422
    assert client.get('/api/agent/notes', params={"after": "bad-cursor"}).status_code == 422
    filtered = client.get('/api/agent/notes', params={"q": "Curated match", "tag": "curated-topic"}).json()
    assert len(filtered['notes']) == 7
    assert client.get(found[0]['read_url']).status_code == 200
    # Human notebook has neither vote totals nor a vote-based ordering.
    human = reader.client.get('/api/notebook/notes').json()
    assert 'rating' not in json.dumps(human)
    assert human['notes'][0]['id'] == records[-1]['id']


def test_queries_compile_for_postgresql():
    for query in (note_ratings.scores(), note_ratings.author_points(), select(scoring.score_relations('postgresql')[0])):
        sql = str(query.compile(dialect=postgresql.dialect()))
        assert 'note_ratings' in sql and 'notebook_publication_withdrawals' in sql


def test_development_self_review_never_enters_the_agent_pool_or_earns_points(system):
    app, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner))
    client = agent(reader)
    assert vote(client, saved, 2).status_code == 200
    with app.state.db.transaction(write=True) as session:
        publication = session.scalar(select(notebook.NotebookPublication))
        publication.test_only_self_review = True
    assert client.get(f'/api/agent/notes/{saved["id"]}').status_code == 404
    assert points(owner)['points'] == 0


def test_note_scopes_do_not_bypass_beta_membership(system):
    from dataclasses import replace
    from types import SimpleNamespace
    from crucible.app import create_app
    app, owner, reviewer, reader, now = system
    saved = publish(owner, reviewer, create(owner))
    guarded = create_app(replace(app.state.settings, access_mode='beta'), clock=lambda: now[0])
    try:
        client = agent(SimpleNamespace(app=guarded, identifier=reader.identifier))
        assert client.get('/api/agent/notes').status_code == 403
        assert vote(client, saved).status_code == 403
        with guarded.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, reader.identifier, 'active', now[0])
        assert vote(client, saved, 2).status_code == 200
        with guarded.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, reader.identifier, 'revoked', now[0])
        assert client.get('/api/agent/notes').status_code == 401
        assert vote(client, saved, 1, 1).status_code == 401
    finally:
        guarded.state.db.engine.dispose()


def test_cli_reads_exact_content_and_retries_the_identical_rating_without_stacking(system):
    from scripts import crucible_cli as cli
    _, owner, reviewer, reader, _ = system
    saved = publish(owner, reviewer, create(owner, content(body='Evidence with Unicode: λ, β.')))
    client = agent(reader)
    class Bridge:
        writes = []
        lose_response = True
        corrupt = False
        def request(self, path, body=None):
            response = client.post(path, json=body) if body is not None else client.get(path)
            assert response.status_code == 200, response.text
            result = response.json()
            if body is not None:
                self.writes.append((path, body))
                if self.lose_response:
                    self.lose_response = False
                    raise cli.ClientError('Lost response', 'network_error')
            elif self.corrupt and 'note' in result:
                result['note']['content']['body'] = 'Altered bytes'
            return result
    bridge = Bridge()
    def run(*args):
        return cli.notes_command(bridge, cli.parser().parse_args(['notes', *map(str, args)]))
    page = run('list', '--order', 'priority', '--search', 'conditional')
    assert saved['id'] in [row['id'] for row in page['notes']]
    record = run('show', saved['id'], '--revision', 1, '--sha256', saved['content_sha256'])['note']
    assert record['content']['body'].endswith('λ, β.')
    args = ('rate', saved['id'], 2, '--revision', 1, '--sha256', saved['content_sha256'], '--expected-version', 0)
    with pytest.raises(cli.ClientError, match='Lost response'):
        run(*args)
    assert run(*args)['rating']['score'] == 2
    assert bridge.writes[0] == bridge.writes[1]
    assert points(owner)['points'] == 2
    bridge.corrupt = True
    with pytest.raises(cli.ClientError, match='invalid content hash'):
        run('show', saved['id'])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['notes', 'rate', saved['id'], '2'])
    with pytest.raises(cli.ClientError, match='full note UUID'):
        run('show', '../../private')


def test_model_guidance_explains_high_bar_reviews_and_real_point_effects():
    root = Path(__file__).resolve().parents[1]
    pipeline = json.loads((root / 'web/agent/pipeline.json').read_text())
    rule = next(row for row in pipeline['rules'] if row['id'] == 'notes.support-ratings')
    assert 'Reviews, synthesis' in rule['instruction'] and '+1/+2 author points' in rule['instruction']
    guide = (root / 'web/map/NOTE-FORMAT.txt').read_text()
    for text in ('every research agent should read', 'Reviews and synthesis', 'expected_version',
                 'notes:rate', 'selected_revision', 'not a snapshot', 'human notebook', 'crucible-points-v11'):
        assert text in guide


def test_additive_migration_preserves_prior_records(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[1]
    for name in tuple(os.environ):
        if name == 'DATABASE_URL' or name.startswith('CRUCIBLE_'):
            monkeypatch.delenv(name)
    database_url = 'sqlite:///' + str(tmp_path / 'historical.db')
    monkeypatch.setenv('DATABASE_URL', database_url)
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CRUCIBLE_SECRET', base64.b64encode(b'r' * 32).decode())
    monkeypatch.setenv('CRUCIBLE_ENV', 'development')
    monkeypatch.setenv('CRUCIBLE_ACCESS_MODE', 'open')
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    # Build an actual historical schema. Stamping today's metadata as 0018
    # leaves later tables behind and cannot exercise a genuine upgrade.
    command.upgrade(config, '0018')
    engine = create_engine(database_url)
    try:
        metadata = MetaData()
        metadata.reflect(engine)
        assert 'note_ratings' not in metadata.tables
        assert 'implementation_builds' not in metadata.tables
        owner_id, note_id, revision_id = (str(uuid4()) for _ in range(3))
        value = content(title='Preserved historical note')
        digest = notebook._hash(value)
        with engine.begin() as connection:
            connection.execute(metadata.tables['users'].insert(), dict(id=owner_id,
                handle='historical_author', password_hash='synthetic', created_at=1.0))
            connection.execute(metadata.tables['notebook_notes'].insert(), dict(id=note_id,
                author_id=owner_id, created_at=2.0, latest_revision=1,
                idempotency_key=str(uuid4()), request_sha256='a' * 64))
            connection.execute(metadata.tables['notebook_revisions'].insert(), dict(id=revision_id,
                note_id=note_id, revision=1, created_at=2.0, content=value,
                content_sha256=digest, idempotency_key=str(uuid4()), request_sha256='b' * 64))
            tables = [table for name, table in metadata.tables.items() if name != 'alembic_version']
            before = {table.name: connection.execute(select(table).order_by(*table.primary_key)).all()
                      for table in tables}
        command.upgrade(config, 'head')
        with engine.connect() as connection:
            assert {table.name: connection.execute(select(table).order_by(*table.primary_key)).all()
                    for table in tables} == before
            assert 'note_ratings' in inspect(connection).get_table_names()
            assert connection.execute(text('SELECT count(*) FROM note_ratings')).scalar_one() == 0
        command.upgrade(config, 'head')
        command.check(config)
    finally:
        engine.dispose()
