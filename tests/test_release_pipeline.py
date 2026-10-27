"""Portable release guards; these tests never build, authenticate or publish."""
import base64
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_pipeline', ROOT / 'deploy/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_reviewed_contract_covers_current_source_and_all_historical_releases():
    contract = release.policy()
    release.check_contract(release.source_files(ROOT), contract)
    assert len(contract['releases']) == 32
    assert sum(row['profiles'] for row in contract['releases']) == 1466
    assert len({(r['scheme_id'], r['version']) for r in contract['releases']}) == 32
    for name in ('crucible/config.py', 'crucible/generation.py', 'crucible/generation_policy.py',
                 'crucible/scoring.py', 'crucible/platform_pins.py', 'requirements.lock',
                 'migrations/versions/0023_notebook_trash.py'):
        assert name in contract['baseline_files'] and name not in contract['mutable_files']


@pytest.mark.parametrize('name', ['requirements.lock', 'crucible/kem.py', 'crucible/config.py',
                                  'crucible/generation.py', 'crucible/platform_pins.py',
                                  'scripts/build_sntrup.py', 'migrations/versions/0023_notebook_trash.py'])
def test_runtime_changes_and_deletions_block_ordinary_release(name):
    contract = release.policy()
    changed = {**contract['baseline_files'], **contract.get('reviewed_overlay_files', {})}
    changed[name] = '0' * 64
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract(changed, contract)
    del changed[name]
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract(changed, contract)


def test_new_executable_modules_require_explicit_policy_review():
    contract = release.policy()
    changed = {**contract['baseline_files'], **contract.get('reviewed_overlay_files', {}),
               'crucible/new_worker.py': '0' * 64}
    with pytest.raises(ValueError, match='new_worker'):
        release.check_contract(changed, contract)


def test_reviewed_overlay_pins_exact_changes_and_keeps_original_inventory():
    baseline = {'crucible/config.py': 'a' * 64}
    contract = {'baseline_files': baseline.copy(), 'mutable_files': [], 'mutable_prefixes': ['web/'],
                'reviewed_overlay_files': {'crucible/config.py': 'b' * 64,
                                           'crucible/data/approved.json': 'c' * 64}}
    files = dict(contract['reviewed_overlay_files'])
    assert release.check_contract(files, contract) == sorted(files)
    assert contract['baseline_files'] == baseline
    for name in files:
        for changed in ({**files, name: 'd' * 64}, {key: value for key, value in files.items() if key != name}):
            with pytest.raises(ValueError, match='ordinary release refused'):
                release.check_contract(changed, contract)
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract({**files, 'crucible/new_worker.py': 'e' * 64}, contract)


def test_reviewed_overlay_cannot_be_bypassed_by_mutable_prefix_or_bad_digest():
    contract = {'baseline_files': {}, 'mutable_files': [], 'mutable_prefixes': ['web/'],
                'reviewed_overlay_files': {'web/pinned.js': 'a' * 64}}
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract({'web/pinned.js': 'b' * 64}, contract)
    contract['reviewed_overlay_files']['web/pinned.js'] = '*'
    with pytest.raises(ValueError, match='Invalid reviewed source overlay'):
        release.check_contract({}, contract)


