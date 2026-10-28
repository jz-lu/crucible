"""Publication plumbing fixtures. Invented measurements are never live evidence."""
import base64
from copy import deepcopy
import hashlib
import io
import zipfile
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from crucible import registry, resource_identity as auth, resource_publications as rp, resource_state as rs
from crucible.app import create_app
from crucible.config import Settings

NOW = 1800000000
IDENTITY = {'repository': auth.REPOSITORY, 'repository_id': auth.REPOSITORY_ID,
            'commit': 'a' * 40, 'run_id': '100', 'run_attempt': '1'}
ORIGIN = 'http://testserver'


def fixture_files(identity=None, *, parameters=None, sizes=None):
    identity = identity or IDENTITY
    scheme, release = 'dhkem-x25519', '1'
    profile = registry.presentation_manifests(registry.adapter_for(scheme, release))[0]
    implementation = {'id': 'test-only', 'fixture': 'Invented measurements; never publish'}
    impl = {'id': implementation['id'], 'sha256': rp.digest(implementation)}
    parameters = {'fixture': 1} if parameters is None else parameters; spec = 'b' * 64
    config = rp.digest({'format': 'crucible-configuration-v1', 'scheme': scheme, 'release': release, 'specification': spec, 'parameters': parameters})
    sizes = sizes or {'public_key': 32, 'ciphertext': 32, 'private_key': 32, 'shared_secret': 32}
    declared = {'id': config, 'parameters': parameters, 'sizes': sizes}
    definition = {'scheme': scheme, 'release': release, 'specification_sha256': spec, 'configurations': [declared]}
    binding = {'scheme_id': scheme, 'version': release, 'scheme': scheme, 'release': release,
        'definition_sha256': rp.digest(definition), 'specification_sha256': spec, 'implementation': impl,
        'profiles': [{'profile_id': profile['profile_id'], 'configuration': config,
                      'protocol_sha256': rp.digest({k: profile.get(k) for k in rp.PROFILE_FIELDS})}]}
    ops = ('keygen', 'encapsulate', 'decapsulate')
    native = {'implementation': impl, 'comparison_profile': 'c' * 64,
        'computation': {op: {'complete': True, 'duration_ns': {'n': 100, 'median': 1000}, 'recorded': 100, 'planned': 100} for op in ops},
        'memory_components': {op + ':rss': {'complete': True, 'values': {'n': 5, 'max': 1024}} for op in ops}}
    resource = {'scheme': scheme, 'release': release, 'configuration': config, 'parameters': parameters, 'sizes_bytes': sizes,
        'native_evidence': [native], 'security': {'test': 'must not enter website security'}, 'correctness': {'test': 'must not replace correctness'}}
    for field, n in (('stack_evidence', 5), ('memory_evidence', 2)):
        resource[field] = [{'implementation': impl, 'comparison_profile': 'c' * 64,
                           'operations': {op: {'measured': n, 'planned': n, 'max_observed_bytes': 500} for op in ops}}]
    plan = {'scheme': scheme, 'release': release, 'definition_sha256': rp.digest(definition), 'implementation': implementation, 'configurations': [declared]}
    plan['sha256'] = rp.digest(plan)
    outcomes = {'plan_sha256': plan['sha256'], 'status': 'complete', 'outcomes': [{'configuration': config, **{k: {'status': 'measured'} for k in ('native', 'rss', 'stack', 'massif')}}]}
    files = {'plan.json': plan, 'workflow.json': outcomes, 'declaration/definition.json': definition, 'resources.json': {'configurations': [resource]}}
    publication = {'format': 'crucible-website-publication-v2', 'source': identity, 'binding': binding,
                   'outcome': 'complete', 'expected': activation_for(binding)}
    return files, publication


