"""Streamlined NTRU Prime research parameter family, release 2 (not registered).

Fifty certified parameter tuples, full uniform ideal-specification samplers,
2020 encodings/hash transform, bounded separate native process. Tiers are an
ordered degree ladder, not a claim of equal increments or proven security.
"""
from dataclasses import dataclass
import hashlib
import hmac
import json
from math import comb, log2
import os
from pathlib import Path
import selectors
import subprocess
import time
from types import MappingProxyType

from .ntru_reference import encoded_length
from . import platform_pins

SCHEME_ID, SCHEME_VERSION = "sntrup", "2"
ROOT = Path(__file__).resolve().parent.parent
PARAMETERS = ROOT / "crucible/data/ntru_tier_profiles.json"
WORKER = ROOT / ".native/ntru-tiers/crucible-ntru-tier-worker"
PARAMETERS_SHA256 = "a9873c40310e54803fb3ba939d3a5d4508e08e0884863d8bb3bf248b88c74e46"
WORKER_SHA256 = "1770bc68b2ec0a295a5b1d47a544e9a33a392428dd93819e594d46eb3c92767b"
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
GENERATION_TIMEOUT_SECONDS = 15.0
FILE_PINS = {
    "crucible/data/ntru_tier_profiles.json": PARAMETERS_SHA256,
    "scripts/native/ntru_tier_worker.c": "948e4440698271c20d0183328bbe26622b0a1ef7bf47ee74e7b3b4f4c3235f5e",
    "scripts/build_ntru_tiers.py": "d4be3dd72facb9a17c62918a9cc529ebcfec9947f8fa54f9df70e244e55b58a7",
    "scripts/ntru_tier_parameters.py": "2b7abb3c7db91657740815969dec97f4ac742bf92e656b001ab9dfa3acc8ec74",
    "crucible/ntru_reference.py": "a18478c044a538221d6255f6211487e39ed872a8eeb713df44ca6d612b41d0ed",
}
SIZE_PARAMETER = {
    "name": "Challenge tier", "symbol": "", "unit": "tier",
    "meaning": "A certified Streamlined NTRU Prime research parameter tuple",
    "mapping": "Tiers 1–50 select distinct increasing polynomial degrees and exact (p,q,w) tuples; Tier 50 uses sntrup761 algebra and encodings. No linear or monotone attack-cost claim is made.",
    "comparison_scope": "scheme-version",
}


class GenerationResourceLimit(ValueError):
    """The process allowance expired; no instance is returned."""


class NativeImplementationError(RuntimeError):
    """An artifact/worker failed; messages never contain secret pipe data."""


GenerationLimit, GenerationUnavailable = GenerationResourceLimit, NativeImplementationError


def _read_parameters():
    raw = PARAMETERS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARAMETERS_SHA256:
        raise NativeImplementationError("NTRU tier parameters differ from their certificate pin")
    return json.loads(raw)["profiles"]


PROFILES = tuple(MappingProxyType(row) for row in _read_parameters())


def profile_for_size(size):
    if type(size) is not int or not 1 <= size <= 50:
        raise ValueError("Challenge tier must be an integer from 1 to 50")
    return f"sntrup-v2-tier-{size}"


def _profile(profile_id):
    if type(profile_id) is not str:
        raise ValueError("Unknown NTRU research tier")
    for row in PROFILES:
        if profile_for_size(row["tier"]) == profile_id:
            return row
    raise ValueError("Unknown NTRU research tier")


