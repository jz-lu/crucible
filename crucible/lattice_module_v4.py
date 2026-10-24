"""Six compressed module-LWE research degrees ending at exact ML-KEM-512.

The registry decides activation. This file alone does not admit a proposal.
Reduced degrees use explicit parameter/purpose hash separation; the degree-256
endpoint uses unmodified FIPS 203 algorithms and bytes. Correctness claims are
conditional and distinguish support proofs from a heuristic compression model.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from crucible import module_compressed_v4_core as core

SCHEME_ID = "ml-kem"
SCHEME_VERSION = "compressed-research-v4"
DEGREES = (8, 16, 32, 64, 128, 256)
PROFILES = tuple(core.Parameters(n, 2, 3) for n in DEGREES)
GENERATION_TIMEOUT_SECONDS = 20.0
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_FILES = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
    "crucible/lattice_module_v4.py", "crucible/module_compressed_v4_core.py")}
SIZE_PARAMETER = {
    "name": "Challenge tier", "symbol": "", "unit": "tier",
    "meaning": "Six natural ring degrees, with an ML-KEM-512 endpoint",
    "mapping": "Degrees8,16,32,64,128,256; fixed q3329, rank2, eta1=3, eta2=2, du10,dv4. The degree256 endpoint is the published ML-KEM-512 parameter set. Smaller degrees are separate research configurations with parameter/purpose-separated hashes.",
    "comparison_scope": "scheme-version",
}


class GenerationLimit(ValueError):
    pass


class GenerationIntegrityError(RuntimeError):
    """Worker/source protocol failed; quarantine rather than retry as capacity."""


def _profile(profile_id):
    if type(profile_id) is str:
        for tier, p in enumerate(PROFILES, 1):
            if profile_id == f"module-lwe-v4-tier-{tier}":
                return p
    raise ValueError("Unknown compressed module-LWE profile")


def _tier(p):
    return DEGREES.index(p.n) + 1


def keygen(profile_id):
    p = _profile(profile_id)
    try:
        return core.keygen_internal(p, os.urandom(32), os.urandom(32))
    except core.SamplingLimit:
        raise GenerationLimit("Module matrix sampling resource bound exceeded") from None


def encapsulate(profile_id, public_key):
    p = _profile(profile_id)
    try:
        return core.encapsulate_internal(p, public_key, os.urandom(p.message_bytes))
    except core.SamplingLimit:
        raise GenerationLimit("Module matrix sampling resource bound exceeded") from None


def decapsulate(profile_id, private_key, ciphertext):
    try:
        return core.decapsulate(_profile(profile_id), private_key, ciphertext)
    except core.SamplingLimit:
        raise GenerationLimit("Module matrix sampling resource bound exceeded") from None


def artifact_manifest():
    value = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
             "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
             "files": [{"path": name, "sha256": checksum} for name, checksum in sorted(_FILES.items())],
             "python_version": platform.python_version(),
             "runtime": "Python standard-library integer arithmetic, SHA3/SHAKE and OS randomness",
             "side_channel_claim": "None; variable-time research reference, no decapsulation service",
             "construction": "Reduced-degree ML-KEM research with exact FIPS203 ML-KEM-512 endpoint"}
    value["artifact_sha256"] = hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    return value


def verify_artifact():
    for name, checksum in _FILES.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum:
            raise GenerationIntegrityError("Module implementation closure changed after import")
    return artifact_manifest()


def _worker(p):
    artifact = verify_artifact()
    expected = 69 + p.public_key_bytes + p.ciphertext_bytes + 64
    command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--generate", str(_tier(p))]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, start_new_session=True, close_fds=True)
    output, deadline = bytearray(), time.monotonic() + GENERATION_TIMEOUT_SECONDS
    selector = selectors.DefaultSelector()
    try:
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise GenerationLimit("Module generation exceeded its process deadline")
            for key, _ in selector.select(remaining):
                data = os.read(key.fileobj.fileno(), min(65536, expected+1-len(output)))
                if not data:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(data)
                    if len(output) > expected:
                        raise GenerationIntegrityError("Module worker exceeded its output bound")
        process.wait(timeout=max(.001, deadline-time.monotonic()))
        if process.returncode == 3 and not output:
            raise GenerationLimit("Module worker reported resource exhaustion")
        if process.returncode or len(output) != expected or output[:4] != b"MLC4" or output[4] not in (0,1):
            raise GenerationIntegrityError("Module worker returned an invalid result")
        if output[5:37].hex() != IMPLEMENTATION_SHA256 or output[37:69].hex() != artifact["artifact_sha256"]:
            raise GenerationIntegrityError("Module worker executed a different implementation closure")
        verify_artifact()
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise GenerationLimit("Module generation exceeded its process deadline") from None
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        process.stdout.close()


def _parameters(p):
    return {"n": p.n, "rank": p.rank, "q": str(core.Q), "eta1": p.eta1, "eta2": p.eta2,
            "du": p.du, "dv": p.dv, "compression": "FIPS203 nearest-integer coefficient quantization",
            "message_bits": p.n, "message_bytes": p.message_bytes, "flattened_dimension": p.n*p.rank,
            "generic_message_search_bits": p.n,
            "hash_domains": "FIPS203 unmodified" if p.n == 256 else "Crucible/module-compressed-v4 purpose and parameter prefix"}


def _encoding(p):
    return {"public_key": "rank polynomials in generalized incomplete-NTT coordinates, canonical12-bit LSB-first coefficients, then rho32",
            "public_key_bytes": p.public_key_bytes,
            "ciphertext": "rank u polynomials compressed to10bits/coefficient, then v compressed to4bits/coefficient; LSB-first packing",
            "ciphertext_bytes": p.ciphertext_bytes,
            "shared_secret": "32bytes,64hexadecimal characters", "shared_secret_bytes": 32,
            "native_plaintext_bytes": p.message_bytes, "native_plaintext_bits": p.n,
            "polynomial_coefficients": p.n, "coefficient_bits": 12}


def _kdf(p):
    prefix = "" if p.n == 256 else "Each hash/XOF includes its purpose and exact parameter prefix. "
    return {"name": "ML-KEM SHA3/SHAKE transform" if p.n == 256 else "Domain-separated reduced-degree ML-KEM transform",
            "framing": prefix + "K||r=SHA3-512(m||H(pk)); native m contains nbits. H uses SHA3-256.",
            "implicit_rejection": prefix + "On reencryption mismatch return SHAKE256(z||ciphertext,32); wrong-length ciphertexts are rejected before decapsulation."}


CORRECTNESS_SPEC = {
    "kind": "statistical", "failure_bound": "2^-128",
    "policy_id": "crucible-honest-agreement-128-v1", "failure_bound_exponent": 128,
    "argument": "All six configurations meet the common target in the explicitly stated independent-noise/uniform-u-rounding model. Degrees8/16 have deterministic zero disagreement; degrees32/64 additionally have conservative compression bounds2^-367/2^-193 without the uniform-rounding assumption. Degrees128/256 require the additional heuristic u-rounding model for the stated target.",
    "scope": "Fresh honest key generation and encapsulation, averaged over the declared randomness for each fixed admitted configuration. One disagreement or honest rejection counts as failure. Resource aborts remain separate; no correctness-conditioned retries.",
    "assumptions": "Faithful arithmetic/encoding/transform; ideal independent expanded CBD noise. For degrees128/256, u-compression errors additionally follow the independent uniform-input rounding law, independent of relevant secrets/errors. This is an explicit heuristic model, not a theorem about actual seeded SHA3/SHAKE executions. v-compression uses its deterministic104 bound, without an independence assumption.",
    "evidence_status": "Exact rational Chernoff/union calculation conditional on its named model; independent NIST endpoint conformance vectors and polynomial algebra tests. No FIPS certification or independent end-to-end implementation audit.",
    "implementation_checks": "tests/test_module_compressed_v4_core.py; tests/test_lattice_module_v4.py; tests/test_mlkem_correctness_certificate.py; one retained honest exchange per challenge.",
    "evidence": "docs/mlkem-correctness-candidates.txt; docs/mlkem-correctness-candidates.json; scripts/mlkem_correctness_certificate.py",
}


def _correctness(p):
    claim = deepcopy(CORRECTNESS_SPEC)
    if p.n in (8,16):
        claim.update(kind="perfect", failure_bound="0", failure_bound_exponent=None,
                     argument=f"The deterministic coefficient residual is at most{21*p.n*p.rank+106}<832, including both compression errors; every full-support honest draw decodes.",
                     evidence_class="deterministic-support-proof",
                     assumptions="Faithful exact arithmetic, canonical encodings, bounded CBD3/CBD2 support and complete reencryption; no hash-independence or rounding-independence assumption is needed for agreement.")
    elif p.n in (32,64):
        bits = {32:367,64:193}[p.n]
        claim.update(evidence_class="conditional-analytical-bound", per_profile_failure_bound=f"2^-{bits}",
                     argument=f"The independent-noise model with worst-case dependent u-compression gives an exact rational Chernoff/union bound at most2^-{bits}, stronger than the common2^-128 target.",
                     assumptions="Faithful arithmetic/encodings/transform and ideal independent expanded CBD noise. u-compression is kept dependent and bounded conservatively; v-compression has deterministic bound104. This is not an unconditional theorem for seeded SHA3/SHAKE.")
    else:
        claim.update(evidence_class="heuristic-model-analytical-bound",
                     per_profile_failure_bound={128:"2^-236",256:"2^-128"}[p.n])
    return claim


def get_manifest(profile_id):
    p = _profile(profile_id)
    tier, endpoint = _tier(p), p.n == 256
    return {"id": profile_id, "profile_id": profile_id, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
        "name": "ML-KEM-512" if endpoint else f"Reduced-degree module lattice — degree{p.n}",
        "profile_label": f"Tier {tier}", "profile_kind": "research-tier", "profile_order": tier,
        "instance_size": tier, "parameter_bits": p.n*p.rank, "parameter_label": "lattice coordinates",
        "size_parameter": dict(SIZE_PARAMETER), "educational": False, "record_inheritance": False,
        "shared_secret_bytes": 32, "encoding": _encoding(p), "kdf": _kdf(p), "parameters": _parameters(p),
        "standard_equivalence": "FIPS203 ML-KEM-512; NIST category1; tested byte conformance, not certification" if endpoint else None,
        "security_claim": "ML-KEM-512 published parameter set; no independent security certification." if endpoint else "Reduced-degree module-LWE research construction; no inherited ML-KEM security guarantee, no calibrated attack-cost estimate. Full n-bit native-message enumeration is an explicit generic attack route; it is not a measured security level.",
        "assumption_context": f"Module-LWE over Z_3329[x]/(x^{p.n}+1), rank2, CBD3 key/encryption secrets and key errors, CBD2 encryption errors, compressed ciphertext and the exact transform/hash assumptions. Smaller rings can have additional algebraic structure.",
        "distribution": f"Fresh independent32-byte key-generation and rejection seeds, uniform full native{p.n}-bit encapsulation message. SHA3/SHAKE expansions, unbiased matrix rejection sampling and full CBD distributions. Exactly one honest exchange; no failure-conditioned retries.",
        "algorithms": {"keygen": "G(d||rank) yields rho,sigma. Expand A directly in incomplete-NTT coordinates; sample CBD3 s,e and form t=A*s+e. Encode t||rho; retain s||pk||H(pk)||z.",
                       "encapsulate": "Validate pk length/canonical coefficients; derive K,r=G(m||H(pk)); form u=A^T*y+e1 and v=t^T*y+e2+Encode(m), compress to du10/dv4 and serialize.",
                       "decapsulate": "Validate lengths and private-key hash; decompress u,v, recover full native m from v-s^T*u, reencrypt, and select K or J(z||ciphertext). Reduced degrees use declared parameter/purpose-separated hashes."},
        "correctness": _correctness(p),
        "admission": {"status": "local-beta", "official_ranked": False, "reason": "Research progression; numerical evidence, model acceptance and independent manual admission remain separate."},
        "implementation": {"source": "crucible/lattice_module_v4.py", "source_sha256": IMPLEMENTATION_SHA256,
                           "artifact_sha256": artifact_manifest()["artifact_sha256"], "version": SCHEME_VERSION,
                           "runtime": "Variable-time Python NTT reference; bounded isolated generator; no public decapsulation API",
                           "evidence": "docs/mlkem-correctness-candidates.txt"},
        "references": [{"title": "FIPS203 ML-KEM: exact endpoint specification", "url": "https://csrc.nist.gov/pubs/fips/203/final"},
                       {"title": "Pinned NIST ACVP vectors", "url": "https://github.com/usnistgov/ACVP-Server/tree/v1.1.0.41/gen-val/json-files"}]}


def manifests():
    return [get_manifest(f"module-lwe-v4-tier-{tier}") for tier in range(1,len(PROFILES)+1)]


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
    start, split = 69, 69+p.public_key_bytes
    public_key, ciphertext, target, recovered = data[start:split], data[split:split+p.ciphertext_bytes], data[-64:-32], data[-32:]
    if bool(data[4]) != hmac.compare_digest(target,recovered):
        raise GenerationIntegrityError("Module worker agreement flag is inconsistent")
    public = {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": profile_id,
              "profile_label": f"Tier {_tier(p)}", "profile_kind": "research-tier", "instance_size": _tier(p),
              "parameter_bits": p.n*p.rank, "parameters": _parameters(p), "public_key": public_key.hex(),
              "ciphertext": ciphertext.hex(), "encoding": _encoding(p), "kdf": _kdf(p),
              "implementation_sha256": IMPLEMENTATION_SHA256, "artifact_sha256": artifact_manifest()["artifact_sha256"]}
    return GeneratedChallenge(public,target,{"correctness_checked": True,"shared_secret_matches": bool(data[4]),
        "honest_trials": 1,"implementation_sha256": IMPLEMENTATION_SHA256})


def parse_shared_secret(value, profile_id):
    _profile(profile_id)
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Recovered key must contain exactly64 hexadecimal characters")
    return bytes.fromhex(value)


def source_bytes():
    return Path(__file__).read_bytes()


if __name__ == "__main__":
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_CPU,(22,22))
    if sys.platform != "darwin":
        resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "--generate":
            raise SystemExit(2)
        profile_id = f"module-lwe-v4-tier-{int(sys.argv[2])}"
        verify_artifact()
        pk,sk = keygen(profile_id)
        ct,target = encapsulate(profile_id,pk)
        recovered = decapsulate(profile_id,sk,ct)
        artifact = verify_artifact()
        sys.stdout.buffer.write(b"MLC4"+bytes([hmac.compare_digest(target,recovered)])
            +bytes.fromhex(IMPLEMENTATION_SHA256)+bytes.fromhex(artifact["artifact_sha256"])+pk+ct+target+recovered)
    except (GenerationLimit, MemoryError):
        raise SystemExit(3) from None
    except Exception:
        # Diagnostic text must never include secret objects or tracebacks.
        raise SystemExit(1) from None
