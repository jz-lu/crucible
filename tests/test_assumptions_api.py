"""Assumptions are read-only, release-specific metadata behind beta admission."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from crucible import machine_auth, registry
from test_api import system
from test_beta_boundary import private_app, connect


def test_sheet_endpoint_does_not_generate_and_cannot_alias_an_unknown_release(system):
    app, _, _ = system
    adapter = registry.adapter_for('teaching-dh-kem', '7')
    with patch.object(adapter, 'generate', side_effect=AssertionError('No generation')):
        client = TestClient(app)
        response = client.get('/api/protocol/assumptions', params={'scheme_id': adapter.SCHEME_ID, 'version': '7'})
        assert response.status_code == 200
        sheet = response.json()['sheet']
        assert sheet['scheme_id'] == adapter.SCHEME_ID
        assert sheet['scheme_version'] == '7'
        assert sheet['curation_status'] == 'draft'
        assert sheet['claims']
        for params in ({'scheme_id': '../anything'}, {'scheme_id': adapter.SCHEME_ID, 'version': 'missing'}):
            assert client.get('/api/protocol/assumptions', params=params).status_code == 404
        archive = client.get('/api/protocol/assumptions', params={'scheme_id': adapter.SCHEME_ID, 'version': '6'})
        assert archive.status_code == 200
        assert archive.json()['sheet']['claims'] == []


def test_private_sheets_use_existing_member_and_challenge_read_boundaries(private_app):
    path = '/api/protocol/assumptions?scheme_id=teaching-dh-kem&version=7'
    visitor = TestClient(private_app)
    assert visitor.get(path).status_code == 401
    assert visitor.get('/assumptions.js').status_code == 200  # Empty renderer, no research data.
    member = private_app.test_login('participant')
    assert member.client.get(path).status_code == 200
    connection = connect(member)
    token = member.client.post('/api/agent/connect/poll', json={'device_code': connection['device_code']}).json()['access_token']
    assert visitor.get(path, headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert machine_auth.required_scope('GET', '/api/protocol/assumptions') == 'challenges:read'
    assert machine_auth.required_scope('POST', '/api/protocol/assumptions') is None
