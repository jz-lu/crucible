"""RFC 9180 DHKEM(X25519, HKDF-SHA256), base KEM, immutable release 1.

The recovered value is RFC 9180's KEM shared_secret, not raw DH, an AEAD key,
or the HPKE exporter secret. Only the published fixed X25519 profile is offered.
X25519 uses the pinned cryptography/OpenSSL implementation; the short labeled
HKDF construction follows RFC 9180 sections 4.1 and 7.1.3 exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from importlib import metadata
from pathlib import Path
import platform
import secrets
from typing import Any

SCHEME_ID = "dhkem-x25519"
SCHEME_VERSION = "1"
PROFILE_ID = "dhkem-x25519-v1-rfc9180"
PROFILE_LABEL = "DHKEM(X25519, HKDF-SHA256)"
CRYPTOGRAPHY_VERSION = "50.0.2"
# Reviewed macOS arm64 wheel; another platform requires an explicit artifact
# review/pin, not acceptance of an arbitrary binary bearing the same version.
CRYPTOGRAPHY_NATIVE_SHA256 = "0aced18998c288668c0eb65cfdc76fd41726370e6d5d9760c0b27143929162d2"
CRYPTOGRAPHY_X25519_SHA256 = "950720524f8f8ae6e3f329c535641e6267b7b5864c80742a9112a42033872f3a"
SHARED_SECRET_BYTES = 32
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SUITE_ID = b"KEM\x00\x20"
SIZE_PARAMETER = {
    "name": "Parameter set", "symbol": "", "unit": "profile",
    "meaning": "Published RFC 9180 parameter set",
    "mapping": "Profile 1 is the fixed RFC 9180 DHKEM(X25519, HKDF-SHA256) KEM (ID 0x0020). It is an ordinal, not security bits or a reduced-size ladder.",
    "comparison_scope": "scheme-version",
}


class GenerationUnavailable(RuntimeError):
    """The reviewed native primitive is unavailable or has changed."""


def _profile(profile_id):
    if type(profile_id) is not str or profile_id != PROFILE_ID:
        raise ValueError("Unknown DHKEM parameter profile")


def _backend():
    try:
        distribution = metadata.distribution("cryptography")
    except metadata.PackageNotFoundError:
        raise GenerationUnavailable("DHKEM requires the pinned cryptography release") from None
    if distribution.version != CRYPTOGRAPHY_VERSION:
        raise GenerationUnavailable("DHKEM requires the pinned cryptography release")
    native_path = Path(distribution.locate_file("cryptography/hazmat/bindings/_rust.abi3.so"))
    source_path = Path(distribution.locate_file("cryptography/hazmat/primitives/asymmetric/x25519.py"))
    try:
        if (native_path.stat().st_size > 32 * 1024 * 1024 or source_path.stat().st_size > 65536
                or hashlib.sha256(native_path.read_bytes()).hexdigest() != CRYPTOGRAPHY_NATIVE_SHA256
                or hashlib.sha256(source_path.read_bytes()).hexdigest() != CRYPTOGRAPHY_X25519_SHA256):
            raise GenerationUnavailable("DHKEM dependency artifact differs from the reviewed pin")
    except OSError:
        raise GenerationUnavailable("DHKEM dependency artifact is unavailable") from None
    # Verify the extension before the first native import, not just after load.
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.bindings import _rust
    if Path(_rust.__file__).resolve() != native_path.resolve() or Path(x25519.__file__).resolve() != source_path.resolve():
        raise GenerationUnavailable("DHKEM dependency import does not match its reviewed location")
    return x25519.X25519PrivateKey, x25519.X25519PublicKey


def artifact_manifest():
    _backend()
    from cryptography.hazmat.backends.openssl.backend import backend
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
              "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
              "specification": "RFC 9180, KEM 0x0020, February 2022",
              "python_runtime": platform.python_version(),
              "dependencies": [{"name": "cryptography", "version": CRYPTOGRAPHY_VERSION,
                  "native_sha256": CRYPTOGRAPHY_NATIVE_SHA256, "x25519_source_sha256": CRYPTOGRAPHY_X25519_SHA256,
                  "openssl": backend.openssl_version_text()}]}
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def _bytes(value, length, label):
    if type(value) is not bytes or len(value) != length:
        raise ValueError(f"{label} must be exactly {length} bytes")
    return value


def _extract(salt, label, ikm):
    return hmac.digest(salt or bytes(32), b"HPKE-v1" + SUITE_ID + label + ikm, "sha256")


def _expand(prk, label, info, length=32):
    # All callers request one SHA-256 block; enforce this instead of silently
    # implementing only the first block of a general HKDF-Expand API.
    if type(length) is not int or not 1 <= length <= 32:
        raise ValueError("Unsupported labeled HKDF output length")
    labeled_info = length.to_bytes(2, "big") + b"HPKE-v1" + SUITE_ID + label + info
    return hmac.digest(prk, labeled_info + b"\x01", "sha256")[:length]


def _shared_secret(dh, enc, public_key):
    return _expand(_extract(b"", b"eae_prk", dh), b"shared_secret", enc + public_key)


@dataclass(frozen=True, repr=False)
class PrivateKey:
    value: bytes

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def derive_keypair(profile_id, ikm):
    """RFC deterministic derivation, exposed for reference-vector reproduction."""
    _profile(profile_id)
    if type(ikm) is not bytes or not 32 <= len(ikm) <= 4096:
        raise ValueError("Key material must contain between 32 and 4096 bytes")
    private_class, _ = _backend()
    value = _expand(_extract(b"", b"dkp_prk", ikm), b"sk", b"")
    key = private_class.from_private_bytes(value)
    return key.public_key().public_bytes_raw(), PrivateKey(value)


def keygen(profile_id):
    # RFC 9180 permits GenerateKeyPair via DeriveKeyPair with fresh Nsk bytes.
    _profile(profile_id)
    return derive_keypair(profile_id, secrets.token_bytes(32))


def _encapsulate_with_private(profile_id, public_key, ephemeral):
    _profile(profile_id)
    _bytes(public_key, 32, "Public key")
    if not isinstance(ephemeral, PrivateKey):
        raise ValueError("Invalid ephemeral private key")
    _bytes(ephemeral.value, 32, "Private key")
    private_class, public_class = _backend()
    key = private_class.from_private_bytes(ephemeral.value)
    enc = key.public_key().public_bytes_raw()
    # OpenSSL rejects a null shared secret; retain an explicit independent
    # check so backend changes cannot silently relax RFC 9180 section 7.1.4.
    dh = key.exchange(public_class.from_public_bytes(public_key))
    if hmac.compare_digest(dh, bytes(32)):
        raise ValueError("Invalid DHKEM public input")
    return enc, _shared_secret(dh, enc, public_key)


def encapsulate(profile_id, public_key):
    _profile(profile_id)
    _bytes(public_key, 32, "Public key")
    _, ephemeral = keygen(profile_id)
    return _encapsulate_with_private(profile_id, public_key, ephemeral)


def decapsulate(profile_id, private_key, ciphertext):
    _profile(profile_id)
    _bytes(ciphertext, 32, "Encapsulation")
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Invalid private key")
    _bytes(private_key.value, 32, "Private key")
    private_class, public_class = _backend()
    key = private_class.from_private_bytes(private_key.value)
    public_key = key.public_key().public_bytes_raw()
    dh = key.exchange(public_class.from_public_bytes(ciphertext))
    if hmac.compare_digest(dh, bytes(32)):
        raise ValueError("Invalid DHKEM public input")
    return _shared_secret(dh, ciphertext, public_key)


def _encoding():
    return {"public_key": "RFC 7748 X25519 u-coordinate, 32-byte little-endian encoding",
            "public_key_bytes": 32, "ciphertext": "RFC 9180 enc: serialized ephemeral X25519 public key",
            "ciphertext_bytes": 32, "shared_secret": "RFC 9180 KEM shared_secret; 64 hexadecimal characters",
            "shared_secret_bytes": 32}


def _kdf_spec():
    return {"name": "RFC 9180 LabeledExtract/LabeledExpand with HKDF-SHA256",
            "suite_id_hex": SUITE_ID.hex(), "context": "enc || pkR", "output": "KEM shared_secret"}


def serialize(profile_id, public_key, ciphertext):
    _profile(profile_id)
    _bytes(public_key, 32, "Public key")
    _bytes(ciphertext, 32, "Encapsulation")
    return {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": PROFILE_ID,
            "instance_size": 1, "parameter_bits": 255, "profile_kind": "standard-parameter-set",
            "profile_label": PROFILE_LABEL, "profile_order": 1,
            "parameters": {"kem_id": "0x0020", "curve": "X25519", "kdf": "HKDF-SHA256"},
            "public_key": public_key.hex(), "ciphertext": ciphertext.hex(),
            "encoding": _encoding(), "kdf": _kdf_spec(), "implementation_sha256": IMPLEMENTATION_SHA256}


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict[str, Any]
    target: bytes
    diagnostics: dict[str, Any]

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id):
    public_key, private_key = keygen(profile_id)
    ciphertext, target = encapsulate(profile_id, public_key)
    recovered = decapsulate(profile_id, private_key, ciphertext)
    return GeneratedChallenge(serialize(profile_id, public_key, ciphertext), target,
        {"correctness_checked": True, "shared_secret_matches": hmac.compare_digest(target, recovered),
         "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256})


def get_manifest(profile_id):
    _profile(profile_id)
    return {"id": PROFILE_ID, "profile_id": PROFILE_ID, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
            "name": PROFILE_LABEL, "profile_label": PROFILE_LABEL, "profile_kind": "standard-parameter-set",
            "profile_order": 1, "instance_size": 1, "parameter_bits": 255, "parameter_label": "field bits",
            "size_parameter": dict(SIZE_PARAMETER), "shared_secret_bytes": 32, "encoding": _encoding(),
            "parameters": {"kem_id": "0x0020", "curve": "X25519", "kdf": "HKDF-SHA256"},
            "kdf": _kdf_spec(), "educational": False,
            "security_claim": "Exact RFC 9180 base DHKEM construction. This challenge tests one honest KEM shared-secret recovery, not the whole HPKE stack or an IND-CCA experiment. X25519 is not post-quantum secure. No measured security level is asserted.",
            "assumption_context": "Elliptic-curve Diffie–Hellman on X25519, with the RFC 9180 DHKEM analysis and HKDF assumptions; not finite-field DH and not raw X25519 output.",
            "distribution": "Fresh independent 32-byte OS-random input to RFC 9180 DeriveKeyPair for recipient and ephemeral keys; X25519 clamping follows RFC 7748. No reuse of private keys across issued instances and no correctness-conditioned retries.",
            "algorithms": {"keygen": "RFC 9180 DeriveKeyPair(random(32)) for KEM 0x0020.",
                "encapsulate": "Generate ephemeral key; enc=SerializePublicKey(pkE); dh=X25519(skE,pkR); K=ExtractAndExpand(dh,enc||pkR). Reject a null DH result.",
                "decapsulate": "dh=X25519(skR,DeserializePublicKey(enc)); K=ExtractAndExpand(dh,enc||SerializePublicKey(pk(skR))). Reject a null DH result."},
            "correctness": {"kind": "perfect", "failure_bound": "0",
                "argument": "For honestly generated X25519 keys, both DH operations agree. Both sides use identical enc||pkR and RFC labeled HKDF, hence identical 32-byte KEM shared secrets.",
                "scope": "The fixed published profile and honest successful generation, assuming faithful primitive and encoding implementation; malformed inputs are outside honest correctness.",
                "assumptions": "Correct X25519 arithmetic, RFC encodings and deterministic SHA-256/HMAC. Confidentiality assumptions are separate.",
                "evidence_status": "RFC vector and local conformance tests; independent implementation audit not claimed.",
                "implementation_checks": "tests/test_dhkem_x25519.py: all pinned unauthenticated RFC vectors, malformed/low-order inputs, freshness and target preservation."},
            "admission": {"status": "local-beta", "official_ranked": False,
                "reason": "Published profile with bounded conformance evidence; operator activation is separate from manual scientific admission."},
            "implementation": {"source": "crucible/dhkem_x25519.py", "source_sha256": IMPLEMENTATION_SHA256,
                "artifact_sha256": artifact_manifest()["artifact_sha256"],
                "version": SCHEME_VERSION, "runtime": f"cryptography=={CRYPTOGRAPHY_VERSION} X25519; Python SHA-256/HMAC labeled HKDF",
                "evidence": "docs/implementation-dhkem-sntrup.txt"},
            "references": [{"title": "RFC 9180 sections 4.1 and 7.1", "url": "https://www.rfc-editor.org/rfc/rfc9180.html"},
                {"title": "RFC 7748 X25519", "url": "https://www.rfc-editor.org/rfc/rfc7748.html"}]}


def manifests():
    return [get_manifest(PROFILE_ID)]
