"""Small UI reads preserve the full catalogue's labels, provenance and progress."""
from contextlib import contextmanager
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from crucible import registry
from crucible.db import Challenge
from crucible.public_json import PROGRESS_FIELDS, catalogue_progress
from test_beta_boundary import private_app, connect
from test_calibrated_api import current_system
from test_people import add_user, receipt, registered_receipt


def flattened(schemes):
    return {(release['id'], release['version']): release
            for scheme in schemes for release in (scheme, *scheme.get('previous_releases', []))}


def detail(client, identity):
    return client.get('/api/schemes', params={'projection': 'release', 'scheme_id': identity[0], 'version': identity[1]})


@contextmanager
def selects(app):
    queries = []
    def capture(_, __, statement, ___, ____, _____):
        if statement.lstrip().lower().startswith(('select', 'with')):
            queries.append(statement)
    event.listen(app.state.db.engine, 'before_cursor_execute', capture)
    try:
        yield queries
    finally:
        event.remove(app.state.db.engine, 'before_cursor_execute', capture)


def test_navigation_has_all_authoritative_labels_and_only_compact_metadata(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    full = client.get('/api/schemes', headers={'Accept-Encoding': 'identity'})
    compact = client.get('/api/schemes?projection=navigation', headers={'Accept-Encoding': 'identity'})
    expected, result = full.json(), compact.json()
    assert result['projection'] == 'navigation'
    assert result['catalogue_revision'] == expected['catalogue_revision']
    assert [row['id'] for row in result['schemes']] == list(registry.CURRENT)
    originals, navigation = flattened(expected['schemes']), flattened(result['schemes'])
    assert set(navigation) == set(originals) == set(registry.ADAPTERS)
    assert len(navigation) == 32 and len(result['schemes']) == 8
    assert sum(len(row['profiles']) for row in navigation.values()) == 1466
    for identity, row in navigation.items():
        original = originals[identity]
        assert set(row) <= {'id', 'version', 'name', 'display_group', 'selection_kind', 'record_inheritance',
                            'default_progression', 'profiles', 'previous_releases', *PROGRESS_FIELDS}
        assert row['default_progression'] is bool(original['default_progression'])
        for field in ('id', 'version', 'name', 'display_group', 'selection_kind', 'record_inheritance', *PROGRESS_FIELDS):
            assert row.get(field) == original.get(field)
        assert len(row['profiles']) == len(original['profiles'])
        for profile, full_profile in zip(row['profiles'], original['profiles']):
            assert set(profile) <= {'id', 'instance_size', 'parameter_bits', 'stage_number', 'stage_label',
                                   'stage_position', 'stage_context', 'profile_label', 'profile_kind',
                                   'profile_order', 'native_parameter_label'}
            assert profile == {key: full_profile[key] for key in profile}
            assert profile['id'] == full_profile['id']
    assert len(compact.content) < len(full.content) / 10
    encoded = client.get('/api/schemes?projection=navigation', headers={'Accept-Encoding': 'gzip'})
    assert encoded.json() == result
    assert int(encoded.headers['content-length']) < 65536
    assert encoded.headers['cache-control'] == 'no-store'


def test_each_exact_release_matches_full_default_without_other_release_bodies(current_system):
    client = TestClient(current_system[0])
    full = client.get('/api/schemes').json()
    for identity, expected in flattened(full['schemes']).items():
        response = detail(client, identity)
        assert response.status_code == 200
        value = response.json()
        assert value['projection'] == 'release'
        assert value['catalogue_revision'] == full['catalogue_revision']
        assert value['current_version'] == registry.CURRENT[identity[0]]
        assert value['scheme'] == {key: item for key, item in expected.items() if key != 'previous_releases'}
        assert 'schemes' not in value and 'releases' not in value
        assert response.headers['cache-control'] == 'no-store'


def test_navigation_uses_cached_spine_and_keeps_two_live_queries(current_system, monkeypatch):
    app, _, _ = current_system
    client = TestClient(app)
    before = client.get('/api/schemes?projection=navigation').json()
    user = add_user(app, 'navigation_progress')
    result = registered_receipt(app, user, registry.ntru_frontier, 17)
    expected = catalogue_progress(client.get('/api/schemes').json()['schemes'])
    for name in ('descriptor', 'catalogue', 'presentation_manifests'):
        monkeypatch.setattr(registry, name, lambda *a: pytest.fail('Warm navigation must use the compact cached spine'))
    with selects(app) as queries:
        after = client.get('/api/schemes?projection=navigation').json()
    assert len(queries) == 2
    assert catalogue_progress(after['schemes']) == expected
    assert after['catalogue_revision'] == before['catalogue_revision']
    assert flattened(after['schemes'])[('sntrup', '3')]['first_recovery']['submission_id'] == result['submission_id']


def test_exact_release_builds_only_requested_descriptor_and_progress_stays_live(current_system, monkeypatch):
    app, _, _ = current_system
    client = TestClient(app)
    registry.catalogue_revision()  # Immutable whole-catalogue identity already established.
    original, calls = registry.descriptor, []
    def counted(adapter):
        calls.append((adapter.SCHEME_ID, adapter.SCHEME_VERSION))
        return original(adapter)
    monkeypatch.setattr(registry, 'descriptor', counted)
    monkeypatch.setattr(registry, 'catalogue', lambda: pytest.fail('Exact details must not build the full catalogue'))
    identity = ('hqc', registry.code_hqc_v3.SCHEME_VERSION)
    with selects(app) as queries:
        before = detail(client, identity).json()
    assert calls == [identity] and len(queries) == 2
    user = add_user(app, 'detail_progress')
    result = registered_receipt(app, user, registry.code_hqc_v3, 3)
    after = detail(client, identity).json()
    assert calls == [identity, identity]
    assert before['scheme']['first_recovery'] is None
    assert after['scheme']['participant_count'] == 1
    assert after['scheme']['first_recovery']['submission_id'] == result['submission_id']


def test_exact_archive_inherits_only_compatible_receipts_with_original_tie_break(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    alice, bob = add_user(app, 'compatible_alice'), add_user(app, 'compatible_bob')
    old = registered_receipt(app, alice, registry.kem, 1)
    newer = registered_receipt(app, bob, registry.dh_v3, 1)
    # A v1 receipt is not compatible and cannot become this release's winner.
    legacy_profile = registry.teaching_dh_v1.manifests()[0]
    def bind_legacy(result, session):
        challenge = session.get(Challenge, result.challenge_id)
        challenge.profile_id = result.profile_id = legacy_profile['id']
        challenge.manifest = legacy_profile
    receipt(app, alice, legacy_profile['parameter_bits'], -1, version='1', mutate=bind_legacy)
    full = flattened(client.get('/api/schemes').json()['schemes'])
    value = detail(client, ('teaching-dh-kem', '4')).json()['scheme']
    assert value == full[('teaching-dh-kem', '4')]
    assert value['participant_count'] == 2
    assert value['first_recovery']['submission_id'] == min(old['submission_id'], newer['submission_id'])
    assert value['first_recovery']['scheme_version'] in ('2', '3')
    current = detail(client, ('teaching-dh-kem', '7')).json()['scheme']
    assert current['first_recovery'] is None and current['participant_count'] == 0


@pytest.mark.parametrize('query', [
    'projection=unknown', 'projection=release', 'projection=release&scheme_id=hqc',
    'projection=release&version=1', 'projection=navigation&scheme_id=hqc', 'projection=navigation&version=1',
    'scheme_id=hqc&version=1', 'projection=navigation&progress_only=true',
    'projection=release&scheme_id=hqc&version=1&progress_only=false', 'progress_only=invalid',
])
def test_invalid_projection_combinations_fail_before_queries(current_system, query):
    app, _, _ = current_system
    with selects(app) as queries:
        response = TestClient(app).get('/api/schemes?' + query)
    assert response.status_code == 422 and queries == []


@pytest.mark.parametrize('identity', [('unknown-scheme', '1'), ('hqc', 'unknown-version')])
def test_unknown_release_does_not_fall_back_to_current(current_system, identity):
    assert detail(TestClient(current_system[0]), identity).status_code == 404


def test_new_projections_keep_browser_and_machine_beta_boundaries(private_app):
    urls = ['/api/schemes?projection=navigation', '/api/schemes?projection=release&scheme_id=teaching-dh-kem&version=7']
    visitor = TestClient(private_app)
    for url in urls:
        assert visitor.get(url).status_code == 401
        assert visitor.head(url).status_code == 401
        assert visitor.get(url, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    member = private_app.test_login('participant')
    connection = connect(member)
    token = member.client.post('/api/agent/connect/poll', json={'device_code': connection['device_code']}).json()['access_token']
    for url in urls:
        assert member.client.get(url).status_code == 200
        assert visitor.get(url, headers={'Authorization': 'Bearer ' + token}).status_code == 200


def test_navigation_metadata_is_copied_and_invalidated_by_provider(current_system, monkeypatch):
    before = registry.navigation_catalogue()
    changed = registry.navigation_catalogue()
    changed[0]['name'] = 'Mutated copy'
    changed[0]['profiles'].clear()
    assert registry.navigation_catalogue() == before
    adapter = registry.dhkem_x25519
    provider = adapter.manifests
    def renamed():
        profiles = deepcopy(provider())
        profiles[0]['profile_label'] = 'Changed published label'
        return profiles
    monkeypatch.setattr(adapter, 'manifests', renamed)
    after = flattened(registry.navigation_catalogue())
    assert after[('dhkem-x25519', '1')]['profiles'][0]['profile_label'] == 'Changed published label'
