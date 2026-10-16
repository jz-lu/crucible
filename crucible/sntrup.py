"""Streamlined NTRU Prime sntrup761, 2020-10-07 wire format, release 1.

Runs a pinned, statically linked libntruprime 20260717 worker. Generation is
one bounded process, never a key-reuse cache or correctness-conditioned loop.
The only registered profile is the published (p,q,w)=(761,4591,286) KEM.
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
import subprocess
import time
from typing import Any

SCHEME_ID = "sntrup"
SCHEME_VERSION = "1"
PROFILE_ID = "sntrup-v1-761"
PROFILE_LABEL = "sntrup761"
PUBLIC_KEY_BYTES, PRIVATE_KEY_BYTES, CIPHERTEXT_BYTES, SHARED_SECRET_BYTES = 1158, 1763, 1039, 32
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
WORKER_SHA256 = "313433b1bae0ff25c7a5b68f452c8307250ac2d3e7f7bbd4f08e7d3fb7bd310a"
WORKER_SOURCE_SHA256 = "6d1db77a88df02a22473e3750d0b79efad9f3145025e56d57d4798d3e71b6cf6"
LIBRARY_VERSION = "20260717"
LIBRARY_ARCHIVE_SHA256 = "768350cd57a9395d80545d2091ed27cd7ccf93c09b41671e4e50e053bcb0bff1"
RANDOMBYTES_ARCHIVE_SHA256 = "fae6fb839096e54ce8abb6dc8ae46ed67b02034474e83cbda088eddd2e584641"
CPUCYCLES_ARCHIVE_SHA256 = "c914c7275952ed00bb188e1170409c83b61abfd0b828cdb2b5a87275be43344e"
GENERATION_TIMEOUT_SECONDS = 15.0
ROOT = Path(__file__).resolve().parent.parent
WORKER = ROOT / ".native/sntrup/crucible-sntrup-worker"
WORKER_SOURCE = ROOT / "scripts/native/sntrup_worker.c"
SIZE_PARAMETER = {
    "name": "Parameter set", "symbol": "", "unit": "profile",
    "meaning": "Published Streamlined NTRU Prime parameter set",
    "mapping": "Profile 1 selects sntrup761 (p=761, q=4591, w=286). This is an ordinal, not security bits; no reduced-parameter ladder is offered.",
    "comparison_scope": "scheme-version",
}


class GenerationResourceLimit(ValueError):
    """A bounded worker timed out; no mathematical agreement claim was made."""


class NativeImplementationError(RuntimeError):
    """An artifact or worker failed; diagnostic text never contains pipe bytes."""


GenerationLimit = GenerationResourceLimit
GenerationUnavailable = NativeImplementationError


def _profile(profile_id):
    if type(profile_id) is not str or profile_id != PROFILE_ID:
        raise ValueError("Unknown Streamlined NTRU Prime parameter profile")


def _bytes(value, size, label):
    if type(value) is not bytes or len(value) != size:
        raise ValueError(f"{label} must be exactly {size} bytes")
    return value


def _verify_artifact():
    try:
        # Read the bounded trusted artifact afresh, not a mutable manifest or a
        # version string. There is no environment-controlled worker override.
        if WORKER.stat().st_size > 16 * 1024 * 1024 or WORKER_SOURCE.stat().st_size > 65536:
            raise NativeImplementationError("Streamlined NTRU Prime artifact size mismatch")
        if (hashlib.sha256(WORKER.read_bytes()).hexdigest() != WORKER_SHA256
                or hashlib.sha256(WORKER_SOURCE.read_bytes()).hexdigest() != WORKER_SOURCE_SHA256):
            raise NativeImplementationError("Streamlined NTRU Prime artifact differs from its reviewed pin")
    except OSError:
        raise NativeImplementationError("Streamlined NTRU Prime worker is unavailable; build the pinned artifact") from None


def artifact_manifest():
    _verify_artifact()
    result = {"format": "crucible-implementation-artifact-v1", "scheme_id": SCHEME_ID,
              "scheme_version": SCHEME_VERSION, "adapter_source_sha256": IMPLEMENTATION_SHA256,
              "specification": "NTRU Prime 2020-10-07; sntrup761",
              "python_runtime": platform.python_version(),
              "worker_sha256": WORKER_SHA256, "worker_source_sha256": WORKER_SOURCE_SHA256,
              "dependencies": [
                  {"name": "libntruprime", "version": LIBRARY_VERSION, "archive_sha256": LIBRARY_ARCHIVE_SHA256},
                  {"name": "librandombytes", "version": "20240318", "archive_sha256": RANDOMBYTES_ARCHIVE_SHA256,
                   "implementation": "kernel; statically linked"},
                  {"name": "libcpucycles", "version": "20260901", "archive_sha256": CPUCYCLES_ARCHIVE_SHA256,
                   "usage": "build and upstream tests only; not linked into worker"}],
              "build": {"platform": "macOS arm64", "compiler": "Apple clang 14.0.3",
                        "upstream_flags": ["--no-valgrind", "--no-trim"],
                        "worker_flags": ["-O2", "-std=c99", "-Wall", "-Wextra"],
                        "external_runtime": ["system libc/libSystem"],
                        "constant_time_tests": "Valgrind/TIMECOP unavailable on this host; not attested"}}
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


_SIZES = {"generate": (0, PUBLIC_KEY_BYTES + CIPHERTEXT_BYTES + 64),
          "keygen": (0, PUBLIC_KEY_BYTES + PRIVATE_KEY_BYTES),
          "encapsulate": (PUBLIC_KEY_BYTES, CIPHERTEXT_BYTES + 32),
          "decapsulate": (PRIVATE_KEY_BYTES + CIPHERTEXT_BYTES, 32)}


def _run(operation, payload=b""):
    if operation not in _SIZES:
        raise ValueError("Unknown native operation")
    size_in, size_out = _SIZES[operation]
    _bytes(payload, size_in, "Native input")
    _verify_artifact()
    # Only fixed executable and operation; secret bytes travel through stdin,
    # never argv/environment, logs, files or user-visible exception messages.
    try:
        proc = subprocess.Popen([str(WORKER), operation], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"}, close_fds=True)
    except OSError:
        raise NativeImplementationError("Streamlined NTRU Prime worker could not start") from None
    deadline = time.monotonic() + GENERATION_TIMEOUT_SECONDS
    chunks = bytearray()
    try:
        os.set_blocking(proc.stdin.fileno(), False)
        os.set_blocking(proc.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            if payload:
                selector.register(proc.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                proc.stdin.close()
            sent = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GenerationResourceLimit("Streamlined NTRU Prime generation allowance was exhausted")
                for key, _ in selector.select(remaining):
                    if key.data == "stdin":
                        sent += os.write(proc.stdin.fileno(), payload[sent:])
                        if sent == len(payload):
                            selector.unregister(proc.stdin)
                            proc.stdin.close()
                    else:
                        data = os.read(proc.stdout.fileno(), size_out + 1 - len(chunks))
                        if not data:
                            selector.unregister(proc.stdout)
                            continue
                        chunks.extend(data)
                        if len(chunks) > size_out:
                            raise NativeImplementationError("Streamlined NTRU Prime worker returned an invalid response")
        try:
            code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise GenerationResourceLimit("Streamlined NTRU Prime generation allowance was exhausted") from None
        if code != 0 or len(chunks) != size_out:
            raise NativeImplementationError("Streamlined NTRU Prime worker returned an invalid response")
        return bytes(chunks)
    except (BrokenPipeError, OSError):
        raise NativeImplementationError("Streamlined NTRU Prime worker communication failed") from None
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
    _profile(profile_id)
    data = _run("keygen")
    return data[:PUBLIC_KEY_BYTES], PrivateKey(data[PUBLIC_KEY_BYTES:])


def encapsulate(profile_id, public_key):
    _profile(profile_id)
    _bytes(public_key, PUBLIC_KEY_BYTES, "Public key")
    data = _run("encapsulate", public_key)
    return data[:CIPHERTEXT_BYTES], data[CIPHERTEXT_BYTES:]


def decapsulate(profile_id, private_key, ciphertext):
    _profile(profile_id)
    if not isinstance(private_key, PrivateKey):
        raise ValueError("Invalid private key")
    _bytes(private_key.value, PRIVATE_KEY_BYTES, "Private key")
    _bytes(ciphertext, CIPHERTEXT_BYTES, "Ciphertext")
    return _run("decapsulate", private_key.value + ciphertext)


def _encoding():
    return {"public_key": "sntrup761 Encode(R/q), NTRU Prime 2020-10-07",
            "public_key_bytes": PUBLIC_KEY_BYTES, "ciphertext": "sntrup761 rounded-polynomial encoding || 32-byte confirmation",
            "ciphertext_bytes": CIPHERTEXT_BYTES, "shared_secret": "32-byte sntrup761 session key, exactly 64 hexadecimal characters",
            "shared_secret_bytes": 32}


def _kdf_spec():
    return {"name": "sntrup761 HashSession, NTRU Prime 2020-10-07",
            "hash": "SHA-512 truncated to its first 32 bytes",
            "framing": "Hash_b(x)=SHA512(one-byte b || x)[0:32]; K=Hash_b(Hash_3(y_bytes) || complete ciphertext)",
            "valid_ciphertext": "b=1, y_bytes=Encode(recovered fixed-weight r)",
            "implicit_rejection": "b=0, y_bytes=the stored private fallback byte string rho",
            "confirmation": "Hash_2(Hash_3(Encode(r)) || Hash_4(public key))"}


def serialize(profile_id, public_key, ciphertext):
    _profile(profile_id)
    _bytes(public_key, PUBLIC_KEY_BYTES, "Public key")
    _bytes(ciphertext, CIPHERTEXT_BYTES, "Ciphertext")
    return {"scheme_id": SCHEME_ID, "version": SCHEME_VERSION, "profile_id": PROFILE_ID,
            "instance_size": 1, "parameter_bits": 761, "profile_kind": "standard-parameter-set",
            "profile_label": PROFILE_LABEL, "profile_order": 1,
            "parameters": {"p": 761, "q": 4591, "w": 286, "polynomial": "x^761-x-1"},
            "public_key": public_key.hex(), "ciphertext": ciphertext.hex(), "encoding": _encoding(),
            "kdf": _kdf_spec(),
            "implementation_sha256": IMPLEMENTATION_SHA256}


@dataclass(frozen=True, repr=False)
class GeneratedChallenge:
    public: dict[str, Any]
    target: bytes
    diagnostics: dict[str, Any]

    def __repr__(self):
        return "GeneratedChallenge(public=<public inputs>, target=<redacted>, diagnostics=<private>)"


def generate(profile_id):
    _profile(profile_id)
    data = _run("generate")
    end_pk, end_ct = PUBLIC_KEY_BYTES, PUBLIC_KEY_BYTES + CIPHERTEXT_BYTES
    public_key, ciphertext = data[:end_pk], data[end_pk:end_ct]
    target, recovered = data[end_ct:end_ct + 32], data[end_ct + 32:]
    return GeneratedChallenge(serialize(profile_id, public_key, ciphertext), target,
        {"correctness_checked": True, "shared_secret_matches": hmac.compare_digest(target, recovered),
         "honest_trials": 1, "implementation_sha256": IMPLEMENTATION_SHA256})


def get_manifest(profile_id):
    _profile(profile_id)
    return {"id": PROFILE_ID, "profile_id": PROFILE_ID, "scheme_id": SCHEME_ID, "version": SCHEME_VERSION,
            "name": PROFILE_LABEL, "profile_label": PROFILE_LABEL, "profile_kind": "standard-parameter-set",
            "profile_order": 1, "instance_size": 1, "parameter_bits": 761, "parameter_label": "polynomial degree",
            "size_parameter": dict(SIZE_PARAMETER), "shared_secret_bytes": 32, "encoding": _encoding(),
            "kdf": _kdf_spec(),
            "parameters": {"p": 761, "q": 4591, "w": 286, "polynomial": "x^761-x-1"}, "educational": False,
            "security_claim": "Published sntrup761 KEM, distinct from NTRU LPRime and SSH hybrid combiners. The passive hourly challenge is not an IND-CCA experiment or a security-level measurement. Post-quantum security remains an assumption, not a measured guarantee.",
            "assumption_context": "Quotient-NTRU problem in the specified non-cyclotomic ring and distribution, with the specified confirmation/implicit-rejection transform and hash assumptions.",
            "distribution": "Fresh libntruprime key generation and encapsulation using OS-backed librandombytes-kernel. Specified small/short polynomial samplers; keygen rejects noninvertible g as required by the specification. No secret reuse across instances and no correctness-conditioned retries. A 15-second worker deadline yields no instance on exhaustion.",
            "algorithms": {"keygen": "NTRU Prime 2020-10-07 sections 2.3.2, 2.3.6 and sntrup761 encodings; p=761,q=4591,w=286.",
                "encapsulate": "Sample fixed-weight r; encode rounded h*r and HashConfirm; derive the specified 32-byte HashSession output.",
                "decapsulate": "Recover candidate r using private f and inverse g; reencrypt and compare; derive HashSession(1,r,C) if valid, otherwise the specified deterministic implicit-rejection key."},
            "correctness": {"kind": "perfect", "failure_bound": "0",
                "argument": "Spec section 2.3.1 and Theorem4: prime p,q, 2p>=3w, q>=16w+1 and irreducible x^p-x-1 modulo q bound core coefficients so honest inversion is exact. The KEM's matching encoding/confirmation/hash layer then agrees.",
                "scope": "Only published sntrup761 with its prescribed samplers and successful key generation. Worker resource aborts and invalid ciphertext implicit rejection are separate events.",
                "assumptions": "Faithful arithmetic, sampling, encodings and native implementation of the published sntrup761 profile. This is not a confidentiality proof or a claim about arbitrary reduced parameters.",
                "evidence_status": "Original submission KAT decapsulation, upstream conventional checks and local boundaries; no independent implementation audit or complete TIMECOP run claimed.",
                "implementation_checks": "tests/test_sntrup.py and docs/implementation-dhkem-sntrup.txt; each issued instance receives one honest agreement self-check."},
            "admission": {"status": "local-beta", "official_ranked": False,
                "reason": "Published profile with bounded conformance evidence; operator activation is separate from manual scientific admission."},
            "implementation": {"source": "crucible/sntrup.py", "source_sha256": IMPLEMENTATION_SHA256,
                "artifact_sha256": artifact_manifest()["artifact_sha256"],
                "version": SCHEME_VERSION, "runtime": "Pinned libntruprime20260717 static native worker; 15-second hard process deadline",
                "worker_source": "scripts/native/sntrup_worker.c", "worker_sha256": WORKER_SHA256,
                "evidence": "docs/implementation-dhkem-sntrup.txt"},
            "references": [{"title": "NTRU Prime specification 2020-10-07", "url": "https://ntruprime.cr.yp.to/nist/ntruprime-20201007.pdf"},
                {"title": "libntruprime release 20260717", "url": "https://libntruprime.cr.yp.to/download.html"}]}


def manifests():
    return [get_manifest(PROFILE_ID)]
