"""Native benchmark boundaries; portable tests never execute cryptography or compilers."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import reference_benchmark_native as native
from scripts.reference_benchmark_adapters import digest


@pytest.fixture
def native_builder(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts/reference_benchmark_native.c').write_text('trusted synthetic driver')
    monkeypatch.setattr(native, 'ROOT', root)
    closure = {'artifact_sha256': 'a' * 64}
    module = SimpleNamespace(SCHEME_ID='ml-kem', SCHEME_VERSION='synthetic', verify_artifact=lambda: closure)
    monkeypatch.setattr(native, 'adapter', lambda _: module)
    def sources(config, inputs):
        inputs['libraries/liboqs.a'] = b'pinned synthetic library'
        inputs['include/oqs/oqs.h'] = b'pinned synthetic declarations'
        return {}
    monkeypatch.setattr(native, '_oqs_inputs', sources)
    monkeypatch.setattr(native.subprocess, 'check_output', lambda *a, **kw: 'Synthetic compiler v1\n')
    commands = []
    def build(command, *, cwd, **kwargs):
        commands.append(command)
        (cwd / 'benchmark').write_bytes(b'synthetic executable, never run')
    monkeypatch.setattr(native.subprocess, 'run', build)
    manifest = {'scheme_id': 'ml-kem', 'version': 'synthetic', 'id': 'profile512',
                'parameters': {'parameter_set': 'ML-KEM-512'}, 'implementation': closure}
    config = replace(native.BuildInputs(), output=root / '.local/native-timing')
    return root, manifest, config, commands


def test_preparation_snapshots_pinned_inputs_and_cache_does_not_recompile(native_builder):
    root, manifest, config, commands = native_builder
    binary, selector, receipt = native.prepare('mlkem', manifest, config)
    assert binary.is_relative_to(root / '.local') and selector == 'ML-KEM-512'
    assert len(commands) == 1
    assert receipt['parameters_sha256'] == digest(manifest['parameters'])
    assert receipt['adapter_artifact_sha256'] == 'a' * 64
    assert receipt['binary_sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert all(not Path(name).is_absolute() for name in receipt['files'])
    assert 'architecture optimized' in receipt['measurement_scope']
    assert native.prepare('mlkem', manifest, config) == (binary, selector, receipt)
    assert len(commands) == 1
    native.verify_binary(binary, receipt)


@pytest.mark.parametrize('damage', ['binary', 'receipt'])
def test_changed_cached_driver_is_rejected_before_execution(native_builder, damage):
    _, manifest, config, commands = native_builder
    binary, _, receipt = native.prepare('mlkem', manifest, config)
    if damage == 'binary':
        binary.write_bytes(b'changed executable')
    else:
        receipt['compiler'] = 'different compiler'
        (binary.parent / 'artifact.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        native.prepare('mlkem', manifest, config)
    assert len(commands) == 1


@pytest.mark.parametrize('location', ['outside', 'local-symlink', 'child-symlink'])
def test_builds_never_write_outside_checkout_local(native_builder, location, tmp_path):
    root, manifest, config, commands = native_builder
    outside = tmp_path / 'unrelated'
    outside.mkdir()
    if location == 'outside': config = replace(config, output=outside / 'native')
    elif location == 'local-symlink': (root / '.local').symlink_to(outside, target_is_directory=True)
    else:
        (root / '.local').mkdir()
        config.output.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='beneath'):
        native.prepare('mlkem', manifest, config)
    assert not list(outside.iterdir()) and not commands


def test_unknown_target_and_wrong_adapter_pin_are_not_compiled(native_builder):
    _, manifest, config, commands = native_builder
    with pytest.raises(ValueError, match='Unknown'):
        native.prepare('uploaded-code', manifest, config)
    manifest['implementation'] = {'artifact_sha256': 'b' * 64}
    with pytest.raises(ValueError):
        native.prepare('mlkem', manifest, config)
    assert not commands


def test_source_inventory_and_digest_are_verified(tmp_path):
    source = tmp_path / 'core'
    source.mkdir()
    (source / 'api.h').write_bytes(b'known header')
    expected = {'api.h': hashlib.sha256(b'known header').hexdigest()}
    inputs = {}
    native._tree(source, expected, 'source', inputs)
    assert inputs == {'source/api.h': b'known header'}
    (source / 'api.h').write_bytes(b'changed header')
    with pytest.raises(ValueError): native._tree(source, expected, 'source', {})
    (source / 'extra.c').write_bytes(b'unknown source')
    with pytest.raises(ValueError, match='inventory'): native._tree(source, expected, 'source', {})