def ec_stage9_fixture():
    # Exact public Stage 9 parameters at the first observed projection failure.
    # Use the existing one-profile plumbing fixture; this is not an EC report
    # or a scientific parameter mapping. The retained real EC ZIP separately
    # exercises the complete 50-profile mapping without executing adapters.
    profile = registry.presentation_manifests(registry.adapter_for('dhkem-x25519', 'curve-compressed-v2'))[8]
    assert profile['profile_id'] == 'ec-compressed-v2-tier-9'
    parameters = {**profile['parameters'], 'transcript_profile': profile['profile_id']}
    files, publication = fixture_files(parameters=parameters,
        sizes={'public_key': 9, 'private_key': 9, 'ciphertext': 9, 'shared_secret': 32})
    files['resources.json']['configurations'][0]['parameters'] = {
        'a': {'type': 'integer', 'decimal': '98423059801381720'},
        'b': {'type': 'integer', 'decimal': '34033973960659062'},
        'p': {'type': 'integer', 'decimal': '98423059801381723'},
        'q': {'type': 'integer', 'decimal': '98423059305967943'},
        'g': [4, 2382486990629884], 'transcript_profile': 'ec-compressed-v2-tier-9'}
    return files, publication


def test_exact_ec_stage9_browser_projection_keeps_canonical_configuration_identity():
    files, publication = ec_stage9_fixture()
    row = files['resources.json']['configurations'][0]
    assert row['parameters'] != files['plan.json']['configurations'][0]['parameters']
    accepted, resources = rp.validate_bundle(archive(files, publication), IDENTITY)
    assert resources['configurations'][0] == row
    assert accepted['binding']['profiles'][0]['configuration'] == row['configuration']


@pytest.mark.parametrize('bad', [
    {'type': 'integer', 'decimal': '98423059801381724'},
    {'type': 'integer', 'decimal': '098423059801381723'},
    {'type': 'integer', 'decimal': '+98423059801381723'},
    {'type': 'integer', 'decimal': '9.8423059801381723e16'},
    {'type': 'integer', 'decimal': 98423059801381723},
    {'type': 'integer', 'decimal': '98423059801381723', 'extra': True},
    {'decimal': '98423059801381723'},
    {'type': 'number', 'decimal': '98423059801381723'},
    98423059801381723, float(98423059801381723), True,
])
def test_reject_noncanonical_or_changed_large_integer_projection(bad):
    files, publication = ec_stage9_fixture()
    files['resources.json']['configurations'][0]['parameters']['p'] = bad
    with pytest.raises(ValueError, match='Configuration resource identity differs'):
        rp.validate_bundle(archive(files, publication), IDENTITY)


def test_nested_signed_boundary_projection_and_sizes_are_exact():
    limit = 2**53 - 1
    parameters = {'nested': [limit, -limit, limit + 1, -limit - 1, True, {'n': 1}]}
    files, publication = fixture_files(parameters=parameters, sizes={'public_key': limit + 1})
    row = files['resources.json']['configurations'][0]
    row['parameters'] = {'nested': [limit, -limit,
        {'type': 'integer', 'decimal': '9007199254740992'},
        {'type': 'integer', 'decimal': '-9007199254740992'}, True, {'n': 1}]}
    row['sizes_bytes'] = {'public_key': {'type': 'integer', 'decimal': '9007199254740992'}}
    rp.validate_bundle(archive(files, publication), IDENTITY)
    for bad in (1.0, True):
        broken = deepcopy(files)
        broken['resources.json']['configurations'][0]['parameters']['nested'][-1]['n'] = bad
        with pytest.raises(ValueError, match='Configuration resource identity differs'):
            rp.validate_bundle(archive(broken, publication), IDENTITY)
    broken = deepcopy(files)
    broken['resources.json']['configurations'][0]['sizes_bytes']['public_key']['decimal'] = '9007199254740993'
    with pytest.raises(ValueError, match='Configuration resource identity differs'):
        rp.validate_bundle(archive(broken, publication), IDENTITY)


def test_configuration_hash_cannot_be_rebased_to_the_browser_projection():
    files, publication = ec_stage9_fixture()
    binding = publication['binding']; row = files['resources.json']['configurations'][0]
    projected_hash = rp.digest({'format': 'crucible-configuration-v1', 'scheme': binding['scheme'],
        'release': binding['release'], 'specification': binding['specification_sha256'], 'parameters': row['parameters']})
    assert projected_hash != row['configuration']
    row['configuration'] = binding['profiles'][0]['configuration'] = projected_hash
    files['plan.json']['configurations'][0]['id'] = projected_hash
    files['workflow.json']['outcomes'][0]['configuration'] = projected_hash
    binding['definition_sha256'] = files['plan.json']['definition_sha256'] = rp.digest(files['declaration/definition.json'])
    plan = files['plan.json']; plan['sha256'] = rp.digest({key: value for key, value in plan.items() if key != 'sha256'})
    files['workflow.json']['plan_sha256'] = plan['sha256']
    publication['expected'] = activation_for(binding)
    with pytest.raises(ValueError, match='Parameter hash differs'):
        rp.validate_bundle(archive(files, publication), IDENTITY)


