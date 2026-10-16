"""Lossless module-LWE research KEM with an eleven-profile dimension ladder.

Independent nonstandard construction. Not standardized ML-KEM, and no inherited
ML-KEM security theorem. Each profile uses its complete native plaintext space.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import selectors
import struct
import subprocess
import sys
import time

SCHEME_ID = "ml-kem"
SCHEME_VERSION = "dimension-research-v2"
ETA = 2
# Primes independently proved minimal above the exact support-bound threshold.
PARAMETER_SETS = ((8, 1, 269), (16, 1, 541), (32, 1, 1039), (64, 1, 2063),
                  (64, 2, 4111), (64, 3, 6163), (128, 2, 8209), (128, 3, 12301),
                  (128, 4, 16411), (128, 5, 20507), (256, 3, 24593))
ESTIMATED_BITS = (None, None, None, None, None, 25.112, 37.96, 63.364, 88.185503, 113.169697, 137.547943)
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
GENERATION_TIMEOUT_SECONDS = 20.0
SIZE_PARAMETER = {
    "name": "Challenge tier", "symbol": "", "unit": "tier",
    "meaning": "Index into eleven distinct dimension profiles of this research construction",
    "mapping": "Increasing total dimensions8,16,32,64,128,192,256,384,512,640,768 with nondecreasing native plaintext capacity. Each modulus is the smallest prime covering the same perfect-correctness bound; no interpolated parameters.",
    "comparison_scope": "scheme-version",
}
INITIAL_SECURITY_MODEL = {
    "id": "module-lwe-dimension-core-svp-v2",
    "name": "Module-LWE dimension research classical Core-SVP reference",
    "status": "heuristic-reference", "tier_range": {"min": 1, "max": 11},
    "scope": "Classical primal uSVP and dual hybrid estimates for each flattened LWE dimension n*rank, m=n*rank, CBD2 secret/error; ADPS16 Core-SVP/GSA. Native-message enumeration is reported separately in candidate encryptions, not Core-SVP operations. Not a complete module/KEM security claim.",
    "estimator_commit": "53da5982597709ba0fdf94ea37a84d822310fd84",
    "limitations": [
        "Flattened LWE omits additional ring/module structure and ciphertext information.",
        "No complete KEM reduction, quantum, multi-target or side-channel claim.",
        "Unsupported tiny dimensions and beta40-floor profiles have no numerical estimate.",
        "Native plaintext lengths below256 bits impose explicit message-enumeration ceilings.",
    ],
    "references": [{"title": "Pinned lattice-estimator", "url": "https://github.com/malb/lattice-estimator/tree/53da5982597709ba0fdf94ea37a84d822310fd84"}],
}


class GenerationLimit(ValueError):
    pass


class GenerationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Parameters:
    tier: int
    n: int
    rank: int
    q: int

    @property
    def message_bytes(self):
        return self.n // 8

    @property
    def binding(self):
        return self.n.to_bytes(2, "little") + bytes([self.rank]) + self.q.to_bytes(4, "little")

    @property
    def noise_bound(self):
        return 2 * self.rank * self.n * ETA**2 + ETA


PROFILES = tuple(Parameters(i, *values) for i, values in enumerate(PARAMETER_SETS, 1))


def _profile(profile_id):
    if type(profile_id) is str:
        for p in PROFILES:
            if profile_id == f"module-lwe-v2-tier-{p.tier}":
                return p
    raise ValueError("Unknown module-LWE dimension profile")


def _parameters(n, rank):
    if type(n) is int and type(rank) is int:
        for p in PROFILES:
            if (p.n, p.rank) == (n, rank):
                return p
    raise ValueError("Unregistered module-LWE dimension parameters")


def _bytes(value, size, label):
    if type(value) is not bytes or len(value) != size:
        raise ValueError(f"{label} must contain exactly {size} bytes")
    return value


def cbd(data, n):
    """CBD2: all four independent bits per native polynomial coefficient."""
    _bytes(data, n // 2, "CBD input")
    return [(v & 1) + ((v >> 1) & 1) - ((v >> 2) & 1) - ((v >> 3) & 1)
            for byte in data for v in (byte & 15, byte >> 4)]


def noise(seed, nonce, p):
    _bytes(seed, 32, "Noise seed")
    if type(nonce) is not int or not 0 <= nonce <= 255:
        raise ValueError("Invalid noise nonce")
    domain = b"Crucible-Module-LWE-v2/noise" + p.binding + seed + bytes([nonce])
    return cbd(hashlib.shake_256(domain).digest(p.n // 2), p.n)


def uniform_poly(seed, p, row, column):
    """Pseudorandom coefficient-domain expansion; unbiased under ideal-XOF bits."""
    _bytes(seed, 32, "Matrix seed")
    if type(row) is not int or type(column) is not int or not 0 <= row < p.rank or not 0 <= column < p.rank:
        raise ValueError("Invalid matrix index")
    width = (p.q.bit_length() + 7) // 8
    mask = (1 << p.q.bit_length()) - 1
    domain = b"Crucible-Module-LWE-v2/A" + p.binding + seed + bytes([row, column])
    length, offset, out = width * p.n * 2, 0, []
    while len(out) < p.n:
        data = hashlib.shake_128(domain).digest(length)
        while offset + width <= len(data) and len(out) < p.n:
            value = int.from_bytes(data[offset:offset + width], "little") & mask
            offset += width
            if value < p.q:
                out.append(value)
        if len(out) < p.n:
            length *= 2
            if length > width * p.n * 32:
                raise GenerationLimit("Matrix rejection sampler exceeded its bounded expansion")
    return out


def poly_mul(a, b, p):
    """Exact schoolbook negacyclic product modulo x^n+1 and q."""
    if len(a) != p.n or len(b) != p.n:
        raise ValueError("Polynomial degree mismatch")
    out = [0] * (2 * p.n - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] += x * y
    return [(out[i] - (out[i + p.n] if i + p.n < len(out) else 0)) % p.q for i in range(p.n)]


def _sum_products(left, right, error, p):
    if len(left) != len(right) or len(error) != p.n:
        raise ValueError("Polynomial vector dimension mismatch")
    out = list(error)
    for a, b in zip(left, right):
        out = [(x + y) % p.q for x, y in zip(out, poly_mul(a, b, p))]
    return out


def pack(polys, p):
    width = (p.q.bit_length() + 7) // 8
    out = bytearray()
    for poly in polys:
        if len(poly) != p.n:
            raise ValueError("Polynomial degree mismatch")
        for value in poly:
            if type(value) is not int or not 0 <= value < p.q:
                raise ValueError("Noncanonical coefficient")
            out.extend(value.to_bytes(width, "little"))
    return bytes(out)


def unpack(data, count, p):
    width = (p.q.bit_length() + 7) // 8
    _bytes(data, count * p.n * width, "Packed polynomials")
    values = [int.from_bytes(data[i:i + width], "little") for i in range(0, len(data), width)]
    if any(v >= p.q for v in values):
        raise ValueError("Noncanonical coefficient")
    return [values[i * p.n:(i + 1) * p.n] for i in range(count)]


def _sizes(p):
    width = (p.q.bit_length() + 7) // 8
    return 43 + p.rank * p.n * width, (p.rank + 1) * p.n * width


def _decode_pk(pk, p):
    _bytes(pk, _sizes(p)[0], "Public key")
    if pk[:4] != b"MLD2" or pk[4:11] != p.binding:
        raise ValueError("Public key belongs to another dimension profile")
    return pk[11:43], unpack(pk[43:], p.rank, p)


@dataclass(frozen=True, repr=False)
class PrivateKey:
    value: bytes

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen_parameters(n, rank, randomness=None):
    p = _parameters(n, rank)
    coins = os.urandom(64) if randomness is None else _bytes(randomness, 64, "Key-generation randomness")
    expanded = hashlib.sha3_512(b"Crucible-Module-LWE-v2/keygen" + p.binding + coins[:32]).digest()
    rho, sigma = expanded[:32], expanded[32:]
    s = [noise(sigma, i, p) for i in range(p.rank)]
    e = [noise(sigma, p.rank + i, p) for i in range(p.rank)]
    t = [_sum_products([uniform_poly(rho, p, i, j) for j in range(p.rank)], s, e[i], p) for i in range(p.rank)]
    pk = b"MLD2" + p.binding + rho + pack(t, p)
    secret = struct.pack("b" * (p.rank * p.n), *(x for poly in s for x in poly))
    return pk, PrivateKey(coins[32:] + secret + pk + hashlib.sha3_256(pk).digest())


def _derive(p, message, pk_hash):
    return hashlib.sha3_512(b"Crucible-Module-LWE-v2/encap" + p.binding + message + pk_hash).digest()


def _encrypt(p, pk, message, coins):
    rho, t = _decode_pk(pk, p)
    _bytes(message, p.message_bytes, "Native plaintext")
    _bytes(coins, 32, "Encryption coins")
    r = [noise(coins, i, p) for i in range(p.rank)]
    e1 = [noise(coins, p.rank + i, p) for i in range(p.rank)]
    e2 = noise(coins, 2 * p.rank, p)
    u = [_sum_products([uniform_poly(rho, p, j, i) for j in range(p.rank)], r, e1[i], p) for i in range(p.rank)]
    bits = int.from_bytes(message, "little")
    encoded = [e2[i] + ((bits >> i) & 1) * ((p.q + 1) // 2) for i in range(p.n)]
    return pack(u + [_sum_products(t, r, encoded, p)], p)


def encapsulate_parameters(n, rank, public_key, randomness=None):
    p = _parameters(n, rank)
    _decode_pk(public_key, p)
    message = os.urandom(p.message_bytes) if randomness is None else _bytes(randomness, p.message_bytes, "Native plaintext")
    derived = _derive(p, message, hashlib.sha3_256(public_key).digest())
    return _encrypt(p, public_key, message, derived[32:]), derived[:32]


def decapsulate_parameters(n, rank, private_key, ciphertext):
    p = _parameters(n, rank)
    pk_size, ct_size = _sizes(p)
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Invalid private key")
    sk = _bytes(private_key.value, 64 + p.rank * p.n + pk_size, "Private key")
    ct = _bytes(ciphertext, ct_size, "Ciphertext")
    pk = sk[32 + p.rank * p.n:-32]
    _decode_pk(pk, p)
    if not hmac.compare_digest(sk[-32:], hashlib.sha3_256(pk).digest()):
        raise ValueError("Private-key hash mismatch")
    secret = list(struct.unpack("b" * (p.rank * p.n), sk[32:32 + p.rank * p.n]))
    if any(abs(x) > ETA for x in secret):
        raise ValueError("Private coefficient outside CBD2 support")
    s = [secret[i * p.n:(i + 1) * p.n] for i in range(p.rank)]
    rejection = lambda: hashlib.shake_256(sk[:32] + ct).digest(32)
    try:
        polys = unpack(ct, p.rank + 1, p)
    except ValueError:
        return rejection()
    product = _sum_products(s, polys[:p.rank], [0] * p.n, p)
    values = [(x - y) % p.q for x, y in zip(polys[p.rank], product)]
    message = sum((((2 * x + p.q // 2) // p.q) & 1) << i for i, x in enumerate(values)).to_bytes(p.message_bytes, "little")
    derived = _derive(p, message, sk[-32:])
    expected = _encrypt(p, pk, message, derived[32:])
    return derived[:32] if hmac.compare_digest(ct, expected) else rejection()


def keygen(profile_id):
    p = _profile(profile_id)
    return keygen_parameters(p.n, p.rank)


def encapsulate(profile_id, public_key):
    p = _profile(profile_id)
    return encapsulate_parameters(p.n, p.rank, public_key)


def decapsulate(profile_id, private_key, ciphertext):
    p = _profile(profile_id)
    return decapsulate_parameters(p.n, p.rank, private_key, ciphertext)


def artifact_manifest():
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID, "scheme_version": SCHEME_VERSION,
              "adapter_source_sha256": IMPLEMENTATION_SHA256, "python_version": platform.python_version(),
              "runtime": "Python standard-library SHA3/SHAKE and OS urandom", "side_channel_claim": "None; variable-time isolated research generator",
              "construction": "Lossless coefficient-domain dimension-ladder module-LWE research KEM; not ML-KEM"}
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def verify_artifact():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != IMPLEMENTATION_SHA256:
        raise GenerationUnavailable("Module-LWE dimension source changed after registration")
    return artifact_manifest()


def _worker(p):
    verify_artifact()
    pk_size, ct_size = _sizes(p)
    expected = 37 + pk_size + ct_size + 64
    proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--generate", str(p.tier)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    os.set_blocking(proc.stdout.fileno(), False)
    output = bytearray()
    deadline = time.monotonic() + GENERATION_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GenerationLimit("Module-LWE dimension generation exceeded its process deadline")
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), min(65536, expected + 1 - len(output)))
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output) > expected:
                        raise GenerationUnavailable("Module-LWE worker exceeded output bound")
        proc.wait(timeout=max(.001, deadline - time.monotonic()))
        if proc.returncode or len(output) != expected or output[:4] != b"MLD2" or output[4] not in (0, 1):
            raise GenerationUnavailable("Module-LWE worker returned an invalid result")
        if output[5:37].hex() != IMPLEMENTATION_SHA256:
            raise GenerationUnavailable("Module-LWE worker executed a different registered source")
        verify_artifact()
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise GenerationLimit("Module-LWE dimension generation exceeded its process deadline") from None
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def _encoding(p):
    pk, ct = _sizes(p)
    return {"public_key": "MLD2 || uint16le(n) || uint8(rank) || uint32le(q) || rho32 || rank coefficient polynomials",
            "public_key_bytes": pk, "ciphertext": "rank u polynomials || one v polynomial; lossless fixed-width little-endian coefficients",
            "ciphertext_bytes": ct, "shared_secret": "32 bytes, 64 hexadecimal characters", "shared_secret_bytes": 32,
            "coefficient_bytes": (p.q.bit_length() + 7) // 8, "polynomial_coefficients": p.n,
            "native_plaintext_bytes": p.message_bytes, "native_plaintext_bits": p.n}


def _parameter_description(p):
    return {"n": p.n, "rank": p.rank, "q": str(p.q), "eta1": ETA, "eta2": ETA, "compression": "none",
            "message_bytes": p.message_bytes, "message_bits": p.n, "flattened_dimension": p.n * p.rank,
            "generic_message_search_bits": p.n}


def _kdf():
    return {"name": "Research reencryption transform with SHA3/SHAKE",
            "framing": "K||coins=SHA3-512('Crucible-Module-LWE-v2/encap'||n16le||rank8||q32le||native_mu||SHA3-256(pk)); full K32",
            "implicit_rejection": "Failed reencryption or noncanonical ciphertext: SHAKE256(private_z32||ct,32)"}


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict
    target: bytes
    diagnostics: dict

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id):
    p = _profile(profile_id)
    data = _worker(p)
    pk_size, ct_size = _sizes(p)
    pk, ct, target = data[37:37 + pk_size], data[37 + pk_size:37 + pk_size + ct_size], data[-64:-32]
    if bool(data[4]) != hmac.compare_digest(target, data[-32:]):
        raise GenerationUnavailable("Inconsistent worker agreement status")
    public = {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile_id, "profile_label": f"Tier {p.tier}",
              "profile_kind": "research-tier", "instance_size": p.tier, "parameter_bits": p.n * p.rank,
              "parameters": _parameter_description(p), "public_key": pk.hex(), "ciphertext": ct.hex(),
              "encoding": _encoding(p), "kdf": _kdf(), "implementation_sha256": IMPLEMENTATION_SHA256,
              "artifact_sha256": artifact_manifest()["artifact_sha256"]}
    return GeneratedChallenge(public, target, {"correctness_checked": True, "shared_secret_matches": bool(data[4]),
                                               "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256})


def parse_shared_secret(value, profile_id):
    _profile(profile_id)
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Recovered key must contain exactly 64 hexadecimal characters")
    return bytes.fromhex(value)


def get_manifest(profile_id):
    p = _profile(profile_id)
    result = {"id": profile_id, "profile_id": profile_id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
              "name": f"Module-lattice dimension research — Tier {p.tier}", "profile_label": f"Tier {p.tier}",
              "profile_kind": "research-tier", "profile_order": p.tier, "instance_size": p.tier,
              "parameter_bits": p.n * p.rank, "parameter_label": "lattice coordinates", "size_parameter": dict(SIZE_PARAMETER),
              "educational": False, "record_inheritance": False, "shared_secret_bytes": 32, "encoding": _encoding(p),
              "kdf": _kdf(), "parameters": _parameter_description(p),
              "security_claim": "Nonstandard research module-LWE KEM. Full native plaintext space grows with ring degree; native-message enumeration is an explicit attack ceiling. Neither standardized ML-KEM nor an inherited security theorem.",
              "assumption_context": f"Module-LWE over Z_q[x]/(x^{p.n}+1), rank{p.rank}, q{p.q}, full CBD2; separate research reencryption transform and hash assumptions.",
              "distribution": f"Full256-bit key-generation seed and private rejection seed. Uniform full native {p.n}-bit plaintext; no restricted subset of a larger plaintext space. SHAKE expands pseudorandom matrix/noise bits; ideal independent XOF bits induce full CBD2 and unbiased rejection sampling. One fresh honest trial; no correctness-conditioned resampling.",
              "algorithms": {"keygen": "Sample coefficient-domain A and full CBD2 s,e; t=A*s+e.",
                             "encapsulate": "Draw full native n-bit mu, derive K and coins from profile-bound mu||H(pk); form u=A^T*r+e1 and v=t^T*r+e2+Encode(mu) without compression.",
                             "decapsulate": "Decode v-s^T*u; reencrypt the entire native mu and return K only on full equality, otherwise a private SHAKE rejection key."},
              "correctness": {"kind": "perfect", "failure_bound": "0",
                              "argument": f"Per-coefficient noise <=2*rank*n*eta^2+eta={p.noise_bound}; floor(q/4)={p.q//4}>{p.noise_bound}. No compression. All native message bits decode for every allowed noise draw.",
                              "scope": "Same zero honest-disagreement bound at all eleven registered profiles; mathematical correctness conditional on completed generation and faithful implementation.",
                              "assumptions": "Exact modular negacyclic arithmetic, full bounded CBD2, canonical encoding, complete reencryption. Resource aborts are separate and never resampled away.",
                              "evidence_status": "Exact support-bound argument and independently generated Sage quotient-ring conformance vectors; no independent cryptographic audit.",
                              "implementation_checks": "tests/test_lattice_module_v2.py; one retained honest agreement check per issued challenge."},
              "admission": {"status": "local-beta", "official_ranked": False, "reason": "Nonstandard research family; independent scientific review remains separate."},
              "implementation": {"source": "crucible/lattice_module_v2.py", "source_sha256": IMPLEMENTATION_SHA256,
                                 "artifact_sha256": artifact_manifest()["artifact_sha256"], "version": SCHEME_VERSION,
                                 "runtime": "Variable-time Python with hard process deadline and bounded output; no decapsulation API",
                                 "evidence": "docs/lattice-module-v2.txt"},
              "references": [{"title": "FIPS203: source primitives, not this derivative specification", "url": "https://csrc.nist.gov/pubs/fips/203/final"},
                             {"title": "Pinned lattice-estimator", "url": "https://github.com/malb/lattice-estimator/tree/53da5982597709ba0fdf94ea37a84d822310fd84"}]}
    if ESTIMATED_BITS[p.tier - 1] is not None:
        result["initial_security_estimate"] = {"bits": ESTIMATED_BITS[p.tier - 1], "model_id": INITIAL_SECURITY_MODEL["id"]}
    return result


def manifests():
    return [get_manifest(f"module-lwe-v2-tier-{p.tier}") for p in PROFILES]


def source_bytes():
    return Path(__file__).read_bytes()


CORRECTNESS_SPEC = {
    "kind": "perfect", "failure_bound": "0",
    "argument": "Every profile has floor(q/4)>2*rank*n*eta^2+eta, with exact lossless negacyclic arithmetic. Hence honest decapsulation agrees for every full native plaintext and CBD2 noise draw.",
    "scope": "All eleven dimension-research-v2 profiles; resource aborts and implementation faults are separate.",
    "assumptions": "Exact negacyclic arithmetic, full bounded CBD2 support, canonical lossless encoding and complete reencryption.",
    "evidence_status": "Exact deterministic support proof and independent Sage quotient-ring vectors; no independent cryptographic audit.",
    "implementation_checks": "tests/test_lattice_module_v2.py; every issued challenge retains its encapsulated target and performs one honest agreement check.",
}


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--generate":
        raise SystemExit(2)
    profile = f"module-lwe-v2-tier-{int(sys.argv[2])}"
    pk, sk = keygen(profile)
    ct, target = encapsulate(profile, pk)
    got = decapsulate(profile, sk, ct)
    sys.stdout.buffer.write(b"MLD2" + bytes([hmac.compare_digest(target, got)]) + bytes.fromhex(IMPLEMENTATION_SHA256) + pk + ct + target + got)
