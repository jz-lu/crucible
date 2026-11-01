"""Verified-owner exports; no provider calls, code execution, or score changes."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible.db import Challenge, Submission
from crucible.github_auth import GitHubIdentity
from crucible.github_contributions import digest
from crucible.method_sources import MethodSource, SourceArtifact
from scripts.solve_teaching_frontier import recover
from test_bounties import make_system, post
from test_note_ratings import agent


@pytest.fixture
def system(tmp_path, monkeypatch):
    value = make_system(f"sqlite:///{tmp_path / 'github.db'}", monkeypatch)
    try:
        yield value
    finally:
        for actor in value[1:-1]:
            actor.client.close()
        value[0].state.db.engine.dispose()


def submitted(system, *, correct=True, source=True):
    app, owner, *_ = system
    method = post(owner, '/api/methods', {'name': 'Synthetic GitHub export',
        'pseudocode': 'Run the public-input teaching solver. This fixture is not a scientific claim.'})['method']
    snapshot = {'format': 'crucible-source-v1', 'files': [
        {'path': 'attack/solve.py', 'content': "raise RuntimeError('must never execute')\n"}]}
    if source:
        post(owner, '/api/methods/' + method['id'] + '/source', snapshot)
    challenge = post(owner, '/api/challenges', {'scheme_id': 'teaching-dh-kem', 'instance_size': 1})['challenge']
    candidate = recover(challenge['public'])
    answer = {'candidate': candidate if correct else '0' * 64, 'method_id': method['id'],
              'compute': {'hardware': 'Synthetic test CPU', 'workers': 1, 'wall_seconds': 1,
                          'precomputation': 'None', 'peak_memory': 'Not measured'}, 'idempotency_key': str(uuid4())}
    result = post(owner, f'/api/challenges/{challenge["id"]}/submit', answer)['submission']
    return result, candidate, snapshot


def endpoint(result):
    return '/api/submissions/' + result['id'] + '/github-export'


def test_export_is_owned_exact_inert_and_read_only(system):
    app, owner, *_ = system
    result, candidate, snapshot = submitted(system)
    with app.state.db.transaction(write=True) as session:
        session.add(GitHubIdentity(github_user_id='12345', user_id=owner.identifier,
            github_login='synthetic-contributor', created_at=system[-1][0], last_login_at=system[-1][0]))
    before = owner.client.get('/api/people/poster').json()['person']['points']
    response = owner.client.get(endpoint(result))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    value = response.json()
    assert value == owner.client.get(endpoint(result)).json()
    assert value['sha256'] == digest({key: item for key, item in value.items() if key != 'sha256'})
    assert value['github_identity'] == {'id': '12345', 'login': 'synthetic-contributor'}
    assert value['target']['repository'] == 'countableclouds/crucible-schemes'
    assert value['target']['directory'].endswith('/' + result['id'])
    receipt = json.loads(next(item['content'] for item in value['files'] if item['path'] == 'receipt.json'))
    assert receipt['status'] == 'correct' and receipt['scheme_version'] == '7'
    assert receipt['verification'] == {'key_recovery': 'server-verified', 'method': 'contributor-reported', 'compute': 'contributor-reported'}
    assert value['source']['sha256'] == receipt['method']['source_sha256']
    assert candidate not in response.text
    for secret in ('target_mac', 'payload_mac', 'idempotency_key', 'password_hash', 'public_key', 'ciphertext', snapshot['files'][0]['content']):
        assert secret not in response.text
    assert owner.client.get('/api/people/poster').json()['person']['points'] == before
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Submission)) == 1
        assert session.scalar(select(func.count()).select_from(MethodSource)) == 1
        assert session.scalar(select(func.count()).select_from(SourceArtifact)) == 1


def test_anonymous_other_account_and_unrecognized_routes_cannot_export(system):
    app, owner, other, *_ = system
    result, *_ = submitted(system)
    assert other.client.get(endpoint(result)).status_code == 404
    assert TestClient(app).get(endpoint(result)).status_code in (401, 403)
    token = agent(owner, scopes=('submissions:read',))
    try:
        assert token.get(endpoint(result)).status_code == 200
        method = owner.client.get('/api/methods').json()['methods'][0]
        assert token.get('/api/methods/' + method['id'] + '/source').status_code == 403
        assert token.get(endpoint(result) + '/extra').status_code == 403
        assert token.post(endpoint(result), json={}).status_code == 403
    finally:
        token.close()
    for scopes in (('methods:read',), ('challenges:read',)):
        token = agent(owner, scopes=scopes)
        assert token.get(endpoint(result)).status_code == 403
        token.close()


def test_incorrect_submission_cannot_be_exported(system):
    result, *_ = submitted(system, correct=False)
    assert system[1].client.get(endpoint(result)).status_code == 409


@pytest.mark.parametrize('change', ['late', 'unchecked', 'wrong-round', 'wrong-profile', 'wrong-release'])
def test_inconsistent_receipts_cannot_claim_verified_exports(system, change):
    app, owner, *_ = system
    result, *_ = submitted(system)
    with app.state.db.transaction(write=True) as session:
        receipt = session.get(Submission, result['id'])
        challenge = session.get(Challenge, receipt.challenge_id)
        if change == 'late': receipt.received_at = challenge.ends_at
        elif change == 'unchecked': challenge.correctness_passed = False
        elif change == 'wrong-round': receipt.round_id += 1
        elif change == 'wrong-profile': receipt.profile_id = 'wrong-profile'
        else: receipt.scheme_version = 'wrong-release'
    assert owner.client.get(endpoint(result)).status_code in (404, 409)


def test_export_without_source_or_github_link_still_prepares_receipt(system):
    result, *_ = submitted(system, source=False)
    value = system[1].client.get(endpoint(result)).json()
    assert value['source'] is None and value['github_identity'] is None
    assert len(value['files']) == 2
