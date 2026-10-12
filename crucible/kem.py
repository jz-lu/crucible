"""A deliberately weak, executable finite-field DH-KEM teaching adapter.

This is an original reference specification for Crucible's educational lane,
not a standardized KEM or an implementation to protect real information.
Every profile is trivially within reach of classical discrete-log attacks;
the profile labels are subgroup-order bit lengths, never security strengths.
Python integer operations are not constant-time. Correctness is algebraic;
confidentiality, chosen-ciphertext security, and implementation certification
are not claimed. No attacker-controlled source code is loaded or evaluated.

The exact distribution is x,r uniform in {1,...,q-1}, independently, using OS
randomness. Public values are g^x and g^r in the order-q subgroup of F_p*, where
p=2q+1 is prime and g=4. Identity elements are excluded. Group elements use the
modulus's fixed byte width, big endian. Malformed/non-subgroup inputs raise
ValueError; this toy adapter has no decapsulation-oracle endpoint.

The SHA-256 KDF consumes DOMAIN followed by seven length-prefixed fields:
ASCII profile ID, p, q, g, public key, ciphertext, and g^(xr). Integer fields
use the same fixed width. Each length prefix is an unsigned 32-bit big-endian
byte count. Binding the transcript and profile gives an unambiguous experiment;
this choice does not assert a formal KEM security reduction.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Any


SCHEME_ID = "teaching-dh-kem"
SCHEME_VERSION = "2"
SHARED_SECRET_BYTES = 32
# A common integer input selects a registered parameter set. Its interpretation
# belongs to this scheme and release; it is independent of shared-key length.
SIZE_PARAMETER = {
    "name": "Instance size",
    "symbol": "n",
    "meaning": "Subgroup-order bit length",
    "unit": "bits",
    "mapping": "For each supported n, choose the first prime q >= 2^(n-1)+1 for which p=2q+1 is prime, with g=4. Only the registered n values 20 through 36 are admitted.",
    "comparison_scope": "scheme-version",
}
# Release 2 adds profiles; retain the original KDF domain for reproducibility.
DOMAIN = b"crucible/teaching-dh-kem/v1/shared-secret\x00"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Profile:
    id: str
    parameter_bits: int
    p: int
    q: int
    g: int = 4

    @property
    def width(self) -> int:
        return (self.p.bit_length() + 7) // 8


# The first safe-prime pair found scanning odd q upward from 2^(bits-1)+1.
# These constants are also checked by exhaustive trial division in the tests.
_PROFILES = {
    p.id: p
    for p in (
        Profile("dh-20", 20, 1048703, 524351),
        Profile("dh-21", 21, 2097779, 1048889),
        Profile("dh-22", 22, 4195259, 2097629),
        Profile("dh-23", 23, 8389163, 4194581),
        Profile("dh-24", 24, 16777907, 8388953),
        Profile("dh-25", 25, 33554519, 16777259),
        Profile("dh-26", 26, 67109543, 33554771),
        Profile("dh-27", 27, 134217827, 67108913),
        Profile("dh-28", 28, 268436867, 134218433),
        Profile("dh-29", 29, 536871263, 268435631),
        Profile("dh-30", 30, 1073742623, 536871311),
        Profile("dh-31", 31, 2147483783, 1073741891),
        Profile("dh-32", 32, 4294967387, 2147483693),
        Profile("dh-33", 33, 8589935363, 4294967681),
        Profile("dh-34", 34, 17179869263, 8589934631),
        Profile("dh-35", 35, 34359739319, 17179869659),
        Profile("dh-36", 36, 68719477403, 34359738701),
    )
}


def _is_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for unsigned integers strictly below 2^64.

    This seven-base set covers the entire stated range. Rejecting larger inputs
    is intentional: the routine must not silently become a probabilistic test.
    The adapter's largest candidate is only 37 bits.
    """
    if type(n) is not int:
        raise ValueError("Primality input must be an integer")
    if n >= 1 << 64:
        raise ValueError("Primality input exceeds this validator's range")
    if n < 2:
        return False
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % small == 0:
            return n == small
    d, powers = n - 1, 0
    while not d & 1:
        d //= 2
        powers += 1
    for base in (2, 325, 9375, 28178, 450775, 9780504, 1795265022):
        a = base % n
        if a == 0:
            continue
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(powers - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def validate_profile(profile: Profile) -> None:
    """Reject parameters outside the exact distribution covered by the proof."""
    if not isinstance(profile, Profile):
        raise ValueError("Expected a registered parameter profile")
    if any(type(v) is not int for v in (profile.p, profile.q, profile.g, profile.parameter_bits)):
        raise ValueError("Profile parameters must be integers")
    if not 3 <= profile.parameter_bits <= 62:
        raise ValueError("Unsupported subgroup-order bit length")
    if profile.q.bit_length() != profile.parameter_bits:
        raise ValueError("Subgroup order does not match its bit-length label")
    if profile.p != 2 * profile.q + 1 or not _is_prime(profile.q) or not _is_prime(profile.p):
        raise ValueError("Expected a safe prime and its prime subgroup order")
    if not 1 < profile.g < profile.p - 1 or pow(profile.g, profile.q, profile.p) != 1:
        raise ValueError("Generator must have exactly the prime order q")


@lru_cache(maxsize=len(_PROFILES))
def _profile(profile_id: str) -> Profile:
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise ValueError("Unknown KEM profile")
    profile = _PROFILES[profile_id]
    validate_profile(profile)
    return profile


def _encode(profile: Profile, value: int) -> bytes:
    return value.to_bytes(profile.width, "big")


def _decode_element(profile: Profile, encoded: bytes) -> int:
    if not isinstance(encoded, bytes) or len(encoded) != profile.width:
        raise ValueError("Group element must use the exact fixed-width byte encoding")
    value = int.from_bytes(encoded, "big")
    if not 1 < value < profile.p - 1 or pow(value, profile.q, profile.p) != 1:
        raise ValueError("Group element must be a nonidentity member of the order-q subgroup")
    return value


def _kdf(profile: Profile, public_key: bytes, ciphertext: bytes, shared: int) -> bytes:
    fields = (
        profile.id.encode("ascii"),
        _encode(profile, profile.p),
        _encode(profile, profile.q),
        _encode(profile, profile.g),
        public_key,
        ciphertext,
        _encode(profile, shared),
    )
    return hashlib.sha256(
        DOMAIN + b"".join(len(field).to_bytes(4, "big") + field for field in fields)
    ).digest()


def keygen(profile_id: str) -> tuple[bytes, int]:
    """Return (canonical public-key bytes, private exponent); never persist x publicly."""
    profile = _profile(profile_id)
    private_key = secrets.randbelow(profile.q - 1) + 1
    return _encode(profile, pow(profile.g, private_key, profile.p)), private_key


def encapsulate(profile_id: str, public_key: bytes) -> tuple[bytes, bytes]:
    """Return (canonical ciphertext bytes, complete 32-byte shared-secret target)."""
    profile = _profile(profile_id)
    public_value = _decode_element(profile, public_key)
    ephemeral_exponent = secrets.randbelow(profile.q - 1) + 1
    ciphertext = _encode(profile, pow(profile.g, ephemeral_exponent, profile.p))
    shared = pow(public_value, ephemeral_exponent, profile.p)
    return ciphertext, _kdf(profile, public_key, ciphertext, shared)


def decapsulate(profile_id: str, private_key: int, ciphertext: bytes) -> bytes:
    """Return K, or raise ValueError for invalid input; never expose this as an oracle."""
    profile = _profile(profile_id)
    if type(private_key) is not int or not 1 <= private_key < profile.q:
        raise ValueError("Private exponent must be an integer in [1, q-1]")
    ciphertext_value = _decode_element(profile, ciphertext)
    public_key = _encode(profile, pow(profile.g, private_key, profile.p))
    return _kdf(profile, public_key, ciphertext, pow(ciphertext_value, private_key, profile.p))


def _parameters(profile: Profile) -> dict[str, str]:
    return {name: _encode(profile, getattr(profile, name)).hex() for name in ("p", "q", "g")}


def _encoding(profile: Profile) -> dict[str, Any]:
    return {
        "group_elements": "fixed-width big-endian bytes, lowercase hexadecimal in JSON",
        "group_element_bytes": profile.width,
        "shared_secret": "exactly 64 hexadecimal characters (32 complete bytes)",
        "shared_secret_bytes": SHARED_SECRET_BYTES,
    }


def _kdf_spec() -> dict[str, Any]:
    return {
        "hash": "SHA-256",
        "domain_hex": DOMAIN.hex(),
        "framing": "domain || uint32be(len(field)) || field, repeated for each field in order",
        "fields": ["profile_id (ASCII)", "p", "q", "g", "public_key", "ciphertext", "shared_group_element"],
        "integer_encoding": "all integer fields use group_element_bytes, big endian",
    }


def serialize(profile_id: str, public_key: bytes, ciphertext: bytes) -> dict[str, Any]:
    """Build public challenge JSON from an explicit allowlist of public inputs.

    Deliberately does not accept a keypair, secret, or generated-result object.
    This avoids accidental private-key disclosure via a generic serializer.
    """
    profile = _profile(profile_id)
    _decode_element(profile, public_key)
    _decode_element(profile, ciphertext)
    return {
        "scheme_id": SCHEME_ID,
        "version": SCHEME_VERSION,
        "profile_id": profile.id,
        "instance_size": profile.parameter_bits,
        "parameter_bits": profile.parameter_bits,
        "parameters": _parameters(profile),
        "public_key": public_key.hex(),
        "ciphertext": ciphertext.hex(),
        "encoding": _encoding(profile),
        "kdf": _kdf_spec(),
        "implementation_sha256": IMPLEMENTATION_SHA256,
    }


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    """Private generation result. Only .public is suitable for client responses."""

    public: dict[str, Any]
    target: bytes
    diagnostics: dict[str, Any]

    def __repr__(self) -> str:
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id: str) -> GeneratedChallenge:
    """Generate once, diagnose once, and preserve Encaps' K even on disagreement.

    No correctness-conditioned retries are performed. Private x and r are not
    returned. Python cannot promise erasure of their temporary in-memory values.
    The caller must store .target confidentially and never serialize this object.
    """
    public_key, private_key = keygen(profile_id)
    ciphertext, target = encapsulate(profile_id, public_key)
    recovered = decapsulate(profile_id, private_key, ciphertext)
    return GeneratedChallenge(
        public=serialize(profile_id, public_key, ciphertext),
        target=target,
        diagnostics={
            "correctness_checked": True,
            "shared_secret_matches": hmac.compare_digest(target, recovered),
            "honest_trials": 1,
            "implementation_sha256": IMPLEMENTATION_SHA256,
        },
    )


