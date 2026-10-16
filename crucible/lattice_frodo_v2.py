"""Frodo dimension-frontier research family, version 2.

The salted FrodoKEM-640-SHAKE algorithms and complete entropy are retained;
only the matrix dimension changes. This is not a standardized parameter set.
The reference n=640 remains available in the archived release; this ladder stops
at n=592, the first candidate meeting its restricted 128-bit planning target.
Pure Python is deliberately readable, NOT a constant-time deployment library.
"""
from __future__ import annotations

from bisect import bisect_left
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

SCHEME_ID = "frodokem"
SCHEME_VERSION = "frontier-research-v2"
Q = 32768
NBAR = 8
CDF = (4643, 13363, 20579, 25843, 29227, 31145, 32103, 32525,
       32689, 32745, 32762, 32766, 32767)
# Frozen at release. Calibration is explicit in docs/lattice-parameter-frontier.txt.
DIMENSIONS = (8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120, 128, 136, 144, 152, 160, 168, 176, 184, 192, 200, 208, 216, 224, 232, 240, 248, 256, 264, 272, 280, 288, 296, 304, 312, 320, 328, 336, 344, 352, 360, 368, 376, 384, 392, 400, 408, 416, 424, 432, 440, 448, 456, 464, 472, 480, 488, 496, 504, 512, 520, 528, 536, 544, 552, 560, 568, 576, 584, 592)
ESTIMATED_BITS = (None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, 12.264, 14.892, 16.936, 18.98, 21.024, 23.068, 25.112, 26.864, 28.908, 30.66, 32.704, 34.748, 36.5, 38.544, 40.588, 42.34, 44.384, 46.428, 48.18, 50.224, 52.268, 54.312, 56.064, 58.108, 60.152, 62.196, 64.24, 66.284, 68.328, 70.372, 72.416, 74.46, 76.504, 78.548, 80.592, 82.636, 84.68, 86.724, 88.768, 90.812, 92.856, 95.192, 97.236, 99.28, 101.324, 103.368, 105.704, 107.748, 109.792, 111.836, 114.172, 116.216, 118.26, 120.596, 122.64, 124.684, 127.02, 128)
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
GENERATION_TIMEOUT_SECONDS = 20.0
SIZE_PARAMETER = {"name": "Challenge tier", "symbol": "", "unit": "tier",
    "meaning": "Ordinal index into the frozen Frodo research dimension ladder",
    "mapping": "74 distinct matrix dimensions, with all noise, salt and secret lengths retained. Not standardized security levels.",
    "comparison_scope": "scheme-version"}
INITIAL_SECURITY_MODEL = {"id": "frodo-lwe-frontier-core-svp-v2", "name": "Frodo research classical Core-SVP reference",
    "status": "heuristic-reference", "tier_range": {"min": 1, "max": 74},
    "target_mapping": "All supported n=136..592 in steps of eight with increasing pinned reference estimates; n=8..128 in steps of eight are unestimated warmups, not discarded as dominated merely because their estimates are unknown. n=592 is the first fixed-q/noise candidate meeting the 128-bit planning target.",
    "scope": "Primal uSVP and dual hybrid LWE estimates; classical ADPS16 Core-SVP with GSA, with a separate 128-bit native-message ceiling; the plotted planning reference is capped at 128. These attack cost units are not identical. Not full KEM security or wall time.",
    "estimator_commit": "53da5982597709ba0fdf94ea37a84d822310fd84",
    "limitations": ["Moment-matched Gaussian approximation to finite Frodo CDF; exact variance 129790/16384.",
        "m=n+16 follows the estimator's Frodo convention. Multi-target and transform attacks are not completely modeled.",
        "Small dimensions hit a beta40 floor; bit estimates are omitted for n<=128 profiles.",
        "Only two heuristic attack families, not a lower bound or independently validated security claim."],
    "references": [{"title": "Pinned lattice-estimator", "url": "https://github.com/malb/lattice-estimator/tree/53da5982597709ba0fdf94ea37a84d822310fd84"}]}


