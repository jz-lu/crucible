"""Research packets freeze evidence without granting publication or admission."""
from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, func
from crucible import notebook, research_reviews as reviews
from crucible.app import create_app
from crucible.db import User
from test_notebook import system, Actor, create, revise, content, request_publication, approve


def request_body(note, **overrides):
    return {'purpose':'source_check','title':'Check the source for this conditional claim',
            'question':'Check the cited lemma and identify the missing hypotheses.',
            'sources':[{'note_id':note['id'],'revision':note['revision'],'content_sha256':note['content_sha256']}],
            'idempotency_key':str(uuid4()), **overrides}


def submit(actor, note, **overrides):
    response=actor.post('/api/notebook/research-requests',request_body(note,**overrides))
    assert response.status_code == 200, response.text
    return response.json()['request']


def decision(record, **overrides):
    return {'packet_sha256':record['packet_sha256'],'outcome':'recommend',
            'checks':{'sources':'checked','mathematics':'not_checked','reproduction':'not_checked'},
            'findings':'Checked the cited source statement; proof not verified.',
            'limitations':'No implementation or independent proof review.',
            'idempotency_key':str(uuid4()), **overrides}


def legacy_technical_request(app, actor, note, created_at):
    """Seed an existing packet through the same frozen-source path as old releases."""
    body = request_body(note, purpose='technical_review')
    identifier = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        user = session.get(User, actor.identifier)
        session.add(reviews.ResearchRequest(id=identifier, author_id=user.id, created_at=created_at,
            purpose=body['purpose'], title=body['title'], question=body['question'],
            sources=[reviews.source_snapshot(session, user, reviews.SourceRef(**ref)) for ref in body['sources']],
            idempotency_key=body['idempotency_key'],
            request_sha256=notebook._hash({key: value for key, value in body.items() if key != 'idempotency_key'})))
    result = actor.client.get('/api/notebook/research-requests/' + identifier)
    assert result.status_code == 200, result.text
    return result.json()['request']


def test_retired_technical_requests_cannot_be_created_or_modify_notes(system):
    app, owner, _, _, _ = system
    note = create(owner)
    before = owner.client.get('/api/notebook/notes/' + note['id']).json()
    result = owner.post('/api/notebook/research-requests', request_body(note, purpose='technical_review'))
    assert result.status_code == 422
    assert owner.client.get('/api/notebook/notes/' + note['id']).json() == before
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(reviews.ResearchRequest)) == 0
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication)) == 0


def test_pending_research_queue_serves_oldest_requests_before_random_identifiers(system):
    app, owner, reviewer, _, now = system
    records = sorted([submit(owner, create(owner)) for _ in range(3)], key=lambda row: row['id'])
    with app.state.db.transaction(write=True) as session:
        for index, record in enumerate(records):
            session.get(reviews.ResearchRequest, record['id']).created_at = now[0] - index
    identifiers, cursor = [], None
    for _ in records:
        page = reviewer.client.get('/api/notebook/research-requests', params={
            'review': 'true', 'limit': 1, **({'after': cursor} if cursor else {})}).json()
        identifiers.extend(record['id'] for record in page['requests'])
        cursor = page['next_after']
    assert identifiers == [record['id'] for record in reversed(records)]
    assert cursor is None


