"""Prime-order elliptic-curve DH research ladder, with exact public proofs.

This is a custom short-Weierstrass/full-point KEM, NOT X25519 or RFC 9180.
The exact X25519 adapter remains a separately identified published reference.
Variable-time arithmetic is suitable for public research challenges only.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets

SCHEME_ID = "dhkem-x25519"
SCHEME_VERSION = "curve-research-v1"
SHARED_SECRET_BYTES = 32
DOMAIN = b"crucible/ec-research-v1/shared-secret\x00"
SOURCE = Path(__file__)
PARAMETERS = SOURCE.with_name("ec_parameters.json")
IMPLEMENTATION_SHA256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
PARAMETER_SHA256 = hashlib.sha256(PARAMETERS.read_bytes()).hexdigest()
SIZE_PARAMETER = {"name": "Challenge tier", "symbol": "t", "unit": "tier",
    "meaning": "Frozen generic elliptic-curve discrete-log reference",
    "mapping": "Tiers 1–50 use distinct certified prime-order curves. Target generic rho exponent is 8+120(t−1)/49; the displayed reference is log2(q)/2 for the actual group order q. This is a generic attack reference, not a security lower bound or an X25519 parameter mapping.",
    "comparison_scope": "ec-generic-rho-v1"}
INITIAL_SECURITY_MODEL = {"id": "ec-generic-rho-v1", "name": "Generic elliptic-curve rho reference",
    "unit": "log2 group operations", "status": "limited-reference",
    "description": "log2(q)/2; omits constants, parallel tradeoffs, curve-specific attacks, implementation attacks and quantum computation. Not an exhaustive security estimate."}


def _add(p, a, left, right):
    if left is None:
        return right
    if right is None:
        return left
    x, y = left
    u, v = right
    if x == u:
        if (y+v) % p == 0:
            return None
        if y != v:
            raise ValueError("Invalid curve arithmetic")
        numerator, denominator = 3*x*x+a, 2*y
    else:
        numerator, denominator = v-y, u-x
    # Also used for primality proofs: nonunits must reject, not masquerade as
    # infinity over a composite modulus.
    slope = numerator * pow(denominator, -1, p) % p
    result_x = (slope*slope-x-u) % p
    return (result_x, (slope*(x-result_x)-y) % p)


def _mul(p, a, point, scalar):
    result = None
    while scalar:
        if scalar & 1:
            result = _add(p, a, result, point)
        point = _add(p, a, point, point)
        scalar >>= 1
    return result


def _lucas(cert, depth=0):
    if depth > 64 or type(cert) is not dict:
        raise ValueError("Invalid prime proof")
    n = cert["n"]
    if type(n) is not int or not 2 <= n < 2**64:
        raise ValueError("Invalid prime proof leaf")
    if n < 65536:
        if any(n % d == 0 for d in range(2, math.isqrt(n)+1)):
            raise ValueError("Composite proof leaf")
        return n
    a = cert["a"]
    product, seen = 1, set()
    for child, exponent in cert["factors"]:
        factor = _lucas(child, depth+1)
        if factor in seen or type(exponent) is not int or not 1 <= exponent <= 63:
            raise ValueError("Invalid Lucas factor")
        seen.add(factor)
        product *= factor**exponent
        if math.gcd(pow(a, (n-1)//factor, n)-1, n) != 1:
            raise ValueError("Invalid Lucas witness")
    if product != n-1 or pow(a, n-1, n) != 1:
        raise ValueError("Invalid Lucas factorization")
    return n


def verify_prime(cert):
    """Verify PARI's public ECPP chain; recursively prove its small last prime.

    Each step supplies m=s*q and a nonsingular curve with mP=O and sP!=O.
    The next proven q exceeds (N^(1/4)+1)^2. Unit denominators ensure these
    equalities reduce correctly modulo every prime divisor of N; Hasse's
    bound rules out a divisor <=sqrt(N). No probable-prime test is used.
    """
    if "lucas" in cert:
        return _lucas(cert["lucas"])
    q = _lucas(cert["terminal"])
    for n, t, s, a, point in reversed(cert["ecpp"]):
        if not (type(n) is int and 2**64 <= n < 2**300 and math.gcd(n, 6) == 1 and t*t < 4*n and s > 0 and n+1-t == s*q):
            raise ValueError("Invalid ECPP chain")
        if q <= (math.isqrt(math.isqrt(n))+2)**2:
            raise ValueError("ECPP order too small")
        x, y = point
        if not (0 <= x < n and 0 <= y < n and 0 <= a < n):
            raise ValueError("Invalid ECPP coordinates")
        b = (y*y-x*x*x-a*x) % n
        if math.gcd(4*a*a*a+27*b*b, n) != 1:
            raise ValueError("Singular ECPP curve")
        sp = _mul(n, a, tuple(point), s)
        if sp is None or _mul(n, a, sp, q) is not None:
            raise ValueError("Invalid ECPP order witness")
        q = n
    return q


@lru_cache(maxsize=1)
def _parameters():
    rows = json.loads(PARAMETERS.read_text())
    if len(rows) != 50:
        raise ValueError("Incomplete curve ladder")
    previous = 0
    for tier, row in enumerate(rows, 1):
        p, q, a, b = row["p"], row["q"], row["a"], row["b"]
        if row["tier"] != tier or verify_prime(row["p_certificate"]) != p or verify_prime(row["q_certificate"]) != q:
            raise ValueError("Invalid curve parameter certificate")
        if not (p % 4 == 3 and a == p-3 and 0 < b < p and (4*a*a*a+27*b*b) % p and q != p and q > previous):
            raise ValueError("Invalid curve parameters")
        x, y = row["g"]
        if not (0 <= x < p and 0 < y < p and (y*y-x*x*x-a*x-b) % p == 0):
            raise ValueError("Invalid curve generator")
        # Prime q and nonzero G imply q divides the order. The Hasse interval
        # contains exactly one positive multiple of q, namely q itself.
        radius = math.isqrt(4*p)
        if (not p+1-radius <= q <= p+1+radius or 2*q <= p+1+radius
                or _mul(p, a, (x, y), q) is not None
                or any(pow(p, k, q) == 1 for k in range(1, 101))):
            raise ValueError("Invalid prime-order curve proof")
        previous = q
    return tuple(rows)


def _profile(profile_id):
    if type(profile_id) is not str or not profile_id.startswith("ec-research-v1-tier-"):
        raise ValueError("Unknown elliptic-curve tier")
    try:
        tier = int(profile_id.removeprefix("ec-research-v1-tier-"))
    except ValueError:
        raise ValueError("Unknown elliptic-curve tier") from None
    if not 1 <= tier <= 50 or profile_id != f"ec-research-v1-tier-{tier}":
        raise ValueError("Unknown elliptic-curve tier")
    return _parameters()[tier-1]


def artifact_manifest():
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
        "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
        "files": [{"path": "crucible/ec_research.py", "sha256": IMPLEMENTATION_SHA256},
                  {"path": "crucible/ec_parameters.json", "sha256": PARAMETER_SHA256}],
        "runtime": "Python integer arithmetic; SHA-256; OS randomness; no Sage runtime"}
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def verify_artifact():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != IMPLEMENTATION_SHA256 or hashlib.sha256(PARAMETERS.read_bytes()).hexdigest() != PARAMETER_SHA256:
        raise RuntimeError("Elliptic-curve implementation changed")
    _parameters()


def _encode(row, point):
    width = (row["p"].bit_length()+7)//8
    if point is None:
        raise ValueError("Identity cannot be encoded")
    return b"\x04" + point[0].to_bytes(width, "big") + point[1].to_bytes(width, "big")


def _decode(row, value):
    width = (row["p"].bit_length()+7)//8
    if type(value) is not bytes or len(value) != 1+2*width or value[0] != 4:
        raise ValueError("Invalid canonical point encoding")
    x, y = int.from_bytes(value[1:1+width], "big"), int.from_bytes(value[1+width:], "big")
    if not (x < row["p"] and y < row["p"] and (y*y-x*x*x-row["a"]*x-row["b"]) % row["p"] == 0):
        raise ValueError("Point is not on the registered curve")
    # The certified entire curve has prime order q, so any affine point has
    # exactly order q. No untrusted domain parameters are accepted.
    return x, y


def _kdf(profile_id, row, public_key, ciphertext, shared):
    domain_parameters = json.dumps({k: row[k] for k in ("p", "a", "b", "q", "g")}, sort_keys=True, separators=(",", ":")).encode()
    pieces = [profile_id.encode(), domain_parameters, public_key, ciphertext, _encode(row, shared)]
    return hashlib.sha256(DOMAIN + b"".join(len(piece).to_bytes(4, "big") + piece for piece in pieces)).digest()


@dataclass(frozen=True, repr=False)
class PrivateKey:
    profile_id: str
    scalar: int

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen(profile_id):
    row = _profile(profile_id)
    scalar = secrets.randbelow(row["q"]-1)+1
    return _encode(row, _mul(row["p"], row["a"], tuple(row["g"]), scalar)), PrivateKey(profile_id, scalar)


def encapsulate(profile_id, public_key):
    row = _profile(profile_id)
    point = _decode(row, public_key)
    scalar = secrets.randbelow(row["q"]-1)+1
    ciphertext = _encode(row, _mul(row["p"], row["a"], tuple(row["g"]), scalar))
    shared = _mul(row["p"], row["a"], point, scalar)
    return ciphertext, _kdf(profile_id, row, public_key, ciphertext, shared)


def decapsulate(profile_id, private_key, ciphertext):
    row = _profile(profile_id)
    if not isinstance(private_key, PrivateKey) or private_key.profile_id != profile_id or type(private_key.scalar) is not int or not 1 <= private_key.scalar < row["q"]:
        raise ValueError("Invalid private key")
    point = _decode(row, ciphertext)
    public_key = _encode(row, _mul(row["p"], row["a"], tuple(row["g"]), private_key.scalar))
    shared = _mul(row["p"], row["a"], point, private_key.scalar)
    return _kdf(profile_id, row, public_key, ciphertext, shared)


def _encoding(row):
    size = 1+2*((row["p"].bit_length()+7)//8)
    return {"public_key": "0x04 || x || y, fixed-width big-endian field coordinates",
        "ciphertext": "Ephemeral point with the same canonical full-point encoding",
        "public_key_bytes": size, "ciphertext_bytes": size,
        "shared_secret": "Complete 32-byte transcript hash, 64 lowercase hexadecimal characters", "shared_secret_bytes": 32}


def get_manifest(profile_id):
    row = _profile(profile_id)
    return {"id": profile_id, "profile_id": profile_id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
        "name": f"Elliptic-curve Diffie–Hellman · Tier {row['tier']}", "profile_label": f"Tier {row['tier']}",
        "profile_kind": "research-tier", "instance_size": row["tier"], "parameter_bits": row["p"].bit_length(),
        "parameter_label": "field bits", "size_parameter": dict(SIZE_PARAMETER), "shared_secret_bytes": 32,
        "parameters": {k: row[k] for k in ("p", "a", "b", "q", "g")}, "encoding": _encoding(row),
        "initial_security_estimate": {"model_id": INITIAL_SECURITY_MODEL["id"], "bits": round(math.log2(row["q"])/2, 6), "target_bits": round(8+120*(row["tier"]-1)/49, 6)},
        "kdf": {"name": "Transcript-bound SHA-256", "domain_hex": DOMAIN.hex(), "framing": "uint32 big-endian length before each component; profile ID, canonical JSON p/a/b/q/g, public key, ciphertext, full shared point", "output_bytes": 32},
        "educational": False, "record_inheritance": True,
        "security_claim": "A custom prime-order elliptic-curve DH research KEM; no IND-CPA/IND-CCA proof or deployment recommendation. Generic rho references are not security lower bounds. These are not X25519 curves or RFC 9180 encodings.",
        "assumption_context": "Computational Diffie–Hellman in the specified prime-order elliptic curve, with a custom transcript hash. Quantum discrete-log algorithms apply.",
        "distribution": "Fresh independent uniform recipient and ephemeral scalars in 1..q−1 from OS randomness. Public curves are deterministically selected once by the published SHAKE seed/search. No short seeds, restricted private scalar range, reused private keys, or correctness retries.",
        "algorithms": {"keygen": "Sample x uniformly in 1..q−1. Publish X=xG; retain x.",
            "encapsulate": "Validate X. Sample r uniformly in 1..q−1; C=rG; Z=rX. Hash the framed profile, parameters, X, C and Z.",
            "decapsulate": "Validate C; reconstruct X=xG; Z=xC. Apply the identical transcript hash."},
        "correctness": {"kind": "perfect", "failure_bound": "0",
            "argument": "Exact ECPP/Lucas proofs establish prime p and q. A nonidentity G with qG=O and uniqueness in Hasse's interval proves the entire curve has order q. For all honest nonzero x,r, r(xG)=x(rG); canonical encodings and deterministic transcript hashing therefore agree.",
            "scope": "Every registered research curve and all honestly sampled nonzero scalars under exact arithmetic/encoding; malformed inputs excluded.",
            "assumptions": "Faithful integer/group arithmetic and deterministic hash implementation; confidentiality is separate.",
            "evidence_status": "Machine-checked public certificates and independent Sage arithmetic tests; independent implementation audit pending.",
            "implementation_checks": "tests/test_ec_research.py; scripts/check_ec_parameters.py; pinned public parameter certificate closure."},
        "admission": {"status": "local-beta", "official_ranked": False, "reason": "Research derivative; independent manual admission pending."},
        "implementation": {"source": "crucible/ec_research.py", "source_sha256": IMPLEMENTATION_SHA256, "artifact_sha256": artifact_manifest()["artifact_sha256"], "version": SCHEME_VERSION, "runtime": "Variable-time Python integer curve arithmetic and SHA-256", "evidence": "docs/implementation-ec-research.txt"},
        "references": [{"title": "PARI ECPP certificate definition", "url": "https://pari.math.u-bordeaux.fr/dochtml/html-stable/Arithmetic_functions.html#primecert"},
            {"title": "Published X25519 reference (different curves and KEM)", "url": "https://www.rfc-editor.org/rfc/rfc9180.html"}]}


def manifests():
    return [get_manifest(f"ec-research-v1-tier-{tier}") for tier in range(1, 51)]


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict
    target: bytes
    diagnostics: dict

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def serialize(profile_id, public_key, ciphertext):
    manifest = get_manifest(profile_id)
    row = _profile(profile_id)
    _decode(row, public_key)
    _decode(row, ciphertext)
    return {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile_id,
        **{k: manifest[k] for k in ("instance_size", "parameter_bits", "profile_kind", "profile_label", "parameters", "encoding", "kdf", "initial_security_estimate")},
        "public_key": public_key.hex(), "ciphertext": ciphertext.hex(), "implementation_sha256": IMPLEMENTATION_SHA256,
        "artifact_sha256": artifact_manifest()["artifact_sha256"]}


def generate(profile_id):
    verify_artifact()
    public_key, private_key = keygen(profile_id)
    ciphertext, target = encapsulate(profile_id, public_key)
    recovered = decapsulate(profile_id, private_key, ciphertext)
    verify_artifact()
    return GeneratedChallenge(serialize(profile_id, public_key, ciphertext), target,
        {"correctness_checked": True, "shared_secret_matches": hmac.compare_digest(target, recovered), "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256,
         "artifact_sha256": artifact_manifest()["artifact_sha256"]})
