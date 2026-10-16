from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest
from crucible import lattice_module_v2 as kem

ROOT = Path(__file__).parents[1]


def test_all_independent_sage_full_construction_vectors():
    source = ROOT / "scripts/lattice_module_v2_reference.py"
    fixture = json.loads((ROOT / "tests/fixtures/module-lwe-dimension-v2.json").read_text())
    assert fixture["reference_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert len(fixture["vectors"]) == 11
    for v in fixture["vectors"]:
        n, k = v["n"], v["rank"]
        pk, sk = kem.keygen_parameters(n, k, bytes.fromhex(v["key_randomness"]))
        ct, key = kem.encapsulate_parameters(n, k, pk, bytes.fromhex(v["encapsulation_message"]))
        assert hashlib.sha256(pk).hexdigest() == v["pk_sha256"]
        assert hashlib.sha256(sk.value).hexdigest() == v["sk_sha256"]
        assert hashlib.sha256(ct).hexdigest() == v["ct_sha256"]
        assert key.hex() == v["shared_secret"]
        assert kem.decapsulate_parameters(n, k, sk, ct) == key
        assert v["independent_honest_message_recovered"] is True


@pytest.mark.parametrize("tier", range(1, 12))
def test_each_distinct_profile_issued_with_full_contract(tier):
    profile = f"module-lwe-v2-tier-{tier}"
    m, r = kem.get_manifest(profile), kem.generate(profile)
    assert r.diagnostics["shared_secret_matches"] is True
    assert r.diagnostics["honest_trials"] == 1
    assert r.public["parameters"] == m["parameters"]
    assert len(bytes.fromhex(r.public["public_key"])) == m["encoding"]["public_key_bytes"]
    assert len(bytes.fromhex(r.public["ciphertext"])) == m["encoding"]["ciphertext_bytes"]
    assert len(r.target) == 32 and r.target.hex() not in repr(r) and r.target.hex() not in json.dumps(r.public)
    assert m["record_inheritance"] is False and m["admission"]["official_ranked"] is False
    assert m["correctness"]["failure_bound"] == "0"


def test_complete_native_plaintext_space_and_no_truncated_seed():
    pk, sk = kem.keygen_parameters(8, 1, bytes(range(64)))
    outputs = set()
    for message in range(256):
        ct, ss = kem.encapsulate_parameters(8, 1, pk, bytes([message]))
        assert kem.decapsulate_parameters(8, 1, sk, ct) == ss
        outputs.add(ct)
    assert len(outputs) == 256
    with pytest.raises(ValueError):
        kem.encapsulate_parameters(8, 1, pk, bytes(32))
    with pytest.raises(ValueError):
        kem.keygen_parameters(8, 1, bytes(8))
    changed = bytearray(range(64))
    changed[31] ^= 1
    assert kem.keygen_parameters(8, 1, bytes(changed))[0] != pk
    changed = bytearray(range(64))
    changed[63] ^= 1
    other_pk, other_sk = kem.keygen_parameters(8, 1, bytes(changed))
    assert other_pk == pk and other_sk.value[:32] != sk.value[:32]


def test_public_only_tier1_solver_recovers_real_issued_instance(monkeypatch):
    spec = importlib.util.spec_from_file_location("dimension_public_solver", ROOT / "scripts/lattice_solve_module_v2_tier1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    issued = kem.generate("module-lwe-v2-tier-1")
    # Public solve may use deterministic encapsulation only: generation, private
    # decapsulation and all fresh randomness are forbidden during this check.
    def forbidden(*args, **kwargs):
        raise AssertionError("Public solver attempted private generation or randomness")
    monkeypatch.setattr(kem, "generate", forbidden)
    monkeypatch.setattr(kem, "keygen_parameters", forbidden)
    monkeypatch.setattr(kem, "decapsulate_parameters", forbidden)
    monkeypatch.setattr(kem.os, "urandom", forbidden)
    assert module.solve(json.loads(json.dumps(issued.public))) == issued.target
    with pytest.raises(ValueError):
        module.solve({**issued.public, "profile_id": "module-lwe-v2-tier-2"})


def test_cbd2_complete_distribution_and_profile_domain_separation():
    values = [kem.cbd(bytes([v | (v << 4)]) * 4, 8)[0] for v in range(16)]
    assert Counter(values) == Counter({-2: 1, -1: 4, 0: 6, 1: 4, 2: 1})
    assert kem.noise(bytes(32), 0, kem.PROFILES[0]) != kem.noise(bytes(32), 1, kem.PROFILES[0])
    assert kem.noise(bytes(32), 0, kem.PROFILES[0]) != kem.noise(bytes(32), 0, kem.PROFILES[1])[:8]


def test_uniform_sampler_rejection_and_abort_are_not_modulo_fallback(monkeypatch):
    p = kem.PROFILES[0]
    class Stream:
        def digest(self, length):
            block = p.q.to_bytes(2, "little") + (p.q - 1).to_bytes(2, "little")
            return (block * ((length + 3) // 4))[:length]
    monkeypatch.setattr(kem.hashlib, "shake_128", lambda _data: Stream())
    assert kem.uniform_poly(bytes(32), p, 0, 0) == [p.q - 1] * p.n
    class BadStream:
        def digest(self, length):
            return b"\xff" * length
    monkeypatch.setattr(kem.hashlib, "shake_128", lambda _data: BadStream())
    with pytest.raises(kem.GenerationLimit):
        kem.uniform_poly(bytes(32), p, 0, 0)


@pytest.mark.parametrize("p", kem.PROFILES)
def test_extremal_noise_and_all_decoding_offsets_keep_same_perfect_bound(p):
    a = [kem.ETA] * p.n
    b = [kem.ETA] + [-kem.ETA] * (p.n - 1)
    positive = kem.poly_mul(a, b, p)[0]
    negative = kem.poly_mul(a, [-value for value in b], p)[0]
    error = (p.rank * (positive - negative) + kem.ETA) % p.q
    assert error == p.noise_bound
    assert p.q // 4 > p.noise_bound
    for bit in (0, 1):
        center = bit * ((p.q + 1) // 2)
        for offset in range(-p.noise_bound, p.noise_bound + 1):
            coefficient = (center + offset) % p.q
            assert ((2 * coefficient + p.q // 2) // p.q) & 1 == bit


def test_dimension_sizes_and_calibration_consistency():
    data = json.loads((ROOT / "docs/lattice-module-v2-calibration.json").read_text())
    assert len(kem.PARAMETER_SETS) == len(set(kem.PARAMETER_SETS)) == len(data["profiles"]) == 11
    manifests = kem.manifests()
    for key in ("public_key_bytes", "ciphertext_bytes"):
        sizes = [m["encoding"][key] for m in manifests]
        assert all(a < b for a, b in zip(sizes, sizes[1:]))
    assert all(a.n <= b.n for a, b in zip(kem.PROFILES, kem.PROFILES[1:]))
    for p, row, m in zip(kem.PROFILES, data["profiles"], manifests):
        assert (p.n, p.rank, str(p.q)) == (row["n"], row["rank"], row["q"])
        assert row["noise_bound"] == p.noise_bound and row["decoding_threshold"] == p.q // 4
        assert m["parameters"]["message_bits"] == row["generic_message_search_bits"] == p.n
        if row["bits"] is None:
            assert "initial_security_estimate" not in m
        else:
            assert m["initial_security_estimate"]["bits"] == round(row["bits"], 6)
            assert m["initial_security_estimate"]["bits"] <= p.n


def test_negacyclic_wrap_and_canonical_encodings():
    for p in kem.PROFILES:
        a, b = [0] * p.n, [0] * p.n
        a[-1], b[1] = 2, 3
        expected = [p.q - 6] + [0] * (p.n - 1)
        assert kem.poly_mul(a, b, p) == expected
        assert kem.unpack(kem.pack([expected], p), 1, p) == [expected]
        with pytest.raises(ValueError):
            kem.unpack(p.q.to_bytes(2, "little") + bytes(2 * (p.n - 1)), 1, p)


def test_complete_implicit_rejection_and_invalid_profile_inputs():
    p = kem.PROFILES[-1]
    pk, sk = kem.keygen_parameters(p.n, p.rank)
    ct, ss = kem.encapsulate_parameters(p.n, p.rank, pk)
    for position in (0, len(ct) // 2, len(ct) - 1):
        modified = ct[:position] + bytes([ct[position] ^ 1]) + ct[position + 1:]
        assert kem.decapsulate_parameters(p.n, p.rank, sk, modified) == hashlib.shake_256(sk.value[:32] + modified).digest(32)
    noncanonical = p.q.to_bytes(2, "little") + ct[2:]
    assert kem.decapsulate_parameters(p.n, p.rank, sk, noncanonical) == hashlib.shake_256(sk.value[:32] + noncanonical).digest(32)
    with pytest.raises(ValueError):
        kem.decapsulate_parameters(p.n, p.rank, sk, ct[:-1])
    with pytest.raises(ValueError):
        kem.encapsulate_parameters(256, 2, pk)
    for n, rank in ((True, 1), (8, True), (12, 1), (256, 4)):
        with pytest.raises(ValueError):
            kem.keygen_parameters(n, rank)
    for profile in ("module-lwe-v2-tier-0", "module-lwe-v2-tier-12", "module-lwe-v2-tier-01", "module-lwe-v1-tier-1", True):
        with pytest.raises(ValueError):
            kem.get_manifest(profile)
    assert repr(sk) == "PrivateKey(<redacted>)"


def test_resource_deadline_and_source_identity(monkeypatch):
    monkeypatch.setattr(kem, "IMPLEMENTATION_SHA256", "0" * 64)
    with pytest.raises(kem.GenerationUnavailable):
        kem.generate("module-lwe-v2-tier-1")
    monkeypatch.undo()
    monkeypatch.setattr(kem, "GENERATION_TIMEOUT_SECONDS", .00001)
    start = time.monotonic()
    with pytest.raises(kem.GenerationLimit):
        kem.generate("module-lwe-v2-tier-1")
    assert time.monotonic() - start < 2


def test_worker_cannot_report_old_source_identity_after_spawn_race(tmp_path, monkeypatch):
    copy = tmp_path / "module_candidate.py"
    copy.write_bytes(kem.source_bytes())
    spec = importlib.util.spec_from_file_location("module_candidate_source_test", copy)
    candidate = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, candidate)
    spec.loader.exec_module(candidate)
    real_popen = candidate.subprocess.Popen
    def changed_after_preflight(*args, **kwargs):
        copy.write_text(copy.read_text() + "\n# Source changed after the parent's preflight check.\n")
        return real_popen(*args, **kwargs)
    monkeypatch.setattr(candidate.subprocess, "Popen", changed_after_preflight)
    with pytest.raises(candidate.GenerationUnavailable, match="different registered source"):
        candidate.generate("module-lwe-v2-tier-1")
