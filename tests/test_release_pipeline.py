"""Portable release guards; these tests never build, authenticate or publish."""
import base64
import importlib.util
import json
from pathlib import Path
import stat
import subprocess

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
    changed = dict(contract['baseline_files'])
    changed[name] = '0' * 64
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract(changed, contract)
    del changed[name]
    with pytest.raises(ValueError, match='ordinary release refused'):
        release.check_contract(changed, contract)


def test_new_executable_modules_require_explicit_policy_review():
    contract = release.policy()
    changed = {**contract['baseline_files'], 'crucible/new_worker.py': '0' * 64}
    with pytest.raises(ValueError, match='new_worker'):
        release.check_contract(changed, contract)


def test_prepare_exact_commit_replaces_directories_including_removed_ui(tmp_path, monkeypatch):
    root, output = tmp_path / 'repo', tmp_path / 'output'
    root.mkdir()
    for name in release.SOURCE_DIRS:
        (root / name).mkdir()
    for name in release.SOURCE_FILES:
        (root / name).write_text('fixed\n')
    (root / 'web/index.html').write_text('<html>new</html>')
    (root / 'crucible/app.py').write_text('value = 1\n')
    contract = {**release.policy(), 'baseline_files': release.source_files(root)}
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


def test_workflow_is_manual_publish_only_with_always_credential_cleanup():
    workflow = (ROOT / '.github/workflows/release-beta.yml').read_text()
    assert 'workflow_dispatch:' in workflow and '\n  push:' not in workflow and '\n  pull_request:' not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert 'DIGITALOCEAN_REGISTRY_TOKEN' in workflow
    assert release.DOCKER == ['docker', '--host', 'unix:///var/run/docker.sock']
    assert '--network none' in workflow and '--provenance=false --load' in workflow
    assert 'deploy create' not in workflow and 'apps update' not in workflow
    assert 'if: always()\n        run: rm -rf -- "$DOCKER_CONFIG"' in workflow
    aggregate = (ROOT / '.github/workflows/tests.yml').read_text()
    assert 'name: CI passed' in aggregate and 'needs: [portable-source-checks, release-contract]' in aggregate
