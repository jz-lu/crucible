"""The header balance uses canonical scores and only the signed-in account."""
from dataclasses import replace

from fastapi.testclient import TestClient

from crucible.app import create_app
from test_api import Actor
from test_calibrated_api import current_system
from test_people import receipt


def test_own_points_matches_profile_and_cannot_select_a_different_account(current_system):
    app, _, _ = current_system
    assert TestClient(app).get('/api/me/points').status_code == 401
    alice, bob = Actor(app), Actor(app)
    a, b = alice.register('points_alice'), bob.register('points_bob')
    assert alice.client.get('/api/me/points').json()['account_points']['points'] == 0
    receipt(app, a['id'], 1)
    receipt(app, b['id'], 1, tick=1)
    for actor, owner, expected in ((alice, a, 100), (bob, b, 0)):
        response = actor.client.get('/api/me/points', params={'user_id': b['id']})
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        value = response.json()['account_points']
        assert value == {'user_id': owner['id'], 'points': expected,
                         'score_status': 'active', 'policy_id': 'crucible-points-v11'}
        assert actor.client.get('/api/people/'+owner['handle']).json()['person']['points'] == expected
        assert not {'records', 'methods', 'compute', 'rank'} & value.keys()
    alice.post('/api/auth/logout', {})
    assert alice.client.get('/api/me/points').status_code == 401


def test_reviewer_points_use_the_same_exclusion_policy(current_system):
    app, clock, settings = current_system
    actor = Actor(app)
    owner = actor.register('points_reviewer')
    receipt(app, owner['id'], 1)
    reviewer_app = create_app(replace(settings, reviewer_ids=(owner['id'],)), clock=lambda: clock[0], initialize=False)
    try:
        client = TestClient(reviewer_app)
        client.cookies.update(actor.client.cookies)
        response = client.get('/api/me/points')
        assert response.status_code == 200
        assert response.json()['account_points']['points'] == 0
        assert response.json()['account_points']['score_status'] == 'reviewer'
        assert client.get('/api/me/points', headers={'Authorization': 'Bearer invalid'}).status_code in (401,403)
    finally:
        reviewer_app.state.db.engine.dispose()
