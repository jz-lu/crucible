"""Published sntrup761 conformance, immutable worker, and failure boundaries."""
import ctypes
import hashlib
import json
from pathlib import Path
import secrets
import sys

import pytest

from crucible import sntrup as kem

VECTORS = json.loads((Path(__file__).parent / "fixtures/sntrup761-kat.json").read_text())["vectors"]


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: "official-KAT-" + v["count"])
def test_original_20201007_known_answer_decapsulation(v):
    actual = kem.decapsulate(kem.PROFILE_ID, kem.PrivateKey(bytes.fromhex(v["sk"])), bytes.fromhex(v["ct"]))
    assert actual.hex() == v["ss"]


def test_full_original_100_vector_file_if_reference_sources_are_available():
    path = kem.ROOT / ".native/sntrup/source/ntruprime-20201007/KAT/kem/sntrup761/kat_kem.rsp"
    if not path.exists():
        pytest.skip("Full original vector archive requires --reference build; bundled representatives run above")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "e720b256c12c50fa4281708342aae2c6a47b01ae83b152f3fd03d7680e376172"
    rows = []
    for block in path.read_text().split("\n\n"):
        row = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "ss" in row:
            rows.append(row)
    assert len(rows) == 100
    for row in rows:
        result = kem.decapsulate(kem.PROFILE_ID, kem.PrivateKey(bytes.fromhex(row["sk"])), bytes.fromhex(row["ct"]))
        assert result.hex() == row["ss"].lower()


def test_one_standard_profile_has_exact_sizes_and_no_security_tier_claim():
    m, = kem.manifests()
    assert m["profile_kind"] == "standard-parameter-set"
    assert m["instance_size"] == m["profile_order"] == 1
    assert m["size_parameter"]["name"] == "Parameter set"
    assert "initial_security_estimate" not in m
    assert m["parameters"] == {"p": 761, "q": 4591, "w": 286, "polynomial": "x^761-x-1"}
    assert m["encoding"]["public_key_bytes"] == 1158
    assert m["encoding"]["ciphertext_bytes"] == 1039
    assert m["shared_secret_bytes"] == 32
    artifact = kem.artifact_manifest()
    assert m["implementation"]["artifact_sha256"] == artifact["artifact_sha256"]
    assert "/Users/" not in json.dumps(artifact)


def test_fresh_generation_checks_agreement_and_leaks_no_secrets():
    first, second = kem.generate(kem.PROFILE_ID), kem.generate(kem.PROFILE_ID)
    assert first.diagnostics["shared_secret_matches"] is True
    assert first.diagnostics["honest_trials"] == 1
    assert first.public["public_key"] != second.public["public_key"]
    assert first.public["ciphertext"] != second.public["ciphertext"]
    assert first.target != second.target
    assert first.target.hex() not in json.dumps(first.public)
    assert first.target.hex() not in repr(first)
    assert not {"sk", "private_key", "rho", "target", "shared_secret"}.intersection(first.public)
    pk, sk = kem.keygen(kem.PROFILE_ID)
    ct, target = kem.encapsulate(kem.PROFILE_ID, pk)
    assert kem.decapsulate(kem.PROFILE_ID, sk, ct) == target
    assert repr(sk) == "PrivateKey(<redacted>)"


def test_invalid_ciphertext_returns_deterministic_implicit_rejection_key():
    v = VECTORS[0]
    sk, ct = kem.PrivateKey(bytes.fromhex(v["sk"])), bytes.fromhex(v["ct"])
    invalid = bytes([ct[0] ^ 1]) + ct[1:]
    one = kem.decapsulate(kem.PROFILE_ID, sk, invalid)
    two = kem.decapsulate(kem.PROFILE_ID, sk, invalid)
    assert one == two and len(one) == 32 and one.hex() != v["ss"]
    # The fallback is independently derivable from the stored private rho:
    # sk=f||ginv||pk||rho||Hash4(pk), with 191-byte short encodings.
    rho = sk.value[2 * 191 + 1158:2 * 191 + 1158 + 191]
    expected = hashlib.sha512(b"\0" + hashlib.sha512(b"\x03" + rho).digest()[:32] + invalid).digest()[:32]
    assert one == expected


@pytest.mark.parametrize("change", ["short-pk", "long-pk", "pk-type", "short-ct", "long-ct", "ct-type", "private-type", "private-length"])
def test_malformed_lengths_fail_before_native_invocation(monkeypatch, change):
    monkeypatch.setattr(kem, "_run", lambda *a: pytest.fail("Malformed input must not reach native code"))
    if change == "short-pk": action = lambda: kem.encapsulate(kem.PROFILE_ID, bytes(1157))
    elif change == "long-pk": action = lambda: kem.encapsulate(kem.PROFILE_ID, bytes(1159))
    elif change == "pk-type": action = lambda: kem.encapsulate(kem.PROFILE_ID, bytearray(1158))
    elif change == "private-type": action = lambda: kem.decapsulate(kem.PROFILE_ID, bytes(1763), bytes(1039))
    elif change == "private-length": action = lambda: kem.decapsulate(kem.PROFILE_ID, kem.PrivateKey(bytes(1762)), bytes(1039))
    else:
        bad = {"short-ct": bytes(1038), "long-ct": bytes(1040), "ct-type": bytearray(1039)}[change]
        action = lambda: kem.decapsulate(kem.PROFILE_ID, kem.PrivateKey(bytes(1763)), bad)
    with pytest.raises(ValueError): action()