def test_retired_technical_history_is_private_frozen_and_not_pending(system):
    app, owner, reviewer, reader, now = system
    note = create(owner, content(title='Preserved private evidence'))
    legacy = legacy_technical_request(app, owner, note, now[0])
    active = [submit(owner, note), submit(owner, note, purpose='map_nomination')]
    revise(owner, note, content(title='Later editable revision'))
    before = owner.client.get('/api/notebook/notes/' + note['id']).json()
    endpoint = '/api/notebook/research-requests/' + legacy['id']
    for actor in (owner, reviewer):
        record = actor.client.get(endpoint).json()['request']
        assert record['retired'] is True and record['can_review'] is False
        assert record['sources'] == legacy['sources']
        assert record['sources'][0]['content']['title'] == 'Preserved private evidence'
        assert record['assessment'] is None
    assert reader.client.get(endpoint).status_code == 404
    assert reader.client.get('/api/notebook/research-requests').json()['requests'] == []
    history = owner.client.get('/api/notebook/research-requests').json()['requests']
    assert next(record for record in history if record['id'] == legacy['id'])['retired'] is True
    pending, cursor = [], None
    for _ in range(3):
        page = reviewer.client.get('/api/notebook/research-requests', params={
            'review': 'true', 'limit': 1, **({'after': cursor} if cursor else {})}).json()
        pending.extend(page['requests'])
        cursor = page['next_after']
        if cursor is None:
            break
    assert {record['id'] for record in pending} == {record['id'] for record in active}
    assert all(record['retired'] is False and record['can_review'] is True for record in pending)
    result = reviewer.post(endpoint + '/assessments', decision(legacy))
    assert result.status_code == 409 and 'retired' in result.json()['detail']
    assert owner.client.get('/api/notebook/notes/' + note['id']).json() == before
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(reviews.ResearchAssessment)) == 0
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication)) == 0


def test_retired_assessments_remain_readable_but_cannot_be_replayed_or_replaced(system):
    app, owner, reviewer, reader, now = system
    note = create(owner)
    legacy = legacy_technical_request(app, owner, note, now[0])
    body = decision(legacy)
    with app.state.db.transaction(write=True) as session:
        session.add(reviews.ResearchAssessment(request_id=legacy['id'], reviewer_id=reviewer.identifier,
            created_at=now[0], outcome=body['outcome'], checks=body['checks'], findings=body['findings'],
            limitations=body['limitations'], test_only_self_review=False,
            idempotency_key=body['idempotency_key'], request_sha256=notebook._hash({
                'request_id': legacy['id'], **{key: value for key, value in body.items() if key != 'idempotency_key'}})))
    endpoint = '/api/notebook/research-requests/' + legacy['id']
    before = owner.client.get(endpoint).json()['request']
    assert before['retired'] is True and before['can_review'] is False
    assert before['assessment']['findings'] == body['findings']
    assert reviewer.client.get(endpoint).json()['request']['assessment'] == before['assessment']
    assert reader.client.get(endpoint).status_code == 404
    for payload in (body, decision(legacy)):
        result = reviewer.post(endpoint + '/assessments', payload)
        assert result.status_code == 409 and 'retired' in result.json()['detail']
    assert owner.client.get(endpoint).json()['request'] == before
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(reviews.ResearchAssessment)) == 1


def test_frozen_private_sources_visible_only_to_requester_and_reviewer(system):
    app, owner, reviewer, reader, _ = system
    first=create(owner,content(title='Exact private claim'))
    record=submit(owner,first)
    revise(owner,first,content(title='Later private draft'))
    endpoint='/api/notebook/research-requests/'+record['id']
    result=reviewer.client.get(endpoint).json()['request']
    assert result['sources'][0]['content']['title']=='Exact private claim'
    assert 'Later private draft' not in json.dumps(result)
    assert reader.client.get(endpoint).status_code==404
    assert reader.client.get('/api/notebook/research-requests').json()['requests']==[]
    assert reviewer.client.get('/api/notebook/research-requests?review=true').json()['requests'][0]['id']==record['id']
    assert reader.client.get('/api/notebook/research-requests?review=true').status_code==403
    assert reviewer.client.get('/api/notebook/notes/'+first['id']).status_code==404
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication))==0


def test_cannot_nominate_another_authors_private_note_even_as_reviewer(system):
    _, owner, reviewer, reader, _ = system
    private=create(owner)
    for actor in (reviewer,reader):
        assert actor.post('/api/notebook/research-requests',request_body(private)).status_code==404
    request_publication(owner,private);approve(reviewer,private)
    record=submit(reader,private)
    assert record['sources'][0]['author_id']==owner.identifier


