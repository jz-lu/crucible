#!/usr/bin/env sage-python
"""Public-input-only LLL/Babai attack for the n=8 Frodo research profile.

Run with Sage: sage -python scripts/lattice_solve_frodo_v2_tier1.py challenge.json
No generation randomness, private key, account credential or server target is used.
"""
import hashlib
import json
from pathlib import Path
import sys
import types

SCHEME_ID = 'frodokem'
SCHEME_VERSION = 'frontier-research-v2'


def _repository_kem():
    root = Path(__file__).resolve().parents[1]
    if not (root/'crucible/lattice_frodo_v2.py').is_file():
        raise ValueError('Run this example from a CLI workspace containing reference/protocol.py, or from the source repository.')
    sys.path.insert(0, str(root))
    from crucible import lattice_frodo_v2
    return lattice_frodo_v2


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
    name = '_crucible_verified_frodo_' + source_pin
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
    kem = kem or _repository_kem()
    from sage.all import ZZ, QQ, matrix, vector, block_matrix, identity_matrix, zero_matrix
    if public.get("scheme_id") != SCHEME_ID or public.get("version") != SCHEME_VERSION or public.get("profile_id") != "frodo-lwe-v2-tier-1" or public.get("parameters", {}).get("n") != 8:
        raise ValueError("This example attacks only the n=8 research profile")
    n = 8
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    kem._bytes(pk, 136, "Public key")
    kem._bytes(ct, 272, "Ciphertext")
    a = matrix(ZZ, kem.public_matrix(n, pk[:16]))
    # Lattice vectors (s, A*s+q*z); closest to (0,b) reveals (s,-e).
    basis = block_matrix([[identity_matrix(ZZ, n), a.transpose()],
                          [zero_matrix(ZZ, n), kem.Q*identity_matrix(ZZ, n)]])
    reduced = basis.LLL()
    orthogonal, _ = reduced.gram_schmidt()
    b = kem.unpack(pk[16:], n*8)
    columns = []
    for j in range(8):
        target = vector(ZZ, [0]*n+b[j::8])
        remainder = vector(QQ, target)
        for i in reversed(range(2*n)):
            coefficient = (remainder.dot_product(orthogonal[i])/orthogonal[i].dot_product(orthogonal[i])).round()
            remainder -= coefficient*reduced[i]
        closest = target-remainder
        s = list(closest[:n])
        residual = [(int(b[i*8+j])-sum(int(a[i,k])*int(s[k]) for k in range(n))) % kem.Q for i in range(n)]
        if any(abs(x) > 12 for x in s) or any(min(x, kem.Q-x) > 12 for x in residual):
            raise RuntimeError("This Babai run did not recover a bounded secret; no result claimed")
        columns.append(s)
    bp, c = kem.unpack(ct[:15*n], n*8), kem.unpack(ct[15*n:15*n+120], 64)
    noise_message = [(c[i*8+j]-sum(int(x)*int(y) for x,y in zip(bp[i*n:(i+1)*n], columns[j]))) % kem.Q
                     for i in range(8) for j in range(8)]
    mu = kem.decode(noise_message)
    reconstructed, k = kem._encrypt(n, pk, mu, ct[-32:])
    if reconstructed != ct:
        raise RuntimeError("Recovered message did not reproduce the ciphertext")
    return hashlib.shake_128(ct+k).digest(16)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: sage -python attack/solve.py challenge.json")
    path=Path(sys.argv[1]).resolve()
    value=json.loads(path.read_text())
    public=value.get("challenge",value).get("public",value)
    implementation=_workspace_kem(value,path.parent) if (path.parent/"reference/protocol.py").exists() else _repository_kem()
    print(solve(public,implementation).hex())