def test_prepare_exact_commit_replaces_directories_including_removed_ui(tmp_path, monkeypatch):
    root, output = tmp_path / 'repo', tmp_path / 'output'
    root.mkdir()
    for name in release.SOURCE_DIRS:
        (root / name).mkdir()
    for name in release.SOURCE_FILES:
        (root / name).write_text('fixed\n')
    (root / 'web/index.html').write_text('<html>new</html>')
    (root / 'crucible/app.py').write_text('value = 1\n')
    contract = {**release.policy(), 'baseline_files': release.source_files(root), 'reviewed_overlay_files': {}}
    contract['baseline_files']['web/deleted.js'] = '0' * 64
    monkeypatch.setattr(release, 'policy', lambda: contract)
    for command in (['init'], ['add', '.'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture']):
        subprocess.run(['git', '-C', str(root), *command], check=True, capture_output=True)
    revision = release.git(root, 'rev-parse', 'HEAD')
    prepared = release.prepare(root, output, revision)
    assert prepared['source_revision'] == revision
    dockerfile = (output / 'context/Dockerfile').read_text()
    assert 'rm -rf /opt/crucible/crucible /opt/crucible/scripts /opt/crucible/web' in dockerfile
    assert 'pip ' not in dockerfile and 'apt' not in dockerfile
    assert not (output / 'context/source/web/deleted.js').exists()
    assert (output / 'context/source/web/index.html').read_text() == '<html>new</html>'
    assert stat.S_IMODE((output / 'context/source/web/index.html').stat().st_mode) == 0o444
    with pytest.raises(ValueError, match='new output'):
        release.prepare(root, output, revision)
    (root / 'web/index.html').write_text('uncommitted')
    with pytest.raises(ValueError, match='clean committed'):
        release.prepare(root, tmp_path / 'another', revision)


def credential_bytes():
    return json.dumps({'auths': {release.REGISTRY: {'auth': base64.b64encode(b'fixture:fixture-password').decode()}}}).encode()


def test_credentials_only_fixed_host_and_private_exclusive_files(tmp_path):
    directory = tmp_path / 'docker'
    release.credentials(directory, credential_bytes())
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / 'config.json').stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        release.credentials(directory, credential_bytes())
    for bad in ({'auths': {}, 'credsStore': 'helper'}, {'auths': {'evil.invalid': {'auth': 'YWJjOmRlZg=='}}}):
        with pytest.raises(ValueError):
            release.credentials(tmp_path / 'invalid', json.dumps(bad).encode())
    assert not (tmp_path / 'invalid').exists()


def test_auth_exchange_is_fixed_bounded_and_does_not_output_token(tmp_path, monkeypatch, capsys):
    observed = {}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            observed['read_limit'] = count
            return credential_bytes()
    class Opener:
        def open(self, request, timeout):
            observed.update(url=request.full_url, timeout=timeout, authorization=request.get_header('Authorization'))
            return Response()
    def opener(*handlers):
        observed['handlers'] = handlers
        return Opener()
    monkeypatch.setattr(release.urllib.request, 'build_opener', opener)
    release.authenticate(tmp_path / 'auth', 'synthetic-token-never-real')
    assert observed['url'] == 'https://api.digitalocean.com/v2/registries/crucible-beta/docker-credentials?read_write=true&expiry_seconds=3600'
    assert observed['timeout'] == 20 and observed['read_limit'] == 16385
    assert observed['handlers'][0].proxies == {}
    assert observed['handlers'][1].redirect_request(None, None, None, None, None, None) is None
    assert capsys.readouterr().out == ''


def test_publish_rejects_remote_config_drift_and_changed_local_image(tmp_path, monkeypatch):
    revision = 'a' * 40
    receipt = {'status': 'passed', 'published': False, 'local_image': 'crucible-beta-check:' + revision,
               'local_image_id': 'sha256:' + 'b' * 64, 'source_revision': revision}
    release.write_json(tmp_path / 'verified.json', receipt)
    release.write_json(tmp_path / 'build-metadata.json', {'containerimage.config.digest': 'sha256:' + 'c' * 64})
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('image', 'inspect'): return json.dumps([{'Id': receipt['local_image_id']}]).encode()
        if args[:2] == ('image', 'tag'): return b''
        if args[:2] == ('image', 'push'): return ('tag: digest: sha256:' + 'd' * 64 + ' size: 1234').encode()
        if args[:2] == ('manifest', 'inspect'): return json.dumps({'config': {'digest': 'sha256:' + 'e' * 64}}).encode()
        raise AssertionError(args)
    monkeypatch.setattr(release, 'docker', docker)
    with pytest.raises(ValueError, match='differs from tested'):
        release.publish(tmp_path, '123', '1')
    assert not (tmp_path / 'published.json').exists()
    assert calls[-1][-1] == release.REPOSITORY + '@sha256:' + 'd' * 64
    monkeypatch.setattr(release, 'docker', lambda *args, **kwargs: b'[{"Id":"changed"}]')
    with pytest.raises(ValueError, match='changed after'):
        release.publish(tmp_path, '123', '2')


def test_check_cleanup_requires_exact_container_ownership(monkeypatch):
    name = 'crucible-release-' + 'a' * 32
    image = 'crucible-beta-check:' + 'b' * 40
    image_id = 'sha256:' + 'c' * 64
    row = {'Id': 'd' * 64, 'Name': '/' + name, 'Image': image_id,
           'Config': {'Image': image, 'Labels': {'org.crucible.release-check': name}}}
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        return json.dumps([row]).encode() if args[:2] == ('container', 'inspect') else b''
    monkeypatch.setattr(release, 'docker', docker)
    release.remove_owned_check(name, image, {image_id})
    assert calls[-1] == ('rm', '-f', row['Id'])
    row['Config']['Labels']['org.crucible.release-check'] = 'another-owner'
    calls.clear()
    with pytest.raises(ValueError, match='ownership differs'):
        release.remove_owned_check(name, image, {image_id})
    assert len(calls) == 1


def test_check_cleanup_accepts_only_verified_absence_after_inspection_error(monkeypatch):
    calls = []
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('container', 'inspect'):
            raise subprocess.CalledProcessError(1, ['docker'])
        return b''
    monkeypatch.setattr(release, 'docker', docker)
    release.remove_owned_check('fixture', 'image', {'id'})
    assert calls[-1] == ('ps', '-aq', '--filter', 'name=^/fixture$')
    assert not any(args[0] == 'rm' for args in calls)


def test_workflow_is_manual_and_promotes_only_after_successful_publication():
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    assert 'workflow_dispatch:' in workflow and '\n  push:' not in workflow and '\n  pull_request:' not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'DIGITALOCEAN_REGISTRY_TOKEN' in workflow
    assert release.DOCKER == ['docker', '--host', 'unix:///var/run/docker.sock']
    assert '--network none' in workflow and '--provenance=false --load' in workflow
    assert 'deploy create' not in workflow and 'apps update' not in workflow
    assert 'group: crucible-beta-release\n  cancel-in-progress: false' in workflow
    assert workflow.index('name: Verify exact native catalogue') < workflow.index('name: Publish the tested image')
    assert workflow.index('name: Publish the tested image') < workflow.index('name: Promote the exact tested image to beta')
    assert "name: Promote the exact tested image to beta\n        if: success() && github.ref == 'refs/heads/main' && inputs.publish && inputs.promote\n" in workflow
    assert 'crucible-release/promotion.json' in workflow
    assert 'if: always()\n        run: rm -rf -- "$RUNNER_TEMP/crucible-registry-auth"' in workflow
    aggregate = (ROOT / '.github/workflows/tests.yml').read_text()
    assert 'name: CI passed' in aggregate
    assert 'needs: [portable-source-checks, critical-workflows, release-contract, browser-checks, metadata-postgresql]' in aggregate
    assert 'WORKFLOW_RESULT: ${{ needs.critical-workflows.result }}' in aggregate
    assert 'test "$WORKFLOW_RESULT" = success' in aggregate
    assert 'BROWSER_RESULT: ${{ needs.browser-checks.result }}' in aggregate
    assert 'test "$BROWSER_RESULT" = success' in aggregate
    assert 'test "$METADATA_RESULT" = success' in aggregate


@pytest.mark.parametrize('succeeded,publish,promote,expected', [
    (False, False, False, False), (False, False, True, False),
    (False, True, False, False), (False, True, True, False),
    (True, False, False, False), (True, False, True, False),
    (True, True, False, False), (True, True, True, True),
])
@pytest.mark.parametrize('ref', ['refs/heads/main', 'refs/heads/schema-bridge', 'refs/tags/main'])
def test_workflow_promotion_requires_main_success_and_both_boolean_inputs(succeeded, publish, promote, expected, ref):
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    inputs = workflow.split('    inputs:\n', 1)[1].split('\npermissions:', 1)[0]
    for name in ('publish', 'promote'):
        block = inputs.split(f'      {name}:\n', 1)[1].splitlines()[:4]
        assert block[1:] == ['        type: boolean', '        required: true', '        default: true']
    step = workflow.split('      - name: Promote the exact tested image to beta\n', 1)[1].split('      - name:', 1)[0]
    condition = step.split('        if: ', 1)[1].splitlines()[0]
    # This exact conjunction has GitHub's boolean input semantics, with no
    # success override or fallback that could promote verification-only runs.
    operands = condition.split(' && ')
    assert operands == ['success()', "github.ref == 'refs/heads/main'", 'inputs.publish', 'inputs.promote']
    values = {'success()': succeeded, "github.ref == 'refs/heads/main'": ref == 'refs/heads/main',
              'inputs.publish': publish, 'inputs.promote': promote}
    assert all(values[item] for item in operands) is (expected and ref == 'refs/heads/main')
    assert workflow.count('deploy/release.py promote ') == 1
    assert 'name: Publish the tested image\n        if: inputs.publish\n' in workflow


@pytest.mark.parametrize('ref,promote,allowed', [
    ('refs/heads/main', True, True), ('refs/heads/main', False, True),
    ('refs/heads/schema-bridge', True, False), ('refs/heads/schema-bridge', False, True),
    ('refs/tags/reviewed', True, False), ('refs/tags/reviewed', False, False),
    ('refs/pull/26/merge', True, False), ('refs/pull/26/merge', False, False),
])
def test_workflow_candidate_branches_require_promotion_disabled(ref, promote, allowed):
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    expected = "startsWith(github.ref, 'refs/heads/') && (github.ref == 'refs/heads/main' || !inputs.promote)"
    for job in ('source-checks', 'native-release'):
        # Read only job-level lines; step conditions cannot satisfy this gate.
        lines = workflow.split(f'  {job}:\n', 1)[1].splitlines()
        condition = next(line.removeprefix('    if: ') for line in lines if line.startswith('    if: '))
        assert condition == expected
    assert (ref.startswith('refs/heads/') and (ref == 'refs/heads/main' or not promote)) is allowed
    assert 'needs: source-checks' in workflow


def run_release_summary(tmp_path, monkeypatch, *, publish, promote):
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    summary = workflow.split('      - name: Release summary\n', 1)[1].split('      - name:', 1)[0]
    assert 'PUBLISH_REQUESTED: ${{ inputs.publish }}' in summary
    assert 'PROMOTE_REQUESTED: ${{ inputs.promote }}' in summary
    script = summary.split('          import json, os, pathlib\n', 1)[1].split('\n          PY', 1)[0]
    script = 'import json, os, pathlib\n' + textwrap.dedent(script)
    monkeypatch.setenv('RELEASE_OUTPUT', str(tmp_path))
    monkeypatch.setenv('GITHUB_REPOSITORY', 'jz-lu/crucible')
    monkeypatch.setenv('PUBLISH_REQUESTED', str(publish).lower())
    monkeypatch.setenv('PROMOTE_REQUESTED', str(promote).lower())
    exec(compile(script, 'release-summary', 'exec'), {})


@pytest.mark.parametrize('publish,promote', [(False, False), (False, True), (True, False), (True, True)])
def test_release_summary_distinguishes_verified_published_and_promoted(tmp_path, monkeypatch, capsys, publish, promote):
    release.write_json(tmp_path / 'verified.json', {'status': 'passed'})
    reference = release.REPOSITORY + '@sha256:' + 'a' * 64
    if publish:
        release.write_json(tmp_path / 'published.json', {
            'source_revision': 'b' * 40, 'registry_reference': reference,
            'registry_tag': 'git-' + 'b' * 40 + '-123-1',
        })
    if publish and promote:
        release.write_json(tmp_path / 'promotion.json', {
            'status': 'passed', 'channel_reference': release.REPOSITORY + ':beta',
            'observed_digest': 'sha256:' + 'a' * 64,
        })
    run_release_summary(tmp_path, monkeypatch, publish=publish, promote=promote)
    output = capsys.readouterr().out
    assert ('Published image: `' + reference + '`' in output) is publish
    assert ('Verified beta channel:' in output) is (publish and promote)
    if not publish:
        assert 'Verification-only run: publication and beta promotion were disabled.' in output
    elif not promote:
        assert 'Beta promotion was deliberately disabled' in output
        assert 'separate reviewed promotion or image selection' in output
    else:
        assert 'Live deployment has not been verified.' in output


def test_release_summary_preserves_uncertain_promotion_warning_even_if_input_disabled(tmp_path, monkeypatch, capsys):
    release.write_json(tmp_path / 'promotion.json', {
        'status': 'failed', 'promotion_attempted': True, 'observed_digest': None,
    })
    run_release_summary(tmp_path, monkeypatch, publish=True, promote=False)
    output = capsys.readouterr().out
    assert 'a tag update may already have triggered deployment' in output
    assert 'deliberately disabled' not in output
    assert 'did not update beta' not in output


@pytest.fixture
def promotion(tmp_path, monkeypatch):
    revision, config = 'a' * 40, 'sha256:' + 'b' * 64
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': config, 'size': 100},
                'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
                            'digest': 'sha256:' + 'c' * 64, 'size': 200}]}
    raw = release.canonical(manifest)
    checked = {'status': 'passed', 'published': False, 'source_revision': revision,
               'local_image': 'crucible-beta-check:' + revision, 'local_image_id': 'sha256:' + 'd' * 64,
               'native_checks': {'runtime': 'passed', **{name: 'passed' for name in release.policy()['native_test_batches']}},
               'release_sha256': 'e' * 64, 'deployment_performed': False}
    release.write_json(tmp_path / 'verified.json', checked)
    release.write_json(tmp_path / 'build-metadata.json', {'containerimage.config.digest': config})
    tag = 'git-' + revision + '-123-1'
    published = {**checked, 'published': True, 'image_config_digest': config,
                 'registry_reference': release.REPOSITORY + '@sha256:' + release.digest(raw),
                 'registry_tag': tag, 'registry_tag_reference': release.REPOSITORY + ':' + tag}
    release.write_json(tmp_path / 'published.json', published)
    state = {'root': tmp_path, 'revision': revision, 'manifest': manifest,
             'published': published, 'source_raw': raw, 'channel_raw': raw,
             'main': [revision, revision], 'events': [], 'mutation_error': None}
    def git(root, *args):
        assert root == tmp_path
        if args == ('rev-parse', 'HEAD'):
            return revision
        assert args == ('ls-remote', '--exit-code', release.GIT_REPOSITORY, 'refs/heads/main')
        state['events'].append('main')
        return state['main'].pop(0) + '\trefs/heads/main'
    def docker(*args, **kwargs):
        if args[:4] == ('buildx', 'imagetools', 'inspect', '--raw'):
            assert kwargs['timeout'] == 60
            if args[4] == release.CHANNEL:
                state['events'].append('channel-read')
                if isinstance(state['channel_raw'], Exception):
                    raise state['channel_raw']
                return state['channel_raw']
            assert args[4] == state['published']['registry_reference']
            state['events'].append('source-read')
            return state['source_raw']
        assert args == ('buildx', 'imagetools', 'create', '--prefer-index=false', '--tag',
                        release.CHANNEL, state['published']['registry_reference'])
        assert kwargs['timeout'] == 180
        # Uncertainty must be durable BEFORE a tag-changing subprocess starts.
        started = json.loads((tmp_path / 'promotion.json').read_text())
        assert started['promotion_attempted'] and started['remote_side_effects_possible']
        assert started['automatic_deployment_may_be_triggered']
        state['events'].append('promote')
        if state['mutation_error']:
            raise state['mutation_error']
        return b''
    monkeypatch.setattr(release, 'git', git)
    monkeypatch.setattr(release, 'docker', docker)
    return state


