"""Periodic reads transfer live records without retransferring protocol bodies."""
from fastapi.testclient import TestClient

from crucible import registry
from crucible.public_json import catalogue_progress
from test_scoring_all_schemes import add_user, current_system, registered_receipt


def test_progress_projection_matches_full_live_data_and_keeps_metadata_revision(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    full = client.get('/api/schemes').json()
    response = client.get('/api/schemes?progress_only=true')
    compact = response.json()
    assert compact['releases'] == catalogue_progress(full['schemes'])
    assert compact['catalogue_revision'] == full['catalogue_revision']
    assert compact['projection'] == 'progress' and 'schemes' not in compact
    assert response.headers['cache-control'] == 'no-store'
    assert len(response.content) < len(client.get('/api/schemes').content) / 20
    assert all(not {'profiles', 'protocol', 'parameters'} & set(row) for row in compact['releases'])
    user = add_user(app, 'progress_reader')
    receipt = registered_receipt(app, user, registry.code_hqc_v2, 3)
    changed = client.get('/api/schemes?progress_only=true').json()
    assert changed['catalogue_revision'] == compact['catalogue_revision']
    hqc = next(row for row in changed['releases'] if (row['id'], row['version']) == ('hqc', registry.code_hqc_v2.SCHEME_VERSION))
    assert hqc['participant_count'] == 1
    assert hqc['first_recovery']['submission_id'] == receipt['submission_id']
    assert changed['releases'] == catalogue_progress(client.get('/api/schemes').json()['schemes'])


def test_revision_changes_with_registered_provider_and_current_release(monkeypatch):
    original_revision = registry.catalogue_revision()
    adapter = registry.adapter_for('sntrup')
    original = adapter.manifests
    def changed_provider():
        manifests = original()
        manifests[0]['profile_label'] = 'Changed immutable presentation fixture'
        return manifests
    monkeypatch.setattr(adapter, 'manifests', changed_provider)
    changed_revision = registry.catalogue_revision()
    assert changed_revision != original_revision
    assert registry.catalogue_revision() == changed_revision
    monkeypatch.setitem(registry.CURRENT, 'hqc', 'weight-research-v1')
    assert registry.catalogue_revision() not in (original_revision, changed_revision)


def test_progress_flag_validation_and_legacy_review_metadata(current_system):
    client = TestClient(current_system[0])
    assert client.get('/api/schemes?progress_only=nonsense').status_code == 422
    for item in client.get('/api/schemes').json()['schemes']:
        assert item['review_status'] == 'local-beta'
        assert item['failure_policy'] == 'hold-on-disagreement-v1'
        if item['id'] in ('teaching-dh-kem', 'rsa-kem'):
            assert client.get(item['protocol']['correctness_url']).status_code == 200
