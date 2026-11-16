"""Independent small-instance checks for fully accounted recovery baselines."""
import math

import pytest

from crucible import dh_v7, rsa_v4, ec_compressed
from crucible.attack_models import classical
from crucible.attack_models.common import MODEL_ID, mul_mod


def test_every_current_classical_stage_has_an_integer_complete_work_bound():
    for adapter in (dh_v7, rsa_v4, ec_compressed):
        results = [classical.estimate(adapter.SCHEME_ID, adapter.SCHEME_VERSION, p)
                   for p in adapter.manifests()]
        assert len(results) == 50
        for result in results:
            assert result["model_id"] == MODEL_ID
            assert type(result["work_bound"]) is int
            assert result["work_bound"] == sum(result["breakdown"].values())
            assert result["work_bound"] > 0
            assert type(result["memory_bits"]) is int and result["memory_bits"] > 0
            assert result["success_lower_bound"] == "1"
            assert result["assumptions"] == []
            assert result["not_best_known"]
            assert "SHA-256" in result["technical"]
        assert results[-1]["work_bound"] > results[0]["work_bound"]


@pytest.mark.parametrize("which", ["first", "middle", "last"])
def test_dh_recovers_exact_shared_key_and_stays_inside_budget(which):
    profile = dh_v7._profile("dh-v7-tier-1")
    upper = dh_v7.exponent_upper_bound(profile)
    x = {"first": 1, "middle": upper//2, "last": upper}[which]
    r = 19
    pk = dh_v7._encode(profile, pow(profile.g, x, profile.p))
    ct = dh_v7._encode(profile, pow(profile.g, r, profile.p))
    expected = dh_v7._kdf(profile, pk, ct, pow(profile.g, x*r, profile.p))
    recovered, meter = classical.recover_dh(profile.id, pk, ct)
    assert recovered == expected
    bound = classical.estimate("teaching-dh-kem", "7", dh_v7.get_manifest(profile.id))
    assert 0 < meter.work <= bound["work_bound"]
    assert meter.charges["sort"] > 0 and meter.charges["lookup"] > 0


@pytest.mark.parametrize("which", ["first", "middle", "last"])
def test_ec_recovers_exact_shared_key_and_keeps_point_sign(which):
    profile_id = "ec-compressed-v2-tier-1"
    row = ec_compressed._profile(profile_id)
    upper = row["q"] - 1
    x = {"first": 1, "middle": upper//2, "last": upper}[which]
    g = tuple(row["g"])
    pk = ec_compressed._encode(row, ec_compressed._mul(row["p"], row["a"], g, x))
    ct_point = ec_compressed._mul(row["p"], row["a"], g, 17)
    ct = ec_compressed._encode(row, ct_point)
    expected = ec_compressed._kdf(profile_id, row, pk, ct, ec_compressed._mul(row["p"], row["a"], ct_point, x))
    recovered, meter = classical.recover_ec(profile_id, pk, ct)
    assert recovered == expected
    bound = classical.estimate("dhkem-x25519", "curve-compressed-v2", ec_compressed.get_manifest(profile_id))
    assert 0 < meter.work <= bound["work_bound"]
    assert meter.charges["decode"] > 0


@pytest.mark.parametrize("secret", [0, 17, 65519, 65519*65521-1])
def test_rsa_inversion_includes_nonunits_and_has_full_cost(secret):
    profile = rsa_v4._profile("rsa-v4-tier-1")
    p, q = 65519, 65521
    assert rsa_v4._trial_is_prime(p) and rsa_v4._trial_is_prime(q)
    assert math.gcd(65537, (p-1)*(q-1)) == 1
    n = p*q
    pk = rsa_v4._encode(profile, n) + b"\x01\x00\x01"
    ct = rsa_v4._encode(profile, pow(secret, 65537, n))
    expected = rsa_v4._kdf(profile, pk, ct, secret)
    recovered, meter = classical.recover_rsa(profile.id, pk, ct)
    assert recovered == expected
    bound = classical.estimate("rsa-kem", "4", rsa_v4.get_manifest(profile.id))
    assert 0 < meter.work <= bound["work_bound"]
    assert "substantially slower" in bound["human"]


def test_bsgs_covers_every_exponent_and_non_square_interval():
    # 2 has order11 modulo23; range0..7 tests an incomplete giant-step block.
    modulus, generator, upper = 23, 2, 7
    bits = modulus.bit_length()
    group_cost = mul_mod(bits)
    for exponent in range(upper+1):
        meter = classical.Meter()
        def combine(left, right):
            meter.charge("mul", group_cost)
            return left*right % modulus
        found = classical._bsgs(pow(generator, exponent, modulus), generator, 1,
                               combine, lambda x: pow(x, -1, modulus),
                               lambda x,e: pow(x,e,modulus), lambda x:x,
                               upper, bits, meter)
        assert found == exponent
        _, work = classical._table_work(upper, bits, group_cost)
        assert meter.work <= sum(work.values())


def test_wrong_release_and_executable_large_instances_fail_closed():
    with pytest.raises(ValueError):
        classical.estimate("rsa-kem", "3", rsa_v4.get_manifest("rsa-v4-tier-1"))
    with pytest.raises(ValueError, match="small instances"):
        classical.recover_dh("dh-v7-tier-50", b"", b"")
    with pytest.raises(ValueError, match="small instances"):
        classical.recover_ec("ec-compressed-v2-tier-50", b"", b"")
    with pytest.raises(ValueError, match="small instances"):
        classical.recover_rsa("rsa-v4-tier-50", b"", b"")