def test_promotion_preserves_exact_manifest_and_immutable_receipts(promotion):
    root = promotion['root']
    before = {name: (root / name).read_bytes() for name in ['verified.json', 'published.json']}
    result = release.promote(root, root, '123', '1')
    assert promotion['events'] == ['source-read', 'main', 'promote', 'channel-read', 'main']
    assert result['status'] == 'passed' and result['channel_digest_verified']
    assert result['expected_digest'] == result['observed_digest'] == promotion['published']['registry_reference'].split('@')[1]
    assert result['main_before'] == result['main_after'] == promotion['revision']
    assert not result['provider_configuration_changed'] and not result['live_deployment_verified']
    assert json.loads((root / 'promotion.json').read_text()) == result
    assert all((root / name).read_bytes() == value for name, value in before.items())
    with pytest.raises(ValueError, match='already recorded'):
        release.promote(root, root, '123', '1')


@pytest.mark.parametrize('invalid', ['verify-only', 'failed-test', 'incomplete-tests', 'wrong-run',
                                    'receipt-drift', 'foreign-registry', 'bad-digest', 'config-drift'])
def test_unverified_or_mismatched_release_never_starts_promotion(promotion, invalid):
    root = promotion['root']
    published = promotion['published']
    checked = json.loads((root / 'verified.json').read_text())
    if invalid == 'verify-only':
        (root / 'published.json').unlink()
    elif invalid in {'failed-test', 'incomplete-tests'}:
        checked['native_checks']['runtime'] = 'failed' if invalid == 'failed-test' else 'skipped'
        published['native_checks'] = checked['native_checks']
        release.write_json(root / 'verified.json', checked)
    elif invalid == 'wrong-run': published['registry_tag'] = 'git-' + promotion['revision'] + '-122-1'
    elif invalid == 'receipt-drift': published['local_image_id'] = 'sha256:' + '0' * 64
    elif invalid == 'foreign-registry': published['registry_reference'] = 'evil.invalid/app@sha256:' + '0' * 64
    elif invalid == 'bad-digest': published['registry_reference'] = release.REPOSITORY + '@sha256:bad'
    elif invalid == 'config-drift': published['image_config_digest'] = 'sha256:' + '0' * 64
    if invalid != 'verify-only': release.write_json(root / 'published.json', published)
    with pytest.raises((ValueError, FileNotFoundError)):
        release.promote(root, root, '123', '1')
    assert promotion['events'] == []
    assert not (root / 'promotion.json').exists()


