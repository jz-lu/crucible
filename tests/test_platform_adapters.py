"""Platform binding tests do not build or execute Linux binaries."""
import builtins
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest
from crucible import dhkem_x25519, sntrup, ntru_tiers, ntru_frontier, platform_pins

ADAPTERS = (dhkem_x25519, sntrup, ntru_tiers, ntru_frontier)


@pytest.mark.parametrize('adapter', ADAPTERS)
@pytest.mark.parametrize('tampering', ['missing', 'changed'])
def test_configured_linux_metadata_rejects_tampering_before_native_code(adapter, tampering, monkeypatch, tmp_path):
    monkeypatch.setattr(platform_pins.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(platform_pins.platform, 'machine', lambda: 'x86_64')
    digest = platform_pins.REVIEWED_METADATA[(adapter.SCHEME_ID, adapter.SCHEME_VERSION, 'Linux', 'x86_64')]
    monkeypatch.setattr(platform_pins, 'METADATA_ROOT', tmp_path)
    if tampering == 'changed':
        (tmp_path / (digest + '.json')).write_text('{}')
    if adapter is dhkem_x25519:
        importer = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith('cryptography.hazmat'):
                pytest.fail('Native import preceded exact metadata validation')
            return importer(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', guarded)
        with pytest.raises(adapter.GenerationUnavailable): adapter._backend()
    else:
        monkeypatch.setattr(adapter.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Unreviewed worker executed'))
        with pytest.raises(adapter.GenerationUnavailable):
            if adapter is sntrup: adapter._run('generate')
            else: adapter._run(adapter.PROFILES[0], 'generate')


@pytest.mark.parametrize('adapter', ADAPTERS)
def test_unlisted_linux_rejects_before_native_import_or_execution(adapter, monkeypatch):
    monkeypatch.setattr(platform_pins.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(platform_pins.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(platform_pins, 'REVIEWED_METADATA', MappingProxyType({}))
    if adapter is dhkem_x25519:
        importer = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith('cryptography.hazmat'):
                pytest.fail('Native cryptography import happened before platform verification')
            return importer(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', guarded)
        with pytest.raises(adapter.GenerationUnavailable): adapter._backend()
    else:
        monkeypatch.setattr(adapter.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Unreviewed worker executed'))
        with pytest.raises(adapter.GenerationUnavailable):
            if adapter is sntrup: adapter._run('generate')
            else: adapter._run(adapter.PROFILES[0], 'generate')


@pytest.mark.parametrize('adapter', ADAPTERS)
def test_platform_identity_and_marker_are_carried_by_manifest(adapter, monkeypatch):
    # Inert Binding injection tests propagation only, not Linux conformance.
    binding = platform_pins.Binding(MappingProxyType({'worker':'a'*64,'native':'b'*64,'x25519_source':'c'*64}),
        'Linux','x86_64','d'*64,json.dumps({'recipe_sha256':'e'*64,'toolchain':'fixture Linux compiler',
                                       'dependencies':{'fixture library':'f'*64},'evidence':{'fixture':'1'*64}}))
    if adapter is dhkem_x25519:
        monkeypatch.setattr(adapter, '_platform_binding', lambda *a: binding)
        monkeypatch.setattr(adapter, '_backend', lambda: None)
        identifier = adapter.PROFILE_ID
    elif adapter is sntrup:
        monkeypatch.setattr(adapter, '_verify_artifact', lambda: binding)
        identifier = adapter.PROFILE_ID
    elif adapter is ntru_tiers:
        monkeypatch.setattr(adapter, 'verify_artifact', lambda: binding)
        identifier = adapter.profile_for_size(1)
    else:
        monkeypatch.setattr(adapter, '_verify_artifact_files', lambda: binding)
        identifier = adapter.profile_for_size(1)
    artifact = adapter.artifact_manifest()
    manifest = adapter.get_manifest(identifier)
    assert artifact['files']['crucible/platform_pins.py'] == hashlib.sha256(Path(platform_pins.__file__).read_bytes()).hexdigest()
    assert artifact['files']['crucible/data/platform_builds/' + 'd'*64 + '.json'] == 'd'*64
    assert manifest['implementation']['platform_build'] == artifact['platform_build'] == binding.identity()
    assert manifest['implementation']['admission'] == binding.implementation_fields()['admission']
    assert manifest['implementation']['artifact_sha256'] == artifact['artifact_sha256']
    if adapter is not dhkem_x25519:
        assert artifact['worker_sha256'] == 'a'*64
        assert artifact['build']['toolchain'] == 'fixture Linux compiler'
        assert 'Apple' not in str(artifact['build'])
    if adapter in (ntru_tiers,ntru_frontier):
        assert artifact['native_dependencies'] == [{'name':'fixture library','static_sha256':'f'*64}]
        for name, checksum in adapter.FILE_PINS.items():
            assert hashlib.sha256((adapter.ROOT/name).read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize('adapter', ADAPTERS)
def test_current_platform_exact_pins_and_admission_marker(adapter):
    # Inspect exact runtime files without launching challenge workers.
    system, machine = platform_pins.platform.system(), platform_pins.platform.machine()
    if (system, machine) not in {('Darwin','arm64'), ('Linux','x86_64')}:
        pytest.skip('No reviewed artifact for this platform')
    if adapter is dhkem_x25519: binding = adapter._platform_binding()
    elif adapter is sntrup: binding = adapter._verify_artifact()
    elif adapter is ntru_tiers: binding = adapter.verify_artifact()
    else: binding = adapter._verify_artifact_files()
    if (system, machine) == ('Darwin', 'arm64'):
        assert not binding.additional
        assert 'admission' not in binding.implementation_fields()
        if adapter is dhkem_x25519:
            assert binding.pins['native'] == adapter.CRYPTOGRAPHY_NATIVE_SHA256
            assert binding.pins['x25519_source'] == adapter.CRYPTOGRAPHY_X25519_SHA256
        else:
            assert binding.pins['worker'] == adapter.WORKER_SHA256
    else:
        assert binding.additional
        expected = platform_pins.REVIEWED_METADATA[(adapter.SCHEME_ID, adapter.SCHEME_VERSION, system, machine)]
        assert binding.metadata_sha256 == expected
        assert binding.implementation_fields()['admission']['platform'] == {'system': system, 'machine': machine}
        record = json.loads((platform_pins.METADATA_ROOT / (expected + '.json')).read_bytes())
        assert dict(binding.pins) == record['files']
