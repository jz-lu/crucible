"""Exact curve proofs, negative inputs and public-only shared-secret recovery."""
import copy
import hashlib
import json
import math
import pytest
from crucible import ec_research as ec


@pytest.mark.parametrize("tier", range(1, 51))
def test_every_certified_curve_agrees_and_has_exact_encoding(tier):
    identifier = f"ec-research-v1-tier-{tier}"
    manifest = ec.get_manifest(identifier)
    pk, sk = ec.keygen(identifier)
    ct, key = ec.encapsulate(identifier, pk)
    assert key == ec.decapsulate(identifier, sk, ct)
    assert len(pk) == manifest["encoding"]["public_key_bytes"]
    assert len(ct) == manifest["encoding"]["ciphertext_bytes"]
    row = ec._profile(identifier)
    extreme = ec.PrivateKey(identifier, row["q"]-1)
    extreme_pk = ec._encode(row, ec._mul(row["p"], row["a"], tuple(row["g"]), extreme.scalar))
    ct, key = ec.encapsulate(identifier, extreme_pk)
    assert key == ec.decapsulate(identifier, extreme, ct)


def test_first_tier_is_solvable_from_public_inputs_only():
    challenge = ec.generate("ec-research-v1-tier-1")
    public = challenge.public
    row = public["parameters"]
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    point, base = ec._decode(row, pk), tuple(row["g"])
    stride = math.isqrt(row["q"])+1
    table, current = {}, None
    for value in range(stride):
        table[current] = value
        current = ec._add(row["p"], row["a"], current, base)
    giant = ec._mul(row["p"], row["a"], base, stride)
    giant = (giant[0], -giant[1] % row["p"])
    for block in range(stride+1):
        if point in table:
            scalar = (block*stride+table[point]) % row["q"]
            break
        point = ec._add(row["p"], row["a"], point, giant)
    else:
        pytest.fail("Public discrete logarithm not found")
    shared = ec._mul(row["p"], row["a"], ec._decode(row, ct), scalar)
    # Independent transcript framing, using only the downloadable contract.
    params = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    pieces = [public["profile_id"].encode(), params, pk, ct, ec._encode(row, shared)]
    recovered = hashlib.sha256(bytes.fromhex(public["kdf"]["domain_hex"]) + b"".join(len(p).to_bytes(4, "big")+p for p in pieces)).digest()
    assert recovered == challenge.target
    assert recovered.hex() not in json.dumps(public)


def test_frozen_curve_seeds_reproduce_the_declared_field_search_and_coefficients():
    # This checks the seed-to-candidate binding independently. Exact accepted
    # group orders are certified separately; earlier rejected SEA candidates
    # can be reproduced with the published offline generator.
    for row in ec._parameters():
        tier = row["tier"]
        seed = f"crucible/ec-research-v1/setup/tier-{tier}".encode()
        bits = math.ceil(16+240*(tier-1)/49)+1
        start = (1 << (bits-1)) + int.from_bytes(hashlib.shake_256(seed+b"/field").digest(40), "big") % (1 << (bits-2))
        start = start-start % 4+3
        assert row["p"] >= start and (row["p"]-start) % 4 == 0
        for candidate in range(start, row["p"], 4):
            # A Fermat witness conclusively proves this preceding candidate
            # composite; no probable-prime result is accepted as a proof.
            assert any(math.gcd(base, candidate) > 1 or pow(base, candidate-1, candidate) != 1 for base in (2,3,5,7,11,13,17))
        expected = int.from_bytes(hashlib.shake_256(seed+b"/curve/"+str(row["attempt"]).encode()).digest(40), "big") % row["p"]
        assert row["b"] == expected


def test_invalid_points_keys_and_profile_aliases_fail():
    identifier = "ec-research-v1-tier-1"
    row = ec._profile(identifier)
    pk, sk = ec.keygen(identifier)
    for invalid in (b"", bytes(len(pk)), b"\x04"+bytes(len(pk)-1), b"\x04"+b"\xff"*(len(pk)-1), pk+b"\x00"):
        with pytest.raises(ValueError):
            ec.encapsulate(identifier, invalid)
        with pytest.raises(ValueError):
            ec.decapsulate(identifier, sk, invalid)
    for scalar in (0, row["q"], -1, True):
        with pytest.raises(ValueError):
            ec.decapsulate(identifier, ec.PrivateKey(identifier, scalar), pk)
    with pytest.raises(ValueError):
        ec.get_manifest("ec-research-v1-tier-01")


def test_corrupted_prime_certificates_are_not_accepted():
    row = ec._parameters()[-1]
    bad = copy.deepcopy(row["p_certificate"])
    bad["ecpp"][0][0] += 2
    with pytest.raises(ValueError):
        ec.verify_prime(bad)
    with pytest.raises(ValueError):
        ec.verify_prime({"lucas": {"n": 91}})
    bad = copy.deepcopy(row["q_certificate"])
    bad["ecpp"][0][4][0] += 1
    with pytest.raises(ValueError):
        ec.verify_prime(bad)


def test_artifact_manifest_binds_public_parameter_certificates(monkeypatch, tmp_path):
    closure = ec.artifact_manifest()
    assert closure["artifact_sha256"] == hashlib.sha256(json.dumps({k: v for k, v in closure.items() if k != "artifact_sha256"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    bad = tmp_path / "parameters.json"
    bad.write_text("[]")
    monkeypatch.setattr(ec, "PARAMETERS", bad)
    with pytest.raises(RuntimeError):
        ec.verify_artifact()
