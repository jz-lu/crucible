"""Inert fixtures exercise trust boundaries; approved metadata bytes are bound."""
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType

import pytest
from crucible import platform_pins as pins


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    worker = tmp_path / 'worker'
    worker.write_bytes(b'inert reviewed test worker')
    monkeypatch.setattr(pins.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(pins.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(pins, 'METADATA_ROOT', tmp_path)
    doc = {'format': pins.FORMAT, 'scheme_id': 'fixture', 'scheme_version': '1',
           'platform': {'system': 'Linux', 'machine': 'x86_64'},
           'files': {'worker': sha(worker.read_bytes())},
           'build': {'recipe_sha256': sha(b'recipe'), 'toolchain': 'fixture only',
                     'dependencies': {'fixture-static-library': sha(b'library')},
                     'evidence': {'conformance': sha(b'tests')}}}
    def approve(value=None, raw=None):
        raw = raw if raw is not None else json.dumps(value or doc).encode()
        digest = sha(raw)
        (tmp_path / (digest + '.json')).write_bytes(raw)
        monkeypatch.setattr(pins, 'REVIEWED_METADATA', MappingProxyType({('fixture', '1', 'Linux', 'x86_64'): digest}))
        return digest
    def select():
        return pins.select('fixture', '1', files={'worker': (worker, 100)}, legacy={'worker': sha(b'original Mac worker')})
    return worker, doc, approve, select


def test_allowlist_contains_only_four_exact_reviewed_linux_candidates():
    assert dict(pins.REVIEWED_METADATA) == {
        ('dhkem-x25519', '1', 'Linux', 'x86_64'): 'e7445b7e05172ad0e5f1c34725292ea4296a5fc68990b848ff856a70aef4b256',
        ('sntrup', '1', 'Linux', 'x86_64'): '3734b32ca8b42c7108dde2066d563b5c4ea2ef9a701896a12bed5cf9a89a7443',
        ('sntrup', '2', 'Linux', 'x86_64'): '2190d681bd78ca1abb2a89e0949c8f1f15ae26adea442e6f111824805e9aa3d4',
        ('sntrup', '3', 'Linux', 'x86_64'): '8f9dfb1b0573d1bf9f632f5ea62af10dbb8cb333bcb168a610f6707668c2a1ef',
    }
    for (scheme, version, system, machine), digest in pins.REVIEWED_METADATA.items():
        raw = (pins.METADATA_ROOT / (digest + '.json')).read_bytes()
        assert sha(raw) == digest
        value = json.loads(raw)
        assert value['format'] == pins.FORMAT and value['scheme_id'] == scheme and value['scheme_version'] == version
        assert value['platform'] == {'system': system, 'machine': machine}
        assert set(value['files']) == ({'native', 'x25519_source'} if scheme == 'dhkem-x25519' else {'worker'})


def test_unlisted_platform_never_trusts_discovered_file_or_metadata(fixture):
    _, _, _, select = fixture
    with pytest.raises(pins.Unavailable, match='No reviewed artifact'):
        select()


def test_reviewed_platform_binds_exact_file_and_emits_admission(fixture):
    worker, doc, approve, select = fixture
    digest = approve()
    binding = select()
    assert binding.identity()['metadata_sha256'] == digest
    assert binding.source_files()['crucible/data/platform_builds/' + digest + '.json'] == digest
    assert binding.identity()['loader_source_sha256'] == sha(Path(pins.__file__).read_bytes())
    assert binding.implementation_fields()['admission'] == {
        'format': 'crucible-platform-admission-v1', 'kind': 'reviewed-additional-platform',
        'platform': {'system': 'Linux', 'machine': 'x86_64'}}
    with pytest.raises(TypeError):
        binding.pins['worker'] = '0' * 64
    original = binding.build()
    original['toolchain'] = 'changed'
    assert binding.build()['toolchain'] == 'fixture only'
    worker.write_bytes(b'tampered')
    with pytest.raises(pins.Unavailable, match='reviewed pin'):
        select()


def test_baseline_keeps_exact_old_pins_without_additional_admission(fixture, monkeypatch):
    worker, _, _, _ = fixture
    monkeypatch.setattr(pins.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(pins.platform, 'machine', lambda: 'arm64')
    binding = pins.select('fixture','1', files={'worker': (worker,100)}, legacy={'worker': sha(worker.read_bytes())})
    assert not binding.additional
    assert 'admission' not in binding.implementation_fields()
    assert binding.build() is None
    with pytest.raises(pins.Unavailable, match='reviewed pin'):
        pins.select('fixture','1',files={'worker':(worker,100)},legacy={'worker':'0'*64})


@pytest.mark.parametrize('change', [
    lambda x: x.update(scheme_id='another'),
    lambda x: x.update(scheme_version='2'),
    lambda x: x.update(format='unknown'),
    lambda x: x.update(platform={'system':'Linux','machine':'aarch64'}),
    lambda x: x.update(extra=True),
    lambda x: x['files'].update(other='1'*64),
    lambda x: x['files'].update(worker='invalid'),
    lambda x: x['build'].update(recipe_sha256='invalid'),
    lambda x: x['build'].update(evidence={}),
    lambda x: x['build'].update(dependencies=[]),
    lambda x: x['build'].update(toolchain=''),
])
def test_malformed_or_mismatched_reviewed_metadata_fails_closed(fixture, change):
    _, doc, approve, select = fixture
    change(doc); approve(doc)
    with pytest.raises(pins.Unavailable):
        select()


@pytest.mark.parametrize('raw', [b'{"format":1,"format":2}', b'NaN', b'\xff', b'{', b'['*2000])
def test_invalid_json_cannot_become_platform_metadata(fixture, raw):
    _, _, approve, select = fixture
    approve(raw=raw)
    with pytest.raises(pins.Unavailable):
        select()


def test_metadata_and_file_bounds_and_symlinks(fixture):
    worker, _, approve, select = fixture
    digest = approve()
    meta = worker.parent / (digest+'.json')
    original = meta.read_bytes()
    meta.write_bytes(b'x'*(pins.MAX_METADATA_BYTES+1))
    with pytest.raises(pins.Unavailable): select()
    meta.write_bytes(original + b' ')
    with pytest.raises(pins.Unavailable, match='digest'): select()
    meta.write_bytes(original)
    worker.write_bytes(b'x'*101)
    with pytest.raises(pins.Unavailable, match='bound'): select()
    worker.unlink(); worker.symlink_to(meta)
    with pytest.raises(pins.Unavailable): select()


def test_loader_tampering_after_import_fails_before_native_file_access(fixture, monkeypatch):
    _, _, approve, select = fixture
    approve(); monkeypatch.setattr(pins, '_SOURCE_SHA256', '0'*64)
    with pytest.raises(pins.Unavailable, match='code changed'): select()


def test_named_pipe_cannot_block_file_verification(fixture):
    worker, _, approve, select = fixture
    approve(); worker.unlink(); os.mkfifo(worker)
    with pytest.raises(pins.Unavailable, match='file bound'): select()
