#!/usr/bin/env python3
"""Offline reproducible research calibration; never run by the web server.

Correctness needs only Python. Estimates additionally need Sage 10.1 and the
official estimator checkout at the pinned commit. No automatic downloads.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ESTIMATOR_COMMIT = "53da5982597709ba0fdf94ea37a84d822310fd84"
ESTIMATOR_ARCHIVE_SHA256 = "4236adc38031c139c8a2721804d8962d6cacd015fae37f6f5215d2ac7f011060"
CDF = (4643,13363,20579,25843,29227,31145,32103,32525,32689,32745,32762,32766,32767)


def distribution():
    out = Counter()
    for u in range(65536):
        magnitude = sum(c < (u >> 1) for c in CDF)
        out[-magnitude if u & 1 else magnitude] += 1
    return out


def correctness_certificate(n):
    """Integer-only proof of a rounded-up Chernoff/union bound (ideal XOF).

    Returning b certifies probability <=2^-b, never a floating-point estimate.
    The n=8 profile is perfectly correct by the deterministic support bound.
    """
    if type(n) is not int or n % 8 or not 8 <= n <= 640:
        raise ValueError("Invalid dimension")
    if 2*n*12*12+12 < 4096:
        return {"n": n, "kind": "perfect", "bound": "0", "support_bound": 2*n*12*12+12}
    a, b = 1071, 1024
    noise = distribution()
    products = Counter()
    for x, cx in noise.items():
        for y, cy in noise.items():
            products[x*y] += cx*cy
    # r=a/b. Convert finite Laurent polynomials to positive integer ratios.
    mn = sum(count * a**(x+12) * b**(12-x) for x, count in noise.items())
    md = 65536*(a*b)**12
    pn = sum(count * a**(x+144) * b**(144-x) for x, count in products.items())
    pd = 65536**2*(a*b)**144
    numerator = 128*mn*pow(pn, 2*n)*b**4096
    denominator = md*pow(pd, 2*n)*a**4096
    exponent = denominator.bit_length()-numerator.bit_length()
    if numerator << exponent > denominator:
        exponent -= 1
    assert numerator << exponent <= denominator
    assert numerator << (exponent+1) > denominator
    return {"n": n, "kind": "statistical", "bound": f"2^-{exponent}", "bound_exponent": exponent,
        "r": "1071/1024", "formula": "128*M_chi(r)*M_product(r)^(2*n)/r^4096",
        "proof": "Exact integer numerator/denominator comparison; includes both tails and all 64 coordinates"}


def estimate(path):
    sys.path.insert(0, str(path))
    from estimator import LWE, ND
    from sage.all import QQ, log, sqrt
    rows = []
    # Moment-matched Gaussian is an explicitly declared estimator approximation,
    # not the exact finite sampler used for correctness or generation.
    sigma = sqrt(QQ(129790)/16384)
    for n in range(8, 641, 8):
        params = LWE.Parameters(n=n, q=32768, Xs=ND.DiscreteGaussian(sigma),
            Xe=ND.DiscreteGaussian(sigma), m=n+16, tag="Frodo matrix research")
        try:
            attacks = LWE.estimate.rough(params, quiet=True, catch_exceptions=False)
            values = {name: {"log2_rop": float(log(cost["rop"], 2)), "beta": int(cost.get("beta", 0))}
                      for name, cost in attacks.items()}
            row = {"n": n, "attacks": values, "bits": min(128, min(c["log2_rop"] for c in values.values()))}
        except AssertionError:
            # The routine does not model tiny dimensions. Do not invent values.
            row = {"n": n, "bits": None, "error": "AssertionError"}
        rows.append(row)
    return rows


def select_dimensions(rows):
    """Closest strictly increasing dimensions to targets; preserve both anchors."""
    chosen = [rows[0]]  # n=8 practical LLL attack; no estimator claim.
    for tier in range(2, 50):
        target = 8+120*(tier-1)/49
        eligible = [r for r in rows if r["n"] > chosen[-1]["n"] and r["n"] < 640 and r["bits"] is not None]
        chosen.append(min(eligible, key=lambda r: (abs(r["bits"]-target), r["n"])))
    return chosen+[rows[-1]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimator", type=Path)
    parser.add_argument("--archive", type=Path, help="Pinned source archive, required with --estimator")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dimension", type=int, default=640)
    args = parser.parse_args()
    if args.estimator:
        if not args.archive or hashlib.sha256(args.archive.read_bytes()).hexdigest() != ESTIMATOR_ARCHIVE_SHA256:
            parser.error("Supply the exact pinned estimator archive; source identity must be verified")
        # A byte-for-byte extraction check prevents swapping a modified checkout.
        import tarfile
        with tarfile.open(args.archive) as archive:
            for entry in archive.getmembers():
                if entry.isfile():
                    relative = Path(*Path(entry.name).parts[1:])
                    if (args.estimator/relative).read_bytes() != archive.extractfile(entry).read():
                        parser.error("Estimator checkout does not match the pinned archive")
        rows = estimate(args.estimator)
        chosen = select_dimensions(rows)
        result = {"model_id": "frodo-lwe-core-svp-v1", "estimator_commit": ESTIMATOR_COMMIT,
            "archive_sha256": ESTIMATOR_ARCHIVE_SHA256, "sage_version": "10.1",
            "method": "LWE.estimate.rough: primal usvp and dual hybrid, ADPS16 classical Core-SVP, GSA",
            "noise_approximation": "Gaussian with exact sampler variance 129790/16384; not an exact distribution model",
            "samples": "m=n+16, following official estimator's Frodo convention; multiple targets and KEM transform not modeled",
            "cap": "128, from the full 128-bit encapsulation message / shared key",
            "limitations": ["Heuristic asymptotic lattice reduction costs are not measured compute or a lower bound.",
                "Tiny dimensions encounter a beta=40 floor or an assertion; no security claim there.",
                "Only two attack families modeled; no quantum, side-channel or complete KEM reduction analysis.",
                "Exact published n640 endpoint retained, rather than silently altering it to fit a target."],
            "profiles": [{"tier": i+1, "target_bits": round(8+120*i/49, 6), **row} for i, row in enumerate(chosen)],
            "candidate_estimates": rows}
    else:
        result = correctness_certificate(args.dimension)
    args.output.write_text(json.dumps(result, indent=2)+"\n")


if __name__ == "__main__":
    main()
