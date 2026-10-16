"""Research NTRU tier correctness, published endpoint, and isolation boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from crucible import ntru_tiers as kem
from crucible import ntru_reference as ref


def test_fifty_distinct_certified_profiles_and_honest_estimate_scope():
    rows = kem.manifests()
    assert len(rows) == 50
    assert [r["instance_size"] for r in rows] == list(range(1, 51))
    assert len({(r["parameters"]["p"], r["parameters"]["q"], r["parameters"]["w"]) for r in rows}) == 50
    assert rows[-1]["parameters"] == {"p": 761, "q": 4591, "w": 286, "polynomial": "x^761-x-1"}
    assert kem._sizes(kem.PROFILES[-1]) == (1158, 1763, 1039)
    for r in rows:
        assert r["initial_security_estimate"] is None
        assert "not the best known attack" in r["attack_reference"]["meaning"]
        assert r["correctness"]["failure_bound"] == "0"
        assert r["admission"]["official_ranked"] is False
    with pytest.raises(TypeError):
        kem.PROFILES[0]["p"] = 1


def test_sage_rechecks_every_prime_and_irreducibility_if_installed(tmp_path):
    sage = shutil.which("sage")
    if not sage:
        pytest.skip("Sage is required for explicit release certification, not server runtime")
    code = """from sage.all import GF, PolynomialRing, ZZ
import json,sys
rows=json.load(open(sys.argv[1]))['profiles']
for row in rows:
 p,q,w=(row[k] for k in ('p','q','w'))
 assert ZZ(p).is_prime(proof=True) and ZZ(q).is_prime(proof=True)
 assert 0<w<=p and 2*p>=3*w and q>=16*w+1 and q<16384 and p<=761
 assert p%4==1 and q%6==1
 R=PolynomialRing(GF(q),'x'); x=R.gen()
 assert (x**p-x-1).is_irreducible()
