"""A short loc names one exact source, never grants access or changes authored bytes."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit, urlencode

import pytest
from sqlalchemy import func, select

from crucible import notebook, notebook_graph, notebook_locs
from test_statement_workflow import trial, create, revise, content, publish, pin, context
from test_note_ratings import agent


def source(owner):
    return create(owner, content(body='## Premise\nA precise premise.\n### Scope\nA limited domain.\n## Result\nA separate conclusion.'))


def target(note, passage='p1-b-1-4'):
    return {**pin(note), 'passage': passage}


def allocate(actor, note, passage='p1-b-1-4'):
    response = actor.post('/api/notebook/locs', target(note, passage))
    assert response.status_code == 200, response.text
    return response.json()


def count(app):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(notebook_locs.NotebookLoc))


def test_reads_do_not_allocate_and_citation_preserves_authored_source(trial):
    app, owner, reviewer, reader = trial
    note = source(owner)
    before = owner.client.get('/api/notebook/notes/' + note['id'] + '?include_passages=true').json()['note']
    assert before['passage_locs'] == {} and count(app) == 0
    assert reader.post('/api/notebook/locs', target(note)).status_code == 404
    publish(owner, reviewer, note)
    # Someone other than the author can give an existing passage its loc.
    saved = allocate(reader, note)
    assert saved['loc'] == 'P.1' and saved['reference'] == target(note)
    assert parse_qs(urlsplit(saved['url']).fragment.split('?')[1]) == {
        'loc':['P.1'],'note':[note['id']], 'revision':['1'], 'sha256':[note['content_sha256']], 'passage':['p1-b-1-4']}
    assert saved['passage']['definition'] == '## Premise\nA precise premise.\n### Scope\nA limited domain.'
    assert allocate(owner, note) == saved and count(app) == 1
    after = owner.client.get('/api/notebook/notes/' + note['id']).json()['note']
    assert after['revisions'][0]['content'] == before['revisions'][0]['content']
    assert after['content_sha256'] == before['content_sha256']
    assert after['passage_locs'] == {'p1-b-1-4': 'P.1'}
    assert reader.client.get('/api/notebook/locs/P.1').json() == saved


def test_loc_stays_at_old_revision_even_when_heading_moves_or_is_identical(trial):
    _, owner, reviewer, reader = trial
    note = source(owner); saved = allocate(owner, note)
    new = revise(owner, note, content(body='Intro changed.\n## Premise\nA precise premise.\n### Scope\nA limited domain.\n## Result\nA separate conclusion.'))
    assert owner.client.get('/api/notebook/locs/' + saved['loc']).json()['reference'] == target(note)
    next_loc = allocate(owner, new, 'p1-b-2-5')
    assert next_loc['loc'] != saved['loc']
    assert reader.client.get('/api/notebook/locs/' + saved['loc']).status_code == 404
    publish(owner, reviewer, new)
    assert reader.client.get('/api/notebook/locs/' + next_loc['loc']).status_code == 200
    assert reader.client.get('/api/notebook/locs/' + saved['loc']).status_code == 404


def test_loc_scopes_csrf_and_invalid_ranges(trial):
    app, owner, reviewer, reader = trial
    note = source(owner); saved = allocate(owner, note)
    assert owner.client.post('/api/notebook/locs', json=target(note)).status_code == 403
    assert owner.post('/api/notebook/locs', {**target(note), 'content_sha256': '0'*64}).status_code == 409
    assert owner.post('/api/notebook/locs', target(note, 'p1-b-1-3')).status_code == 422
    assert count(app) == 1
    with agent(owner, ('notes:read',)) as scoped:
        assert scoped.get('/api/notebook/locs/' + saved['loc']).status_code == 404
        assert scoped.post('/api/notebook/locs', json=target(note)).status_code == 403
    with agent(owner, ('notes:read', 'notebook:read', 'notebook:write')) as scoped:
        assert scoped.get('/api/notebook/locs/' + saved['loc']).status_code == 200
        assert scoped.post('/api/notebook/locs', json=target(note)).status_code == 200
    publish(owner, reviewer, note)
    with agent(reader, ('notes:read',)) as scoped:
        assert scoped.get('/api/notebook/locs/' + saved['loc']).status_code == 200
    with agent(reader, ('notebook:write',)) as scoped:
        assert scoped.post('/api/notebook/locs', json=target(note)).status_code == 404
    with agent(reader, ('notebook:write', 'notes:read')) as scoped:
        assert scoped.post('/api/notebook/locs', json=target(note)).status_code == 200


@pytest.mark.parametrize('loc', ['P.0', 'P.01', 'A.1', 'P.2147483648', 'P.99999999999', 'P.-1'])
def test_unknown_or_malformed_loc_reveals_nothing(trial, loc):
    _, owner, _, _ = trial
    assert owner.client.get('/api/notebook/locs/' + loc).status_code == 404


def test_loc_allocation_races_are_idempotent(trial):
    app, owner, _, _ = trial
    note = source(owner)
    def reserve(_):
        with app.state.db.transaction(write=True) as session:
            return notebook_locs.ensure(session, notebook_locs.PassagePin(**target(note))).number
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(reserve, range(12)))) == 1
    assert count(app) == 1


def test_loc_survives_deletion_as_unavailable_and_is_never_reused(trial):
    app, owner, _, reader = trial
    note = source(owner); saved = allocate(owner, note)
    result = owner.post('/api/notebook/notes/' + note['id'] + '/delete', {
        'revision':note['revision'], 'content_sha256':note['content_sha256'], 'permanent':True, 'idempotency_key':str(uuid4())})
    assert result.status_code == 200, result.text
    for actor in (owner, reader):
        assert actor.client.get('/api/notebook/locs/' + saved['loc']).status_code == 404
    assert count(app) == 1
    next_loc = allocate(owner, source(owner))
    assert next_loc['loc'] != saved['loc']


def test_short_links_index_exact_source_and_normalized_ref_resolves_as_dependency(trial):
    app, owner, reviewer, reader = trial
    note = source(owner); publish(owner, reviewer, note); saved = allocate(reader, note)
    dependent = create(owner, content(references=[{'title':'Source premise','url':saved['url'],'relation':'responds_to'}],
        arguments=[{'id':'conditional-result','title':'A conditional result','conclusion':'Result.',
            'scope':'The same limited domain.', 'status':'conditional',
            'dependencies':[{'statement':saved['reference'], 'use':'Uses the specified premise.'}]}]))
    with app.state.db.transaction() as session:
        row = session.scalar(select(notebook_graph.NotebookReference))
        assert (row.target_note_id, row.target_revision, row.target_sha256) == (note['id'], note['revision'], note['content_sha256'])
    publish(owner, reviewer, dependent)
    result = reader.client.get(context(dependent, 'argument', 'conditional-result'))
    assert result.status_code == 200, result.text
    assert result.json()['argument']['dependencies'][0]['resolved_statement']['source'] == pin(note)
    related = reader.client.get(context(note, 'passage', 'p1-b-1-4'))
    assert related.status_code == 200, related.text
    assert related.json()['related_research'][0]['source'] == pin(dependent)


def test_exported_loc_cannot_retarget_another_installations_same_number(tmp_path):
    from test_notebook import import_workspace
    fixtures = []
    try:
        for name in ('first', 'second'):
            directory = tmp_path / name; directory.mkdir()
            fixture = trial.__wrapped__(directory); fixtures.append(fixture)
        a, b = [next(fixture) for fixture in fixtures]
        first = source(a[1]); original = allocate(a[1], first)
        cited = create(a[1], content(references=[{'title':'Exact premise','url':original['url'],'relation':'supports'}]))
        archive = a[1].client.get('/api/notebook/export', params={'note_id':cited['id']}).json()
        second = source(b[1]); unrelated = allocate(b[1], second)
        assert original['loc'] == unrelated['loc'] == 'P.1'
        imported = import_workspace(b[1], archive)
        assert imported.status_code == 200, imported.text
        with b[0].state.db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(notebook_graph.NotebookReference)) == 0
            assert list(notebook_graph.references({'references':[{'url':'/#notebook?loc=P.1'}]}, session)) == []
        params = {key:value[0] for key,value in parse_qs(urlsplit(original['url']).fragment.split('?')[1]).items() if key!='loc'}
        assert b[1].client.get('/api/notebook/locs/P.1', params=params).status_code == 404
        assert b[1].client.get('/api/notebook/locs/P.1').json()['reference'] == target(second)
    finally:
        for fixture in fixtures:
            fixture.close()


def test_cli_allocates_resolves_and_lists_passages(trial):
    from scripts import crucible_cli as cli
    _, owner, reviewer, _ = trial
    note = source(owner); publish(owner, reviewer, note)
    with agent(owner, ('notes:read','notebook:write')) as scoped:
        class Bridge:
            def request(self, path, body=None):
                result = scoped.post(path, json=body) if body is not None else scoped.get(path)
                assert result.status_code == 200, result.text
                return result.json()
        def run(*args):
            return cli.notes_command(Bridge(), cli.parser().parse_args(['notes', *map(str,args)]))
        assert run('show', note['id'], '--passages')['note']['passages'][0]['id'] == 'p1-b-1-4'
        result = run('cite', note['id'], '--revision',1,'--sha256',note['content_sha256'],'--passage','p1-b-1-4')
        assert run('resolve', result['loc']) == result
        with pytest.raises(cli.ClientError):
            run('resolve', result['loc'], '--revision',2)