def test_exact_hash_and_idempotent_submission(system):
    _, owner, *_=system
    note=create(owner);body=request_body(note)
    response=owner.post('/api/notebook/research-requests',body)
    record=response.json()['request']
    assert owner.post('/api/notebook/research-requests',body).json()['request']['id']==record['id']
    assert owner.post('/api/notebook/research-requests',{**body,'question':'Changed'}).status_code==409
    wrong=request_body(note);wrong['sources'][0]['content_sha256']='0'*64
    assert owner.post('/api/notebook/research-requests',wrong).status_code==409
    duplicate=request_body(note);duplicate['sources']*=2
    assert owner.post('/api/notebook/research-requests',duplicate).status_code==422


def test_curated_literature_can_support_a_request_without_becoming_a_scheme(system):
    _, owner, *_=system
    collection=json.loads((Path(__file__).resolve().parents[1]/'web/map/notes.json').read_text())
    source=next(n for n in collection['notes'] if n['id']=='literature-high-corruption-csp')
    revision=source['revisions'][0]
    c={k:v for k,v in revision.items() if k in notebook.CONTENT_FIELDS}
    record=submit(owner,{'id':'curated:'+source['id'],'revision':1,'content_sha256':notebook._hash(c)},purpose='source_check')
    assert record['sources'][0]['origin']=='curated-research'
    assert record['sources'][0]['content']==c
    assert record['assessment'] is None
    assert not record['can_review']


def test_only_configured_reviewer_can_record_immutable_scoped_assessment(system):
    app,owner,reviewer,reader,_=system
    record=submit(owner,create(owner),purpose='map_nomination')
    endpoint='/api/notebook/research-requests/'+record['id']+'/assessments'
    body=decision(record)
    for actor in (owner,reader):
        assert actor.post(endpoint,body).status_code==403
    assert reviewer.post(endpoint,{**body,'packet_sha256':'0'*64}).status_code==409
    assert reviewer.post(endpoint,{**body,'checks':{**body['checks'],'sources':'not_checked'}}).status_code==422
    result=reviewer.post(endpoint,body)
    assert result.status_code==200,result.text
    assessment=result.json()['request']['assessment']
    assert assessment['reviewer']['handle']=='reviewer'
    assert assessment['checks']['mathematics']=='not_checked'
    assert assessment['test_only_self_review'] is False
    assert reviewer.post(endpoint,body).status_code==200
    assert reviewer.post(endpoint,decision(record)).status_code==409
    assert reviewer.client.get('/api/notebook/research-requests?review=true').json()['requests']==[]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(reviews.ResearchAssessment))==1
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication))==0


def test_self_review_cannot_become_independent_by_switching_development_off(system):
    app,owner,reviewer,reader,now=system
    note=create(reviewer)
    record=submit(reviewer,note)
    endpoint='/api/notebook/research-requests/'+record['id']+'/assessments'
    assert reviewer.post(endpoint,decision(record)).status_code==403
    dev=create_app(replace(app.state.settings,allow_self_review=True),clock=lambda:now[0])
    try:
        result=Actor(dev,reviewer.identifier).post(endpoint,decision(record))
        assert result.status_code==200,result.text
        assert result.json()['request']['assessment']['test_only_self_review'] is True
        historical=reviewer.client.get('/api/notebook/research-requests/'+record['id']).json()['request']
        assert historical['assessment']['effective'] is False
    finally:
        dev.state.db.engine.dispose()


def test_review_packets_do_not_accept_forged_authority_or_caller_content(system):
    _,owner,reviewer,*_=system
    note=create(owner,content(extensions={'assessment':{'decision':'approved'},'highlighted':True}))
    body=request_body(note)
    body['sources'][0]['content']={'title':'Forged server source'}
    assert owner.post('/api/notebook/research-requests',body).status_code==422
    record=submit(owner,note)
    assert record['assessment'] is None


def test_request_and_assessment_access_requires_browser_csrf_and_no_bearer(system):
    _,owner,*_=system
    body=request_body(create(owner))
    assert owner.client.post('/api/notebook/research-requests',json=body).status_code==403
    assert owner.client.get('/api/notebook/research-requests',headers={'Authorization':'Bearer invalid'}).status_code in (401,403)


