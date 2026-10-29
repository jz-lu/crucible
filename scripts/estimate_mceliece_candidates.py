#!/usr/bin/env python3
"""Reproduce a small, explicitly incomplete generic-ISD candidate comparison.

Run only in a separate research environment with the pinned estimator checkout.
This script is not imported by the application and installs nothing.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

from mceliece_candidate_screen import Candidate, ESTIMATOR_COMMIT, control_ray


def verify_checkout(path):
    revision = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if revision != ESTIMATOR_COMMIT:
        raise ValueError(f"Expected estimator commit {ESTIMATOR_COMMIT}")
    status = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True)
    if status.strip():
        raise ValueError("Estimator checkout must be clean, including untracked files")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--estimator-checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify_checkout(args.estimator_checkout)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.estimator_checkout.resolve()))
    from cryptographic_estimators.SDEstimator import SDProblem, Prange, Stern, MayOzerov

    contexts = [
        {"id": "constant-access-unbounded-memory", "memory_access": 0, "memory_bound_log2_bits": None},
        {"id": "constant-access-8GiB", "memory_access": 0, "memory_bound_log2_bits": 36},
        {"id": "cube-root-access-unbounded-memory", "memory_access": 3, "memory_bound_log2_bits": None},
    ]
    selected = sorted(set(control_ray()) | {Candidate(12, 840, 64), Candidate(12, 1680, 64), Candidate(7, 120, 4)})
    result = {
        "format": "crucible-mceliece-partial-isd-screen-v1",
        "source": {"repository": "https://github.com/Crypto-TII/CryptographicEstimators", "commit": ESTIMATOR_COMMIT},
        "python": platform.python_version(),
        "dependencies": {name: importlib.metadata.version(name) for name in
                         ("scipy", "numpy", "sympy", "prettytable", "python-flint", "pytest")},
        "status": "partial-generic-model-not-security-bits-or-live-selection",
        "game": "one planted weight-t solution to one syndrome; one public key and ciphertext",
        "model": {"nsolutions_log2": 0, "bit_complexities": 1, "algorithms": ["Prange", "Stern", "MayOzerov"],
                  "time_units": "estimator modeled bit operations", "memory_units": "estimator modeled bits",
                  "scope": "generic decoding only; optimizer uses upstream default finite ranges/heuristics",
                  "not_covered": ["remaining upstream ISD algorithms", "progressive sieving ISD (2026/633)",
                                  "Goppa structural recovery (2026/1630,1984,1986)", "quantum or many-target attacks",
                                  "full-size empirical calibration", "complete cryptanalytic lower bounds"]},
        "contexts": contexts, "rows": [],
    }
    for c in selected:
        row = {**c.row(), "estimates": []}
        for context in contexts:
            problem = SDProblem(c.n, c.k, c.t, nsolutions=0,
                                memory_bound=context["memory_bound_log2_bits"] or math.inf)
            for algorithm in (Prange, Stern, MayOzerov):
                estimator = algorithm(problem, bit_complexities=1, memory_access=context["memory_access"])
                time = estimator.time_complexity()
                memory = estimator.memory_complexity()
                finite = math.isfinite(time) and math.isfinite(memory)
                row["estimates"].append({"context": context["id"], "algorithm": algorithm.__name__,
                                         "status": "estimated" if finite else "no finite result",
                                         "log2_bit_operations": time if finite else None,
                                         "log2_memory_bits": memory if finite else None,
                                         "parameters": estimator.optimal_parameters() if finite else None})
        result["rows"].append(row)
        print(f"Estimated {c.key}; structural coverage remains unresolved.", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
