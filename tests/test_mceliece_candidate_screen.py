"""Offline scientific-screen boundaries; no native runtime/admission changes."""
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

import pytest

from scripts import mceliece_candidate_screen as screen
from scripts import verify_tii254_key as verifier


ROOT = Path(__file__).resolve().parents[1]


def test_enumeration_is_complete_in_declared_finite_domain():
    rows = list(screen.candidates())
    expected = {(m, n, t) for m in range(7, 13) for t in range(2, 65)
                for n in range(8, min(3488, 2**m) + 1, 8) if m*t < n}
    assert {(c.m, c.n, c.t) for c in rows} == expected
    assert len(rows) == len(expected) == 45497
    assert rows == sorted(set(rows))
    assert screen.Candidate(12, 3488, 64) in rows
    assert screen.Candidate(8, 223, 12) not in rows  # Alignment is a screen restriction.


@pytest.mark.parametrize("args", [(True, 8, 2), (7, 128, 1), (7, 129, 2), (7, 14, 2), (0, 8, 2)])
def test_invalid_prerequisites_are_rejected(args):
    with pytest.raises(ValueError):
        screen.Candidate(*args)


def test_public_rows_are_padded_individually_and_anchor_matches_specification():
    c = screen.Candidate(9, 440, 11)
    assert c.sizes() == {"public_key_bytes": 4257, "ciphertext_bytes": 13,
                         "public_row_padding_bits": 3, "ciphertext_padding_bits": 5}
    assert c.sizes()["public_key_bytes"] != (c.r * c.k + 7) // 8
    anchor = screen.Candidate(12, 3488, 64).sizes()
    assert (anchor["public_key_bytes"], anchor["ciphertext_bytes"]) == (261120, 96)


