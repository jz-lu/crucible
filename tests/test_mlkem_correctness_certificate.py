"""Mathematical regression checks for the explicitly separated evidence models."""
from copy import deepcopy
from fractions import Fraction
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "mlkem_correctness_certificate", ROOT / "scripts/mlkem_correctness_certificate.py")
certificate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(certificate)


def test_fips_compression_support_and_decode_margin():
    assert certificate.compression_error_counts(10) == {-2: 128, -1: 1024, 0: 1024, 1: 1024, 2: 129}
    assert max(map(abs, certificate.compression_error_counts(4))) == 104
    assert certificate.threshold() == 728


def test_conservative_mgf_dominates_every_possible_compression_error():
    for eta in (2, 3):
        secret = certificate.cbd(eta)
        for x in (Fraction(11, 10), Fraction(3, 2), Fraction(2)):
            for e1 in range(-2, 3):
                bound = certificate.mgf(secret, x ** (abs(e1) + 2))
                for rounding_error in range(-2, 3):
                    assert certificate.mgf(secret, x ** (e1 + rounding_error)) <= bound


def test_exact_endpoint_comparison_and_evidence_separation():
    rigorous = certificate.certificate(256, 2, 3, "conservative-compression")
    modeled = certificate.certificate(256, 2, 3, "independent-uniform-u-compression")
    assert rigorous["certified_dyadic_bits"] == 43
    assert not rigorous["meets_target"]
    assert modeled["certified_dyadic_bits"] == 128
    assert modeled["meets_target"]
    assert 128 < modeled["approximate_chernoff_bits"] < 128.1
    assert certificate.certificate(64, 2, 3, "conservative-compression")["meets_target"]
    assert not certificate.certificate(128, 2, 3, "conservative-compression")["meets_target"]


def test_small_configuration_is_zero_by_actual_support():
    assert certificate.certificate(16, 2, 3, "conservative-compression")["failure_bound"] == "0"
    with pytest.raises(ValueError, match="Unreviewed"):
        certificate.certificate(16, 2, 3, "invented-model")


@pytest.fixture(scope="module")
def artifact():
    return json.loads((ROOT / "docs/mlkem-correctness-candidates.json").read_text())


def test_all_saved_certificates_recompute_exactly(artifact):
    certificate.verify(artifact)
    for row in artifact["configurations"]:
        assert row["evidence"][1]["meets_target"]


def test_rejects_duplicated_profiles_even_if_counts_match(artifact):
    changed = deepcopy(artifact)
    changed["configurations"][1] = deepcopy(changed["configurations"][0])
    with pytest.raises(ValueError, match="uniqueness"):
        certificate.verify(changed)


def test_rejects_upgraded_target_or_parameter_tampering(artifact):
    for field, new_value in (("meets_target", True), ("q", 24593), ("certified_dyadic_bits", 128)):
        changed = deepcopy(artifact)
        row = next(row for row in changed["configurations"]
                   if row["candidate_anchor"] == "ML-KEM-512" and row["n"] == 256 and row["rank"] == 2)
        row["evidence"][0][field] = new_value
        with pytest.raises(ValueError, match="Certificate mismatch"):
            certificate.verify(changed)