class GenerationLimit(ValueError):
    """A bounded computation ended without a completed mathematical trial."""


class GenerationUnavailable(RuntimeError):
    """Implementation or worker failure; never expose private pipe contents."""


def _dimension(n):
    if type(n) is not int or not 8 <= n <= 640 or n % 8:
        raise ValueError("Dimension must be a multiple of eight between 8 and 640")
    return n


def _profile(profile_id):
    if type(profile_id) is not str:
        raise ValueError("Unknown Frodo research profile")
    profiles = {f"frodo-lwe-v2-tier-{i+1}": (i+1, n) for i, n in enumerate(DIMENSIONS)}
    try:
        return profiles[profile_id]
    except KeyError:
        raise ValueError("Unknown Frodo research profile") from None


def _bytes(value, size, label):
    if type(value) is not bytes or len(value) != size:
        raise ValueError(f"{label} must contain exactly {size} bytes")
    return value


def _shake(value, length):
    return hashlib.shake_128(value).digest(length)


def sample_words(data):
    """Exact specified 16-bit sampler: sign in low bit, CDF in upper 15."""
    if type(data) is not bytes or len(data) % 2:
        raise ValueError("Noise input must contain complete 16-bit words")
    return [(-1 if w & 1 else 1) * bisect_left(CDF, w >> 1)
            for (w,) in struct.iter_unpack("<H", data)]


def public_matrix(n, seed):
    _dimension(n)
    _bytes(seed, 16, "Matrix seed")
    return [[w & (Q-1) for (w,) in struct.iter_unpack("<H", _shake(struct.pack("<H", i)+seed, 2*n))]
            for i in range(n)]


def pack(values):
    if len(values) % 8:
        raise ValueError("Packing requires a multiple of eight coefficients")
    out = bytearray()
    accumulator = bits = 0
    for value in values:
        if type(value) is not int or not 0 <= value < Q:
            raise ValueError("Noncanonical coefficient")
        accumulator = (accumulator << 15) | value
        bits += 15
        while bits >= 8:
            bits -= 8
            out.append((accumulator >> bits) & 255)
        accumulator &= (1 << bits)-1
    return bytes(out)


