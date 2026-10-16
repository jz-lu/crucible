#!/usr/bin/env python3
"""Explicit public-input Tier 1 demonstration for the dimension research KEM.

Enumerate all 256 native plaintexts and compare complete ciphertexts. In a CLI
workspace this explicitly executes the downloaded reference/protocol.py after
checking its source and artifact pins against challenge.json. Integrity checks
are not a safety audit: run downloaded code only after reviewing/trusting it.
No network requests, private keys or generator randomness are used.
"""
import hashlib
import json
from pathlib import Path
import sys
import types

SCHEME_ID = 'ml-kem'
SCHEME_VERSION = 'packed-research-v3'


def _repository_kem():
    root = Path(__file__).resolve().parents[1]
    if not (root/'crucible/lattice_module_v3.py').is_file():
        raise ValueError('Run this example from a CLI workspace containing reference/protocol.py, or from the source repository.')
    sys.path.insert(0, str(root))
    from crucible import lattice_module_v3
    return lattice_module_v3


def _workspace_kem(package, workspace):
    """Verify locally stored pins before explicitly executing local source bytes."""
    challenge = package['challenge']; public = challenge['public']; manifest = package['scheme_manifest']
    if (challenge.get('scheme_id') != SCHEME_ID or challenge.get('scheme_version') != SCHEME_VERSION
            or public.get('scheme_id') != SCHEME_ID or public.get('version') != SCHEME_VERSION
            or manifest.get('scheme_id') != SCHEME_ID or manifest.get('version') != SCHEME_VERSION):
        raise ValueError('The workspace is for a different scheme release')
    source_path = workspace/'reference/protocol.py'
    artifacts_path = workspace/'reference/implementation-artifacts.json'
    if source_path.is_symlink() or artifacts_path.is_symlink():
        raise ValueError('Reference files must not be symbolic links')
    source = source_path.read_bytes()
    if len(source) > 2*1024*1024:
        raise ValueError('Reference source is oversized')
    source_pin = hashlib.sha256(source).hexdigest()
    expected = manifest['implementation']
    if source_pin != expected['source_sha256'] or source_pin != public['implementation_sha256']:
        raise ValueError('Reference source differs from the challenge pin')
    raw = artifacts_path.read_bytes()
    if len(raw) > 2*1024*1024:
        raise ValueError('Reference artifact document is oversized')
    artifact = json.loads(raw)
    payload = {key: value for key, value in artifact.items() if key != 'artifact_sha256'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=True, allow_nan=False).encode()).hexdigest()
    if (digest != artifact.get('artifact_sha256') or digest != expected['artifact_sha256']
            or digest != public['artifact_sha256'] or artifact.get('adapter_source_sha256') != source_pin
            or artifact.get('scheme_id') != SCHEME_ID or artifact.get('scheme_version') != SCHEME_VERSION):
        raise ValueError('Reference artifacts differ from the challenge pin')
    name = '_crucible_verified_module_' + source_pin
    module = types.ModuleType(name)
    module.__file__ = str(source_path)
    sys.modules[name] = module  # dataclass decorators resolve their defining module.
    try:
        exec(compile(source, str(source_path), 'exec'), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if module.SCHEME_ID != SCHEME_ID or module.SCHEME_VERSION != SCHEME_VERSION:
        raise ValueError('Reference source identifies a different release')
    return module


def solve(public, kem=None):
    if (public.get('scheme_id') != SCHEME_ID or public.get('version') != SCHEME_VERSION
            or public.get('profile_id') != 'module-lwe-v3-tier-1'):
        raise ValueError('This example supports only packed-research-v3 Tier 1')
    kem = kem or _repository_kem()
    pk = bytes.fromhex(public['public_key']); ct = bytes.fromhex(public['ciphertext'])
    p = kem._profile('module-lwe-v3-tier-1')
    kem._decode_pk(pk, p)
    kem.unpack(ct, p.rank+1, p)
    for candidate in range(256):
        encrypted, key = kem.encapsulate_parameters(p.n, p.rank, pk, bytes([candidate]))
        if encrypted == ct:
            return key
    raise ValueError('No native plaintext reproduced the complete ciphertext')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 attack/solve.py challenge.json')
    path = Path(sys.argv[1]).resolve()
    value = json.loads(path.read_text())
    challenge = value.get('challenge', value)
    public = challenge.get('public', challenge)
    reference = path.parent/'reference/protocol.py'
    implementation = _workspace_kem(value, path.parent) if reference.exists() else _repository_kem()
    print(solve(public, implementation).hex())
