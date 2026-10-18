#!/usr/bin/env python3
"""Reproducible exact-rational correctness bounds for candidate ML-KEM ladders.

This certifies a stated noise experiment, not Python/native implementation
conformance or the unproved independent-uniform-compression modeling bridge.
It never imports challenge code and never activates a profile.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

Q = 3329
DU = 10
DV = 4
TARGET_BITS = 128
DEGREES = (8, 16, 32, 64, 128, 256)
MODES = ("conservative-compression", "independent-uniform-u-compression")


def cbd(eta: int) -> dict[int, Fraction]:
    return {i: Fraction(math.comb(2 * eta, i + eta), 2 ** (2 * eta))
            for i in range(-eta, eta + 1)}


def round_ratio(numerator: int, denominator: int) -> int:
    return (2 * numerator + denominator) // (2 * denominator)


def compression_error_counts(d: int) -> dict[int, int]:
    counts: Counter[int] = Counter()
    for coefficient in range(Q):
        encoded = round_ratio((1 << d) * coefficient, Q) % (1 << d)
        decoded = round_ratio(Q * encoded, 1 << d)
        error = (decoded - coefficient + Q // 2) % Q - Q // 2
        counts[error] += 1
    return dict(sorted(counts.items()))


def combine(left: dict[int, Fraction], right: dict[int, Fraction], operation):
    result: dict[int, Fraction] = {}
    for a, pa in left.items():
        for b, pb in right.items():
            value = operation(a, b)
            result[value] = result.get(value, Fraction()) + pa * pb
    assert sum(result.values()) == 1
    return result


def distributions(eta1: int, mode: str):
    wide, small = cbd(eta1), cbd(2)
    product = combine(wide, wide, lambda a, b: a * b)
    if mode == "conservative-compression":
        # Conditional on A,r,e1, the actual compression error is arbitrary but
        # bounded by 2. The symmetric CBD MGF increases with |e1+error|.
        shifted = {abs(i) + 2: sum(p for j, p in small.items() if abs(j) == abs(i))
                   for i in small}
    elif mode == "independent-uniform-u-compression":
        rounding = {i: Fraction(count, Q)
                    for i, count in compression_error_counts(DU).items()}
        shifted = combine(small, rounding, lambda a, b: a + b)
    else:
        raise ValueError("Unknown evidence model")
    second_product = combine(wide, shifted, lambda a, b: a * b)
    assert all(product.get(-i) == p for i, p in product.items())
    assert all(second_product.get(-i) == p for i, p in second_product.items())
    return product, second_product, small


def mgf(distribution: dict[int, Fraction], x: Fraction) -> Fraction:
    return sum((probability * x ** value
                for value, probability in distribution.items()), Fraction())


def log_mgf(distribution: dict[int, Fraction], t: float) -> float:
    return math.log(sum(float(p) * math.exp(t * value)
                        for value, p in distribution.items()))


@lru_cache(maxsize=1)
def threshold() -> int:
    # Enumerate both encoded bits. A failure requires |E| >= 832; the one-bit
    # decoder is asymmetric by one integer because q is odd.
    failures = []
    for bit in (0, 1):
        center = round_ratio(Q * bit, 2)
        failures.extend(abs(error) for error in range(-Q // 2, Q // 2 + 1)
                        if round_ratio(2 * ((center + error) % Q), Q) % 2 != bit)
    return min(failures) - max(map(abs, compression_error_counts(DV)))


def choose_witness(n: int, rank: int, eta1: int, mode: str) -> Fraction:
    # Floating-point optimization only proposes a rational witness. Acceptance
    # and all bound comparisons below use integer arithmetic exclusively.
    first, second, additive = distributions(eta1, mode)

    def objective(t: float) -> float:
        return n * rank * (log_mgf(first, t) + log_mgf(second, t)) \
            + log_mgf(additive, t) - threshold() * t

    lower, upper = 0.0, 1.0
    for _ in range(75):
        a, b = lower + (upper - lower) / 3, upper - (upper - lower) / 3
        if objective(a) < objective(b):
            upper = b
        else:
            lower = a
    return Fraction(round(math.exp((lower + upper) / 2) * 1_000_000), 1_000_000)


def dyadic_bits(bound: Fraction) -> int:
    if bound >= 1:
        return 0
    bits = max(0, bound.denominator.bit_length() - bound.numerator.bit_length())
    while bound.numerator << bits > bound.denominator:
        bits -= 1
    while bound.numerator << (bits + 1) <= bound.denominator:
        bits += 1
    return bits


def certificate(n: int, rank: int, eta1: int, mode: str,
                witness: Fraction | None = None) -> dict:
    if n not in DEGREES or rank not in (1, 2, 3) or eta1 not in (2, 3) or mode not in MODES:
        raise ValueError("Unreviewed parameter domain")
    common = {"mode": mode, "n": n, "rank": rank, "eta1": eta1, "eta2": 2,
              "q": Q, "du": DU, "dv": DV, "target_bits": TARGET_BITS}
    # Includes both actual compression errors, with no independence assumption.
    support_bound = n * rank * (eta1 ** 2 + eta1 * (2 + 2)) + 2 + 104
    if support_bound < 832:
        return {**common, "argument": "deterministic-support", "support_bound": support_bound,
                "failure_bound": "0", "meets_target": True}
    x = witness if witness is not None else choose_witness(n, rank, eta1, mode)
    if x <= 1:
        raise ValueError("Chernoff witness must be greater than one")
    first, second, additive = distributions(eta1, mode)
    bound = 2 * n * (mgf(first, x) * mgf(second, x)) ** (n * rank) \
        * mgf(additive, x) / x ** threshold()
    bound = min(Fraction(1), bound)
    digest = hashlib.sha256((hex(bound.numerator) + "/" + hex(bound.denominator)).encode()).hexdigest()
    bits = dyadic_bits(bound)
    return {**common, "argument": "exact-rational-chernoff-union-bound",
            "support_bound": support_bound, "threshold_after_v_compression": threshold(),
            "witness": {"numerator": x.numerator, "denominator": x.denominator},
            "failure_bound": f"2^-{bits}", "certified_dyadic_bits": bits,
            "approximate_chernoff_bits": round(math.log2(bound.denominator) - math.log2(bound.numerator), 9),
            "rational_bound_sha256": digest,
            "meets_target": (bound.numerator << TARGET_BITS) <= bound.denominator}


def generate() -> dict:
    rows = []
    for anchor, eta1, maximum_rank in (("ML-KEM-512", 3, 2), ("ML-KEM-768", 2, 3)):
        for n in DEGREES:
            for rank in range(1, maximum_rank + 1):
                rows.append({"candidate_anchor": anchor, "n": n, "rank": rank,
                             "evidence": [certificate(n, rank, eta1, mode) for mode in MODES]})
    return {"schema": "crucible-mlkem-correctness-candidates-v1", "status": "candidate-not-admission",
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "u_compression_error_counts": compression_error_counts(DU),
            "v_compression_absolute_bound": max(map(abs, compression_error_counts(DV))),
            "model_warning": "Independent-uniform-u-compression is an extra unproved modeling assumption; exact arithmetic certifies the modeled experiment, not its equivalence to the implemented KEM.",
            "configurations": rows}


def verify(document: dict) -> None:
    if document.get("schema") != "crucible-mlkem-correctness-candidates-v1":
        raise ValueError("Unsupported certificate schema")
    expected_keys = {(anchor, n, rank)
                     for anchor, maximum_rank in (("ML-KEM-512", 2), ("ML-KEM-768", 3))
                     for n in DEGREES for rank in range(1, maximum_rank + 1)}
    seen = set()
    count = 0
    for row in document["configurations"]:
        key = (row["candidate_anchor"], row["n"], row["rank"])
        if key not in expected_keys or key in seen:
            raise ValueError("Candidate identity/uniqueness mismatch")
        seen.add(key)
        if [item["mode"] for item in row["evidence"]] != list(MODES):
            raise ValueError("Evidence coverage mismatch")
        for item in row["evidence"]:
            expected_eta = 3 if row["candidate_anchor"] == "ML-KEM-512" else 2
            if (item["n"], item["rank"], item["eta1"]) != (row["n"], row["rank"], expected_eta):
                raise ValueError("Evidence is not bound to candidate parameters")
            w = item.get("witness")
            x = Fraction(w["numerator"], w["denominator"]) if w else None
            expected = certificate(item["n"], item["rank"], item["eta1"], item["mode"], x)
            if expected != item:
                raise ValueError(f"Certificate mismatch: {row['candidate_anchor']} {row['n']} {row['rank']}")
            count += 1
    if count != 60 or seen != expected_keys:
        raise ValueError("Candidate coverage mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(json.loads(args.verify.read_text()))
        print("Verified 60 exact-rational/support certificates across 30 candidate configurations.")
    else:
        data = generate()
        encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.write_text(encoded)
        else:
            print(encoded, end="")


if __name__ == "__main__":
    main()