def unpack(data, count):
    _bytes(data, count * 15 // 8, "Packed matrix")
    if count % 8:
        raise ValueError("Packing requires a multiple of eight coefficients")
    out = []
    accumulator = bits = 0
    for value in data:
        accumulator = (accumulator << 8) | value
        bits += 8
        if bits >= 15:
            bits -= 15
            out.append((accumulator >> bits) & (Q-1))
        accumulator &= (1 << bits)-1
    return out


def encode(message):
    _bytes(message, 16, "Message")
    integer = int.from_bytes(message, "little")
    return [((integer >> (2*i)) & 3) << 13 for i in range(64)]


def decode(values):
    if len(values) != 64:
        raise ValueError("Message matrix must have 64 coefficients")
    integer = sum(((((v & (Q-1)) + 4096) >> 13) & 3) << (2*i) for i, v in enumerate(values))
    return integer.to_bytes(16, "little")


@dataclass(frozen=True, repr=False)
class PrivateKey:
    value: bytes

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen_dimension(n, randomness=None):
    n = _dimension(n)
    coins = os.urandom(64) if randomness is None else _bytes(randomness, 64, "Key-generation randomness")
    fallback, seed, z = coins[:16], coins[16:48], coins[48:]
    seed_a = _shake(z, 16)
    noise = sample_words(_shake(b"\x5f"+seed, 4*n*8))
    # Frodo's serialized S is the transpose: eight contiguous length-n rows.
    s, e = noise[:8*n], noise[8*n:]
    columns = [s[i*n:(i+1)*n] for i in range(8)]
    b = [(sum(x*y for x, y in zip(row, column))+e[i*8+j]) & (Q-1)
         for i, row in enumerate(public_matrix(n, seed_a)) for j, column in enumerate(columns)]
    pk = seed_a + pack(b)
    sk = fallback + pk + struct.pack("<"+"H"*len(s), *(x & 65535 for x in s)) + _shake(pk, 16)
    return pk, PrivateKey(sk)


def _encrypt(n, pk, message, salt):
    """Deterministic encapsulation core, reused for complete reencryption."""
    derived = _shake(_shake(pk, 16)+message+salt, 48)
    noise = sample_words(_shake(b"\x96"+derived[:32], 2*(2*n+8)*8))
    sp, ep, epp = noise[:8*n], noise[8*n:16*n], noise[16*n:]
    a = public_matrix(n, pk[:16])
    a_columns = list(zip(*a))
    bp = [(sum(x*y for x, y in zip(sp[i*n:(i+1)*n], column))+ep[i*n+j]) & (Q-1)
          for i in range(8) for j, column in enumerate(a_columns)]
    b = unpack(pk[16:], 8*n)
    b_columns = [b[j::8] for j in range(8)]
    encoded = encode(message)
    c = [(sum(x*y for x, y in zip(sp[i*n:(i+1)*n], column))+epp[i*8+j]+encoded[i*8+j]) & (Q-1)
         for i in range(8) for j, column in enumerate(b_columns)]
    return pack(bp)+pack(c)+salt, derived[32:]


def encapsulate_dimension(n, public_key, randomness=None):
    n = _dimension(n)
    _bytes(public_key, 16+15*n, "Public key")
    coins = os.urandom(48) if randomness is None else _bytes(randomness, 48, "Encapsulation randomness")
    ct, k = _encrypt(n, public_key, coins[:16], coins[16:])
    return ct, _shake(ct+k, 16)


def decapsulate_dimension(n, private_key, ciphertext):
    n = _dimension(n)
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Invalid private key")
    sk = _bytes(private_key.value, 48+31*n, "Private key")
    ct = _bytes(ciphertext, 15*n+152, "Ciphertext")
    pk_end = 32+15*n
    pk = sk[16:pk_end]
    if not hmac.compare_digest(sk[-16:], _shake(pk, 16)):
        raise ValueError("Private key public-key hash is inconsistent")
    s = [w for (w,) in struct.iter_unpack("<h", sk[pk_end:-16])]
    bp, c = unpack(ct[:15*n], 8*n), unpack(ct[15*n:15*n+120], 64)
    columns = [s[j*n:(j+1)*n] for j in range(8)]
    w = [(c[i*8+j]-sum(x*y for x, y in zip(bp[i*n:(i+1)*n], col))) & (Q-1)
         for i in range(8) for j, col in enumerate(columns)]
    reconstructed, k = _encrypt(n, pk, decode(w), ct[-32:])
    # Research Python is not constant-time: no externally supplied decapsulation service.
    return _shake(ct+(k if hmac.compare_digest(ct, reconstructed) else sk[:16]), 16)


def keygen(profile_id):
    return keygen_dimension(_profile(profile_id)[1])


def encapsulate(profile_id, public_key):
    return encapsulate_dimension(_profile(profile_id)[1], public_key)


def decapsulate(profile_id, private_key, ciphertext):
    return decapsulate_dimension(_profile(profile_id)[1], private_key, ciphertext)


def artifact_manifest():
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
        "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
        "construction": "Salted FrodoKEM-640-SHAKE; only n varies; Python research implementation",
        "reference_commit": "a2f9dec8917ccc3464b3378d46b140fa7353320d",
        "python_version": platform.python_version(), "runtime": "Python standard library hashlib SHAKE128; OS urandom",
        "side_channel_claim": "None: variable-time, isolated challenge generator, not a deployment library"}
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def verify_artifact():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != IMPLEMENTATION_SHA256:
        raise GenerationUnavailable("Frodo research source changed after registration")
    return artifact_manifest()


