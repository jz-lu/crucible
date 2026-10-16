#!/usr/bin/env sage-python
"""Offline exact-prime/correctness checks and pinned LWE reference estimates.

No interpolation and no security-target parameter search. The eleven profiles
are natural increasing dimensions, with the smallest correctness-covering prime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

COMMIT = "53da5982597709ba0fdf94ea37a84d822310fd84"
ARCHIVE_SHA256 = "4236adc38031c139c8a2721804d8962d6cacd015fae37f6f5215d2ac7f011060"
DIMENSIONS = ((8, 1), (16, 1), (32, 1), (64, 1), (64, 2), (64, 3),
              (128, 2), (128, 3), (128, 4), (128, 5), (256, 3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimator", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        parser.error("Wrong estimator archive")
    with tarfile.open(args.archive) as archive:
        for member in archive.getmembers():
            if member.isfile():
                relative = Path(*Path(member.name).parts[1:])
                if (args.estimator / relative).read_bytes() != archive.extractfile(member).read():
                    parser.error("Estimator checkout changed")
    sys.path.insert(0, str(args.estimator))
    from estimator import LWE, ND
    from sage.all import ZZ, log, next_prime

    rows = []
    for tier, (n, rank) in enumerate(DIMENSIONS, 1):
        bound = 2 * rank * n * 2**2 + 2
        # floor(q/4)>bound iff q>=4*bound+4. next_prime is strict.
        q = int(next_prime(4 * bound + 3, proof=True))
        assert ZZ(q).is_prime(proof=True) and q // 4 > bound
        assert all(not ZZ(candidate).is_prime(proof=True) for candidate in range(4 * bound + 4, q))
        row = {"tier": tier, "n": n, "rank": rank, "q": str(q), "noise_bound": bound,
               "decoding_threshold": q // 4, "native_plaintext_bits": n,
               "generic_message_search_bits": n, "bits": None}
        if n * rank < 32:
            row["reason"] = "Dimension below supported range of pinned rough estimator; no numerical claim"
        else:
            params = LWE.Parameters(n=n * rank, q=q, Xs=ND.CenteredBinomial(2), Xe=ND.CenteredBinomial(2), m=n * rank)
            attacks = LWE.estimate.rough(params, quiet=True, catch_exceptions=False)
            row["attacks"] = {name: {"log2_rop": float(log(cost["rop"], 2)), "beta": int(cost.get("beta", 0))}
                              for name, cost in attacks.items()}
            row["flattened_lwe_bits"] = min(v["log2_rop"] for v in row["attacks"].values())
            if min(v["beta"] for v in row["attacks"].values()) <= 40:
                row["reason"] = "Estimator beta40 floor; numerical security claim omitted"
            else:
                row["bits"] = row["flattened_lwe_bits"]
        rows.append(row)
    result = {"model_id": "module-lwe-dimension-core-svp-v2", "estimator_commit": COMMIT,
              "archive_sha256": ARCHIVE_SHA256, "sage_version": "10.1",
              "method": "LWE.estimate.rough; ADPS16 classical Core-SVP/GSA, primal uSVP and dual hybrid",
              "parameters": "Each exact flattened dimension n*rank with m=n*rank, full CBD2 secret/error; native uniform n-bit plaintext, full256-bit seeds",
              "prime_verification": "Every q proved prime and minimal among integers with floor(q/4)>2*rank*n*eta^2+eta",
              "correctness": "Fixed perfect honest agreement (delta0) across all profiles, conditional on completed generation and faithful implementation",
              "limitations": ["Not a full module/KEM analysis: ring/module structure and ciphertext information omitted.",
                              "Core-SVP heuristic costs are not measured operations, seconds or security lower bounds.",
                              "Native plaintext enumeration uses at most2^n candidate encryptions; these are not the same cost units as Core-SVP operations, so the two numbers are not silently minimized. KDF output length does not remove this attack.",
                              "Small unsupported/floor profiles intentionally have no numerical estimate."],
              "profiles": rows}
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
