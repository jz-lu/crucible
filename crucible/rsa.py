"""Small RSA-based shared-secret encapsulation, Crucible reference release 1.

This original toy adapter is not RFC 9690 compliant: it uses small-to-large research
moduli and a custom transcript-bound SHA-256 KDF, not the RFC's KDF3. It is for
public attack exercises only. No confidentiality or chosen-ciphertext security
claim is made; Python integer arithmetic is variable-time.

For size n, independently sample p and q uniformly from primes of ceil(n/2)
and floor(n/2) bits for which gcd(65537, prime-1)=1. Reject pairs with p=q or
bit_length(p*q)!=n. This defines the exact key distribution. Encapsulation draws
z uniformly from 0..N-1 using OS randomness, sends z^e mod N, and hashes the
framed transcript plus z. These are parameter/distribution checks, never retries
conditioned on a correctness test. A generated challenge is checked exactly once.

N, e, c and z use ceil(n/8) bytes, big endian; pk is N||e. The KDF hashes DOMAIN
followed by uint32be(len(field))||field for profile_id ASCII, N, e, c, z in order.
The public serializer accepts only pk and c and never receives private material.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import math
from pathlib import Path
import secrets
from typing import Any

SCHEME_ID = "rsa-kem"
SCHEME_VERSION = "1"
SHARED_SECRET_BYTES = 32
EXPONENT = 65537
DOMAIN = b"crucible/rsa-kem/v1/shared-secret\x00"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SIZE_PARAMETER = {
    "name": "Instance size", "symbol": "n", "meaning": "RSA modulus bit length", "unit": "bits",
    "mapping": "For each registered n from 20 through 128, generate distinct primes of ceil(n/2) and floor(n/2) bits, with e=65537 invertible modulo (p-1)(q-1), and require their product N to have exactly n bits.",
    "comparison_scope": "scheme-version",
}


@dataclass(frozen=True)
class Profile:
    id: str
    parameter_bits: int

    @property
    def width(self):
        return (self.parameter_bits + 7) // 8


_PROFILES = {f"rsa-{bits}": Profile(f"rsa-{bits}", bits) for bits in range(20, 129)}


def _profile(profile_id):
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise ValueError("Unknown RSA parameter profile")
    return _PROFILES[profile_id]


def _is_prime(n: int) -> bool:
    """Deterministic Miller-Rabin for unsigned integers strictly below 2^64.

    This seven-base set covers the entire stated range. Rejecting larger inputs
    is intentional: the routine must not silently become a probabilistic test.
    This release samples factors of at most 64 bits, always strictly below 2^64.
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



def _sample_prime(bits):
    while True:
        candidate = (1 << (bits - 1)) | (secrets.randbelow(1 << (bits - 2)) << 1) | 1
        if _is_prime(candidate) and math.gcd(EXPONENT, candidate - 1) == 1:
            return candidate


def _encode(profile, value):
    return value.to_bytes(profile.width, "big")


def _public_values(profile, public_key):
    if not isinstance(public_key, bytes) or len(public_key) != 2 * profile.width:
        raise ValueError("Public key must encode N and e at the exact fixed width")
    modulus = int.from_bytes(public_key[:profile.width], "big")
    exponent = int.from_bytes(public_key[profile.width:], "big")
    if modulus.bit_length() != profile.parameter_bits or modulus % 2 != 1 or exponent != EXPONENT:
        raise ValueError("Public key does not match the registered modulus size and exponent")
    return modulus, exponent


def _ciphertext_value(profile, modulus, ciphertext):
    if not isinstance(ciphertext, bytes) or len(ciphertext) != profile.width:
        raise ValueError("Ciphertext must use the exact fixed-width encoding")
    value = int.from_bytes(ciphertext, "big")
    if value >= modulus:
        raise ValueError("Ciphertext must be an integer in [0, N-1]")
    return value


def _kdf(profile, public_key, ciphertext, secret_integer):
    fields = (profile.id.encode("ascii"), public_key[:profile.width], public_key[profile.width:],
              ciphertext, _encode(profile, secret_integer))
    return hashlib.sha256(DOMAIN + b"".join(len(field).to_bytes(4, "big") + field for field in fields)).digest()


