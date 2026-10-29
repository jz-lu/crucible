#!/usr/bin/env python3
"""Offline McEliece candidate screen. Never creates or admits live profiles.

Integer feasibility, byte costs and model outputs are separate evidence layers.
In particular, a large estimated cost for one attack is NOT a security bound.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible.code_parameters import MC_LENGTHS

FORMAT = "crucible-mceliece-candidate-screen-v1"
ESTIMATOR_COMMIT = "071cce7a942af423422cb0c6d6af55778c72e4d1"
REQUIRED_COVERAGE = ("generic_decoding", "goppa_structural_recovery")


@dataclass(frozen=True, order=True)
class Candidate:
    m: int
    n: int
    t: int

    def __post_init__(self):
        if any(type(x) is not int for x in (self.m, self.n, self.t)):
            raise ValueError("m, n and t must be integers")
        if not (1 <= self.m <= 16 and 2 <= self.t and self.m * self.t < self.n <= 2**self.m):
            raise ValueError("Outside the bounded integer prerequisites")

    @property
    def key(self):
        return f"m{self.m}-n{self.n}-t{self.t}"

    @property
    def r(self):
        return self.m * self.t

    @property
    def k(self):
        return self.n - self.r

    def sizes(self):
        # CM specification 6.2 pads EACH public matrix row, not the whole matrix.
        return {
            "public_key_bytes": self.r * ((self.k + 7) // 8),
            "ciphertext_bytes": (self.r + 7) // 8,
            "public_row_padding_bits": (-self.k) % 8,
            "ciphertext_padding_bits": (-self.r) % 8,
        }

    def row(self):
        return {"candidate_id": self.key, "m": self.m, "n": self.n,
                "t": self.t, "r": self.r, "k": self.k, **self.sizes()}


def candidates():
    """Declared finite screen; byte alignment is a search restriction, not CM law."""
    for m in range(7, 13):
        for n in range(8, min(3488, 2**m) + 1, 8):
            for t in range(2, min(64, (n - 1) // m) + 1):
                yield Candidate(m, n, t)


def poly_remainder(a, b):
    while a and a.bit_length() >= b.bit_length():
        a ^= b << (a.bit_length() - b.bit_length())
    return a


def poly_gcd(a, b):
    while b:
        a, b = b, poly_remainder(a, b)
    return a


def square_mod(a, modulus):
    square = 0
    for bit in range(a.bit_length()):
        square |= ((a >> bit) & 1) << (2 * bit)
    return poly_remainder(square, modulus)


def irreducible_binary(polynomial):
    """Exact finite-field irreducibility test, including all small-factor degrees."""
    degree = polynomial.bit_length() - 1
    if degree < 1:
        return False
    x = poly_remainder(2, polynomial)
    power = x
    for d in range(1, degree + 1):
        power = square_mod(power, polynomial)
        if d <= degree // 2 and poly_gcd(power ^ x, polynomial) != 1:
            return False
    return power == x


def first_field_polynomial(m):
    return next(p for p in range(2**m + 1, 2**(m + 1), 2) if irreducible_binary(p))


def nearest_positive(numerator, denominator):
    """Nearest integer, ties upward; no platform-dependent float rounding."""
    return (2 * numerator + denominator) // (2 * denominator)


def control_ray():
    """Six diagnostic points preserving the anchor's support and parity fractions.

    Not optimized, a proposed live route, or equal security steps. This gives a
    reproducible small sample for studying joint m/n/t changes before selection.
    """
    for m in range(7, 13):
        n = 8 * nearest_positive(3488 * 2**m, 4096 * 8)
        t = nearest_positive(768 * n, 3488 * m)
        yield Candidate(m, n, t)


def fixed_weight_work(c):
    """Generalized spec 5.4 ideal FixedWeight probability, not a worker claim.

    Unsupported candidates have no executable sampler yet. This mathematical
    batch rule must not be confused with admitting a changed native SYS_T macro.
    """
    tau, threshold = c.t, 2**c.m
    while c.n < threshold:
        tau *= 2
        threshold //= 2
    q = 2**c.m
    enough = Fraction(sum(math.comb(tau, i) * c.n**i * (q - c.n)**(tau - i)
                          for i in range(c.t, tau + 1)), q**tau)
    distinct = Fraction(math.prod(range(c.n - c.t + 1, c.n + 1)), c.n**c.t)
    probability = enough * distinct
    return {"batch": tau, "success_probability": float(probability),
            "expected_batches": float(1 / probability)}


def diagnostic_row(c):
    return {**c.row(), "field_polynomial_bits": first_field_polynomial(c.m),
            "prange_log2_trials_only": math.log2(math.comb(c.n, c.t))
                                         - math.log2(math.comb(c.r, c.t)),
            "fixed_weight": fixed_weight_work(c),
            "live_registered": c.m == 12 and c.t == 64 and c.n in MC_LENGTHS,
            "implementation_admission": "not determined by this screen",
            "current_security_bits": None,
            "missing_evidence": ["complete applicable attack-model review",
                                 "new tuples: extension-field representation and native conformance"]}


def comparison_blockers(row, context):
    """A deliberately conservative gate for a conditional modeled comparison.

    Passing this gate is not admission or mathematical certification. Evidence
    must explicitly assert coverage under one common accounting context. Current
    estimates fail it: unknown structural attacks are never infinity/zero.
    """
    blockers = []
    for kind in REQUIRED_COVERAGE:
        evidence = row.get("attack_evidence", {}).get(kind)
        if not isinstance(evidence, dict) or evidence.get("coverage") != "reviewed-for-domain":
            blockers.append(kind + ": coverage unresolved")
            continue
        work = evidence.get("log2_work")
        if isinstance(work, bool) or not isinstance(work, (int, float)) or not math.isfinite(work) or work < 0:
            blockers.append(kind + ": finite work estimate missing")
        if evidence.get("context") != context:
            blockers.append(kind + ": incompatible game or resource accounting")
        if not evidence.get("source_revision") or not evidence.get("review_reference"):
            blockers.append(kind + ": provenance/review missing")
    return blockers


def modeled_frontier(rows, context):
    """Conditional byte/covered-attack-cost Pareto set, with blocked rows explicit.

    Higher minimum modeled work and lower key/ciphertext sizes are preferable
    only within the declared model. No timings, optimal-security claim or live
    progression follow. Equal points remain present (no arbitrary tie deletion).
    """
    eligible, blocked = [], []
    for row in rows:
        reasons = comparison_blockers(row, context)
        if reasons:
            blocked.append({"candidate_id": row["candidate_id"], "reasons": reasons})
        else:
            cost = min(row["attack_evidence"][k]["log2_work"] for k in REQUIRED_COVERAGE)
            eligible.append((row, cost))

    def dominates(a, b):
        ar, aw = a
        br, bw = b
        weak = (ar["public_key_bytes"] <= br["public_key_bytes"] and
                ar["ciphertext_bytes"] <= br["ciphertext_bytes"] and aw >= bw)
        strict = (ar["public_key_bytes"] < br["public_key_bytes"] or
                  ar["ciphertext_bytes"] < br["ciphertext_bytes"] or aw > bw)
        return weak and strict

    return {"conditional_modeled_frontier": sorted(row["candidate_id"] for row, work in eligible
             if not any(dominates(other, (row, work)) for other in eligible)),
            "blocked": sorted(blocked, key=lambda r: r["candidate_id"]),
            "live_admission": False}


def study():
    digest = hashlib.sha256()
    counts = {}
    count = 0
    for c in candidates():
        digest.update((json.dumps(c.row(), sort_keys=True, separators=(",", ":")) + "\n").encode())
        counts[str(c.m)] = counts.get(str(c.m), 0) + 1
        count += 1
    ray = [diagnostic_row(c) for c in control_ray()]
    return {"format": FORMAT, "status": "offline-screen-only-no-new-live-progression",
            "domain": {"m": [7, 12], "t": [2, 64], "n_alignment": 8,
                       "constraints": "m*t<n<=min(3488,2**m)", "candidate_count": count,
                       "counts_by_m": counts, "ordered_candidate_rows_sha256": digest.hexdigest()},
            "encoding": "CM 2022-10-23 section 6.2; zero padding per public row and syndrome",
            "not_established": ["full-rank systematic generation for each candidate",
                                "extension-field polynomial and serialized private-key format",
                                "generalized decoder and KEM conformance",
                                "complete generic and structural attack analysis",
                                "runtime/memory Pareto efficiency or global optimum"],
            "control_ray_rule": {"n": "8*nearest(3488*2^m/(4096*8)); ties upward",
                                 "t": "nearest(768*n/(3488*m)); ties upward",
                                 "purpose": "diagnostic sample, not a selected live progression"},
            "control_ray": ray,
            "incumbent_examples": [diagnostic_row(Candidate(12, n, 64)) for n in (840, 1680, 3488)],
            "old_proxy_counterexample": diagnostic_row(Candidate(7, 120, 4)),
            "comparison": modeled_frontier(ray, {"game": "single public-key/ciphertext shared-secret recovery"}),
            "required_before_new_release": ["review applicable attacks including low-degree structure",
                                            "select and justify tradeoff objective",
                                            "independent native implementation/correctness/conformance review",
                                            "new immutable release/progression; preserve old evidence"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-jsonl", type=Path, help="Optional complete finite candidate table")
    args = parser.parse_args()
    result = study()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    if args.candidate_jsonl:
        args.candidate_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.candidate_jsonl.open("w") as stream:
            for c in candidates():
                stream.write(json.dumps(c.row(), sort_keys=True, separators=(",", ":")) + "\n")
    print(f"Screened {result['domain']['candidate_count']} integer tuples; no live admission or security claim.")


if __name__ == "__main__":
    main()