def _sizes(row):
    p, q = row["p"], row["q"]
    pk = encoded_length(p, q)
    return pk, 3 * ((p + 3) // 4) + pk + 32, encoded_length(p, (q + 2) // 3) + 32


def verify_artifact():
    try:
        for name, expected in FILE_PINS.items():
            path = ROOT / name
            if path.stat().st_size > 2 * 1024 * 1024 or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise NativeImplementationError("NTRU research source differs from its reviewed pin")
        return platform_pins.select(SCHEME_ID, SCHEME_VERSION,
            files={"worker": (WORKER, 16 * 1024 * 1024)}, legacy={"worker": WORKER_SHA256})
    except platform_pins.Unavailable:
        raise NativeImplementationError("NTRU research worker differs from its reviewed pin or is unavailable") from None
    except OSError:
        raise NativeImplementationError("NTRU research artifact unavailable; run the explicit pinned builder") from None


def artifact_manifest():
    binding = verify_artifact()
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
        "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
        "worker_sha256": binding.pins["worker"], "files": dict(FILE_PINS),
        "upstream": {"name": "NTRU Prime reference", "date": "2020-10-07",
            "archive_sha256": "280e5dad090144446a49cc0e283163b8167a7961f5ccb183817501fcc3b695ce"},
        "native_dependencies": [
            {"name": "librandombytes-kernel", "version": "20240318", "static_sha256": "ce788c274450011db177c035404f3a268e3c2357c2859fe5bc331be1c3b75f88"},
            {"name": "OpenSSL libcrypto", "static_sha256": "4b3f32521ed371b110cdea53670aa2561163163e20cc71ddbaf6530916bf868f"}],
        "build": "Apple clang 14.0.3; C99 -O2; statically linked crypto and randomness; macOS arm64",
        "changes_from_reference": "Runtime public parameters selected only from a certified table; exact uniform samplers replace near-uniform 32-bit mappings; bounded binary pipe wrapper.",
        "security_scope": "No constant-time audit; no confidentiality estimate or independent admission implied."}
    result.setdefault("files", {}).update(binding.source_files())
    result["platform_build"] = binding.identity()
    if binding.additional:
        result["build"] = binding.build()
        result["native_dependencies"] = [
            {"name": name, "static_sha256": digest}
            for name, digest in sorted(binding.build()["dependencies"].items())]
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def _bytes(value, size, label):
    if type(value) is not bytes or len(value) != size:
        raise ValueError(f"{label} must be exactly {size} bytes")


