"""Bounded owner-only history pages, stable ties and cursor/session isolation."""
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import beta_access
from crucible.db import Challenge, Method, Submission
from test_notebook import system
from test_notebook_agents import agent
from test_beta_boundary import private_app


PATH = '/api/me/submissions'


def seed(app, owner_id, rows):
    """Create only synthetic historical receipts; never run a KEM or attack."""
    with app.state.db.transaction(write=True) as session:
        method = Method(id=str(uuid4()), user_id=owner_id, name='Pagination fixture',
                        pseudocode='PRIVATE_METHOD_BODY', created_at=0)
        session.add(method)
        session.flush()
        first_round = (session.scalar(select(func.max(Challenge.round_id)).where(
            Challenge.user_id == owner_id)) or 0) + 1
        pending = []
        for index, (identifier, received_at) in enumerate(rows):
            challenge = Challenge(id=str(uuid4()), user_id=owner_id,
                scheme_id='teaching-dh-kem', scheme_version='4', profile_id='dh-20',
                round_id=first_round + index, issued_at=0, starts_at=0, ends_at=3600,
                public_data={'synthetic':'PRIVATE_INSTANCE_BODY'},
                manifest={'scheme_id':'teaching-dh-kem', 'version':'4', 'id':'dh-20',
                    'instance_size':20, 'profile_kind':'research-tier', 'profile_label':'Stage 1'},
                target_mac='a' * 64, correctness_passed=True)
            session.add(challenge)
            pending.append(Submission(id=identifier, challenge_id=challenge.id, user_id=owner_id,
                method_id=method.id, profile_id='dh-20', scheme_version='4', round_id=challenge.round_id,
                idempotency_key=str(uuid4()), payload_mac='b' * 64, correct=index % 2 == 0,
                received_at=received_at, elapsed_seconds=1, ai_model='Synthetic model',
                compute={'notes':'PRIVATE_COMPUTE_BODY'}))
        session.flush()
        session.add_all(pending)


def identifier(value):
    return str(UUID(int=value))


def ids(response):
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    return [item['id'] for item in response.json()['submissions']]


def test_default_history_stays_fifty_and_explicit_pages_are_bounded(system):
    app, owner, *_ = system
    rows = [(identifier(n), float(n)) for n in range(1, 102)]
    seed(app, owner.identifier, rows)
    first = owner.client.get(PATH)
    assert ids(first) == [identifier(n) for n in range(101, 51, -1)]
    assert first.json()['next_after'] == identifier(52)
    large = owner.client.get(PATH, params={'limit':100})
    assert ids(large) == [identifier(n) for n in range(101, 1, -1)]
    assert large.json()['next_after'] == identifier(2)
    last = owner.client.get(PATH, params={'limit':100, 'after':large.json()['next_after']})
    assert ids(last) == [identifier(1)] and last.json()['next_after'] is None
    empty = owner.client.get(PATH, params={'after':identifier(1)})
    assert ids(empty) == [] and empty.json()['next_after'] is None
    # History remains a summary with the pre-existing fields, not a body feed.
    assert set(first.json()['submissions'][0]) == {'id', 'status', 'received_at', 'method_id',
        'ai_model', 'selection_kind', 'profile_kind', 'profile_label', 'record_inheritance',
        'scoring_eligible', 'scoring_policy_id', 'profile_id', 'scheme_id', 'scheme_name', 'scheme_version',
        'round_id', 'method_name'}
    assert 'PRIVATE_' not in first.text


def test_history_names_use_public_release_labels_and_survive_unavailable_releases(system):
    app, owner, *_ = system
    seed(app, owner.identifier, [(identifier(1), 1), (identifier(2), 2)])
    with app.state.db.transaction(write=True) as session:
        for number in (1, 2):
            record = session.get(Submission, identifier(number))
            challenge = session.get(Challenge, record.challenge_id)
            # Historical internal names are not the human-facing public label.
            challenge.manifest = {**challenge.manifest, 'name':'Teaching DH-KEM'}
            if number == 2:
                challenge.scheme_id = 'retired-unregistered-kem'
                challenge.scheme_version = record.scheme_version = '1'
    response = owner.client.get(PATH)
    assert ids(response) == [identifier(2), identifier(1)]
    rows = {row['id']:row for row in response.json()['submissions']}
    assert rows[identifier(1)]['scheme_name'] == 'Finite-field Diffie–Hellman'
    assert rows[identifier(2)]['scheme_name'] == 'retired-unregistered-kem'
    assert 'Teaching DH-KEM' not in response.text