def get_manifest(profile_id: str) -> dict[str, Any]:
    """Describe one pinned teaching profile without any live challenge secrets."""
    profile = _profile(profile_id)
    return {
        "id": profile.id,
        "profile_id": profile.id,
        "scheme_id": SCHEME_ID,
        "version": SCHEME_VERSION,
        "name": f"Teaching DH-KEM · {profile.parameter_bits}-bit subgroup",
        "instance_size": profile.parameter_bits,
        "size_parameter": dict(SIZE_PARAMETER),
        "parameter_bits": profile.parameter_bits,
        "parameter_label": "subgroup-order bits",
        "modulus_bits": profile.p.bit_length(),
        "shared_secret_bytes": SHARED_SECRET_BYTES,
        "parameters": _parameters(profile),
        "encoding": _encoding(profile),
        "kdf": _kdf_spec(),
        "educational": True,
        "security_claim": "None. Deliberately weak parameters; not for protecting information.",
        "assumption_context": "Finite-field discrete logarithms / computational Diffie-Hellman; no security reduction claimed for this adapter.",
        "distribution": "Independent uniform x,r in {1,...,q-1}, sampled from OS randomness. No correctness-conditioned rejection or retries.",
        "algorithms": {
            "keygen": "x <- uniform(1..q-1); pk = g^x mod p; sk = x",
            "encapsulate": "r <- uniform(1..q-1); c = g^r mod p; K = KDF(profile, pk, c, pk^r mod p)",
            "decapsulate": "validate c; pk = g^sk mod p; K = KDF(profile, pk, c, c^sk mod p)",
        },
        "correctness": {
            "kind": "perfect",
            "failure_bound": "0",
            "argument": "For every admitted p,q,g and every honest x,r, (g^x)^r = g^(xr) = (g^r)^x modulo p. Both parties hash identical profile, parameters, public key, ciphertext, and fixed-width shared element, so their 32-byte outputs agree. Since q is prime and x,r are nonzero modulo q, honest public values pass the subgroup and nonidentity checks.",
            "scope": "All registered profiles (every integer subgroup-order size from 20 through 36 bits) and all honest random choices. Claim concerns the specified arithmetic and encoding; it does not prove executable conformity or confidentiality.",
            "assumptions": "Exact modular arithmetic; stated safe-prime/group checks; identical deterministic SHA-256 computation. Collision resistance is not needed for honest agreement.",
            "evidence_status": "Local reference argument and executable tests; independent review pending.",
            "implementation_checks": "tests/test_kem.py: exhaustive primality checks for constants, known-answer vector, round trips, input validation, and deliberate mismatch preservation. This manifest is not a test-run attestation.",
        },
        "admission": {
            "status": "teaching-only",
            "official_ranked": False,
            "reason": "Educational local lane. Independent correctness/specification review and public admission policy are pending.",
        },
        "implementation": {
            "source": "crucible/kem.py",
            "source_sha256": IMPLEMENTATION_SHA256,
            "version": SCHEME_VERSION,
            "runtime": "Python standard library; variable-time integer arithmetic",
        },
        "references": [
            {"title": "Executable reference specification and its tests", "path": "crucible/kem.py"},
            {"title": "NIST SP 800-227, section 2.2 (definition of KEM correctness; not approval of this adapter)", "url": "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-227.pdf"},
        ],
    }


def manifests() -> list[dict[str, Any]]:
    return [get_manifest(profile_id) for profile_id in _PROFILES]
