#!/usr/bin/env python3
"""Publish-only beta releases from an explicitly reviewed, unchanged runtime.

No database, migration, implementation admission, provider deployment or secrets
are part of the image. Runtime changes require a new reviewed contract.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = Path(__file__).with_name('release-runtime.json')
SOURCE_DIRS = ('crucible', 'scripts', 'web', 'docs', 'migrations')
SOURCE_FILES = ('requirements.lock', 'pyproject.toml', 'alembic.ini')
REGISTRY = 'registry.digitalocean.com'
REPOSITORY = REGISTRY + '/crucible-beta/app'
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']


class ReleaseError(ValueError):
    """An operator-readable fixed message; never raw provider error text."""


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def policy():
    return json.loads(POLICY_PATH.read_text())


def runtime_path(name):
    return name.split('/')[0] in SOURCE_DIRS or name in SOURCE_FILES


def source_files(root):
    """Inventory only the source tree; never traverse data directories or links."""
    result = {}
    for name in SOURCE_DIRS:
        directory = root / name
        require(directory.is_dir() and not directory.is_symlink(), 'Missing source directory: ' + name)
        for path in sorted(directory.rglob('*')):
            require(not path.is_symlink(), 'Symlink in runtime source')
            if '__pycache__' in path.parts:
                continue
            if path.is_file():
                result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    for name in SOURCE_FILES:
        path = root / name
        require(path.is_file() and not path.is_symlink(), 'Missing runtime input: ' + name)
        result[name] = digest(path.read_bytes())
    return result


def check_contract(files, contract):
    baseline = contract['baseline_files']
    changed = sorted(name for name in files.keys() | baseline.keys() if files.get(name) != baseline.get(name))
    allowed = set(contract['mutable_files'])
    blocked = [name for name in changed if name not in allowed and
               not any(name.startswith(prefix) for prefix in contract['mutable_prefixes'])]
    require(not blocked, 'Reviewed runtime changed; ordinary release refused: ' + ', '.join(blocked))
    return changed


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], timeout=30).decode().strip()


def prepare(root, output, revision):
    require(re.fullmatch('[0-9a-f]{40}', revision), 'Expected full Git commit')
    require(git(root, 'rev-parse', 'HEAD') == revision, 'Checkout differs from selected commit')
    require(not git(root, 'status', '--porcelain'), 'Release requires a clean committed checkout')
    require(not output.exists() and not output.is_relative_to(root), 'Use a new output directory outside the checkout')
    files = source_files(root)
    tracked = set(git(root, 'ls-files', '-z').split('\0'))
    require(set(files) <= tracked, 'Untracked runtime input')
    require({name for name in tracked if runtime_path(name)} == set(files), 'Runtime inventory differs from Git')
    contract = policy()
    changed = check_contract(files, contract)
    output.mkdir(parents=True)
    context = output / 'context'
    context.mkdir()
    for name in files:
        target = context / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
        target.chmod(0o444)
    release_id = 'beta-git-' + revision
    inventory = {'format': 'crucible-release-source-v1', 'source_revision': revision,
                 'base_image': contract['app_base'], 'base_config': contract['app_base_config'],
                 'files': files, 'changed_paths': changed,
                 'runtime_contract_sha256': digest(POLICY_PATH.read_bytes())}
    release = {'format': 'crucible-host-release-v1', 'release_id': release_id,
               'source_revision': revision, 'runtime_platform': 'Linux x86_64 Python ' + contract['python_version'],
               'runtime_inventory_file': 'RELEASE_SOURCE.json',
               'runtime_inventory_sha256': digest(canonical(inventory)),
               'review_evidence': 'Retained reviewed runtime; this release requires native image verification before publication.'}
    metadata = context / 'metadata'
    metadata.mkdir()
    (metadata / 'RELEASE_SOURCE.json').write_bytes(canonical(inventory))
    write_json(metadata / 'RELEASE.json', release)
    (metadata / 'SOURCE_REVISION').write_text(revision + '\n')
    for path in metadata.iterdir():
        path.chmod(0o444)
    # Replace complete source directories. COPY alone would leave deleted files
    # from the base image in production. Native directories and venv are retained.
    (context / 'Dockerfile').write_text(
        'FROM ' + contract['app_base'] + '\nUSER 0:0\n'
        'RUN rm -rf /opt/crucible/crucible /opt/crucible/scripts /opt/crucible/web /opt/crucible/docs /opt/crucible/migrations\n'
        'COPY --chown=0:0 source/ /opt/crucible/\n'
        'COPY --chown=0:0 metadata/ /opt/crucible/\nUSER 10001:10001\n'
        'LABEL org.opencontainers.image.revision="' + revision + '" org.crucible.release="' + release_id + '"\n')
    for path in context.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)
    prepared = {'format': 'crucible-release-prepared-v1', 'source_revision': revision,
                'release_id': release_id, 'release_sha256': digest((metadata / 'RELEASE.json').read_bytes()),
                'runtime_contract_sha256': digest(POLICY_PATH.read_bytes()),
                'retained_backup_image': contract['retained_backup_image'], 'changed_paths': changed}
    write_json(output / 'prepared.json', prepared)
    shutil.copy2(POLICY_PATH, output / POLICY_PATH.name)
    shutil.copy2(Path(__file__), output / 'release.py')
    return prepared


def verify_runtime(root, contract):
    """Executed inside the exact new image, without app startup or database."""
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Native Linux AMD64 required')
    require(platform.python_version() == contract['python_version'], 'Python runtime changed')
    require(os.geteuid() == 10001 and os.getegid() == 10001, 'Unexpected image user')
    inventory = json.loads((root / 'RELEASE_SOURCE.json').read_text())
    release = json.loads((root / 'RELEASE.json').read_text())
    require(digest(canonical(inventory)) == release['runtime_inventory_sha256'], 'Release inventory mismatch')
    require(inventory['runtime_contract_sha256'] == digest(POLICY_PATH.read_bytes()), 'Runtime contract mismatch')
    require(source_files(root) == inventory['files'], 'Image source differs from exact commit')
    require((root / 'SOURCE_REVISION').read_text().strip() == release['source_revision'] == inventory['source_revision'], 'Revision mismatch')
    check_contract(inventory['files'], contract)
    sys.path.insert(0, str(root))
    from crucible import registry
    require(Path(registry.__file__).resolve() == root / 'crucible/registry.py', 'Wrong registry import')
    registry.validate_registry()
    expected = {(r['scheme_id'], r['version']): r for r in contract['releases']}
    require(set(registry.ADAPTERS) == set(expected), 'Registered release set changed')
    profiles = 0
    for identity, row in expected.items():
        manifests = registry.adapter_for(*identity).manifests()
        require(len(manifests) == row['profiles'] and digest(canonical(manifests)) == row['manifests_sha256'], 'Reviewed scheme manifest changed: ' + repr(identity))
        profiles += len(manifests)
    require(len(expected) == 32 and profiles == 1466, 'Unexpected reviewed catalogue')
    require(not any(name in sys.modules for name in ('crucible.app', 'crucible.db', 'crucible.config')), 'Unexpected application initialization')
    return {'status': 'passed', 'releases': 32, 'profiles': profiles, 'database_access': False,
            'source_revision': release['source_revision']}


def docker(*args, timeout=120):
    return subprocess.check_output([*DOCKER, *args], timeout=timeout)


def remove_owned_check(name, image, image_ids):
    try:
        container, = json.loads(docker('container', 'inspect', name, timeout=20))
    except subprocess.CalledProcessError:
        require(not docker('ps', '-aq', '--filter', 'name=^/' + name + '$', timeout=20).strip(),
                'Container cleanup needs operator inspection')
        return
    require(container['Name'] == '/' + name and container['Config']['Image'] == image and
            container['Config']['Labels'].get('org.crucible.release-check') == name and
            container['Image'] in image_ids and re.fullmatch('[0-9a-f]{64}', container['Id']),
            'Refusing cleanup of a container whose ownership differs')
    docker('rm', '-f', container['Id'], timeout=20)


def check_image(root, output, image):
    require(re.fullmatch(r'crucible-beta-check:[0-9a-f]{40}', image), 'Unexpected local image tag')
    contract = policy()
    inspected, = json.loads(docker('image', 'inspect', image))
    require(inspected['Os'] == 'linux' and inspected['Architecture'] == 'amd64' and
            inspected['Config']['User'] == '10001:10001', 'Unexpected native image configuration')
    prepared = json.loads((output / 'prepared.json').read_text())
    build = json.loads((output / 'build-metadata.json').read_text())
    config_digest = build['containerimage.config.digest']
    require(re.fullmatch('sha256:[0-9a-f]{64}', config_digest), 'Missing built image config identity')
    require(prepared['source_revision'] == image.split(':')[1], 'Image/source mismatch')
    evidence = output / 'evidence'
    evidence.mkdir()
    results = {}
    batches = {'runtime': ['-B', '/checks/release.py', 'verify-runtime']}
    for name, files in contract['native_test_batches'].items():
        batches[name] = ['-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
                         '-o', 'faulthandler_timeout=30', *['/tests/' + f for f in files]]
    for label, arguments in batches.items():
        name = 'crucible-release-' + uuid.uuid4().hex
        command = [*DOCKER, 'run', '--name', name, '--label', 'org.crucible.release-check=' + name,
                   '--platform', 'linux/amd64', '--network', 'none',
                   '--read-only', '--cpus', '2', '--memory', '1g', '--memory-swap', '1g', '--pids-limit', '128',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
                   '--mount', 'type=bind,src=' + str(output) + ',dst=/checks,readonly',
                   '--mount', 'type=bind,src=' + str(root / 'tests') + ',dst=/tests,readonly',
                   '--entrypoint', '/opt/venv/bin/python', image, *arguments]
        try:
            with (evidence / (label + '.log')).open('xb') as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            require(completed.returncode == 0, 'Native release check failed: ' + label)
            if label != 'runtime':
                log_text = (evidence / (label + '.log')).read_text()
                require(' passed' in log_text and ' skipped' not in log_text, 'Native test suite did not execute completely: ' + label)
            results[label] = 'passed'
        finally:
            # UUID name belongs to this disposable check only. No service or
            # unrelated container is selected, even when the test times out.
            remove_owned_check(name, image, {inspected['Id'], config_digest})
    receipt = {**prepared, 'status': 'passed', 'native_checks': results, 'local_image': image,
               'local_image_id': inspected['Id'], 'deployment_performed': False, 'published': False}
    write_json(output / 'verified.json', receipt)
    return receipt


def credentials(directory, raw):
    require(len(raw) <= 16384, 'Invalid registry credential input')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate registry credential field')
            value[key] = item
        return value
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'auths'} and
            type(value['auths']) is dict and set(value['auths']) == {REGISTRY}, 'Unexpected Docker credential hosts')
    entry = value['auths'][REGISTRY]
    require(type(entry) is dict and set(entry) == {'auth'} and type(entry['auth']) is str, 'Credential helpers are not accepted')
    decoded = base64.b64decode(entry['auth'], validate=True)
    require(base64.b64encode(decoded).decode() == entry['auth'], 'Noncanonical registry authentication')
    user, separator, password = decoded.partition(b':')
    require(separator and user and password and all(32 <= b <= 126 for b in decoded), 'Invalid registry authentication')
    directory.mkdir(mode=0o700)
    with open(directory / 'config.json', 'x', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(value, stream)


def authenticate(directory, token):
    """Exchange the repository token for a fresh one-hour registry credential."""
    require(not directory.exists(), 'Use a new registry authentication directory')
    require(type(token) is str and 20 <= len(token) <= 4096 and
            all(33 <= ord(c) <= 126 for c in token), 'Missing registry API token')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        'https://api.digitalocean.com/v2/registries/crucible-beta/docker-credentials?read_write=true&expiry_seconds=3600',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    with opener.open(request, timeout=20) as response:
        require(response.status == 200, 'Registry authentication exchange failed')
        raw = response.read(16385)
    credentials(directory, raw)


def publish(output, run_id, attempt):
    require(re.fullmatch('[0-9]+', run_id) and re.fullmatch('[0-9]+', attempt), 'Expected GitHub run identity')
    receipt = json.loads((output / 'verified.json').read_text())
    require(receipt['status'] == 'passed' and receipt['published'] is False, 'Verified image required')
    local = receipt['local_image']
    inspected, = json.loads(docker('image', 'inspect', local))
    require(inspected['Id'] == receipt['local_image_id'], 'Image changed after verification')
    build = json.loads((output / 'build-metadata.json').read_text())
    config_digest = build['containerimage.config.digest']
    require(re.fullmatch('sha256:[0-9a-f]{64}', config_digest), 'Missing exact built config identity')
    tag = 'git-' + receipt['source_revision'] + '-' + run_id + '-' + attempt
    target = REPOSITORY + ':' + tag
    docker('image', 'tag', local, target)
    # The SHA + unique GitHub run/attempt makes this a fresh, non-reused tag.
    # The digest, not tag mutability, is authoritative after publication.
    raw = docker('image', 'push', target, timeout=900).decode()
    matches = re.findall(r'digest: (sha256:[0-9a-f]{64})', raw)
    require(len(matches) == 1, 'Uncertain push outcome; inspect registry before retrying')
    reference = REPOSITORY + '@' + matches[0]
    remote = json.loads(docker('manifest', 'inspect', reference))
    require(remote['config']['digest'] == config_digest, 'Published image config differs from tested build')
    receipt.update(published=True, registry_reference=reference, registry_tag=tag,
                   registry_tag_reference=target,
                   image_config_digest=config_digest, deployment_performed=False)
    write_json(output / 'published.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['contract', 'prepare', 'verify-runtime', 'check-image', 'authenticate', 'publish'])
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--image')
    parser.add_argument('--run-id')
    parser.add_argument('--attempt')
    args = parser.parse_args()
    if args.command == 'contract':
        result = {'status': 'passed', 'changed_paths': check_contract(source_files(args.source), policy()),
                  'scope': 'Source contract only; native verification not run'}
    elif args.command == 'verify-runtime':
        result = verify_runtime(Path('/opt/crucible'), policy())
    elif args.command == 'prepare':
        result = prepare(args.source.resolve(), args.output.resolve(), args.revision)
    elif args.command == 'check-image':
        result = check_image(args.source.resolve(), args.output.resolve(), args.image)
    elif args.command == 'authenticate':
        authenticate(args.output, os.environ.pop('DIGITALOCEAN_REGISTRY_TOKEN', ''))
        result = {'credentials_written': True}
    else:
        result = publish(args.output.resolve(), args.run_id, args.attempt)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        # Never print authentication input or raw Docker command errors.
        result = {'status': 'failed', 'failure_class': type(error).__name__}
        if isinstance(error, ReleaseError):
            result['detail'] = str(error)
        print(json.dumps(result), file=sys.stderr)
        raise SystemExit(1)