print(len(rows))
"""
    env = os.environ | {"DOT_SAGE": str(tmp_path / "sage")}
    result = subprocess.run([sage, "-python", "-c", code, str(kem.PARAMETERS)], env=env,
                            capture_output=True, text=True, timeout=120, check=True)
    assert result.stdout.strip() == "50"


@pytest.mark.parametrize("tier", range(1, 51))
def test_each_profile_independent_arithmetic_agreement_and_key_shapes(tier):
    profile = kem.profile_for_size(tier)
    row = kem.PROFILES[tier - 1]
    p, q, w = (row[k] for k in ("p", "q", "w"))
    pk, sk = kem.keygen(profile)
    ct, key = kem.encapsulate(profile, pk)
    assert kem.decapsulate(profile, sk, ct) == key
    assert ref.decapsulate(p, q, w, sk.value, ct) == key
    n = (p + 3) // 4
    f, ginv = ref.small_decode(sk.value[:n], p), ref.small_decode(sk.value[n:2 * n], p)
    assert sum(x != 0 for x in f) == w and set(f) <= {-1, 0, 1}
    h = [x - (q - 1) // 2 for x in ref.decode(pk, [q] * p)]
    g = [ref.center(3 * x, q) for x in ref.multiply(h, f, q)]
    assert set(g) <= {-1, 0, 1}
    assert ref.multiply(g, ginv, 3) == [1] + [0] * (p - 1)
    assert sk.value[2 * n:2 * n + len(pk)] == pk
    invalid = ct[:-1] + bytes([ct[-1] ^ 1])
    rejected = kem.decapsulate(profile, sk, invalid)
    assert rejected != key and rejected == ref.decapsulate(p, q, w, sk.value, invalid)


def test_published_endpoint_original_known_answers_and_full_archive():
    fixture = Path(__file__).parent / "fixtures/sntrup761-kat.json"
    rows = json.loads(fixture.read_text())["vectors"]
    archive = kem.ROOT / ".native/sntrup/source/ntruprime-20201007/KAT/kem/sntrup761/kat_kem.rsp"
    if archive.exists():
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == "e720b256c12c50fa4281708342aae2c6a47b01ae83b152f3fd03d7680e376172"
        rows = [dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
                for block in archive.read_text().split("\n\n") if "ss = " in block]
        assert len(rows) == 100
    for row in rows:
        out = kem.decapsulate(kem.profile_for_size(50), kem.PrivateKey(bytes.fromhex(row["sk"])), bytes.fromhex(row["ct"]))
        assert out.hex() == row["ss"].lower()


@pytest.mark.parametrize("tier", [1, 2])
def test_public_only_small_tier_attack_recovers_full_encapsulated_secret(tier):
    generated = kem.generate(kem.profile_for_size(tier))
    assert generated.diagnostics["shared_secret_matches"]
    public = json.loads(json.dumps(generated.public))
    assert not {"target", "private_key", "rho", "sk"}.intersection(public)
    assert ref.recover_low_tier(public) == generated.target
    assert generated.target.hex() not in json.dumps(public)
    assert generated.target.hex() not in repr(generated)


def test_encapsulation_and_keys_are_fresh_and_generation_is_one_trial():
    first = kem.generate(kem.profile_for_size(1))
    second = kem.generate(kem.profile_for_size(2))
    assert first.public["profile_id"] != second.public["profile_id"]
    assert first.target != second.target
    for generated in (first, second):
        assert generated.diagnostics["honest_trials"] == 1
        assert generated.diagnostics["shared_secret_matches"]


def test_disagreement_keeps_encaps_target_and_never_retries(monkeypatch):
    row = kem.PROFILES[0]
    pk, _, ct = kem._sizes(row)
    calls = []
    def run(*args):
        calls.append(args)
        return bytes(pk + ct) + b"t" * 32 + b"d" * 32
    monkeypatch.setattr(kem, "_run", run)
    result = kem.generate(kem.profile_for_size(1))
    assert result.target == b"t" * 32
    assert result.diagnostics["shared_secret_matches"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("profile", [None, True, [], {}, "sntrup761", "sntrup-v2-tier-0", "sntrup-v2-tier-51", "sntrup-v2-tier-01"])
def test_unknown_profile_cannot_start_worker(monkeypatch, profile):
    monkeypatch.setattr(kem, "_run", lambda *a: pytest.fail("Unknown profile started worker"))
    with pytest.raises(ValueError):
        kem.generate(profile)


@pytest.mark.parametrize("tier", [True, 0, 51, 1.0, "1", None])
def test_noncanonical_tier_cannot_select_profile(tier):
    with pytest.raises(ValueError):
        kem.profile_for_size(tier)


def test_malformed_inputs_cannot_start_worker(monkeypatch):
    monkeypatch.setattr(kem, "_run", lambda *a: pytest.fail("Malformed input started worker"))
    profile = kem.profile_for_size(1)
    pk, sk, ct = kem._sizes(kem.PROFILES[0])
    for bad in (bytes(pk - 1), bytes(pk + 1), bytearray(pk), "00"):
        with pytest.raises(ValueError):
            kem.encapsulate(profile, bad)
    for bad in (bytes(ct - 1), bytes(ct + 1), bytearray(ct)):
        with pytest.raises(ValueError):
            kem.decapsulate(profile, kem.PrivateKey(bytes(sk)), bad)
    with pytest.raises(ValueError):
        kem.decapsulate(profile, bytes(sk), bytes(ct))


def test_modified_parameter_and_worker_pins_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(kem, "WORKER_SHA256", "0" * 64)
    with pytest.raises(kem.GenerationUnavailable, match="reviewed pin"):
        kem.generate(kem.profile_for_size(1))
    bad = tmp_path / "parameters.json"
    bad.write_text(kem.PARAMETERS.read_text().replace('"p": 5,', '"p": 9,'))
    monkeypatch.setattr(kem, "PARAMETERS", bad)
    with pytest.raises(kem.GenerationUnavailable, match="certificate pin"):
        kem._read_parameters()


@pytest.mark.parametrize("behavior", ["overflow", "short", "stderr", "sleep"])
def test_process_resource_and_output_boundaries_redact_secrets(monkeypatch, tmp_path, behavior):
    worker = tmp_path / "worker"
    actions = {"overflow": "os.write(1,b'SECRET'*100000)", "short": "os.write(1,b'SECRET')",
               "stderr": "os.write(2,b'SECRET');sys.exit(2)", "sleep": "time.sleep(20)"}
    worker.write_text(f"#!{sys.executable}\nimport os,sys,time\n" + actions[behavior] + "\n")
    worker.chmod(0o700)
    monkeypatch.setattr(kem, "WORKER", worker)
    monkeypatch.setattr(kem, "verify_artifact", lambda: None)
    monkeypatch.setattr(kem, "GENERATION_TIMEOUT_SECONDS", 0.2 if behavior == "sleep" else 5)
    expected = kem.GenerationLimit if behavior == "sleep" else kem.GenerationUnavailable
    with pytest.raises(expected) as error:
        kem.generate(kem.profile_for_size(1))
    assert "SECRET" not in str(error.value)


def test_public_attack_refuses_large_parameters_before_enumeration():
    public = {"parameters": {"p": 761, "q": 4591, "w": 286}}
    with pytest.raises(ValueError, match="allowance"):
        ref.recover_low_tier(public)