def desired_document(binding, source=IDENTITY, producer='d' * 64):
    return {'format': 'crucible-resource-desired-v1', 'source': source, 'bindings': [{
        'binding': binding, 'producer_sha256': producer,
        'fingerprint': rp.digest({'binding': binding, 'producer': producer})}]}


def activation_for(binding, source=IDENTITY, producer='d' * 64):
    document = desired_document(binding, source, producer)
    entry = document['bindings'][0]
    return {'activation_id': rp.digest({'format': 'crucible-resource-activation-v1',
        'snapshot_id': rp.digest(document), 'scheme_id': binding['scheme_id'], 'version': binding['version'],
        'fingerprint': entry['fingerprint']}), 'fingerprint': entry['fingerprint'], 'producer_sha256': producer}


def announce(db, source=IDENTITY, producer='d' * 64, binding=None):
    binding = binding or fixture_files()[1]['binding']
    return rs.ingest_control(db, desired_document(binding, source, producer), source, NOW, rp.validate_binding)


def archive(files, publication, *, extra=None):
    raw_files = {k: rp.canonical(v) for k, v in files.items()}
    manifest = {'format': 'crucible-evaluation-bundle-v1', 'plan_sha256': files['plan.json']['sha256'],
        'files': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in raw_files.items()]}
    manifest_raw = rp.canonical(manifest)
    publication = {**publication, 'evaluation_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
                   'resources_sha256': hashlib.sha256(raw_files['resources.json']).hexdigest()}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('publication.json', rp.canonical(publication)); z.writestr('bundle/manifest.json', manifest_raw)
        for name, raw in raw_files.items(): z.writestr('bundle/' + name, raw)
        if extra: z.writestr(extra, b'Unlisted')
    return stream.getvalue()


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv('CRUCIBLE_RESOURCE_PUBLISHING', 'true')
    # Portable HTTP fixture: keep the real X25519 declaration/identity checks,
    # without requiring unrelated Linux native libraries in source CI.
    adapter = registry.adapter_for('dhkem-x25519', '1')
    monkeypatch.setattr(registry, 'ADAPTERS', {('dhkem-x25519', '1'): adapter})
    monkeypatch.setattr(registry, 'CURRENT', {'dhkem-x25519': '1'})
    app = create_app(Settings(f"sqlite:///{tmp_path/'resource.db'}", b'r' * 32, origins=(ORIGIN,), rate_limits=False), clock=lambda: NOW, initialize=True)
    announce(app.state.db)
    yield app, TestClient(app)
    app.state.db.engine.dispose()


def test_retained_releases_idempotency_and_older_job_cannot_rollback(system):
    app, client = system
    assert client.get('/api/protocol/resources?scheme_id=dhkem-x25519&version=1').json()['publication'] is None
    raw = archive(*fixture_files())
    first = rp.ingest(app.state.db, raw, IDENTITY, NOW)
    assert first['current'] and rp.ingest(app.state.db, raw, IDENTITY, NOW + 1) == first
    newer = {**IDENTITY, 'run_id': '101'}
    newraw = archive(*fixture_files(newer)); second = rp.ingest(app.state.db, newraw, newer, NOW + 2)
    assert second['current'] and not rp.ingest(app.state.db, raw, IDENTITY, NOW + 3)['current']
    response = client.get('/api/protocol/resources?scheme_id=dhkem-x25519&version=1').json()
    assert response['publication']['id'] == second['publication_id']
    assert 'security' not in response['resources']['configurations'][0]
    assert 'correctness' not in response['resources']['configurations'][0]
    assert client.get(response['publication']['evidence_url']).content == newraw
    files, pub = fixture_files(); files['resources.json']['invented_extra'] = 'same run, different bytes'
    with pytest.raises(HTTPException) as exc: rp.ingest(app.state.db, archive(files, pub), IDENTITY, NOW)
    assert exc.value.status_code == 409


