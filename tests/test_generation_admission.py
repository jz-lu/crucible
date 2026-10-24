"""Read issuance configuration accurately without granting it or hiding old work."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from crucible import registry
from crucible.config import approved_beta_releases
from crucible.app import create_app, current_round, generation_admission, release_generation_admission
from crucible.db import Challenge
from crucible.security import mac
from test_beta_boundary import private_app
from test_browser_admission import selects


def current_releases():
    return tuple(f'{scheme}@{version}' for scheme, version in registry.CURRENT.items())


def test_packaged_approved_selection_is_registered_and_current():
    assert set(approved_beta_releases()) <= set(current_releases())


@pytest.mark.parametrize('configuration', ['all-current', 'missing-ecdh', 'wrong-ecdh-version'])
def test_every_current_profile_uses_exact_release_allowlist(configuration):
    releases = current_releases()
    if configuration != 'all-current':
        releases = tuple(value for value in releases if not value.startswith('dhkem-x25519@'))
    if configuration == 'wrong-ecdh-version':
        releases += ('dhkem-x25519@' + registry.ec_research.SCHEME_VERSION,)
    settings = SimpleNamespace(production=True, access_mode='beta', beta_releases=releases)
    for scheme, version in registry.CURRENT.items():
        expected = configuration == 'all-current' or scheme != 'dhkem-x25519'
        for manifest in registry.presentation_manifests(registry.adapter_for(scheme, version)):
            before = dict(manifest)
            assert generation_admission(settings, manifest, current_version=version) == {
                'admitted': expected, 'reason': None if expected else 'release_not_admitted'}
            assert manifest == before


def test_archived_profile_remains_unissuable_even_if_allowlisted():
    adapter = registry.ec_research
    manifest = registry.presentation_manifests(adapter)[0]
    settings = SimpleNamespace(production=True, access_mode='beta',
                               beta_releases=(adapter.SCHEME_ID + '@' + adapter.SCHEME_VERSION,))
    assert generation_admission(settings, manifest, current_version=registry.CURRENT[adapter.SCHEME_ID]) == {
        'admitted': False, 'reason': 'archived_release'}


def test_development_policy_is_preserved_without_consulting_hosted_allowlist():
    settings = SimpleNamespace(production=False, access_mode='public', beta_releases=())
    for identifier, version in registry.CURRENT.items():
        manifest = registry.presentation_manifests(registry.adapter_for(identifier, version))[0]
        assert generation_admission(settings, manifest, current_version=version) == {'admitted': True, 'reason': None}
    unknown = {'scheme_id': 'unadmitted-fixture', 'version': '1', 'educational': False}
    assert generation_admission(settings, unknown, current_version='1') == {
        'admitted': False, 'reason': 'release_not_admitted'}


@contextmanager
def hosted_client(private_app, releases):
    actor = private_app.test_login('participant')
    settings = replace(private_app.state.settings, production=True, mail_backend='disabled',
                       beta_releases=releases)
    app = create_app(settings, clock=lambda: private_app.test_now[0])
    client = TestClient(app, base_url='https://testserver')
    raw = actor.client.cookies[private_app.state.settings.cookie_name]
    client.cookies.set(settings.cookie_name, raw)
    headers = {'Origin': 'http://testserver',
               'X-CSRF-Token': mac(settings.secret, 'csrf-v1', raw.encode())}
    try:
        yield app, client, headers
    finally:
        client.close()
        actor.client.close()
        app.state.db.engine.dispose()


@pytest.mark.parametrize('configuration', ['all-current', 'missing-ecdh', 'wrong-ecdh-version'])
def test_challenge_get_exposes_admission_without_extra_queries_or_generation(private_app, configuration):
    admitted = configuration == 'all-current'
    releases = current_releases() if admitted else tuple(v for v in current_releases() if not v.startswith('dhkem-x25519@'))
    if configuration == 'wrong-ecdh-version':
        releases += ('dhkem-x25519@' + registry.ec_research.SCHEME_VERSION,)
    manifest = registry.presentation_manifests(registry.ec_compressed)[0]
    params = {'profile_id': manifest['id'], 'scheme_id': manifest['scheme_id'], 'scheme_version': manifest['version']}
    with hosted_client(private_app, releases) as (app, client, headers):
        with selects(app) as queries:
            response = client.get('/api/challenges', params=params)
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert len(queries) == 4  # Fresh admission, handler authorization, exact challenge, round state.
        data = response.json()
        assert data['challenge'] is None and data['round_started'] is False
        assert data['generation_admission'] == {'admitted': admitted,
            'reason': None if admitted else 'release_not_admitted'}
        if not admitted:
            denied = client.post('/api/challenges', json=params, headers=headers)
            assert denied.status_code == 409
            assert denied.json()['detail'] == 'This profile has not been admitted to this challenge track.'


def test_existing_challenge_remains_readable_when_new_issuance_is_disabled(private_app):
    manifest = registry.presentation_manifests(registry.ec_compressed)[0]
    identifier = str(uuid4())
    round_ = current_round(private_app.test_now[0])
    with private_app.state.db.transaction(write=True) as session:
        session.add(Challenge(id=identifier, user_id=private_app.test_member,
            scheme_id=manifest['scheme_id'], scheme_version=manifest['version'], profile_id=manifest['id'],
            round_id=round_['id'], starts_at=round_['starts_at'], ends_at=round_['ends_at'],
            issued_at=private_app.test_now[0], public_data={'synthetic_fixture': True}, manifest=manifest,
            target_mac='0' * 64, correctness_passed=True))
    releases = tuple(v for v in current_releases() if not v.startswith('dhkem-x25519@'))
    with hosted_client(private_app, releases) as (_, client, _):
        response = client.get('/api/challenges', params={'profile_id': manifest['id'],
            'scheme_id': manifest['scheme_id'], 'scheme_version': manifest['version']})
        assert response.status_code == 200
        data = response.json()
        assert data['generation_admission'] == {'admitted': False, 'reason': 'release_not_admitted'}
        assert data['challenge']['id'] == identifier
        assert data['challenge']['public'] == {'synthetic_fixture': True}
        assert data['round_started'] is True


def test_archived_get_explains_read_only_release(private_app):
    adapter = registry.ec_research
    manifest = registry.presentation_manifests(adapter)[0]
    releases = current_releases() + (adapter.SCHEME_ID + '@' + adapter.SCHEME_VERSION,)
    with hosted_client(private_app, releases) as (_, client, _):
        response = client.get('/api/challenges', params={'profile_id': manifest['id'],
            'scheme_id': manifest['scheme_id'], 'scheme_version': manifest['version']})
        assert response.status_code == 200
        assert response.json()['generation_admission'] == {'admitted': False, 'reason': 'archived_release'}


def test_release_summary_defers_mixed_profiles_to_exact_selection(monkeypatch):
    settings = SimpleNamespace(production=False, access_mode='public', beta_releases=())
    monkeypatch.setattr(registry, 'presentation_issuance_profiles', lambda _: ((True, None), (False, None)))
    assert release_generation_admission(settings, 'teaching-dh-kem', '7') == {
        'admitted': None, 'reason': 'profile_specific'}
    for educational in (True, False):
        manifest = {'scheme_id': 'teaching-dh-kem', 'version': '7', 'educational': educational}
        assert generation_admission(settings, manifest, current_version='7')['admitted'] is educational
    # Production admission is release-wide and needs no profile presentation work.
    monkeypatch.setattr(registry, 'presentation_issuance_profiles', lambda _: pytest.fail('Unnecessary profile read'))
    hosted = SimpleNamespace(production=True, access_mode='beta', beta_releases=('teaching-dh-kem@7',))
    assert release_generation_admission(hosted, 'teaching-dh-kem', '7') == {'admitted': True, 'reason': None}


def test_static_policy_projection_tracks_provider_identity_and_stays_immutable():
    class Adapter:
        IMPLEMENTATION_SHA256 = 'test-fixture'
        manifests = staticmethod(lambda: [{'educational': True, 'profile_kind': 'research-tier'},
                                         {'educational': True, 'profile_kind': 'research-tier'}])
    first = registry.presentation_issuance_profiles(Adapter)
    assert first == ((True, 'research-tier'),)
    assert registry.presentation_issuance_profiles(Adapter) is first
    Adapter.manifests = staticmethod(lambda: [{'educational': False, 'profile_kind': None}])
    assert registry.presentation_issuance_profiles(Adapter) == ((False, None),)
    assert first == ((True, 'research-tier'),)


def test_catalogue_admission_is_not_cached_across_settings_or_added_to_manifests(private_app):
    restricted = ('teaching-dh-kem@7', 'rsa-kem@4')
    revision = None
    for releases, expected in ((current_releases(), True), (restricted, False), (current_releases(), True)):
        with hosted_client(private_app, releases) as (_, client, _):
            for params in ({}, {'projection': 'navigation'}, {'progress_only': 'true'},
                           {'projection': 'release', 'scheme_id': 'dhkem-x25519', 'version': registry.CURRENT['dhkem-x25519']}):
                response = client.get('/api/schemes', params=params)
                assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
                data = response.json()
                revision = revision or data['catalogue_revision']
                assert data['catalogue_revision'] == revision
                rows = ([data['scheme']] if 'scheme' in data else
                        data['releases'] if 'releases' in data else data['schemes'])
                selected = next(row for row in rows if row['id'] == 'dhkem-x25519'
                                and row['version'] == registry.CURRENT['dhkem-x25519'])
                assert selected['generation_admission'] == {'admitted': expected,
                    'reason': None if expected else 'release_not_admitted'}
    assert all('generation_admission' not in row for row in registry.navigation_catalogue())
    assert all('generation_admission' not in row for row in registry.presentation_manifests(registry.ec_compressed))


def test_anonymous_challenge_get_does_not_disclose_admission(private_app):
    manifest = registry.presentation_manifests(registry.ec_compressed)[0]
    response = TestClient(private_app).get('/api/challenges', params={'profile_id': manifest['id'],
        'scheme_id': manifest['scheme_id'], 'scheme_version': manifest['version']})
    assert response.status_code == 401 and response.headers['cache-control'] == 'no-store'
    assert 'generation_admission' not in response.json()