def test_list_does_not_send_frozen_bodies_and_paginates(system):
    _,owner,*_=system
    note=create(owner,content(body='PRIVATE_RESEARCH_BODY'))
    ids={submit(owner,note)['id'] for _ in range(2)}
    first=owner.client.get('/api/notebook/research-requests?limit=1').json()
    assert 'PRIVATE_RESEARCH_BODY' not in json.dumps(first)
    second=owner.client.get('/api/notebook/research-requests?limit=1&after='+first['next_after']).json()
    assert {first['requests'][0]['id'],second['requests'][0]['id']}==ids
    assert second['next_after'] is None


from test_notebook_access import notebook_access


def test_beta_revocation_blocks_existing_packets_and_new_assessments(notebook_access):
    from crucible import beta_access
    from fastapi.testclient import TestClient
    from test_notebook_access import create as create_access_note
    app, now, ids, actors = notebook_access
    owner, reviewer = actors['author'], actors['reviewer']
    note = create_access_note(owner, 'private review evidence')
    record = submit(owner, note)
    endpoint = '/api/notebook/research-requests/' + record['id']
    assert reviewer.client.get(endpoint).status_code == 200
    with TestClient(app) as anonymous:
        assert anonymous.get(endpoint).status_code == 401
    with app.state.db.transaction(write=True) as session:
        for name in ('author', 'reviewer'):
            beta_access.set_membership(session, ids[name], 'revoked', now[0])
    for actor in (owner, reviewer):
        assert actor.client.get(endpoint).status_code == 401
        assert actor.client.get('/api/notebook/research-requests').status_code == 401
    assert owner.post('/api/notebook/research-requests', request_body(note)).status_code == 401
    assert reviewer.post(endpoint + '/assessments', decision(record)).status_code == 401


def test_visualization_nomination_is_frozen_review_without_publication_activation_or_points(system):
    from crucible import registry
    from crucible.db import AdapterVersion
    from crucible.scoring_awards import SchemeAdmissionCredit
    app, owner, reviewer, _, _ = system
    first = create(owner, content(title='A visualization candidate', body='Exact proposed conditional claim.'))
    before = owner.client.get('/api/people').json()
    releases = dict(registry.CURRENT)
    with app.state.db.transaction() as session:
        pins = session.scalar(select(func.count()).select_from(AdapterVersion))
    record = submit(owner, first, purpose='visualization_nomination',
                    question='For the exact Frodo release assumptions view, consider the conditional claim in this note; verify its scope before displaying it.')
    revise(owner, first, content(title='Later private follow-up'))
    result = reviewer.post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
    assert result.status_code == 200, result.text
    stored = result.json()['request']
    assert stored['purpose'] == 'visualization_nomination'
    assert stored['sources'][0]['content']['body'] == 'Exact proposed conditional claim.'
    assert stored['assessment']['outcome'] == 'recommend'
    assert 'award points' in stored['scope'] and 'change a visualization' in stored['scope']
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionCredit)) == 0
        assert session.scalar(select(func.count()).select_from(notebook.NotebookPublication)) == 0
        assert session.scalar(select(func.count()).select_from(AdapterVersion)) == pins
    assert dict(registry.CURRENT) == releases
    assert owner.client.get('/api/people').json() == before
    assert reviewer.client.get('/api/notebook/notes/' + first['id']).status_code == 404
    original = owner.client.get('/api/notebook/notes/' + first['id'] + '?revision=1').json()['note']
    summary = original['revisions'][0]['requests'][0]
    assert summary['kind'] == 'visualization_nomination' and summary['status'] == 'recommend'


def source_filter(note, **changes):
    return {'source_note_id': note['id'], 'source_revision': note['revision'],
            'source_sha256': note['content_sha256'], **changes}