def _worker(n):
    verify_artifact()
    expected = 37+(16+15*n)+(152+15*n)+32
    proc = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--generate", str(n)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin"})
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    os.set_blocking(proc.stdout.fileno(), False)
    output = bytearray()
    deadline = time.monotonic()+GENERATION_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise GenerationLimit("Frodo research generation exceeded its process deadline")
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), min(65536, expected+1-len(output)))
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output) > expected:
                        raise GenerationUnavailable("Frodo research worker output exceeded its bound")
        proc.wait(timeout=max(.001, deadline-time.monotonic()))
        if proc.returncode or len(output) != expected or output[:4] != b"FLF2" or output[4] not in (0, 1):
            raise GenerationUnavailable("Frodo research worker returned an invalid result")
        if output[5:37].hex() != IMPLEMENTATION_SHA256:
            raise GenerationUnavailable("Frodo worker executed a different registered source")
        verify_artifact()
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise GenerationLimit("Frodo research generation exceeded its process deadline") from None
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()


def _encoding(n):
    return {"public_key": "16-byte seed_A || MSB-first packed 15-bit n-by-8 matrix",
        "public_key_bytes": 16+15*n, "ciphertext": "packed 15-bit 8-by-n Bprime || packed 8-by-8 C || 32-byte salt",
        "ciphertext_bytes": 152+15*n, "shared_secret": "16 bytes, 32 hexadecimal characters", "shared_secret_bytes": 16}