@pytest.mark.parametrize("profile", [None, True, [], "sntrup761", "sntrup-v1-762"])
def test_unknown_profile_rejected_before_worker(monkeypatch, profile):
    monkeypatch.setattr(kem, "_run", lambda *a: pytest.fail("Unknown profile must not run"))
    with pytest.raises(ValueError): kem.generate(profile)


def test_disagreement_preserves_original_target_without_retry(monkeypatch):
    calls = []
    def run(*args):
        calls.append(args)
        return bytes(1158 + 1039) + b"t" * 32 + b"d" * 32
    monkeypatch.setattr(kem, "_run", run)
    result = kem.generate(kem.PROFILE_ID)
    assert result.target == b"t" * 32
    assert result.diagnostics["shared_secret_matches"] is False
    assert calls == [("generate",)]


def test_changed_artifact_and_missing_worker_are_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(kem, "WORKER_SHA256", "0" * 64)
    with pytest.raises(kem.GenerationUnavailable, match="reviewed pin"):
        kem.generate(kem.PROFILE_ID)
    monkeypatch.setattr(kem, "WORKER", tmp_path / "missing")
    with pytest.raises(kem.GenerationUnavailable, match="unavailable"):
        kem.generate(kem.PROFILE_ID)


@pytest.mark.parametrize("behavior", ["overflow", "short", "stderr", "sleep"])
def test_worker_output_is_bounded_errors_redacted_and_timeout_distinct(monkeypatch, tmp_path, behavior):
    worker = tmp_path / "fake-worker"
    actions = {"overflow": "os.write(1, b'PRIVATE-KEY' * 10000)",
               "short": "os.write(1, b'PRIVATE-KEY')",
               "stderr": "os.write(2, b'PRIVATE-KEY'); sys.exit(2)",
               "sleep": "time.sleep(20)"}
    worker.write_text(f"#!{sys.executable}\nimport os,sys,time\n" + actions[behavior] + "\n")
    worker.chmod(0o700)
    monkeypatch.setattr(kem, "WORKER", worker)
    monkeypatch.setattr(kem, "_verify_artifact", lambda: None)
    monkeypatch.setattr(kem, "GENERATION_TIMEOUT_SECONDS", 0.2 if behavior == "sleep" else 5.0)
    expected = kem.GenerationLimit if behavior == "sleep" else kem.GenerationUnavailable
    with pytest.raises(expected) as error:
        kem.generate(kem.PROFILE_ID)
    assert "PRIVATE" not in str(error.value)


def test_differential_original_reference_if_built():
    # Explicit optional cross-implementation test. Build helper --reference
    # produces this artifact solely for tests; production never loads it.
    path = kem.ROOT / ".native/sntrup/reference-20201007.dylib"
    if not path.exists():
        pytest.skip("Original 2020 reference comparison requires --reference build")
    lib = ctypes.CDLL(str(path))
    u8 = ctypes.c_ubyte
    for name, n in [("keypair", 2), ("enc", 3), ("dec", 3)]:
        function = getattr(lib, "crypto_kem_sntrup761_ref_" + name)
        function.argtypes, function.restype = [ctypes.c_void_p] * n, ctypes.c_int
    lib.randombytes_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    lib.randombytes_init.restype = None
    for _ in range(20):
        lib.randombytes_init((u8 * 48).from_buffer_copy(secrets.token_bytes(48)), None, 256)
        pk, sk = kem.keygen(kem.PROFILE_ID)
        ct, target = kem.encapsulate(kem.PROFILE_ID, pk)
        out = (u8 * 32)()
        assert lib.crypto_kem_sntrup761_ref_dec(out, ct, sk.value) == 0 and bytes(out) == target
        outct, outss = (u8 * 1039)(), (u8 * 32)()
        assert lib.crypto_kem_sntrup761_ref_enc(outct, outss, pk) == 0
        assert kem.decapsulate(kem.PROFILE_ID, sk, bytes(outct)) == bytes(outss)
        rpk, rsk = (u8 * 1158)(), (u8 * 1763)()
        assert lib.crypto_kem_sntrup761_ref_keypair(rpk, rsk) == 0
        ct, target = kem.encapsulate(kem.PROFILE_ID, bytes(rpk))
        assert lib.crypto_kem_sntrup761_ref_dec(out, ct, bytes(rsk)) == 0 and bytes(out) == target
        invalid = bytes([ct[0] ^ 1]) + ct[1:]
        assert lib.crypto_kem_sntrup761_ref_dec(out, invalid, bytes(rsk)) == 0
        assert kem.decapsulate(kem.PROFILE_ID, kem.PrivateKey(bytes(rsk)), invalid) == bytes(out)
