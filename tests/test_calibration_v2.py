"""Freeze the initial comparison convention, not a claim of attack resistance."""
import math

import pytest

from crucible import calibration_v2 as calibration


EXPECTED_MODULUS_BITS = (32, 42, 52, 62, 72, 81, 91, 101, 111, 121, 141, 164, 190, 217, 247, 279, 313, 350, 389, 430, 474, 521, 571, 623, 678, 735, 796, 859, 925, 994, 1067, 1142, 1221, 1302, 1387, 1475, 1566, 1661, 1759, 1861, 1966, 2074, 2186, 2302, 2421, 2544, 2670, 2801, 2935, 3072)


def test_tier_targets_are_linear_between_eight_and_the_frozen_anchor():
    assert calibration.target_bits(1) == 8
    assert calibration.target_bits(25) == pytest.approx(66.77551020408163)
    assert calibration.target_bits(50) == 128
    for tier in range(1, 50):
        assert calibration.target_bits(tier+1) - calibration.target_bits(tier) == pytest.approx(120/49)


def test_every_tier_uses_the_smallest_distinct_integer_modulus_meeting_its_target():
    actual = tuple(calibration.modulus_bits_for_tier(tier) for tier in range(1, 51))
    assert actual == EXPECTED_MODULUS_BITS
    assert len(set(actual)) == 50
    for tier, bits in enumerate(actual, 1):
        target = calibration.target_bits(tier)
        assert calibration.estimate_bits(bits) >= target
        if bits > calibration.MIN_MODULUS_BITS:
            assert calibration.estimate_bits(bits - 1) < target
    assert actual[-1] == 3072
    assert calibration.estimate_bits(actual[-1]) == 128


def test_rho_and_normalized_nfs_branches_are_explicit_and_monotone():
    assert calibration.estimate_bits(32) == 8
    assert calibration.estimate_bits(120) == 30
    assert calibration.estimate_bits(128) == pytest.approx(31.0499176, abs=1e-6)
    assert calibration.estimate_bits(1024) == pytest.approx(80.0516144, abs=1e-6)
    estimates = [calibration.estimate_bits(n) for n in range(32, 3073)]
    assert all(math.isfinite(value) for value in estimates)
    assert all(left < right for left, right in zip(estimates, estimates[1:]))


def test_rounded_achieved_reference_is_separate_from_target():
    assert calibration.profile_estimate(25, 678) == {
        "model_id": "classical-baseline-v2", "bits": 66.818913, "target_bits": 66.77551,
    }
    assert calibration.profile_estimate(50, 3072) == {
        "model_id": "classical-baseline-v2", "bits": 128.0, "target_bits": 128.0,
    }
    # A mistaken caller must not obtain its requested target as a claimed result.
    assert calibration.profile_estimate(50, 32)["bits"] == 8
    assert calibration.profile_estimate(50, 32)["target_bits"] == 128


def test_dh_uses_actual_subgroup_order_and_does_not_ignore_a_cheaper_generic_attack():
    assert calibration.profile_estimate(50, 3072, subgroup_order=65537)["bits"] == round(math.log2(65537) / 2, 6)
    assert calibration.profile_estimate(1, 32, subgroup_order=131071)["bits"] > 8
    assert calibration.profile_estimate(50, 3072, subgroup_order=(1 << 1536) + 1)["bits"] == 128
    # There is no primality assertion here: certificates belong to the adapter.
    assert calibration.profile_estimate(1, 32, subgroup_order=4)["bits"] == 1


@pytest.mark.parametrize("value", [0, 51, True, 1.0, "1", None])
def test_invalid_tiers_cannot_silently_select_a_profile(value):
    for function in (calibration.target_bits, calibration.modulus_bits_for_tier):
        with pytest.raises(ValueError, match="Tier must be an integer"):
            function(value)


@pytest.mark.parametrize("value", [31, 3073, True, 32.0, "32", None])
def test_modulus_estimation_stays_in_the_documented_domain(value):
    with pytest.raises(ValueError, match="Modulus bit length must be an integer"):
        calibration.estimate_bits(value)


@pytest.mark.parametrize("value", [0, 1, True, 65537.0, "65537", 1 << 32])
def test_invalid_subgroup_metadata_cannot_be_turned_into_an_estimate(value):
    with pytest.raises(ValueError, match="Subgroup order must be an integer"):
        calibration.profile_estimate(1, 32, subgroup_order=value)


def test_model_provenance_does_not_present_a_security_certificate_or_time_prediction():
    model = calibration.MODEL
    assert model["id"] == "classical-baseline-v2"
    assert model["status"] == "heuristic-reference"
    assert model["anchor"] == {"tier": 50, "target_bits": 128, "modulus_bits": 3072}
    assert "including setup work" in model["scope"]
    limits = " ".join(model["limitations"])
    for phrase in ("not a NIST formula", "ECM", "low-tier", "seconds", "amortized", "IND-CPA", "quantum", "separately versioned"):
        assert phrase in limits
    assert all(reference["url"].startswith("https://") for reference in model["references"])