def _kdf():
    return {"name": "Salted Frodo SHAKE128", "hash": "SHAKE128",
        "framing": "pkh=SHAKE128(pk,16); seedSE||k=SHAKE128(pkh||mu||salt,48); K=SHAKE128(ct||k,16)",
        "implicit_rejection": "Failed reencryption: K=SHAKE128(ct||private_fallback_s,16)"}


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict
    target: bytes
    diagnostics: dict

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id):
    tier, n = _profile(profile_id)
    output = _worker(n)
    pk_end, ct_end = 37+16+15*n, 37+16+15*n+152+15*n
    pk, ct, target = output[37:pk_end], output[pk_end:ct_end], output[ct_end:ct_end+16]
    if bool(output[4]) != hmac.compare_digest(target, output[ct_end+16:]):
        raise GenerationUnavailable("Frodo worker agreement status is inconsistent")
    public = {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile_id,
        "profile_kind": "research-tier", "profile_label": f"Tier {tier}", "instance_size": tier,
        "parameter_bits": n, "parameters": {"n": n, "nbar": 8, "q": Q, "extracted_bits": 2,
            "noise": "Frodo640 CDF", "salt_bytes": 32, "seed_SE_bytes": 32},
        "public_key": pk.hex(), "ciphertext": ct.hex(), "encoding": _encoding(n), "kdf": _kdf(),
        "implementation_sha256": IMPLEMENTATION_SHA256, "artifact_sha256": artifact_manifest()["artifact_sha256"]}
    return GeneratedChallenge(public, target, {"correctness_checked": True, "shared_secret_matches": bool(output[4]),
        "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256})


def parse_shared_secret(value, profile_id):
    _profile(profile_id)
    if type(value) is not str or len(value) != 32 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Recovered key must be exactly 32 hexadecimal characters")
    return bytes.fromhex(value)


def get_manifest(profile_id):
    tier, n = _profile(profile_id)
    result = {"id": profile_id, "profile_id": profile_id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
        "name": f"Frodo matrix research — Tier {tier}", "profile_label": f"Tier {tier}",
        "record_inheritance": False, "profile_kind": "research-tier", "profile_order": tier, "instance_size": tier, "parameter_bits": n,
        "parameter_label": "matrix dimension", "size_parameter": dict(SIZE_PARAMETER), "educational": False,
        "shared_secret_bytes": 16, "encoding": _encoding(n), "kdf": _kdf(),
        "parameters": {"n": n, "nbar": 8, "q": Q, "extracted_bits": 2, "CDF": list(CDF),
            "salt_bytes": 32, "seed_SE_bytes": 32, "message_bytes": 16},
        "security_claim": "Bounded fixed-q/noise parameter frontier, not global optimization or a claim that published FrodoKEM-640 is wasteful. Research parameter generalization, not a published FrodoKEM parameter set. Tier numbers are not validated security bits. Matrix dimension changes; seeds, noise law, message and shared-secret lengths retain their full specified distributions.",
        "assumption_context": "Unstructured LWE with the Frodo640 finite noise distribution, plus salted reencryption-transform and SHAKE assumptions.",
        "distribution": "One fresh key generation and encapsulation from OS randomness per trial. No conditioned resampling, reduced seeds or reused secret keys.",
        "algorithms": {"keygen": "A from SHAKE128 row expansion; B=A*S+E modulo 32768, exact Frodo640 CDF samplers.",
            "encapsulate": "Full 128-bit mu and 256-bit salt; derive full 256-bit noise seed, Bprime=Sprime*A+Eprime, C=Sprime*B+Edoubleprime+Encode(mu), then SHAKE128(ct||k,16).",
            "decapsulate": "Decode C-Bprime*S, reconstruct the complete salted ciphertext, compare, and use the prescribed implicit rejection key on mismatch."},
        "correctness": {"kind": "statistical", "failure_bound": "At most 2^-133 for every profile (conservative common bound; per-profile calculation in evidence).",
            "argument": "For each of 64 coordinates, Z=Edoubleprime+sum(Sprime*E-Eprime*S). Exact rational Chernoff bound at r=1071/1024, followed by a 64-coordinate union bound; decoding can fail only if |Z|>=4096. n<=592 is covered by the conservative n=640 certificate; the same 2^-133 envelope is held fixed.",
            "scope": "Honest generation under ideal SHAKE expansion; mathematical agreement, not implementation safety or confidentiality.",
            "assumptions": "Independent finite-CDF noise in the standard ideal-XOF model and faithful arithmetic/encoding. Actual SHAKE is computational, not a literal source of independent infinite randomness.",
            "evidence_status": "Byte-conformant native anchor; exact integer correctness certificate; no independent cryptographic audit.",
            "implementation_checks": "Native salted Frodo640 conformance at anchor, sampler enumeration, rounding boundaries, negative ciphertexts and all-tier round trips."},
        "admission": {"status": "local-beta", "official_ranked": False, "reason": "Experimental nonstandard parameter ladder; separate from published reference profiles and scientific admission."},
        "implementation": {"source": "crucible/lattice_frodo_v2.py", "source_sha256": IMPLEMENTATION_SHA256,
            "artifact_sha256": artifact_manifest()["artifact_sha256"], "version": SCHEME_VERSION,
            "runtime": "Variable-time pure Python in a bounded isolated process; not a deployment library", "evidence": "docs/lattice-parameter-frontier.txt"},
        "references": [{"title": "FrodoKEM current specification and reference code", "url": "https://frodokem.org/"},
            {"title": "Pinned salted Frodo reference", "url": "https://github.com/microsoft/PQCrypto-LWEKE/tree/a2f9dec8917ccc3464b3378d46b140fa7353320d"}]}
    if n == 8:
        result["correctness"]["kind"] = "perfect"
        result["correctness"]["failure_bound"] = "0"
        result["correctness"]["argument"] = "Every error coordinate has absolute value at most 2*n*12^2+12=2316<4096, so all 64 message coordinates decode exactly for every possible noise sample."
    if ESTIMATED_BITS[tier-1] is not None:
        result["initial_security_estimate"] = {"bits": ESTIMATED_BITS[tier-1],
            "model_id": INITIAL_SECURITY_MODEL["id"]}
    return result


def manifests():
    return [get_manifest(f"frodo-lwe-v2-tier-{tier}") for tier in range(1, 75)]


def source_bytes():
    return Path(__file__).read_bytes()


CORRECTNESS_SPEC = get_manifest("frodo-lwe-v2-tier-74")["correctness"]


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--generate":
        raise SystemExit(2)
    n = _dimension(int(sys.argv[2]))
    pk, sk = keygen_dimension(n)
    ct, target = encapsulate_dimension(n, pk)
    recovered = decapsulate_dimension(n, sk, ct)
    sys.stdout.buffer.write(b"FLF2"+bytes([hmac.compare_digest(target, recovered)])+bytes.fromhex(IMPLEMENTATION_SHA256)+pk+ct+target+recovered)
