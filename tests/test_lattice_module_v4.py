"""Candidate adapter isolation, provenance and honest-outcome accounting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from crucible import lattice_module_v4 as module


@pytest.mark.parametrize("tier", range(1,7))
def test_real_isolated_roundtrip_and_exact_profile_wire_sizes(tier):
    profile_id = f"module-lwe-v4-tier-{tier}"
    manifest = module.get_manifest(profile_id)
    first, second = module.generate(profile_id), module.generate(profile_id)
    assert first.diagnostics["honest_trials"] == 1
    assert first.diagnostics["shared_secret_matches"]
    assert len(bytes.fromhex(first.public["public_key"])) == manifest["encoding"]["public_key_bytes"]
    assert len(bytes.fromhex(first.public["ciphertext"])) == manifest["encoding"]["ciphertext_bytes"]
    assert len(first.target) == 32
    assert first.public["public_key"] != second.public["public_key"]
    assert first.public["ciphertext"] != second.public["ciphertext"]
    assert first.target.hex() not in repr(first)
    assert "target" not in first.public and "private_key" not in first.public


def test_fixed_parameters_native_capacity_and_standard_endpoint():
    rows = module.manifests()
    assert [row["parameters"]["n"] for row in rows] == [8,16,32,64,128,256]
    for row in rows:
        p = row["parameters"]
        assert (p["q"],p["rank"],p["eta1"],p["eta2"],p["du"],p["dv"]) == ("3329",2,3,2,10,4)
        assert p["message_bits"] == p["n"]
        assert "initial_security_estimate" not in row
    assert (rows[-1]["encoding"]["public_key_bytes"],rows[-1]["encoding"]["ciphertext_bytes"]) == (800,768)
    assert rows[-1]["standard_equivalence"].startswith("FIPS203 ML-KEM-512")
    assert all(row["standard_equivalence"] is None for row in rows[:-1])


def test_certificates_cover_exact_configurations_and_do_not_hide_model_gap():
    document = json.loads((module.ROOT / "docs/mlkem-correctness-candidates.json").read_text())
    for row in module.manifests():
        n = row["parameters"]["n"]
        certificate = next(c for c in document["configurations"]
                           if c["candidate_anchor"] == "ML-KEM-512" and c["n"] == n and c["rank"] == 2)
        modeled = next(e for e in certificate["evidence"] if e["mode"] == "independent-uniform-u-compression")
        conservative = next(e for e in certificate["evidence"] if e["mode"] == "conservative-compression")
        assert modeled["meets_target"]
        claim = row["correctness"]
        if n <= 16:
            assert claim["kind"] == "perfect" and conservative["failure_bound"] == "0"
        elif n <= 64:
            assert claim["per_profile_failure_bound"] == conservative["failure_bound"]
            assert claim["evidence_class"] == "conditional-analytical-bound"
        else:
            assert claim["per_profile_failure_bound"] == modeled["failure_bound"]
            assert claim["evidence_class"] == "heuristic-model-analytical-bound"
            assert "heuristic" in claim["assumptions"] and not conservative["meets_target"]
    assert "uniform-u-rounding" in module.CORRECTNESS_SPEC["argument"]


def test_hash_closure_covers_core_and_adapter_and_is_not_mutable(tmp_path,monkeypatch):
    artifact = module.verify_artifact()
    assert {entry["path"] for entry in artifact["files"]} == set(module._FILES)
    claimed = artifact.pop("artifact_sha256")
    assert claimed == hashlib.sha256(json.dumps(artifact,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
    clone = deepcopy(artifact)
    clone["files"][0]["sha256"] = "0"*64
    assert module.artifact_manifest()["files"] != clone["files"]
    for name in module._FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes((module.ROOT / name).read_bytes())
    monkeypatch.setattr(module,"ROOT",tmp_path)
    module.verify_artifact()
    (tmp_path / "crucible/module_compressed_v4_core.py").write_text("changed")
    with pytest.raises(module.GenerationIntegrityError,match="closure"):
        module.verify_artifact()


def payload(p, *, agrees=True):
    return (b"MLC4"+bytes([agrees])+bytes.fromhex(module.IMPLEMENTATION_SHA256)
            +bytes.fromhex(module.artifact_manifest()["artifact_sha256"])
            +b"p"*p.public_key_bytes+b"c"*p.ciphertext_bytes+b"a"*32+(b"a" if agrees else b"b")*32)


def test_disagreement_is_retained_once_without_retry(monkeypatch):
    calls = []
    def fake_worker(p):
        calls.append(p)
        return payload(p,agrees=False)
    monkeypatch.setattr(module,"_worker",fake_worker)
    result = module.generate("module-lwe-v4-tier-1")
    assert len(calls) == 1 and result.diagnostics["honest_trials"] == 1
    assert result.diagnostics["shared_secret_matches"] is False
    assert result.target == b"a"*32


def replace_child(monkeypatch, code):
    original = subprocess.Popen
    def child(command, **kwargs):
        return original([sys.executable,"-I","-B","-c",code],**kwargs)
    monkeypatch.setattr(module.subprocess,"Popen",child)


@pytest.mark.parametrize("mutation", ["magic","flag","source","artifact","short","long"])
def test_worker_rejects_protocol_or_provenance_corruption(monkeypatch,mutation):
    p = module.PROFILES[0]
    data = bytearray(payload(p))
    if mutation == "magic": data[0] ^= 1
    elif mutation == "flag": data[4] = 2
    elif mutation == "source": data[5] ^= 1
    elif mutation == "artifact": data[37] ^= 1
    elif mutation == "short": data = data[:-1]
    elif mutation == "long": data += b"x"
    replace_child(monkeypatch,f"import sys;sys.stdout.buffer.write(bytes.fromhex('{data.hex()}'))")
    with pytest.raises(module.GenerationIntegrityError):
        module._worker(p)


def test_worker_timeout_and_resource_abort_remain_availability_failures(monkeypatch):
    replace_child(monkeypatch,"import time;time.sleep(20)")
    monkeypatch.setattr(module,"GENERATION_TIMEOUT_SECONDS",.03)
    with pytest.raises(module.GenerationLimit):
        module._worker(module.PROFILES[0])


def test_worker_declared_resource_abort_is_not_an_implementation_incident(monkeypatch):
    replace_child(monkeypatch,"raise SystemExit(3)")
    with pytest.raises(module.GenerationLimit):
        module._worker(module.PROFILES[0])


@pytest.mark.parametrize("value", [None,True,"module-lwe-v4-tier-01","module-lwe-v4-tier-7"])
def test_exact_identifiers_only(value):
    with pytest.raises(ValueError):
        module.get_manifest(value)