@pytest.mark.parametrize('mutation', ['profile', 'implementation', 'incomplete', 'parameters', 'path', 'source', 'samples'])
def test_reject_bad_bindings_incomplete_or_escaping_evidence(system, mutation):
    app, _ = system; files, pub = fixture_files(); extra = None
    if mutation == 'profile': pub['binding']['profiles'][0]['protocol_sha256'] = '0' * 64
    if mutation == 'implementation': pub['binding']['implementation']['id'] = 'unapproved'
    if mutation == 'incomplete': files['workflow.json']['outcomes'][0]['massif']['status'] = 'failed'
    if mutation == 'parameters': files['resources.json']['configurations'][0]['parameters'] = {'fixture': 2}
    if mutation == 'path': extra = '../escape'
    if mutation == 'source': pub['source'] = {**IDENTITY, 'commit': 'd' * 40}
    if mutation == 'samples': files['resources.json']['configurations'][0]['native_evidence'][0]['computation']['keygen']['duration_ns']['n'] = 99
    with pytest.raises(HTTPException) as exc: rp.ingest(app.state.db, archive(files, pub, extra=extra), IDENTITY, NOW)
    assert exc.value.status_code == 422
    with app.state.db.transaction() as session: assert rp.latest(session, 'dhkem-x25519', '1') is None


def b64(raw): return base64.urlsafe_b64encode(raw).decode().rstrip('=')


@pytest.fixture(scope='module')
def signing_key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()
    return private, [{'kid': 'fixture', 'kty': 'RSA', 'use': 'sig', 'alg': 'RS256',
        'n': b64(numbers.n.to_bytes(256, 'big')), 'e': b64(numbers.e.to_bytes(3, 'big'))}]


def token(private, **overrides):
    claims = {'iss': auth.ISSUER, 'aud': ORIGIN + auth.UPLOAD_PATH, 'repository': auth.REPOSITORY,
        'repository_id': auth.REPOSITORY_ID, 'repository_owner_id': auth.OWNER_ID,
        'sub': f'repo:countableclouds@{auth.OWNER_ID}/crucible-schemes@{auth.REPOSITORY_ID}:ref:refs/heads/main',
        'ref': 'refs/heads/main', 'ref_type': 'branch', 'workflow_ref': auth.WORKFLOW,
        'workflow_sha': IDENTITY['commit'], 'sha': IDENTITY['commit'], 'event_name': 'push',
        'run_id': '100', 'run_attempt': '1', 'iat': NOW - 10, 'nbf': NOW - 10, 'exp': NOW + 290, **overrides}
    message = b64(rp.canonical({'typ': 'JWT', 'alg': 'RS256', 'kid': 'fixture'})) + '.' + b64(rp.canonical(claims))
    return 'Bearer ' + message + '.' + b64(private.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256()))


@pytest.mark.parametrize('change', [{}, {'aud': 'https://other.invalid'}, {'repository_id': '1'}, {'repository_owner_id': '1'},
    {'ref': 'refs/heads/other'}, {'workflow_ref': auth.WORKFLOW.replace('publish-resources', 'evaluate')},
    {'event_name': 'pull_request'}, {'exp': NOW - 1}, {'workflow_sha': 'b' * 40}, {'sub': 'repo:someone/fork:ref:refs/heads/main'}])
def test_signed_identity_pins_workflow_repository_audience_and_expiry(signing_key, change):
    private, keys = signing_key
    if change:
        with pytest.raises(HTTPException) as exc: auth.verify(token(private, **change), ORIGIN + auth.UPLOAD_PATH, NOW, keys=keys)
        assert exc.value.status_code == 401
    else: assert auth.verify(token(private), ORIGIN + auth.UPLOAD_PATH, NOW, keys=keys) == IDENTITY


