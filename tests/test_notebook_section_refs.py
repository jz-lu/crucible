"""Derived labels are deterministic, revision-pinned and permission-neutral."""
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from crucible import notebook, notebook_section_refs as refs, notebook_graph
from test_statement_workflow import trial, create, revise, content, publish, pin, argument, block
from test_notebook_locs import count
from test_note_ratings import agent


def read(actor, note):
    response = actor.client.get('/api/notebook/notes/' + note['id'])
    assert response.status_code == 200, response.text
    return response.json()['note']


def test_outline_tracks_real_hierarchy_without_allocating_or_classifying_prose():
    value = content(body='## Assumption · Sampler\nText.\n#### Range\nFinite.\n## Lemma · Bound\nText.\n```\n# Fake heading\n```\n## Discussion\nAn assumption is mentioned here.')
    rows = refs.outline(value)
    assert [(r['number'], r['kind']) for r in rows] == [('1','Assumption'), ('1.1','Section'), ('2','Lemma'), ('3','Section')]
    assert len({r['key'] for r in rows}) == 4
    assert refs.outline(value) == rows


def test_section_containers_and_historical_statements_share_one_outline():
    value = content(body='## Lemma · Bound\nProof.', sections=[{'id':'explanation','title':'An explanation',
        'body':'## Experiment\nText.\n[[statement:rare-failure]]'}], statements=[block(), {'id':'bound','location':{'heading':'Lemma · Bound'}}])
    rows = refs.outline(value)
    assert [(r['number'],r['kind']) for r in rows] == [('1','Section'),('1.1','Section'),('1.1.1','Assumption'),('2','Lemma')]
    assert rows[-1]['aliases'] == ['statement:bound']


def test_get_copy_and_reordering_preserve_exact_source_without_allocation(trial):
    app, owner, reviewer, reader = trial
    saved = create(owner, content(body='## Assumption · Sampler\nExact domain.\n### Range\nFinite.\n## Lemma · Bound\nProof.'))
    shown = read(owner, saved)
    first = shown['section_refs_by_revision']['1']['passage:p1-b-1-4']
    assert first == {'number':'1','label':str(shown['number'])+'.1','kind':'Assumption','title':'Assumption · Sampler',
        'loc':str(shown['number'])+'.1@1','anchor':{'passage':'p1-b-1-4'},'used':False}
    url = '/api/notebook/locs/' + first['loc']
    assert reader.client.get(url).status_code == 404
    answer = owner.client.get(url).json()
    assert answer['reference'] == {**pin(saved), 'passage':'p1-b-1-4'}
    assert count(app) == 0
    publish(owner, reviewer, saved)
    moved = revise(owner, saved, content(body='## Lemma · Bound\nProof.\n## Assumption · Sampler\nExact domain.'))
    assert owner.client.get(url).json() == answer
    new = read(owner, moved)['section_refs_by_revision']['2']['passage:p1-b-3-4']
    assert new['loc'] == str(shown['number'])+'.2@2'
    assert owner.client.get('/api/notebook/locs/'+new['loc']).json()['reference']['revision'] == 2
    # Installation-local shorthand cannot silently retarget an imported exact link.
    assert owner.client.get(url, params={'note':saved['id'],'revision':2,'sha256':moved['content_sha256'],'passage':'p1-b-3-4'}).status_code == 404
    assert count(app) == 0 and not read(owner, saved)['section_refs_by_revision']['1']['passage:p1-b-1-4']['used']
    assert reader.client.get(url).status_code == 200
    with agent(reader, ('notes:read',)) as client:
        assert client.get(url).status_code == 200
        assert client.get('/api/agent/notes/'+saved['id']).json()['note']['section_refs_by_revision']['1']


def test_usage_is_saved_citation_not_click_and_respects_private_source(trial):
    _, owner, reviewer, reader = trial
    target = create(owner, content(body='## Assumption · Sampler\nA domain.'))
    publish(owner, reviewer, target)
    shown = read(reader, target)
    entry = shown['section_refs_by_revision']['1']['passage:p1-b-1-2']
    resolved = reader.client.get('/api/notebook/locs/' + entry['loc']).json()
    assert not read(reader, target)['section_refs_by_revision']['1']['passage:p1-b-1-2']['used']
    text = '[Assumption](%s)' % resolved['url']
    literal = create(owner, content(body='```\n'+text+'\n```'))
    assert not read(owner, target)['section_refs_by_revision']['1']['passage:p1-b-1-2']['used']
    cite = create(owner, content(body=text))
    assert read(owner, target)['section_refs_by_revision']['1']['passage:p1-b-1-2']['used']
    assert not read(reader, target)['section_refs_by_revision']['1']['passage:p1-b-1-2']['used']
    publish(owner, reviewer, cite)
    assert read(reader, target)['section_refs_by_revision']['1']['passage:p1-b-1-2']['used']
    # Exact source remains unchanged; the marker is only derived metadata.
    assert read(reader, target)['content_sha256'] == target['content_sha256']


def test_argument_dependency_marks_legacy_statement_and_read_model_uses_same_label(trial):
    app, owner, _, _ = trial
    saved = create(owner, content(body='## Setup\n[[statement:rare-failure]]',statements=[block()],arguments=[argument()]))
    shown = read(owner, saved)
    record = shown['section_refs_by_revision']['1']['statement:rare-failure']
    assert record['used'] and record['kind'] == 'Assumption' and record['number'] == '1.1'
    assert owner.client.get('/api/notebook/locs/'+record['loc']).json()['reference']['statement_id'] == 'rare-failure'
    with app.state.db.transaction() as session:
        label = refs.display_label(session,pin(saved),saved['revisions'][0]['content'],'statement:rare-failure')
        assert label['label'] == 'Assumption '+record['label']
    # Composite historical heading cannot masquerade as a prose-only passage.
    assert owner.client.get('/api/notebook/locs/'+str(shown['number'])+'.1@1').status_code == 404


@pytest.mark.parametrize('value',['0.1@1','56.0@1','56.01@1','56.2','56.2@0','56.2@101','2147483648.2@1','S.56.2@1'])
def test_invalid_short_references(value):
    assert refs.parse(value) is None
