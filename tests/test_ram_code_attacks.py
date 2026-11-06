"""Independent small-instance checks for the explicit code-attack accounts."""
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random

import pytest

from crucible.attack_models import codes


def hamming_matrix():
    return [[(j >> i) & 1 for j in range(1, 8)] for i in range(3)]


def syndrome(matrix, vector):
    return [sum(a * b for a, b in zip(row, vector)) % 2 for row in matrix]


def test_fixed_support_probability_is_exact_not_random_matrix_assumption():
    for n, r, t in [(7, 3, 1), (9, 5, 2), (10, 6, 3)]:
        support = set(range(t))
        sets = list(combinations(range(n), r))
        hits = sum(support <= set(j) for j in sets)
        assert codes.support_hit_probability(n, r, t) == Fraction(hits, len(sets))


def test_prange_all_information_sets_recover_every_hamming_error():
    matrix = hamming_matrix()
    budget = codes.prange_budget(7, 3, 1)
    for location in range(7):
        target = [int(i == location) for i in range(7)]
        s = syndrome(matrix, target)
        successes = 0
        hit_and_full_rank = 0
        for columns in combinations(range(7), 3):
            candidate, core_cost = codes.solve_selected_columns(matrix, s, columns)
            assert core_cost < budget["per_trial"]
            if candidate is not None:
                assert syndrome(matrix, candidate) == s
                hit_and_full_rank += location in columns
                if sum(candidate) == 1:
                    assert candidate == target
                    successes += 1
        assert successes == hit_and_full_rank
        assert Fraction(successes, 35) == (
            codes.support_hit_probability(7, 3, 1)
            * codes.exact_rank_density(matrix, {location})
        )
        assert codes.exact_rank_density(matrix, {location}) >= Fraction(1, 8)


def test_rank_assumption_is_genuine_and_can_fail():
    # Full-row-rank alone does not give the required conditional rank density.
    matrix = [[1, 0, 0, 0], [0, 1, 0, 0]]
    assert codes.exact_rank_density(matrix, {2}) == 0
    assert "has not been established" in codes.rank_density_assumption()["human"]


def test_repeat_budget_has_conditional_success_margin():
    hit = codes.support_hit_probability(7, 3, 1)
    trials = codes.prange_budget(7, 3, 1)["trials"]
    # No average-over-instances success probability is amplified here.
    conditional_trial_probability = hit / 16
    conditional_failure = (1 - conditional_trial_probability) ** trials
    assert conditional_failure < Fraction(1, 4)
    assert Fraction(7, 8) * (1 - conditional_failure) >= Fraction(21, 32)


@pytest.mark.parametrize("args", [(7, 3, 4), (7, 8, 1), (7, 3, 0), (True, 1, 1)])
def test_invalid_dimensions_rejected(args):
    with pytest.raises(ValueError):
        codes.prange_budget(*args)


def test_singular_information_set_rejected():
    result, _ = codes.solve_selected_columns([[1, 1, 0], [0, 0, 1]], [1, 0], [0, 1])
    assert result is None


def test_bounded_sampler_draw_width_and_abort_are_explicit():
    class ZeroBits:
        def __init__(self):
            self.widths = []
        def getrandbits(self, width):
            self.widths.append(width)
            return 0
    rng = ZeroBits()
    assert codes.bounded_uniform_columns(7, 7, rng) == list(range(7))
    assert rng.widths == [3, 3, 3, 2, 2, 1, 0]
    class AllOnes:
        calls = 0
        def getrandbits(self, width):
            self.calls += 1
            return (1 << width) - 1
    rng = AllOnes()
    assert codes.bounded_uniform_columns(7, 3, rng) is None
    assert rng.calls == 64


def test_complete_bounded_prange_reference_recovers_public_syndrome():
    matrix = hamming_matrix()
    target = [0, 0, 1, 0, 0, 0, 0]
    result, trials = codes.recover_syndrome(matrix, syndrome(matrix, target), 1,
                                           random.Random(20261105), 100)
    assert result == target
    assert trials <= 100


