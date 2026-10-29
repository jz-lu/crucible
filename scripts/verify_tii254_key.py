#!/usr/bin/env python3
"""Independent, small public-artifact checker; does not perform a key-recovery attack.

Checks Saarinen's published TII-254 equivalent key against its public binary code
using coefficient expansion over GF(2), independently of the artifact's Sage
Frobenius-closure/GF(256) row-space implementation. Not a general KEM decoder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.mceliece_candidate_screen import irreducible_binary


class Field:
    def __init__(self, m, modulus):
        if not 2 <= m <= 8 or modulus.bit_length() != m + 1 or not irreducible_binary(modulus):
            raise ValueError("Expected a small, valid binary extension field")
        self.m, self.modulus, self.q = m, modulus, 2**m

    def multiply(self, a, b):
        out = 0
        while b:
            if b & 1:
                out ^= a
            b >>= 1
            a <<= 1
            if a & self.q:
                a ^= self.modulus
        return out

    def inverse(self, a):
        if not a:
            raise ValueError("Division by zero")
        result, power = 1, self.q - 2
        while power:
            if power & 1:
                result = self.multiply(result, a)
            a = self.multiply(a, a)
            power >>= 1
        return result

    @staticmethod
    def trim(p):
        while p and p[-1] == 0:
            p.pop()
        return p

    def remainder(self, a, b):
        a, b = self.trim(a[:]), self.trim(b[:])
        if not b:
            raise ValueError("Polynomial division by zero")
        inverse = self.inverse(b[-1])
        while len(a) >= len(b):
            shift, factor = len(a) - len(b), self.multiply(a[-1], inverse)
            for i, coefficient in enumerate(b):
                a[i + shift] ^= self.multiply(factor, coefficient)
            self.trim(a)
        return a

    def gcd(self, a, b):
        while b:
            a, b = b, self.remainder(a, b)
        return a

    def irreducible(self, polynomial):
        degree = len(polynomial) - 1
        if degree < 2 or polynomial[-1] != 1:
            return False
        x, power = [0, 1], [0, 1]
        for d in range(1, degree + 1):
            for _ in range(self.m):
                square = [0] * (2*len(power) - 1)
                for i, coefficient in enumerate(power):
                    square[2*i] = self.multiply(coefficient, coefficient)
                power = self.remainder(square, polynomial)
            difference = power[:] + [0] * max(0, 2-len(power))
            difference[1] ^= 1
            self.trim(difference)
            if d <= degree // 2 and len(self.gcd(polynomial, difference)) != 1:
                return False
        return power == x

    def evaluate(self, polynomial, x):
        result = 0
        for coefficient in reversed(polynomial):
            result = self.multiply(result, x) ^ coefficient
        return result


def binary_rref(rows, n):
    rows = rows[:]
    pivot = 0
    for column in range(n):
        found = next((i for i in range(pivot, len(rows)) if rows[i] >> column & 1), None)
        if found is None:
            continue
        rows[pivot], rows[found] = rows[found], rows[pivot]
        for i in range(len(rows)):
            if i != pivot and rows[i] >> column & 1:
                rows[i] ^= rows[pivot]
        pivot += 1
    return rows[:pivot]


def verify(public_text, key):
    m, modulus = key["field_degree"], key["field_modulus"]
    support, polynomial = key["support_integers"], key["goppa_coefficients"]
    n, t = len(support), len(polynomial) - 1
    if (m, n, t) != (8, 223, 12):
        raise ValueError("This checker is bounded to the published TII-254 case")
    field = Field(m, modulus)
    if any(type(x) is not int or not 0 <= x < field.q for x in support + polynomial):
        raise ValueError("Noncanonical field element")
    if len(set(support)) != n or not field.irreducible(polynomial):
        raise ValueError("Distinct support and irreducible monic Goppa polynomial required")
    lines = []
    for line in public_text.splitlines():
        if not (line.startswith("[") and line.endswith("]")):
            raise ValueError("Malformed public matrix")
        lines.append([int(v) for v in line[1:-1].replace(",", " ").split()])
    if len(lines) != m*t + 1 or any(len(row) != n for row in lines[:-1]) or len(lines[-1]) != m+1:
        raise ValueError("Incorrect matrix dimensions")
    if any(v not in (0, 1) for row in lines for v in row):
        raise ValueError("Public matrix must be binary")
    if sum(v << i for i, v in enumerate(lines[-1])) != modulus:
        raise ValueError("Field modulus mismatch")
    public_rows = [sum(v << i for i, v in enumerate(row)) for row in lines[:-1]]
    reconstructed = [0] * (m*t)
    for column, x in enumerate(support):
        value = field.inverse(field.evaluate(polynomial, x))
        for degree in range(t):
            for bit in range(m):
                reconstructed[m*degree+bit] |= ((value >> bit) & 1) << column
            value = field.multiply(value, x)
    actual, expected = binary_rref(reconstructed, n), binary_rref(public_rows, n)
    if len(actual) != m*t or len(expected) != m*t or actual != expected:
        raise ValueError("Recovered key does not reproduce the full public binary row space")
    return {"status": "verified-equivalent-public-code", "m": m, "n": n, "t": t,
            "binary_rank": len(actual), "field_irreducible": True, "goppa_irreducible": True,
            "support_distinct": True, "row_space_equal": True,
            "not_verified": ["original secret-key identity", "GPU recovery execution or total attack cost",
                             "attack scaling to full-size McEliece", "a production KEM decoder"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True,
                        help="Directory with public_key.txt and recovered_key.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    public = (args.artifact / "public_key.txt").read_bytes()
    key = (args.artifact / "recovered_key.json").read_bytes()
    result = verify(public.decode("ascii"), json.loads(key))
    result["input_sha256"] = {"public_key.txt": hashlib.sha256(public).hexdigest(),
                              "recovered_key.json": hashlib.sha256(key).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("Verified the supplied equivalent TII-254 key against all 96 public binary rows.")


if __name__ == "__main__":
    main()