@pytest.mark.parametrize('invalid', ['wrong-bytes', 'invalid-json', 'index', 'config-list',
                                    'wrong-config', 'duplicate-fields', 'oversized'])
def test_malformed_or_changed_source_manifest_blocks_before_tag_write(promotion, invalid):
    root = promotion['root']
    raw, manifest = promotion['source_raw'], promotion['manifest']
    if invalid == 'wrong-bytes': raw += b'\n'
    elif invalid == 'invalid-json': raw = b'not JSON'
    elif invalid == 'index':
        raw = release.canonical({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': []})
    elif invalid == 'config-list': manifest['config'] = []; raw = release.canonical(manifest)
    elif invalid == 'wrong-config': manifest['config']['digest'] = 'sha256:' + '0' * 64; raw = release.canonical(manifest)
    elif invalid == 'duplicate-fields': raw = raw[:-1] + b',"schemaVersion":2}'
    elif invalid == 'oversized': raw = b' ' * 1048577
    promotion['source_raw'] = raw
    if invalid != 'wrong-bytes':
        promotion['published']['registry_reference'] = release.REPOSITORY + '@sha256:' + release.digest(raw)
        release.write_json(root / 'published.json', promotion['published'])
    with pytest.raises(ValueError):
        release.promote(root, root, '123', '1')
    result = json.loads((root / 'promotion.json').read_text())
    assert result['status'] == 'blocked' and not result['promotion_attempted']
    assert promotion['events'] == ['source-read']


def test_stale_main_refuses_to_move_beta(promotion):
    promotion['main'][0] = 'f' * 40
    root = promotion['root']
    with pytest.raises(ValueError, match='Main advanced'):
        release.promote(root, root, '123', '1')
    result = json.loads((root / 'promotion.json').read_text())
    assert result['status'] == 'blocked' and not result['remote_side_effects_possible']
    assert promotion['events'] == ['source-read', 'main']


@pytest.mark.parametrize('failure', ['push-timeout', 'readback-timeout', 'different-manifest', 'main-race'])
def test_promotion_failure_retains_side_effect_uncertainty_and_observed_digest(promotion, failure):
    root = promotion['root']
    if failure == 'push-timeout': promotion['mutation_error'] = subprocess.TimeoutExpired('docker', 180)
    elif failure == 'readback-timeout': promotion['channel_raw'] = subprocess.TimeoutExpired('docker', 60)
    elif failure == 'different-manifest': promotion['channel_raw'] += b'\n'
    else: promotion['main'][1] = 'f' * 40
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        release.promote(root, root, '123', '1')
    result = json.loads((root / 'promotion.json').read_text())
    assert result['status'] == 'needs_review' and result['promotion_attempted']
    assert result['remote_side_effects_possible'] and result['automatic_deployment_may_be_triggered']
    assert promotion['events'].count('promote') == 1  # No hidden retry or rollback.
    if failure in {'push-timeout', 'readback-timeout'}:
        assert result['observed_digest'] is None and not result['channel_digest_verified']
    elif failure == 'different-manifest':
        assert result['observed_digest'] == 'sha256:' + release.digest(promotion['channel_raw'])
        assert result['observed_digest'] != result['expected_digest']
    else:
        assert result['channel_digest_verified'] and result['main_after'] != result['source_revision']
    assert (root / 'published.json').exists()


def test_main_lookup_rejects_malformed_or_ambiguous_output(promotion, monkeypatch):
    for response in ['a' * 40, 'a' * 40 + '\trefs/heads/other',
                     'a' * 40 + '\trefs/heads/main\n' + 'b' * 40 + '\trefs/heads/main']:
        monkeypatch.setattr(release, 'git', lambda *args: response)
        with pytest.raises(ValueError, match='current main revision'):
            release.current_main(promotion['root'])