@dataclass(frozen=True, repr=False)
class PrivateKey:
    p: int
    q: int
    d: int

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen(profile_id):
    profile = _profile(profile_id)
    while True:
        p = _sample_prime((profile.parameter_bits + 1) // 2)
        q = _sample_prime(profile.parameter_bits // 2)
        modulus = p * q
        if p != q and modulus.bit_length() == profile.parameter_bits:
            break
    private = PrivateKey(p, q, pow(EXPONENT, -1, (p - 1) * (q - 1)))
    return _encode(profile, modulus) + _encode(profile, EXPONENT), private


def encapsulate(profile_id, public_key):
    profile = _profile(profile_id)
    modulus, exponent = _public_values(profile, public_key)
    secret_integer = secrets.randbelow(modulus)
    ciphertext = _encode(profile, pow(secret_integer, exponent, modulus))
    return ciphertext, _kdf(profile, public_key, ciphertext, secret_integer)


def decapsulate(profile_id, private_key, ciphertext):
    profile = _profile(profile_id)
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Expected a private RSA key")
    p, q, d = private_key.p, private_key.q, private_key.d
    if any(type(value) is not int for value in (p, q, d)):
        raise ValueError("Private RSA parameters must be integers")
    if (p.bit_length() != (profile.parameter_bits + 1) // 2 or
            q.bit_length() != profile.parameter_bits // 2 or p == q or
            not _is_prime(p) or not _is_prime(q)):
        raise ValueError("Private factors do not match the registered distribution")
    modulus, phi = p * q, (p - 1) * (q - 1)
    if modulus.bit_length() != profile.parameter_bits or not 0 < d < phi or EXPONENT * d % phi != 1:
        raise ValueError("Invalid private exponent or modulus")
    value = _ciphertext_value(profile, modulus, ciphertext)
    public_key = _encode(profile, modulus) + _encode(profile, EXPONENT)
    return _kdf(profile, public_key, ciphertext, pow(value, d, modulus))


def _encoding(profile):
    return {
        "integers": "fixed-width big-endian bytes, lowercase hexadecimal in JSON",
        "integer_bytes": profile.width,
        "public_key": "N || e, each integer_bytes long",
        "public_key_bytes": 2 * profile.width,
        "ciphertext": "c, exactly integer_bytes long",
        "ciphertext_bytes": profile.width,
        "shared_secret": "exactly 64 hexadecimal characters (32 complete bytes)",
        "shared_secret_bytes": SHARED_SECRET_BYTES,
    }


def _kdf_spec():
    return {"hash": "SHA-256", "domain_hex": DOMAIN.hex(),
            "framing": "domain || uint32be(len(field)) || field, repeated for each field in order",
            "fields": ["profile_id (ASCII)", "N", "e", "ciphertext", "secret_integer"],
            "integer_encoding": "all integer fields use integer_bytes, big endian"}


def serialize(profile_id, public_key, ciphertext):
    profile = _profile(profile_id)
    modulus, exponent = _public_values(profile, public_key)
    _ciphertext_value(profile, modulus, ciphertext)
    return {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile.id,
            "instance_size": profile.parameter_bits, "parameter_bits": profile.parameter_bits,
            "parameters": {"N": _encode(profile, modulus).hex(), "e": _encode(profile, exponent).hex()},
            "public_key": public_key.hex(), "ciphertext": ciphertext.hex(),
            "encoding": _encoding(profile), "kdf": _kdf_spec(), "implementation_sha256": IMPLEMENTATION_SHA256}


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
    profile = _profile(profile_id)
    return {
        "id": profile.id, "profile_id": profile.id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
        "name": f"RSA over an integer modulus · {profile.parameter_bits}-bit modulus",
        "instance_size": profile.parameter_bits, "parameter_bits": profile.parameter_bits,
        "parameter_label": "modulus bits", "modulus_bits": profile.parameter_bits,
        "size_parameter": dict(SIZE_PARAMETER), "shared_secret_bytes": SHARED_SECRET_BYTES,
        "parameters": {"e": _encode(profile, EXPONENT).hex()},
        "encoding": _encoding(profile), "kdf": _kdf_spec(), "educational": True,
        "security_claim": "None. A research parameter ladder with a custom KDF; not a standardized RSA-KEM implementation or suitable for protecting information.",
        "assumption_context": "RSA inversion; factoring N enables inversion, but equivalence is not claimed. No security reduction is claimed for this adapter.",
        "distribution": "Independent uniform primes p,q of ceil(n/2), floor(n/2) bits with gcd(65537,p-1)=gcd(65537,q-1)=1, conditioned on p!=q and bit_length(p*q)=n. Independent uniform z in 0..N-1. OS randomness; no correctness-conditioned retries.",
        "algorithms": {
            "keygen": "Sample p,q from the specified distribution; N=pq; e=65537; d=e^(-1) mod ((p-1)(q-1)); pk=(N,e); sk=(p,q,d)",
            "encapsulate": "z <- uniform(0..N-1); c=z^e mod N; K=KDF(profile,N,e,c,z)",
            "decapsulate": "validate c; z=c^d mod N; K=KDF(profile,N,e,c,z)",
        },
        "correctness": {
            "kind": "perfect", "failure_bound": "0",
            "argument": "For distinct primes p,q and ed=1 mod (p-1)(q-1), z^(ed)=z modulo both p and q for every z in 0..N-1, including nonunits, by Fermat's theorem and the zero case. The Chinese remainder theorem gives (z^e)^d=z modulo N. Identical transcript encodings and deterministic SHA-256 then give identical 32-byte outputs.",
            "scope": "All registered sizes 20..128 and every honest key and encapsulation draw. This is an arithmetic/encoding argument, not executable certification or a confidentiality guarantee.",
            "assumptions": "Distinct primes, invertible e, exact modular arithmetic, and identical deterministic encoding and hashing. Collision resistance is not needed for honest agreement.",
            "evidence_status": "Local reference argument and executable tests; independent review pending.",
            "implementation_checks": "tests/test_extended.py: round trips across the size ladder, exact prime/size checks, fixed vector, malformed input, public-only recovery, and mismatch preservation. This manifest is not a test-run attestation.",
        },
        "admission": {"status": "teaching-only", "official_ranked": False,
                      "reason": "Educational local lane; independent review and public admission policy pending."},
        "implementation": {"source": "crucible/rsa.py", "source_sha256": IMPLEMENTATION_SHA256,
                           "version": SCHEME_VERSION, "runtime": "Python standard library; variable-time integer arithmetic"},
        "references": [{"title": "RSA-KEM construction background (RFC 9690); this custom toy adapter is not compliant", "url": "https://www.rfc-editor.org/rfc/rfc9690.html"}],
    }


def manifests():
    return [get_manifest(profile_id) for profile_id in _PROFILES]