def test_cursor_orders_ties_and_new_arrivals_do_not_repeat_old_rows(system):
    app, owner, *_ = system
    seed(app, owner.identifier, [(identifier(1), 10), (identifier(4), 20),
        (identifier(2), 20), (identifier(3), 20), (identifier(5), 30)])
    first = owner.client.get(PATH, params={'limit':2})
    assert ids(first) == [identifier(5), identifier(4)]
    seed(app, owner.identifier, [(identifier(6), 40)])
    second = owner.client.get(PATH, params={'limit':2, 'after':first.json()['next_after']})
    assert ids(second) == [identifier(3), identifier(2)]
    third = owner.client.get(PATH, params={'limit':2, 'after':second.json()['next_after']})
    assert ids(third) == [identifier(1)] and third.json()['next_after'] is None
    assert ids(owner.client.get(PATH, params={'limit':2}))[0] == identifier(6)


def test_unknown_foreign_and_removed_cursor_have_same_error(system):
    app, owner, other, *_ = system
    seed(app, owner.identifier, [(identifier(1), 1), (identifier(2), 2)])
    seed(app, other.identifier, [(identifier(99), 99)])
    expected = {'detail':'Invalid submissions cursor. Refresh your submissions and try again.'}
    for cursor in (identifier(99), identifier(999)):
        response = owner.client.get(PATH, params={'after':cursor})
        assert response.status_code == 422 and response.json() == expected
        assert response.headers['cache-control'] == 'no-store'
    # Administrative removal cannot turn a stale cursor into an unrestricted first page.
    with app.state.db.transaction(write=True) as session:
        session.delete(session.get(Submission, identifier(2)))
    stale = owner.client.get(PATH, params={'after':identifier(2)})
    assert stale.status_code == 422 and stale.json() == expected
    assert ids(other.client.get(PATH)) == [identifier(99)]


@pytest.mark.parametrize('query', [{'limit':0}, {'limit':101}, {'limit':-1},
    {'limit':'invalid'}, {'limit':'1.5'}, {'after':'not-a-uuid'}, {'after':''}])
def test_invalid_limits_and_cursor_shapes_fail_without_rows(system, query):
    _, owner, *_ = system
    response = owner.client.get(PATH, params=query)
    assert response.status_code == 422 and 'submissions' not in response.json()


def test_history_pagination_retains_scope_and_bearer_identity_over_cookie(system):
    app, owner, other, *_ = system
    seed(app, owner.identifier, [(identifier(1), 1), (identifier(2), 2)])
    seed(app, other.identifier, [(identifier(99), 99)])
    reader = agent(app, owner, ['submissions:read'])
    reader.cookies.update(other.client.cookies)
    page = reader.get(PATH, params={'limit':1})
    assert ids(page) == [identifier(2)]
    assert ids(reader.get(PATH, params={'limit':1, 'after':page.json()['next_after']})) == [identifier(1)]
    assert reader.get(PATH, params={'after':identifier(99)}).status_code == 422
    assert agent(app, owner, ['notebook:read']).get(PATH, params={'limit':1}).status_code == 403
    assert owner.client.get(PATH, params={'limit':1}, headers={'Authorization':'Bearer invalid'}).status_code == 401
    with TestClient(app) as anonymous:
        assert anonymous.get(PATH, params={'limit':1}).status_code == 401


def test_beta_membership_revocation_denies_the_next_history_page(private_app):
    actor = private_app.test_login('participant')
    seed(private_app, private_app.test_member, [(identifier(1), 1), (identifier(2), 2)])
    first = actor.client.get(PATH, params={'limit':1})
    assert ids(first) == [identifier(2)]
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, 'revoked', private_app.test_now[0])
    # Membership revocation also invalidates the existing browser session.
    denied = actor.client.get(PATH, params={'limit':1, 'after':first.json()['next_after']})
    assert denied.status_code == 401 and 'submissions' not in denied.json()