def test_binary_field_polynomials_by_independent_trial_division():
    def remainder(a, b):
        # Polynomial coefficient long division, independently represented as sets.
        a = {i for i in range(a.bit_length()) if a >> i & 1}
        b = {i for i in range(b.bit_length()) if b >> i & 1}
        while a and max(a) >= max(b):
            shift = max(a) - max(b)
            a ^= {i + shift for i in b}
        return a
    for m in range(2, 13):
        p = screen.first_field_polynomial(m)
        assert p.bit_length() == m + 1
        for degree in range(1, m // 2 + 1):
            for divisor in range(2**degree, 2**(degree + 1)):
                assert remainder(p, divisor)
    assert not screen.irreducible_binary(0b10101)  # (x^2+x+1)^2
    assert screen.first_field_polynomial(12) == 0x1009


def test_control_ray_uses_declared_rational_rule_without_fixed_stage_target():
    ray = list(screen.control_ray())
    assert [(c.m, c.n, c.t) for c in ray] == [
        (7, 112, 4), (8, 216, 6), (9, 440, 11),
        (10, 872, 19), (11, 1744, 35), (12, 3488, 64)]
    for c in ray:
        assert abs(c.n / 2**c.m - 3488/4096) <= 4 / 2**c.m
        assert abs(c.r - 768*c.n/3488) <= c.m / 2


def test_fixed_weight_model_has_correct_full_support_boundary():
    c = screen.Candidate(7, 128, 4)
    actual = screen.fixed_weight_work(c)
    assert actual["batch"] == 4
    assert actual["success_probability"] == pytest.approx(127*126*125 / 128**3)
    assert screen.fixed_weight_work(screen.Candidate(12, 840, 64))["expected_batches"] > 11
    assert 1.7 < screen.fixed_weight_work(screen.Candidate(12, 3488, 64))["expected_batches"] < 1.9


CONTEXT = {"game": "one syndrome", "unit": "bit operations", "memory_access": "constant"}


def covered_row(candidate, generic=40, structural=30):
    row = candidate.row()
    row["attack_evidence"] = {
        kind: {"coverage": "reviewed-for-domain", "log2_work": cost,
               "context": deepcopy(CONTEXT), "source_revision": "test-fixture-not-real-evidence",
               "review_reference": "test-fixture-not-an-admission"}
        for kind, cost in zip(screen.REQUIRED_COVERAGE, (generic, structural))}
    return row


def test_unknown_structural_model_cannot_win_comparison():
    row = covered_row(screen.Candidate(7, 112, 4))
    del row["attack_evidence"]["goppa_structural_recovery"]
    result = screen.modeled_frontier([row], CONTEXT)
    assert result["conditional_modeled_frontier"] == []
    assert "goppa_structural_recovery: coverage unresolved" in result["blocked"][0]["reasons"]
    assert not result["live_admission"]


@pytest.mark.parametrize("context", [None, {}, "unspecified", {"game": "one syndrome"},
                                    {**CONTEXT, "unit": " "}, {**CONTEXT, "memory_access": None}])
def test_missing_accounting_cannot_be_self_certified_by_matching_evidence(context):
    row = covered_row(screen.Candidate(7, 112, 4))
    for evidence in row["attack_evidence"].values():
        evidence["context"] = deepcopy(context)
    result = screen.modeled_frontier([row], context)
    assert not result["conditional_modeled_frontier"]
    assert any("explicit game" in reason for reason in result["blocked"][0]["reasons"])


@pytest.mark.parametrize("invalid", [None, math.inf, math.nan, True, -1])
def test_missing_or_infinite_work_cannot_mean_unbounded_security(invalid):
    row = covered_row(screen.Candidate(7, 112, 4))
    row["attack_evidence"]["goppa_structural_recovery"]["log2_work"] = invalid
    assert not screen.modeled_frontier([row], CONTEXT)["conditional_modeled_frontier"]


def test_incompatible_accounting_or_unreviewed_models_are_separate():
    row = covered_row(screen.Candidate(7, 112, 4))
    row["attack_evidence"]["generic_decoding"]["context"]["memory_access"] = "cube root"
    assert not screen.modeled_frontier([row], CONTEXT)["conditional_modeled_frontier"]
    row = covered_row(screen.Candidate(7, 112, 4))
    row["attack_evidence"]["generic_decoding"]["coverage"] = "partial"
    assert not screen.modeled_frontier([row], CONTEXT)["conditional_modeled_frontier"]


def test_conditional_pareto_comparison_keeps_legitimate_tradeoffs():
    cheap = covered_row(screen.Candidate(7, 112, 4), 40, 30)
    dominated = covered_row(screen.Candidate(7, 120, 4), 50, 29)
    costly = covered_row(screen.Candidate(8, 216, 6), 50, 45)
    result = screen.modeled_frontier([cheap, dominated, costly], CONTEXT)
    assert result["conditional_modeled_frontier"] == [cheap["candidate_id"], costly["candidate_id"]]
    assert not result["live_admission"]  # Even synthetic complete rows never activate.


def test_committed_screen_reproduces_exactly_and_no_security_curve_is_created():
    artifact = json.loads((ROOT / "docs/mceliece-candidate-screen-20261029.json").read_text())
    assert screen.study() == artifact
    digest = hashlib.sha256()
    for c in screen.candidates():
        digest.update((json.dumps(c.row(), sort_keys=True, separators=(",", ":")) + "\n").encode())
    assert digest.hexdigest() == artifact["domain"]["ordered_candidate_rows_sha256"]
    assert artifact["comparison"]["conditional_modeled_frontier"] == []
    assert all(r["current_security_bits"] is None for r in artifact["control_ray"])


def test_partial_isd_artifact_remains_scoped_and_binds_same_candidates():
    artifact = json.loads((ROOT / "docs/mceliece-partial-isd-screen-20261029.json").read_text())
    assert artifact["source"]["commit"] == screen.ESTIMATOR_COMMIT
    assert artifact["model"]["nsolutions_log2"] == 0
    assert "Goppa structural recovery (2026/1630,1984,1986)" in artifact["model"]["not_covered"]
    for row in artifact["rows"]:
        c = screen.Candidate(row["m"], row["n"], row["t"])
        assert all(row[k] == v for k, v in c.row().items())
        assert len(row["estimates"]) == 9
        assert all(e["status"] == "estimated" and math.isfinite(e["log2_bit_operations"])
                   for e in row["estimates"])
    # This does not reproduce external estimates. The separately documented
    # pinned runner does that; CI checks the artifact's provenance/scope contract.


def test_independent_toy_goppa_matrix_correctness_and_row_encoding():
    """A fully enumerated tiny instance, not validation of a general native decoder.

    GF16 = F2[z]/(z^4+z+1), g=x^2+x+z^3, support0..11.
    Independently construct H, reduce its leading8 columns, enumerate EVERY
    weight<=2 error, and check distinct syndromes plus canonical padded rows.
    """
    def multiply(a, b):
        out = 0
        while b:
            if b & 1:
                out ^= a
            b >>= 1
            a <<= 1
            if a & 16:
                a ^= 19
        return out
    def evaluate(x):
        return multiply(x, x) ^ x ^ 8
    assert all(evaluate(x) != 0 for x in range(16))  # Degree2: no roots iff irreducible.
    rows = [0] * 8
    for column, x in enumerate(range(12)):
        inverse = next(y for y in range(1, 16) if multiply(y, evaluate(x)) == 1)
        for power, value in enumerate((inverse, multiply(x, inverse))):
            for bit in range(4):
                rows[4*power + bit] |= ((value >> bit) & 1) << column
    for column in range(8):
        pivot = next(i for i in range(column, 8) if rows[i] >> column & 1)
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for i in range(8):
            if i != column and rows[i] >> column & 1:
                rows[i] ^= rows[column]
    public = bytes(row >> 8 for row in rows)
    assert public.hex() == "0b0704080e0d0201"
    assert len(public) == screen.Candidate(4, 12, 2).sizes()["public_key_bytes"] == 8
    assert all(byte < 16 for byte in public)  # Four zero padding bits PER row.
    syndromes = {}
    for weight in range(3):
        for positions in combinations(range(12), weight):
            error = sum(1 << j for j in positions)
            syndrome = sum(((row & error).bit_count() % 2) << i for i, row in enumerate(rows))
            assert syndrome not in syndromes
            syndromes[syndrome] = error
    assert len(syndromes) == 1 + 12 + math.comb(12, 2) == 79


def test_published_tii254_equivalent_key_reproduces_the_entire_public_code():
    fixture = ROOT / "tests/fixtures/tii254"
    public = (fixture / "public_key.txt").read_bytes()
    key = (fixture / "recovered_key.json").read_bytes()
    receipt = json.loads((ROOT / "docs/mceliece-tii254-verification-20261029.json").read_text())
    result = verifier.verify(public.decode("ascii"), json.loads(key))
    assert result == {k: v for k, v in receipt.items() if k != "input_sha256"}
    assert receipt["input_sha256"] == {"public_key.txt": hashlib.sha256(public).hexdigest(),
                                      "recovered_key.json": hashlib.sha256(key).hexdigest()}
    assert result["binary_rank"] == 96


@pytest.mark.parametrize("mutation", ["public_bit", "duplicate_support", "polynomial", "field"])
def test_tii254_verifier_rejects_corrupted_public_or_recovered_inputs(mutation):
    fixture = ROOT / "tests/fixtures/tii254"
    public = (fixture / "public_key.txt").read_text()
    key = json.loads((fixture / "recovered_key.json").read_text())
    if mutation == "public_bit":
        lines = public.splitlines()
        # Change a nonpivot bit. The public matrix still has full binary rank.
        row = lines[0][1:-1].split()
        row[-1] = str(1-int(row[-1]))
        lines[0] = "[" + " ".join(row) + "]"
        public = "\n".join(lines)
    elif mutation == "duplicate_support":
        key["support_integers"][0] = key["support_integers"][1]
    elif mutation == "polynomial":
        key["goppa_coefficients"][0] ^= 1
    else:
        key["field_modulus"] = 0x11b  # Another valid field representation is NOT interchangeable.
    with pytest.raises(ValueError):
        verifier.verify(public, key)


def test_small_field_inverse_and_irreducibility_checks():
    field = verifier.Field(4, 19)
    assert all(field.multiply(x, field.inverse(x)) == 1 for x in range(1, 16))
    assert field.irreducible([8, 1, 1])
    assert not field.irreducible([0, 1, 1])
    assert not field.irreducible([1, 0, 1])  # Repeated factor (X+1)^2.
