"""Complete browsing snapshots agree with the independent legacy leaderboard."""
import json

from fastapi.testclient import TestClient
import pytest
from crucible import registry
from crucible.db import Challenge, Submission
from test_calibrated_api import current_system
from test_people import add_user, receipt, registered_receipt
from test_scheme_projections import detail, flattened, selects


WINNER_FIELDS = {"rank", "submission_id", "handle", "profile_id", "scheme_id", "scheme_version",
                 "instance_size", "is_inherited", "received_at", "method"}


def assert_matches_board(client, release, profile_id):
    response = client.get('/api/leaderboard', params={
        'scheme_id': release['id'], 'scheme_version': release['version'], 'profile_id': profile_id})
    assert response.status_code == 200, response.text
    board = response.json()
    summary = release['recovery_summary']['profiles'][profile_id]
    assert summary['includes_compatible_versions'] == board['includes_compatible_versions']
    if not board['entries']:
        assert summary['winner'] is None
        return
    first = board['entries'][0]
    expected = {key: first[key] for key in WINNER_FIELDS}
    expected['method'] = {'name': first['method']['name']}
    assert summary['winner'] == expected


def test_empty_snapshot_is_complete_for_all_registered_profiles_without_extra_queries(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    with selects(app) as queries:
        response = client.get('/api/schemes?projection=navigation')
    assert response.status_code == 200
    assert len(queries) == 2
    assert 'methods.name' in queries[0]
    assert 'pseudocode' not in queries[0] and 'public_data' not in queries[0] and 'compute' not in queries[0]
    releases = flattened(response.json()['schemes'])
    assert set(releases) == set(registry.ADAPTERS)
    for identity, release in releases.items():
        summary = release['recovery_summary']
        assert set(summary) == {'format', 'complete', 'scheme_id', 'scheme_version', 'profiles'}
        assert summary['format'] == 'crucible-recovery-summary-v1' and summary['complete'] is True
        assert (summary['scheme_id'], summary['scheme_version']) == identity
        expected_ids = [row['id'] for row in registry.adapter_for(*identity).manifests()]
        assert list(summary['profiles']) == expected_ids
        assert all(set(row) == {'winner', 'includes_compatible_versions'} and row['winner'] is None
                   for row in summary['profiles'].values())
    # Map links need only release identity, not stage records.
    assert all(set(row) == {'id', 'version'} for row in client.get('/api/schemes?projection=map').json()['schemes'])


def test_current_archived_named_and_noninheriting_releases_match_legacy_boards(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    early, late = add_user(app, 'early'), add_user(app, 'late')
    for release_index, adapter in enumerate(registry.ADAPTERS.values()):
        profiles = adapter.manifests()
        for user, profile, tick in ((early, profiles[0], 0), (late, profiles[-1], 1)):
            def bind(result, session):
                challenge = session.get(Challenge, result.challenge_id)
                challenge.profile_id = result.profile_id = profile['id']
                challenge.manifest = profile
            receipt(app, user, profile.get('instance_size', profile['parameter_bits']), release_index * 2 + tick,
                    scheme=adapter.SCHEME_ID, version=adapter.SCHEME_VERSION, mutate=bind)
    response = client.get('/api/schemes?projection=navigation')
    releases = flattened(response.json()['schemes'])
    for identity, release in releases.items():
        profiles = release['profiles']
        # Includes exact first/last sets, an unsolved named set, and inherited
        # interior stages for every current, archived and comparison release.
        for index in sorted({0, min(1, len(profiles)-1), len(profiles)//2, len(profiles)-1}):
            assert_matches_board(client, release, profiles[index]['id'])
    encoded = response.text
    for private in ('private-pseudocode-not-in-person', 'private-compute-not-in-person',
                    'private-target-mac', 'private-payload-mac', 'private-test-password-hash'):
        assert private not in encoded
    assert not {'pseudocode', 'compute', 'challenge_id', 'ai_model'} & set(
        next(row['winner'] for release in releases.values()
             for row in release['recovery_summary']['profiles'].values() if row['winner']))


def test_earliest_lower_stage_and_receipt_ties_remain_distinct_from_highest_record(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    alice, bob = add_user(app, 'alice'), add_user(app, 'bob')
    adapter = registry.dh_v7
    lower = registered_receipt(app, alice, adapter, 1)
    middle = registered_receipt(app, bob, adapter, 3, 1)
    high = registered_receipt(app, alice, adapter, 5, 2)
    tied = registered_receipt(app, bob, adapter, 6, 2)
    release = detail(client, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)).json()['scheme']
    profiles = release['recovery_summary']['profiles']
    assert profiles['dh-v7-tier-1']['winner']['submission_id'] == lower['submission_id']
    assert profiles['dh-v7-tier-2']['winner']['submission_id'] == middle['submission_id']
    assert profiles['dh-v7-tier-4']['winner']['submission_id'] == min(high['submission_id'], tied['submission_id'])
    assert profiles['dh-v7-tier-7']['winner'] is None
    for profile in release['profiles']:
        assert_matches_board(client, release, profile['id'])
    # A later snapshot must notice a changed lower-stage winner even when the
    # highest recovered profile and old largest-record fields are unchanged.
    with app.state.db.transaction(write=True) as session:
        session.get(Submission, lower['submission_id']).correct = False
    fresh = detail(client, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)).json()['scheme']
    assert fresh['first_recovery'] == release['first_recovery']
    assert fresh['recovery_summary']['profiles']['dh-v7-tier-1']['winner']['submission_id'] == middle['submission_id']


@pytest.mark.parametrize('fault', [
    'incorrect', 'incorrect-generation', 'wrong-round', 'before-issue', 'at-deadline',
    'foreign-method', 'wrong-user', 'wrong-profile', 'wrong-version', 'unregistered-profile', 'foreign-scheme',
])
def test_invalid_receipt_cannot_hide_later_valid_snapshot_winner(current_system, fault):
    app, _, _ = current_system
    client = TestClient(app)
    alice, bob = add_user(app, 'alice'), add_user(app, 'bob')
    adapter = registry.dh_v7
    bad = registered_receipt(app, alice, adapter, 5)
    good = registered_receipt(app, bob, adapter, 5, 1)
    with app.state.db.transaction(write=True) as session:
        result = session.get(Submission, bad['submission_id'])
        challenge = session.get(Challenge, result.challenge_id)
        if fault == 'incorrect': result.correct = False
        elif fault == 'incorrect-generation': challenge.correctness_passed = False
        elif fault == 'wrong-round': result.round_id += 1
        elif fault == 'before-issue': result.received_at = challenge.issued_at - 1
        elif fault == 'at-deadline': result.received_at = challenge.ends_at
        elif fault == 'foreign-method': result.method_id = session.get(Submission, good['submission_id']).method_id
        elif fault == 'wrong-user': result.user_id = bob
        elif fault == 'wrong-profile': result.profile_id = 'dh-v7-tier-6'
        elif fault == 'wrong-version': result.scheme_version = '6'
        elif fault == 'unregistered-profile': result.profile_id = challenge.profile_id = 'unregistered-profile'
        else: challenge.scheme_id = 'rsa-kem'
    release = detail(client, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)).json()['scheme']
    for target in ('dh-v7-tier-1', 'dh-v7-tier-5', 'dh-v7-tier-6'):
        assert_matches_board(client, release, target)
    assert release['recovery_summary']['profiles']['dh-v7-tier-1']['winner']['submission_id'] == good['submission_id']
    assert bad['submission_id'] not in json.dumps(release['recovery_summary'])


def test_compatibility_belongs_to_source_profiles_and_archive_limits_stay_strict(current_system, monkeypatch):
    app, _, _ = current_system
    client = TestClient(app)
    user = add_user(app, 'archived_solver')
    old = registered_receipt(app, user, registry.kem, len(registry.kem.manifests()))  # dh-36 v2
    registered_receipt(app, user, registry.dh_v3, len(registry.dh_v3.manifests()), 1)  # dh-128 v3
    for version in ('2', '3', '4'):
        release = detail(client, ('teaching-dh-kem', version)).json()['scheme']
        for profile in release['profiles']:
            assert_matches_board(client, release, profile['id'])
    with monkeypatch.context() as restricted:
        restricted.setitem(registry.COMPATIBLE, ('teaching-dh-kem', '4'), {'2': ('dh-20',)})
        release = detail(client, ('teaching-dh-kem', '4')).json()['scheme']
        assert release['recovery_summary']['profiles']['dh-20']['winner'] is None
        assert_matches_board(client, release, 'dh-20')
        assert_matches_board(client, release, 'dh-37')
        archive = detail(client, ('teaching-dh-kem', '2')).json()['scheme']
        assert archive['recovery_summary']['profiles']['dh-20']['winner']['submission_id'] == old['submission_id']


def test_named_set_and_noninheriting_profile_do_not_credit_other_sizes(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    user = add_user(app, 'exact_solver')
    for adapter in (registry.pq_mlkem, registry.code_mceliece):
        profiles = adapter.manifests()
        assert len(profiles) > 1
        assert profiles[0].get('profile_kind') == 'standard-parameter-set' or profiles[0].get('record_inheritance') is False
        registered_receipt(app, user, adapter, len(profiles))
        release = detail(client, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)).json()['scheme']
        assert release['recovery_summary']['profiles'][profiles[0]['id']]['winner'] is None
        assert_matches_board(client, release, profiles[0]['id'])
        assert_matches_board(client, release, profiles[-1]['id'])
