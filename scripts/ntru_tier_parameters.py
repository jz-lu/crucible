#!/usr/bin/env python3
"""Explicit offline SageMath parameter certification; never called by a server.

Run with Sage's Python. The frozen output is a research parameter family, not
new published sntrup parameter sets or a calibration of security bits.
"""
import argparse
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main():
    from sage.all import GF, PolynomialRing, ZZ, prime_range, version
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = []
    for p in prime_range(5, 762):
        p = int(p)
        if p % 4 != 1:
            continue
        w = max(1, p * 286 // 761)
        choices = [4591] if p == 761 else prime_range(16 * w + 1, 16384)
        for q in choices:
            q = int(q)
            if q % 6 != 1:
                continue
            ring = PolynomialRing(GF(q), "x")
            x = ring.gen()
            if not (x**p - x - 1).is_irreducible():
                continue
            assert ZZ(p).is_prime(proof=True) and ZZ(q).is_prime(proof=True)
            assert 2 * p >= 3 * w and q >= 16 * w + 1
            candidates.append({"p": p, "q": q, "w": w})
            print(f"certified p={p} q={q} w={w}", flush=True)
            break
    if len(candidates) < 50 or candidates[-1] != {"p": 761, "q": 4591, "w": 286}:
        raise SystemExit("Fewer than 50 admissible candidates, or missing sntrup761 anchor")
    # Select 50 increasing degrees with both endpoints included. No adjustment
    # of the secret distribution or invented attack-estimate target occurs.
    chosen = [candidates[(i * (len(candidates) - 1) + 24) // 49] for i in range(50)]
    profiles = [{"tier": i + 1, **row} for i, row in enumerate(chosen)]
    result = {"format": "crucible-ntru-tier-parameters-v1", "profiles": profiles,
              "certificate": {"tool": version(), "prime_proof": True,
                  "irreducibility": "SageMath GF(q)[x]: (x^p-x-1).is_irreducible() for every exact profile",
                  "conditions": ["p,q prime", "p mod4=1", "q mod6=1", "0<w<=p", "2p>=3w", "q>=16w+1", "q<16384", "p<=761"],
                  "meaning": "Exact algebra/encoding conditions; not a confidentiality or side-channel certificate"},
              "selection": "50 increasing degrees from admissible prime degrees 5..761; w=max(1,floor(286p/761)); first admissible prime q in [16w+1,16384), q mod6=1; final (761,4591,286)."}
    result["parameters_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Wrote 50 parameter certificates; " + result["parameters_sha256"], flush=True)


if __name__ == "__main__":
    main()
