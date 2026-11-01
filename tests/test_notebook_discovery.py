"""Public support and publication age drive the human notebook, not drafts."""
import json
import pytest
from sqlalchemy import update

from crucible.db import LoginSession

from test_notebook import system, create, content, revise, request_publication, approve
from test_notebook_browse import curated, page, browse
from test_note_ratings import agent, vote, publish
from test_notebook_moderation import withdraw


@pytest.fixture(autouse=True)
def sessions_cover_age_changes(system):
    app, *_, now = system
    with app.state.db.transaction(write=True) as session:
        session.execute(update(LoginSession).values(expires_at=now[0] + 30 * 86400))


def test_support_can_lift_older_notes_but_fresh_work_can_overtake_them(system, curated):
    _, owner, reviewer, reader, now = system
    old = publish(owner, reviewer, create(owner, content(title='Useful older work')))
    assert vote(agent(reader), old, 2).status_code == 200
    now[0] += 86400
    new = publish(owner, reviewer, create(owner, content(title='New unrated work')))
    assert [note['id'] for note in page(reader)['notes']] == [old['id'], new['id']]
    assert page(reader, order='recent')['notes'][0]['id'] == new['id']
    now[0] += 2 * 86400
    fresh = publish(owner, reviewer, create(owner, content(title='Fresh research')))
    ranked = page(reader)['notes']
    assert [note['id'] for note in ranked] == [fresh['id'], old['id'], new['id']]
    assert ranked[1]['rating'] == {'score': 2, 'raters': 1, 'upvotes': 0, 'double_upvotes': 1}
    assert 'your_rating' not in json.dumps(ranked)


def test_publication_time_is_real_approval_time_and_does_not_reset_on_revision(system):
    _, owner, reviewer, reader, now = system
    saved = create(owner)
    now[0] += 3600
    publish(owner, reviewer, saved)
    published = now[0]
    assert vote(agent(reader), saved).status_code == 200
    now[0] += 86400
    changed = revise(owner, saved)
    assert page(reader)['notes'][0]['published_at'] == published
    assert page(owner)['notes'][0]['published_at'] == published
    publish(owner, reviewer, changed)
    listed = page(reader)['notes'][0]
    full = reader.client.get('/api/notebook/notes/' + saved['id']).json()['note']
    assert listed['published_at'] == full['published_at'] == published
    assert listed['rating'] == full['rating'] == {'score': 1, 'raters': 1, 'upvotes': 1, 'double_upvotes': 0}
    assert listed['revisions'][0]['created_at'] != published
    private = create(owner, content(title='Private research'))
    own = next(note for note in page(owner)['notes'] if note['id'] == private['id'])
    assert own['published_at'] is None and own['rating']['score'] == 0


def test_pages_freeze_age_and_merge_curated_support_before_pagination(system, curated):
    _, owner, reviewer, reader, now = system
    notes = [publish(owner, reviewer, create(owner, content(title=f'Observation {i}'))) for i in range(4)]
    client = agent(reader)
    bundled = client.get('/api/agent/notes/curated:curated-example-000').json()['note']
    assert vote(client, bundled, 2).status_code == 200
    assert vote(client, notes[0]).status_code == 200
    expected = page(reader, include_curated=True)['notes']
    found, after = [], None
    for _ in range(12):
        result = page(reader, include_curated=True, limit=1, **({'after': after} if after else {}))
        found.extend(result['notes'])
        after = result['next_after']
        now[0] += 86400
        if not after:
            break
    assert [note['id'] for note in found] == [note['id'] for note in expected]
    assert len(found) == len({note['id'] for note in found}) == 11
    index = reader.client.get('/api/notebook/notes?projection=index&include_curated=true').json()
    paged, _ = browse(reader, include_curated=True, limit=2)
    assert [note['id'] for note in index['notes']] == [note['id'] for note in paged]
    metadata = reader.client.get('/api/notebook/curated-note-numbers').json()['metadata'][bundled['id']]
    assert metadata['rating']['score'] == 2 and metadata['published_at'] == 1
    assert reader.client.get('/api/notebook/notes', params={'order': 'recent', 'after':
        page(reader, limit=1)['next_after']}).status_code == 422


def test_withdrawn_support_disappears_from_human_totals(system):
    _, owner, reviewer, reader, _ = system
    first = publish(owner, reviewer, create(owner))
    assert vote(agent(reader), first, 2).status_code == 200
    second = publish(owner, reviewer, revise(owner, first))
    withdraw(reviewer, first)
    listed = page(reader)['notes'][0]
    assert listed['revision'] == second['revision']
    assert listed['rating']['score'] == 0