def test_exact_primary_source_filter_preserves_privacy_and_excludes_supporting_notes(system):
    _, owner, reviewer, requester, _ = system
    primary = create(owner, content(body='PRIVATE BODY NOT IN REQUEST SUMMARY'))
    support = create(owner, content(title='A supporting source'))
    for note in (primary, support):
        request_publication(owner, note); approve(reviewer, note)
    refs = [{'note_id': n['id'], 'revision': n['revision'], 'content_sha256': n['content_sha256']}
            for n in (primary, support)]
    record = submit(requester, primary, sources=refs, question='PRIVATE QUESTION NOT IN SUMMARY')
    endpoint = '/api/notebook/research-requests'
    result = requester.client.get(endpoint, params=source_filter(primary))
    assert result.status_code == 200
    summary = result.json()['requests'][0]
    assert summary['id'] == record['id']
    assert summary['primary_source'] == refs[0]
    assert summary['sources'] is None and summary['question'] is None
    assert 'PRIVATE BODY' not in result.text and 'PRIVATE QUESTION' not in result.text
    assert requester.client.get(endpoint, params=source_filter(support)).json()['requests'] == []
    assert requester.client.get(endpoint, params=source_filter(primary, source_sha256='0' * 64)).json()['requests'] == []
    assert requester.client.get(endpoint, params=source_filter(primary, source_revision=2)).json()['requests'] == []
    assert owner.client.get(endpoint, params=source_filter(primary)).json()['requests'] == []
    assert owner.client.get(endpoint, params={**source_filter(primary), 'review': 'true'}).status_code == 403
    assert reviewer.client.get(endpoint, params=source_filter(primary)).json()['requests'] == []
    assert reviewer.client.get(endpoint, params={**source_filter(primary), 'review': 'true'}).json()['requests'][0]['id'] == record['id']


def test_curated_source_filter_is_exact_and_preserves_pagination(system):
    _, owner, reviewer, _, _ = system
    collection = json.loads((Path(__file__).resolve().parents[1] / 'web/map/notes.json').read_text())
    source = next(n for n in collection['notes'] if n['id'] == 'literature-high-corruption-csp')
    revision = source['revisions'][0]
    note = {'id': 'curated:' + source['id'], 'revision': 1,
            'content_sha256': notebook._hash({k: v for k, v in revision.items() if k in notebook.CONTENT_FIELDS})}
    ids = {submit(owner, note, purpose='visualization_nomination')['id'] for _ in range(2)}
    params = {**source_filter(note), 'limit': 1}
    first = owner.client.get('/api/notebook/research-requests', params=params).json()
    assert first['next_after'] and len(first['requests']) == 1
    second = owner.client.get('/api/notebook/research-requests', params={**params, 'after': first['next_after']}).json()
    assert {first['requests'][0]['id'], second['requests'][0]['id']} == ids
    assert second['next_after'] is None
    assert first['requests'][0]['primary_source']['note_id'] == note['id']
    assert reviewer.client.get('/api/notebook/research-requests', params=params).json()['requests'] == []


def test_source_filters_require_complete_strict_identity(system):
    _, owner, *_ = system
    note = create(owner)
    endpoint = '/api/notebook/research-requests'
    complete = source_filter(note)
    for missing in complete:
        assert owner.client.get(endpoint, params={key: value for key, value in complete.items() if key != missing}).status_code == 422
    for invalid in ('0', '-1', '101', '1.0', '1e0', 'true', '01'):
        assert owner.client.get(endpoint, params={**complete, 'source_revision': invalid}).status_code == 422
    for invalid in ('', 'x', 'A' * 64, '0' * 65):
        assert owner.client.get(endpoint, params={**complete, 'source_sha256': invalid}).status_code == 422
    assert owner.client.get(endpoint, params={**complete, 'source_note_id': 'x' * 109}).status_code == 422


def test_request_summary_identifies_development_only_assessment(system):
    app, _, reviewer, _, now = system
    note = create(reviewer)
    record = submit(reviewer, note, purpose='visualization_nomination')
    dev = create_app(replace(app.state.settings, allow_self_review=True), clock=lambda: now[0])
    try:
        response = Actor(dev, reviewer.identifier).post('/api/notebook/research-requests/' + record['id'] + '/assessments', decision(record))
        assert response.status_code == 200, response.text
        summary = reviewer.client.get('/api/notebook/research-requests', params=source_filter(note)).json()['requests'][0]
        assert summary['assessment'] == {'outcome': 'recommend', 'effective': False, 'test_only_self_review': True}
    finally:
        dev.state.db.engine.dispose()
