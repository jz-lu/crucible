"""Calibrated RSA recovery profiles, Crucible reference release 3.

Fresh balanced RSA factors are proved prime by private recursive Pocklington
certificates. The specified structured prime distribution is NOT uniform over
all primes. It is independent across newly generated keys, conditioned on the
declared factor/exponent/modulus checks and successful bounded generation.
There are no correctness-conditioned retries. No extra crypto dependency is
needed: this source artifact contains the sampler, certificate checker and KDF.

The initial classical calibration is a nominal factoring-cost model, not a
security guarantee or a proof of this custom KEM's security. This adapter is
not RFC 9690 compliant; it uses a custom transcript-bound SHA-256 KDF and
variable-time Python arithmetic. No IND-CPA or IND-CCA claim is made.

Tier and modulus size are separate fields. N,e,c,z use ceil(modulus_bits/8)
bytes; pk is N||e. KDF input is DOMAIN then uint32be(length)||field for
profile_id ASCII,N,e,c,z, in that order. Certificates and RSA factors remain
private and never enter the public serializer or correctness diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import math
from pathlib import Path
import secrets
import time
from typing import Any

SCHEME_ID = "rsa-kem"
SCHEME_VERSION = "3"
SHARED_SECRET_BYTES = 32
EXPONENT = 65537
DOMAIN = b"crucible/rsa-kem/v3/shared-secret\x00"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
SIZE_PARAMETER = {
    "name": "Challenge tier", "symbol": "t", "meaning": "Calibrated classical reference tier", "unit": "tier",
    "mapping": "Tiers 1..50 select the immutable RSA modulus-bit table in this release. The nominal target is 8+120*(t-1)/49 bits: a linear reference from 8 bits at tier 1 to 128 bits at tier 50. Choose the smallest n>=32 with min(n/4,128*F(n)/F(3072)) at least the target, where F(n)=(n*ln(2))^(1/3)*(ln(n*ln(2)))^(2/3). Tier 50 selects a 3072-bit modulus. This restricted rho/NFS reference omits ECM and other attacks; it is not a best-known attack estimate or a security guarantee.",
    "comparison_scope": "scheme-version",
}


@dataclass(frozen=True)
class Profile:
    id: str
    instance_size: int
    parameter_bits: int

    @property
    def width(self):
        return (self.parameter_bits + 7) // 8


# Frozen values of classical-baseline-v2, not a dependency on mutable registry
# or calibration code. The tests independently compare the published model.
MODULUS_BITS_BY_TIER = (
    32, 42, 52, 62, 72, 81, 91, 101, 111, 121,
    141, 164, 190, 217, 247, 279, 313, 350, 389, 430,
    474, 521, 571, 623, 678, 735, 796, 859, 925, 994,
    1067, 1142, 1221, 1302, 1387, 1475, 1566, 1661, 1759, 1861,
    1966, 2074, 2186, 2302, 2421, 2544, 2670, 2801, 2935, 3072,
)
_PROFILES = {f"rsa-v3-tier-{tier}": Profile(f"rsa-v3-tier-{tier}", tier, bits)
             for tier, bits in enumerate(MODULUS_BITS_BY_TIER, 1)}


def _initial_security_estimate(profile):
    def leading_term(bits):
        logarithm = bits * math.log(2)
        return logarithm ** (1 / 3) * math.log(logarithm) ** (2 / 3)
    tier, bits = profile.instance_size, profile.parameter_bits
    target = 8 + 120 * (tier - 1) / 49
    estimate = min(bits / 4, 128 * leading_term(bits) / leading_term(3072))
    return {"bits": round(estimate, 6), "target_bits": round(target, 6),
            "model_id": "classical-baseline-v2"}


def _profile(profile_id):
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise ValueError("Unknown RSA parameter profile")
    return _PROFILES[profile_id]


MAX_PRIME_BITS = 1536
MAX_CANDIDATES = 131072
MAX_PAIR_ATTEMPTS = 64
MAX_GENERATION_SECONDS = 5.0


class PrimeGenerationLimit(ValueError):
    """A bounded search exhausted its allowance; no instance was generated."""


@dataclass
class _GenerationBudget:
    remaining: int = MAX_CANDIDATES
    deadline: float = field(default_factory=lambda: time.monotonic() + MAX_GENERATION_SECONDS)

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise PrimeGenerationLimit("The bounded prime-generation time allowance was exhausted")

    def spend(self):
        self.check_deadline()
        if self.remaining <= 0:
            raise PrimeGenerationLimit("The bounded prime-generation search was exhausted")
        self.remaining -= 1


@dataclass(frozen=True, repr=False)
class PrimeCertificate:
    value: int
    factor: PrimeCertificate | None = None
    multiplier: int = 0

    def __repr__(self):
        return "PrimeCertificate(<private>)"


def _trial_is_prime(value):
    if type(value) is not int or not 2 <= value < (1 << 16):
        return False
    if value % 2 == 0:
        return value == 2
    return all(value % divisor for divisor in range(3, math.isqrt(value) + 1, 2))


_SMALL_PRIMES = tuple(value for value in range(3, 1000, 2) if _trial_is_prime(value))


def _prove_prime(bits, budget=None):
    """Sample a structured prime, with an exact private primality certificate.

    Base case: uniform odd k-bit candidates, exact trial division (k<=16).
    Otherwise draw one certified q of ceil(k/2)+1 bits, then uniform R in the
    exact interval making p=2Rq+1 k bits. Reject small factors or a failed
    base-2 Pocklington certificate. q^2>p, Fermat, and the gcd condition prove
    primality; there is no probable-prime error probability. All levels share
    a finite candidate budget and monotonic time deadline. Checks occur between
    bounded arithmetic operations, so the deadline is not a hard real-time SLA.
    Uniformity over all k-bit primes is not claimed.
    """
    if type(bits) is not int or not 2 <= bits <= MAX_PRIME_BITS:
        raise ValueError("Unsupported certified prime bit length")
    budget = _GenerationBudget() if budget is None else budget
    if bits <= 16:
        while True:
            budget.spend()
            value = (1 << (bits - 1)) | (secrets.randbelow(1 << (bits - 2)) << 1) | 1
            if _trial_is_prime(value):
                return PrimeCertificate(value)
    factor = _prove_prime((bits + 1) // 2 + 1, budget)
    step = 2 * factor.value
    minimum = ((1 << (bits - 1)) - 1 + step - 1) // step
    maximum = ((1 << bits) - 2) // step
    while True:
        budget.spend()
        multiplier = minimum + secrets.randbelow(maximum - minimum + 1)
        value = step * multiplier + 1
        if any(value % prime == 0 for prime in _SMALL_PRIMES):
            continue
        if (factor.value * factor.value > value and pow(2, value - 1, value) == 1
                and math.gcd(pow(2, 2 * multiplier, value) - 1, value) == 1):
            return PrimeCertificate(value, factor, multiplier)


def _validate_prime_certificate(certificate, *, _depth=0):
    """Check a bounded proof tree exactly; return its proved prime value."""
    if not isinstance(certificate, PrimeCertificate) or _depth > 16:
        raise ValueError("Invalid private prime certificate")
    value = certificate.value
    if type(value) is not int or not 2 <= value or value.bit_length() > MAX_PRIME_BITS:
        raise ValueError("Invalid certified prime value")
    if value.bit_length() <= 16:
        if (certificate.factor is not None or type(certificate.multiplier) is not int
                or certificate.multiplier != 0 or not _trial_is_prime(value)):
            raise ValueError("Invalid trial-division prime certificate")
        return value
    factor = _validate_prime_certificate(certificate.factor, _depth=_depth + 1)
    multiplier = certificate.multiplier
    if (type(multiplier) is not int or multiplier <= 0
            or multiplier.bit_length() > MAX_PRIME_BITS
            or factor.bit_length() != (value.bit_length() + 1) // 2 + 1
            or value != 2 * multiplier * factor + 1 or factor * factor <= value
            or pow(2, value - 1, value) != 1
            or math.gcd(pow(2, 2 * multiplier, value) - 1, value) != 1):
        raise ValueError("Invalid Pocklington prime certificate")
    return value


def _sample_prime(bits, budget):
    while True:
        certificate = _prove_prime(bits, budget)
        if math.gcd(EXPONENT, certificate.value - 1) == 1:
            return certificate


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
    p_certificate: PrimeCertificate
    q_certificate: PrimeCertificate

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen(profile_id):
    profile = _profile(profile_id)
    budget = _GenerationBudget()
    for _ in range(MAX_PAIR_ATTEMPTS):
        p_certificate = _sample_prime((profile.parameter_bits + 1) // 2, budget)
        q_certificate = _sample_prime(profile.parameter_bits // 2, budget)
        p, q = p_certificate.value, q_certificate.value
        modulus = p * q
        if p != q and modulus.bit_length() == profile.parameter_bits:
            break
    else:
        raise PrimeGenerationLimit("The bounded RSA key-pair search was exhausted")
    private = PrivateKey(p, q, pow(EXPONENT, -1, (p - 1) * (q - 1)), p_certificate, q_certificate)
    budget.check_deadline()
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
            _validate_prime_certificate(private_key.p_certificate) != p or
            _validate_prime_certificate(private_key.q_certificate) != q):
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
            "instance_size": profile.instance_size, "parameter_bits": profile.parameter_bits,
            "initial_security_estimate": _initial_security_estimate(profile),
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
        "name": f"RSA over an integer modulus · Tier {profile.instance_size}",
        "instance_size": profile.instance_size, "parameter_bits": profile.parameter_bits,
        "parameter_label": "modulus bits", "modulus_bits": profile.parameter_bits,
        "initial_security_estimate": _initial_security_estimate(profile),
        "size_parameter": dict(SIZE_PARAMETER), "shared_secret_bytes": SHARED_SECRET_BYTES,
        "parameters": {"e": _encode(profile, EXPONENT).hex()},
        "encoding": _encoding(profile), "kdf": _kdf_spec(), "educational": True,
        "security_claim": "No confidentiality guarantee. Tiers use a restricted classical rho/NFS reference model that omits ECM and other attacks, not best-known security estimates. This structured-prime, custom-KDF construction is not a standardized RSA-KEM implementation or suitable for protecting information.",
        "assumption_context": "RSA inversion; factoring N enables inversion, but equivalence is not claimed. No security reduction is claimed for this adapter.",
        "distribution": "For each factor bit length k, sample a private recursive Pocklington certificate: for k<=16, uniform odd k-bit candidates tested by exact trial division; otherwise first sample a certified q of ceil(k/2)+1 bits, then uniform R in the interval making p=2Rq+1 exactly k bits, accepting only the published base-2 certificate checks. This is a structured distribution, not uniform over all primes. Independently generate factors of ceil(n/2), floor(n/2) bits with gcd(65537,prime-1)=1; accept distinct factors whose product has exactly n bits. A key search allows 131072 candidates across all recursive calls, at most 64 factor pairs, and a shared 5-second monotonic deadline checked between bounded arithmetic operations and before returning a key. This is not a hard real-time SLA. Failed bounded generation returns no key. Fresh OS randomness for each new key and uniform z in 0..N-1 for encapsulation. No correctness-conditioned retries.",
        "algorithms": {
            "keygen": "Look up modulus size n from tier t. Sample independently certified p,q from the specified bounded distribution; N=pq; e=65537; d=e^(-1) mod ((p-1)(q-1)); pk=(N,e); sk=(p,q,d,private prime certificates). Raise PrimeGenerationLimit if bounded sampling fails.",
            "encapsulate": "z <- uniform(0..N-1); c=z^e mod N; K=KDF(profile,N,e,c,z)",
            "decapsulate": "Validate private primality certificates, factor sizes, distinctness, modulus size and private inverse; validate fixed-width c in 0..N-1; z=c^d mod N; K=KDF(profile,N,e,c,z). Invalid input raises ValueError; no implicit-rejection key is returned.",
        },
        "correctness": {
            "kind": "perfect", "failure_bound": "0",
            "argument": "For distinct primes p,q and ed=1 mod (p-1)(q-1), z^(ed)=z modulo both p and q for every z in 0..N-1, including nonunits, by Fermat's theorem and the zero case. The Chinese remainder theorem gives (z^e)^d=z modulo N. Identical transcript encodings and deterministic SHA-256 then give identical 32-byte outputs.",
            "scope": "All registered tiers 1..50, conditioned on successful bounded key generation, and every honest encapsulation draw including nonunits. A sampling-limit abort produces no instance and is separate from agreement. This arithmetic/encoding argument is not executable certification or a confidentiality guarantee.",
            "assumptions": "Exact trial division for leaves and exact Pocklington certificate checks prove distinct prime factors; e is invertible. Exact modular arithmetic and identical deterministic encoding and hashing. No probable-prime assumption; collision resistance is not needed for honest agreement.",
            "evidence_status": "Local reference argument and executable tests; independent review pending.",
            "implementation_checks": "tests/test_rsa_v3.py: frozen calibration mapping, independent prime-certificate verification, boundary round trips, fixed vector, nonunit inputs, malformed input, bounded failures, public-only serialization, and mismatch preservation. This manifest is not a test-run attestation.",
        },
        "admission": {"status": "teaching-only", "official_ranked": False,
                      "reason": "Educational local lane; independent review and public admission policy pending."},
        "implementation": {"source": "crucible/rsa_v3.py", "source_sha256": IMPLEMENTATION_SHA256,
                           "version": SCHEME_VERSION, "runtime": "Python standard library; variable-time integer arithmetic"},
        "references": [
            {"title": "RSA-KEM construction background (RFC 9690); this custom adapter is not compliant", "url": "https://www.rfc-editor.org/rfc/rfc9690.html"},
            {"title": "Pocklington primality proof and constructive prime generation, Handbook of Applied Cryptography chapter 4", "url": "https://cacr.uwaterloo.ca/hac/about/chap4.pdf"},
        ],
    }


def manifests():
    return [get_manifest(profile_id) for profile_id in _PROFILES]