@pytest.mark.parametrize('job_claims', [{}, {'job_workflow_ref': auth.WORKFLOW, 'job_workflow_sha': IDENTITY['commit']}])
def test_real_signed_upload_boundary_and_beta_read_privacy(system, signing_key, monkeypatch, job_claims):
    app, client = system; private, keys = signing_key
    monkeypatch.setattr(auth, 'signing_keys', lambda _: keys)
    raw = archive(*fixture_files())
    assert client.post(auth.UPLOAD_PATH, content=raw, headers={'Content-Type': 'application/zip'}).status_code == 401
    response = client.post(auth.UPLOAD_PATH, content=raw, headers={'Content-Type': 'application/zip', 'Authorization': token(private, **job_claims)})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert client.post(auth.UPLOAD_PATH, content=raw, headers={'Content-Type': 'application/zip', 'Authorization': token(private), 'Origin': ORIGIN}).status_code == 401
    assert client.post('/api/notebook/notes', json={}, headers={'Authorization': token(private)}).status_code == 401
    object.__setattr__(app.state.settings, 'access_mode', 'beta')
    assert client.get('/api/protocol/resources?scheme_id=dhkem-x25519&version=1').status_code == 401
    assert client.get('/api/resource-publications/' + response.json()['publication_id'] + '/download').status_code == 401


@pytest.mark.parametrize('job_claims', [
    {}, {'job_workflow_ref': auth.WORKFLOW, 'job_workflow_sha': IDENTITY['commit']},
])
def test_direct_job_identity_accepts_only_the_same_workflow_source(signing_key, job_claims):
    private, keys = signing_key
    assert auth.verify(token(private, event_name='workflow_dispatch', **job_claims),
                       ORIGIN + auth.UPLOAD_PATH, NOW, keys=keys) == IDENTITY


@pytest.mark.parametrize('change', [
    {'job_workflow_ref': auth.WORKFLOW.replace('publish-resources', 'evaluate')},
    {'job_workflow_ref': auth.WORKFLOW.replace('refs/heads/main', 'refs/heads/other')},
    {'job_workflow_ref': ''}, {'job_workflow_ref': None},
    {'job_workflow_sha': None}, {'job_workflow_sha': 'b' * 40}, {'job_workflow_sha': ''},
    {'environment': 'production'}, {'repository_id': '1'}, {'repository_owner_id': '1'},
    {'workflow_ref': auth.WORKFLOW.replace('publish-resources', 'evaluate')},
    {'workflow_sha': 'b' * 40}, {'ref': 'refs/heads/other'}, {'aud': 'https://other.invalid'},
])
def test_job_claims_do_not_delegate_or_bypass_identity_checks(signing_key, change):
    private, keys = signing_key
    claims = {'event_name': 'workflow_dispatch', 'job_workflow_ref': auth.WORKFLOW,
              'job_workflow_sha': IDENTITY['commit'], **change}
    with pytest.raises(HTTPException) as exc:
        auth.verify(token(private, **claims), ORIGIN + auth.UPLOAD_PATH, NOW, keys=keys)
    assert exc.value.status_code == 401


def test_matching_job_claims_still_require_a_valid_signature(signing_key):
    private, keys = signing_key
    value = token(private, event_name='workflow_dispatch', job_workflow_ref=auth.WORKFLOW,
                  job_workflow_sha=IDENTITY['commit'])
    prefix, signature = value.rsplit('.', 1)
    altered = ('A' if signature[0] != 'A' else 'B') + signature[1:]
    with pytest.raises(HTTPException) as exc:
        auth.verify(prefix + '.' + altered, ORIGIN + auth.UPLOAD_PATH, NOW, keys=keys)
    assert exc.value.status_code == 401


def test_authenticated_upload_bounds_body_and_expanded_archive(system, signing_key, monkeypatch):
    app, client = system; private, keys = signing_key
    monkeypatch.setattr(auth, 'signing_keys', lambda _: keys)
    raw = archive(*fixture_files())
    headers = {'Content-Type': 'application/zip', 'Authorization': token(private)}
    with monkeypatch.context() as scoped:
        scoped.setattr(rp, 'MAX_UPLOAD', len(raw) - 1)
        assert client.post(auth.UPLOAD_PATH, content=raw, headers=headers).status_code == 413
    with monkeypatch.context() as scoped:
        scoped.setattr(rp, 'MAX_EXPANDED', 1)
        assert client.post(auth.UPLOAD_PATH, content=raw, headers=headers).status_code == 422
    with app.state.db.transaction() as session:
        assert rp.latest(session, 'dhkem-x25519', '1') is None
