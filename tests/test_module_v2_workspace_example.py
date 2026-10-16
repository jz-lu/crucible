"""The public example must work from a standalone, hash-bound CLI workspace."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from crucible import lattice_module_v2 as kem

ROOT = Path(__file__).resolve().parents[1]


def workspace(tmp_path):
    identifier = 'module-lwe-v2-tier-1'
    generated = kem.generate(identifier)
    (tmp_path/'attack').mkdir(); (tmp_path/'reference').mkdir()
    shutil.copyfile(ROOT/'scripts/lattice_solve_module_v2_tier1.py', tmp_path/'attack/solve.py')
    shutil.copyfile(ROOT/'crucible/lattice_module_v2.py', tmp_path/'reference/protocol.py')
    package = {'challenge': {'scheme_id': kem.SCHEME_ID, 'scheme_version': kem.SCHEME_VERSION,
                            'profile_id': identifier, 'public': generated.public},
               'scheme_manifest': kem.get_manifest(identifier)}
    (tmp_path/'challenge.json').write_text(json.dumps(package))
    (tmp_path/'reference/implementation-artifacts.json').write_text(json.dumps(kem.artifact_manifest()))
    return package, generated.target


def run(tmp_path):
    # -I ignores PYTHONPATH and excludes the repository/current directory imports.
    return subprocess.run([sys.executable, '-I', 'attack/solve.py', 'challenge.json'],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')})


def test_example_runs_using_only_cli_downloaded_files(tmp_path):
    _, target = workspace(tmp_path)
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert bytes.fromhex(result.stdout.strip()) == target
    assert not (tmp_path/'crucible').exists()


@pytest.mark.parametrize('tamper', ['source', 'artifact', 'self_consistent_artifact', 'manifest_version'])
def test_example_rejects_reference_tampering_before_execution(tmp_path, tamper):
    package, _ = workspace(tmp_path)
    if tamper == 'source':
        source = tmp_path/'reference/protocol.py'
        source.write_text("raise RuntimeError('SHOULD_NOT_EXECUTE')\n" + source.read_text())
    elif tamper == 'manifest_version':
        package['scheme_manifest']['version'] = 'another-release'
        (tmp_path/'challenge.json').write_text(json.dumps(package))
    else:
        path = tmp_path/'reference/implementation-artifacts.json'
        artifact = json.loads(path.read_text()); artifact['construction'] = 'changed'
        if tamper == 'self_consistent_artifact':
            artifact['artifact_sha256'] = hashlib.sha256(json.dumps(
                {key: value for key, value in artifact.items() if key != 'artifact_sha256'},
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        path.write_text(json.dumps(artifact))
    result = run(tmp_path)
    assert result.returncode != 0
    assert result.stdout == ''
    assert 'SHOULD_NOT_EXECUTE' not in result.stderr
    assert 'pin' in result.stderr or 'different scheme release' in result.stderr