def test_field_inverses_and_rs_codeword_syndromes():
    for x in range(1, 256):
        assert codes.gf256_mul(x, codes._gf_inverse(x)) == 1
    message = bytes(range(16))
    for length in (18, 24, 46):
        encoded = codes.rs_encode(message, length)
        assert encoded[-16:] == message
        assert all(codes._poly_eval(encoded, codes._gf_pow(2, i)) == 0
                   for i in range(1, length - 16 + 1))


def test_rs_decoding_at_full_unique_error_radius():
    rng = random.Random(20261105)
    for length in (18, 24, 46):
        for _ in range(5):
            message = rng.randbytes(16)
            encoded = list(codes.rs_encode(message, length))
            for i in rng.sample(range(length), (length - 16) // 2):
                encoded[i] ^= rng.randrange(1, 256)
            assert codes.rs_decode(encoded) == message


def _cyclic_product(a, b, n):
    result = 0
    mask = (1 << n) - 1
    for j in range(n):
        if b >> j & 1:
            result ^= ((a << j) | (a >> (n - j))) & mask
    return result


def test_hqc_public_only_support_recovery_completes_full_shared_secret():
    # Actual first-stage dimensions and retained2025 wire/KDF convention;
    # deterministic synthetic coins, not an assertion of distributional evidence.
    n, length, rm_length = 2309, 18, 128
    seed = bytes(range(32))
    h = int.from_bytes(hashlib.shake_256(seed + b"\x01").digest((n + 7) // 8), "little") & ((1 << n) - 1)
    x, y = 1 << 14, 1 << 91
    r1, r2, error = (1 << 3) | (1 << 200), (1 << 77) | (1 << 1400), (1 << 100) | (1 << 500)
    message, salt = bytes(range(16)), bytes(reversed(range(16)))
    s = x ^ _cyclic_product(h, y, n)
    u = r1 ^ _cyclic_product(h, r2, n)
    codeword = sum(codes.rm_word(symbol) << (128 * j)
                   for j, symbol in enumerate(codes.rs_encode(message, length)))
    v = (codeword ^ _cyclic_product(s, r2, n) ^ error) & ((1 << (length * rm_length)) - 1)
    pk = seed + s.to_bytes((n + 7) // 8, "little")
    ct = u.to_bytes((n + 7) // 8, "little") + v.to_bytes(length * rm_length // 8, "little") + salt
    public = {"parameters": {"n": n, "rs_length": length, "rm_length": rm_length, "rs_dimension": 16},
              "public_key": pk.hex(), "ciphertext": ct.hex()}
    # The attacker searches public inputs rather than being handed y.
    recovered = next(1 << j for j in range(n) if (s ^ _cyclic_product(h, 1 << j, n)).bit_count() == 1)
    expected = hashlib.sha3_512(hashlib.sha3_256(pk + b"\x01").digest() + message + salt + b"\x00").digest()[:32]
    assert recovered == y
    assert codes.recover_hqc_from_y(public, recovered) == expected


def test_hqc_uniqueness_bound_follows_exact_toy_enumeration():
    # Primitive prime n=5, w=1. Fix x,y, enumerate all uniformly random h.
    n = 5
    bad = 0
    for h in range(1 << n):
        x, y = 1, 2
        s = x ^ _cyclic_product(h, y, n)
        alternatives = sum((s ^ _cyclic_product(h, 1 << j, n)).bit_count() == 1
                           for j in range(n))
        bad += alternatives > 1
    assert Fraction(bad, 1 << n) <= codes._hqc_unique_failure(n, 1)


def test_hqc_correctness_premise_matches_existing_support_certificate():
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "crucible/data/hqc_model_profiles.json").read_text())["profiles"]
    evidence = json.loads((root / "docs/hqc-support-review-20261029.json").read_text())["profiles"]
    assert len(rows) == len(evidence) == 66
    certified_stages = []
    for row, certificate in zip(rows, evidence):
        result = codes.estimate("hqc", "modeled-research-v3", row)
        guaranteed = certificate["deterministic_guarantee_established"]
        premise_ids = {entry["id"] for entry in result["assumptions"]}
        assert ("hqc-honest-decoding-transfer-v1" not in premise_ids) == guaranteed
        assert ("2^-128" not in result["success_lower_bound"]) == guaranteed
        # The support theorem removes only the decoder premise, not the
        # independent small-secret uniqueness or information-set assumptions.
        assert "hqc-unique-key-transfer-v1" in premise_ids
        if guaranteed:
            certified_stages.append(certificate["stage"])
            assert codes._hqc_support_correctness(row) == (
                certificate["residual_weight_maximum"],
                certificate["decoder_error_capacity"] + 1,
            )
            assert "zero honest decoding failure" in result["technical"]
    assert certified_stages == [1, 2, 3, 4]


def test_hqc_support_correctness_uses_actual_parameters_and_strict_boundary():
    params = {"n": 2309, "secret_weight": 1, "encryption_weight": 2,
              "error_weight": 59, "rs_length": 18, "rs_dimension": 16,
              "rm_length": 128, "rs_delta": 1, "rm_repetitions": 1,
              "code_length": 2304}
    assert codes._hqc_support_correctness(params) == (63, 64)
    assert codes._hqc_support_correctness({**params, "error_weight": 60}) is None
    assert codes._hqc_support_correctness({**params, "error_weight": 61}) is None
    for field, value in (("encryption_weight", None), ("rs_dimension", 15),
                         ("rs_delta", 2), ("rm_repetitions", 2),
                         ("code_length", 2305)):
        assert codes._hqc_support_correctness({**params, field: value}) is None
    # Metadata cannot discharge the correctness premise for a different tuple.
    for error_weight, guaranteed in ((59, True), (60, False)):
        profile = {"profile_id": "hqc-v3-tier-1", "instance_size": 1,
                   "parameters": {**params, "error_weight": error_weight}}
        result = codes.estimate("hqc", "modeled-research-v3", profile)
        premise_ids = {entry["id"] for entry in result["assumptions"]}
        assert ("hqc-honest-decoding-transfer-v1" not in premise_ids) == guaranteed


def test_hqc_support_evidence_is_bound_in_current_note_sources():
    root = Path(__file__).resolve().parents[1]
    presentations = json.loads((root / "crucible/data/scheme_note_presentations.json").read_text())
    release = presentations["releases"]["hqc@modeled-research-v3"]
    refs = {entry["id"]: entry for entry in release["source_refs"]}
    for ref_id, path in (
        ("support-review", "docs/hqc-module-progression-review-20261029.txt"),
        ("support-certificate", "docs/hqc-support-review-20261029.json"),
    ):
        assert refs[ref_id]["path"] == path
        assert refs[ref_id]["sha256"] == hashlib.sha256((root / path).read_bytes()).hexdigest()
    correctness = next(section for section in release["sections"] if section["id"] == "correctness")
    assert {"support-review", "support-certificate"} <= set(correctness["source_ref_ids"])


def test_all_real_code_profiles_have_finite_accounted_complete_budgets():
    root = Path(__file__).resolve().parents[1]
    rows = json.loads((root / "crucible/data/hqc_model_profiles.json").read_text())["profiles"]
    costs = []
    for row in rows:
        result = codes.estimate("hqc", "modeled-research-v3", row)
        assert result["work_bound"] == sum(result["breakdown"].values())
        assert codes._hqc_unique_failure(row["n"], row["weight"]) < Fraction(1, 64)
        assert result["breakdown"]["key_derivation"] > 0
        assert result["breakdown"]["rs_decode"] > 0
        assert result["memory_bits"] > 0
        costs.append(result["work_bound"])
    assert costs == sorted(costs)
    from crucible.code_parameters import MC_LENGTHS
    for n in MC_LENGTHS:
        result = codes.estimate("classic-mceliece", "length-research-v1", {"n": n, "m": 12, "t": 64})
        assert result["work_bound"] == sum(result["breakdown"].values())
        assert result["breakdown"]["key_derivation"] > 0
        assert result["assumptions"][0]["id"] == "prange-structured-rank-density-v1"


def test_unknown_release_is_not_silently_normalized():
    with pytest.raises(ValueError):
        codes.estimate("hqc", "future", {})
