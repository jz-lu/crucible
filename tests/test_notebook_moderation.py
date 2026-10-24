"""Moderation hides an exact shared revision without destroying audit evidence."""
import json
from uuid import uuid4

from sqlalchemy import func, select

from crucible import notebook, notebook_moderation as moderation, research_reviews
from test_notebook import system, create, content, revise, publication_body, request_publication, approve
from test_notebook_sharing import seed_legacy_shares
from test_research_reviews import submit, request_body


def endpoint(note):
    return '/api/notebook/notes/' + note['id']


def withdraw(actor, note, **changes):
    return actor.post(endpoint(note) + '/publication-withdrawals',
        publication_body(note, reason='Private information needs removal.', **changes))


def shared(owner, reviewer):
    note = create(owner, content(title='Sensitive fixture title', claim='Sensitive fixture claim',
        body='Sensitive fixture body', tags=['sensitive-fixture']))
    request_publication(owner, note)
    approve(reviewer, note)
    return note


def test_moderation_is_exact_retryable_and_retains_restricted_audit(system):
    app, owner, reviewer, reader, _ = system
    note = shared(owner, reviewer)
    original = owner.client.get(endpoint(note)).json()['note']['revisions'][0]
    body = publication_body(note, reason='Restricted moderation explanation')
    route = endpoint(note) + '/publication-withdrawals'
    assert owner.post(route, body).status_code == 403
    assert reader.post(route, body).status_code == 403
    assert reviewer.client.post(route, json=body).status_code == 403  # CSRF
    assert reviewer.client.post(route, json=body, headers={'Authorization':'Bearer invalid'}).status_code in (401,403)
    assert reviewer.post(route, {**body, 'reason':' '}).status_code == 422
    assert reviewer.post(route, {**body, 'content_sha256':'0'*64}).status_code == 409
    response = reviewer.post(route, body)
    assert response.status_code == 200, response.text
    assert reviewer.post(route, body).json() == response.json()
    assert reviewer.post(route, {**body, 'reason':'Changed retry'}).status_code == 409
    assert withdraw(reviewer, note).status_code == 409
    for actor in (owner, reviewer):
        current = actor.client.get(endpoint(note)).json()['note']['revisions'][0]
        assert current['content'] == original['content']
        assert current['content_sha256'] == original['content_sha256']
        assert current['publication'] == original['publication']
        assert current['publication_status'] == 'withdrawn'
        assert current['moderation']['reason'] == body['reason']
    public = reader.client.get(endpoint(note) + '?revision=1')
    assert public.status_code == 410 and public.json()['detail'] == moderation.NOTICE
    assert 'Sensitive fixture' not in public.text and body['reason'] not in public.text
    assert reader.client.get(endpoint(note)).status_code == 410
    assert reader.client.get(endpoint(note) + '?revision=2').status_code == 404
    assert reader.client.get('/api/notebook/notes').json()['notes'] == []
    assert reader.client.get('/api/notebook/notes?q=Sensitive').json()['notes'] == []
    assert reader.client.get('/api/notebook/notes?tag=sensitive-fixture').json()['notes'] == []
    assert owner.client.get('/api/notebook/notes').json()['notes'][0]['publication_status'] == 'withdrawn'
    exported = owner.client.get('/api/notebook/export').json()
    assert exported['notes'][0]['revisions'][0]['body'] == original['content']['body']
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(moderation.PublicationWithdrawal)) == 1
        assert session.scalar(select(notebook.NotebookPublication)).decision == 'approved'


def test_withdrawal_blocks_private_share_bypass_and_preserves_other_revisions(system):
    _, owner, reviewer, reader, _ = system
    first = shared(owner, reviewer)
    seed_legacy_shares(owner, first, ['reader'])
    assert reader.client.get('/api/notebook/private-shares').status_code == 410
    assert reader.client.get('/api/notebook/notes?scope=shared').status_code == 410
    before = reader.client.get('/api/notebook/timeline').json()['total']
    assert withdraw(reviewer, first).status_code == 200
    assert reader.client.get(endpoint(first)).status_code == 410
    assert reader.client.get('/api/notebook/private-shares').status_code == 410
    assert reader.client.get('/api/notebook/notes?scope=shared').status_code == 410
    assert reader.client.get('/api/notebook/timeline').json()['total'] == before - 1
    second = revise(owner, first, content(title='Corrected revision', body='Sensitive information removed.'))
    assert reader.client.get(endpoint(first)).status_code == 410
    assert reader.client.get(endpoint(first) + '?revision=2').status_code == 404
    assert reader.client.get(endpoint(first) + '?revision=1').status_code == 410
    assert withdraw(reviewer, second).status_code == 404  # Private revisions are not silently moderated.
    request_publication(owner, second); approve(reviewer, second)
    assert reader.client.get(endpoint(first)).json()['note']['revisions'][0]['content']['title'] == 'Corrected revision'
    assert reader.client.get('/api/notebook/timeline').json()['total'] == before


def test_frozen_packets_do_not_republish_a_withdrawn_third_party_source(system):
    app, owner, reviewer, reader, _ = system
    source = shared(owner, reviewer)
    packet = submit(reader, source)
    assert 'Sensitive fixture body' in json.dumps(packet)
    assert withdraw(reviewer, source).status_code == 200
    path = '/api/notebook/research-requests/' + packet['id']
    restricted = reader.client.get(path).json()['request']
    assert restricted['withdrawn_source'] is True
    assert restricted['packet_sha256'] == packet['packet_sha256']
    assert 'Sensitive fixture' not in json.dumps(restricted)
    assert restricted['sources'] == [] and restricted['assessment'] is None
    assert 'Sensitive fixture' not in reader.client.get('/api/notebook/research-requests').text
    reviewed = reviewer.client.get(path).json()['request']
    assert reviewed['sources'] == packet['sources']
    assert reader.post('/api/notebook/research-requests', request_body(source)).status_code == 404
    with app.state.db.transaction() as session:
        record = session.get(research_reviews.ResearchRequest, packet['id'])
        assert research_reviews.packet_hash(record) == packet['packet_sha256']
        assert record.sources == packet['sources']