def _run(row, operation, payload=b""):
    pk, sk, ct = _sizes(row)
    sizes = {"generate": (0, pk + ct + 64), "keygen": (0, pk + sk),
             "encapsulate": (pk, ct + 32), "decapsulate": (sk + ct, 32),
             "sample-short": (0, (row["p"] + 3) // 4)}
    if operation not in sizes:
        raise ValueError("Unknown NTRU operation")
    size_in, size_out = sizes[operation]
    _bytes(payload, size_in, "Native input")
    verify_artifact()
    try:
        proc = subprocess.Popen([str(WORKER), str(row["tier"]), operation], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, close_fds=True)
    except OSError:
        raise NativeImplementationError("NTRU research worker could not start") from None
    deadline, chunks = time.monotonic() + GENERATION_TIMEOUT_SECONDS, bytearray()
    try:
        os.set_blocking(proc.stdin.fileno(), False)
        os.set_blocking(proc.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "out")
            if payload:
                selector.register(proc.stdin, selectors.EVENT_WRITE, "in")
            else:
                proc.stdin.close()
            sent = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GenerationResourceLimit("NTRU research generation allowance was exhausted")
                for key, _ in selector.select(remaining):
                    if key.data == "in":
                        sent += os.write(proc.stdin.fileno(), payload[sent:])
                        if sent == len(payload):
                            selector.unregister(proc.stdin)
                            proc.stdin.close()
                    else:
                        data = os.read(proc.stdout.fileno(), size_out + 1 - len(chunks))
                        if not data:
                            selector.unregister(proc.stdout)
                        else:
                            chunks.extend(data)
                            if len(chunks) > size_out:
                                raise NativeImplementationError("NTRU research worker returned an invalid response")
        try:
            code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise GenerationResourceLimit("NTRU research generation allowance was exhausted") from None
        if code != 0 or len(chunks) != size_out:
            raise NativeImplementationError("NTRU research worker returned an invalid response")
        return bytes(chunks)
    except (BrokenPipeError, OSError):
        raise NativeImplementationError("NTRU research worker communication failed") from None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        if not proc.stdin.closed:
            proc.stdin.close()
        proc.stdout.close()


@dataclass(frozen=True, repr=False)
class PrivateKey:
    value: bytes

    def __repr__(self):
        return "PrivateKey(<redacted>)"


def keygen(profile_id):
    row = _profile(profile_id)
    pk, _, _ = _sizes(row)
    result = _run(row, "keygen")
    return result[:pk], PrivateKey(result[pk:])


def encapsulate(profile_id, public_key):
    row = _profile(profile_id)
    pk, _, ct = _sizes(row)
    _bytes(public_key, pk, "Public key")
    result = _run(row, "encapsulate", public_key)
    return result[:ct], result[ct:]


def decapsulate(profile_id, private_key, ciphertext):
    row = _profile(profile_id)
    _, sk, ct = _sizes(row)
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Invalid private key")
    _bytes(private_key.value, sk, "Private key")
    _bytes(ciphertext, ct, "Ciphertext")
    return _run(row, "decapsulate", private_key.value + ciphertext)


def _encoding(row):
    pk, _, ct = _sizes(row)
    return {"public_key": "2020 Streamlined NTRU Prime mixed-radix R/q encoding", "public_key_bytes": pk,
        "ciphertext": "2020 rounded-polynomial encoding || 32-byte HashConfirm", "ciphertext_bytes": ct,
        "shared_secret": "32-byte HashSession output, hexadecimal", "shared_secret_bytes": 32}


def _kdf_spec():
    return {"name": "Streamlined NTRU Prime 2020 HashSession", "hash": "SHA-512 truncated to 32 bytes",
        "framing": "Hash_b(x)=SHA512(one-byte b || x)[0:32]; key=Hash_b(Hash_3(y_bytes)||complete_ciphertext)",
        "valid_ciphertext": "b=1; y_bytes=Small_encode(recovered r)",
        "implicit_rejection": "b=0; y_bytes=stored random fallback rho",
        "confirmation": "Hash_2(Hash_3(Small_encode(r))||Hash_4(public_key))"}


def serialize(profile_id, public_key, ciphertext):
    row = _profile(profile_id)
    pk, _, ct = _sizes(row)
    _bytes(public_key, pk, "Public key")
    _bytes(ciphertext, ct, "Ciphertext")
    return {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile_id,
        "instance_size": row["tier"], "parameter_bits": row["p"],
        "profile_kind": "research-tier", "profile_label": f"Tier {row['tier']}",
        "record_inheritance": False, "scoring_eligible": False,
        "parameters": {k: row[k] for k in ("p", "q", "w")} | {"polynomial": f"x^{row['p']}-x-1"},
        "public_key": public_key.hex(), "ciphertext": ciphertext.hex(), "encoding": _encoding(row),
        "kdf": _kdf_spec(), "implementation_sha256": IMPLEMENTATION_SHA256}


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict
    target: bytes
    diagnostics: dict

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id):
    row = _profile(profile_id)
    pk, _, ct = _sizes(row)
    data = _run(row, "generate")
    target, recovered = data[pk + ct:pk + ct + 32], data[pk + ct + 32:]
    return GeneratedChallenge(serialize(profile_id, data[:pk], data[pk:pk + ct]), target,
        {"correctness_checked": True, "shared_secret_matches": hmac.compare_digest(target, recovered),
         "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256})


def get_manifest(profile_id):
    row = _profile(profile_id)
    p, q, w = (row[k] for k in ("p", "q", "w"))
    return {"id": profile_id, "profile_id": profile_id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
        "name": f"Streamlined NTRU Prime — Tier {row['tier']}", "instance_size": row["tier"],
        "profile_kind": "research-tier", "profile_label": f"Tier {row['tier']}", "parameter_bits": p,
        "parameter_label": "polynomial degree", "size_parameter": dict(SIZE_PARAMETER),
        "parameters": {"p": p, "q": q, "w": w, "polynomial": f"x^{p}-x-1"},
        "shared_secret_bytes": 32, "encoding": _encoding(row), "kdf": _kdf_spec(), "educational": False,
        "record_inheritance": False, "scoring_eligible": False,
        "security_claim": "Research parameter family. No equal or monotone security increments and no 128-bit endpoint claim. Tier 50 has sntrup761 algebra and byte formats, but uses the ideal exact uniform sampler. Recovery is a passive single-encapsulation result, not full IND-CCA security.",
        "assumption_context": "Quotient-NTRU problem for the exact non-cyclotomic ring and uniform small/fixed-weight distributions, plus the published KEM hash assumptions.",
        "initial_security_estimate": None,
        "attack_reference": {"model": "Classical exhaustive encapsulation-input enumeration",
            "candidate_count": str(comb(p, w) * 2**w), "log2_candidate_count": round(log2(comb(p, w)) + w, 4),
            "meaning": "An explicit public attack upper bound, not the best known attack or a security-bit estimate; lattice and hybrid attacks may be much faster."},
        "distribution": "Uniform ternary g conditioned only on required invertibility mod3; uniform weight-w f and r with independent signs and supports. OS-backed randomness with rejection sampling. Fresh secrets per challenge, no seed truncation, no key reuse and no correctness-conditioned retries. Worker resource exhaustion produces no instance.",
        "algorithms": {"keygen": "Sample g invertible mod3, uniform weight-w f; h=g/(3f) mod (q,x^p-x-1); append public key, uniform rho and Hash4(pk) to secret key.",
            "encapsulate": "Sample uniform weight-w r; round h*r, encode and confirm; apply the published HashSession domain 1.",
            "decapsulate": "Recover r by mod-q/mod3 arithmetic, reencrypt and compare; valid ciphertext uses domain1, mismatch uses rho and domain0."},
        "correctness": {"kind": "perfect", "failure_bound": "0",
            "argument": "NTRU Prime 2020 Theorem4: |3fm+gr| coefficientwise <=8w<q/2, so centered lifting loses no information; reducing mod3 and multiplying g^-1 recovers r exactly. Irreducibility guarantees nonzero f is invertible modq. The matching encoding/confirmation layer preserves agreement.",
            "scope": "All 50 pinned tuples, the exact specified samplers and successful generation; process aborts and invalid-ciphertext rejection are separate events.",
            "assumptions": "Correct arithmetic, entropy, encodings and faithful implementation. Each tuple checks prime p,q, irreducibility, 2p>=3w and q>=16w+1 plus implementation-domain restrictions.",
            "evidence_status": "Parameter certificates and local conformance tests; not independent manual scientific admission or a constant-time audit.",
            "implementation_checks": "tests/test_ntru_tiers.py; one independent honest decapsulation per issued instance."},
        "admission": {"status": "local-beta", "official_ranked": False,
            "reason": "Local research availability is separate from independent manual admission and scoring enrollment."},
        "implementation": {**verify_artifact().implementation_fields(), "source": "crucible/ntru_tiers.py", "source_sha256": IMPLEMENTATION_SHA256,
            "artifact_sha256": artifact_manifest()["artifact_sha256"], "version": SCHEME_VERSION,
            "runtime": "Pinned static native process with 15-second wall deadline; reference samplers are not constant-time audited",
            "evidence": "docs/implementation-ntru-tiers.txt"},
        "references": [{"title": "NTRU Prime specification 2020-10-07", "url": "https://ntruprime.cr.yp.to/nist/ntruprime-20201007.pdf"}]}


def manifests():
    return [get_manifest(profile_for_size(i)) for i in range(1, 51)]
