#!/usr/bin/env python3
"""Public-input demonstration for compressed module-LWE research Tier 1.

Enumerate the complete 256-message native space and compare full ciphertexts.
No private key, generator randomness, key generation or decapsulation is used.
In a CLI workspace this explicitly executes the downloaded public reference
core after verifying the full two-file closure against the challenge pins.
Hashes establish identity, not safety: review/trust downloaded code first.
No network requests or automatic installation are performed.
"""
import hashlib
import json
from pathlib import Path
import sys
import types

SCHEME_ID = "ml-kem"
SCHEME_VERSION = "compressed-research-v4"
PROFILE_ID = "module-lwe-v4-tier-1"
ADAPTER_PATH = "crucible/lattice_module_v4.py"
CORE_PATH = "crucible/module_compressed_v4_core.py"


def _read(root, relative, limit=2*1024*1024):
    path = root
    for component in Path(relative).parts:
        if component in ("", ".", ".."):
            raise ValueError("Invalid reference path")
        path = path / component
        if path.is_symlink():
            raise ValueError("Reference paths must not contain symbolic links")
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Reference file missing or oversized")
    value = path.read_bytes()
    if len(value) > limit:
        raise ValueError("Reference file oversized")
    return value


def _repository_core():
    root = Path(__file__).resolve().parents[1]
    if not (root / CORE_PATH).is_file():
        raise ValueError("Run from a downloaded CLI workspace or the source repository")
    sys.path.insert(0,str(root))
    from crucible import module_compressed_v4_core
    return module_compressed_v4_core


def _workspace_core(package, workspace):
    """Verify all declared local bytes before executing only the reference core."""
    challenge, manifest = package["challenge"], package["scheme_manifest"]
    public = challenge["public"]
    if (challenge.get("scheme_id") != SCHEME_ID or challenge.get("scheme_version") != SCHEME_VERSION
            or public.get("scheme_id") != SCHEME_ID or public.get("version") != SCHEME_VERSION
            or manifest.get("scheme_id") != SCHEME_ID or manifest.get("version") != SCHEME_VERSION
            or manifest.get("id") != PROFILE_ID or public.get("profile_id") != PROFILE_ID):
        raise ValueError("Challenge is not compressed module-LWE Tier 1")
    source = _read(workspace,"reference/protocol.py")
    checksum = hashlib.sha256(source).hexdigest()
    expected = manifest["implementation"]
    if checksum != expected["source_sha256"] or checksum != public["implementation_sha256"]:
        raise ValueError("Adapter source differs from challenge pin")
    artifact = json.loads(_read(workspace,"reference/implementation-artifacts.json"))
    payload = {key:value for key,value in artifact.items() if key != "artifact_sha256"}
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",", ":"),
                                           ensure_ascii=True,allow_nan=False).encode()).hexdigest()
    if (fingerprint != artifact.get("artifact_sha256") or fingerprint != expected.get("artifact_sha256")
            or fingerprint != public.get("artifact_sha256") or artifact.get("adapter_source_sha256") != checksum
            or artifact.get("scheme_id") != SCHEME_ID or artifact.get("scheme_version") != SCHEME_VERSION):
        raise ValueError("Artifact identity differs from challenge pin")
    files = artifact.get("files")
    if (not isinstance(files,list) or len(files) != 2
            or any(not isinstance(row,dict) or set(row) != {"path","sha256"} for row in files)
            or {row["path"] for row in files} != {ADAPTER_PATH,CORE_PATH}):
        raise ValueError("Unexpected implementation closure")
    verified = {}
    for row in files:
        data = _read(workspace,"reference/source/"+row["path"])
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Implementation closure differs from challenge pin")
        verified[row["path"]] = data
    if verified[ADAPTER_PATH] != source:
        raise ValueError("Adapter copies disagree")
    core_source = verified[CORE_PATH]
    name = "_crucible_verified_public_core_"+hashlib.sha256(core_source).hexdigest()
    module = types.ModuleType(name)
    module.__file__ = str(workspace / "reference/source" / CORE_PATH)
    sys.modules[name] = module
    try:
        exec(compile(core_source,module.__file__,"exec"),module.__dict__)
    except BaseException:
        sys.modules.pop(name,None)
        raise
    return module


def solve(public, core=None):
    if (public.get("scheme_id") != SCHEME_ID or public.get("version") != SCHEME_VERSION
            or public.get("profile_id") != PROFILE_ID):
        raise ValueError("This demonstration supports only compressed-research-v4 Tier 1")
    core = core or _repository_core()
    p = core.Parameters(8,2,3)
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    core.validate_public_key(p,pk)
    if len(ct) != p.ciphertext_bytes:
        raise ValueError("Ciphertext has the wrong length")
    for value in range(256):
        candidate_ciphertext, key = core.encapsulate_internal(p,pk,bytes([value]))
        if candidate_ciphertext == ct:
            return key
    raise ValueError("No native message reproduced the complete ciphertext")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 attack/solve.py challenge.json")
    path = Path(sys.argv[1]).resolve()
    package = json.loads(path.read_text())
    challenge = package.get("challenge",package)
    public = challenge.get("public",challenge)
    core = _workspace_core(package,path.parent) if (path.parent / "reference/protocol.py").exists() else _repository_core()
    print(solve(public,core).hex())
