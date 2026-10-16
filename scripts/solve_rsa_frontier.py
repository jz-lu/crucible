#!/usr/bin/env python3
"""Offline factoring example for Crucible's tiny RSA encapsulation challenges.

Usage: python3 scripts/solve_rsa.py downloaded-challenge.json
Prints a candidate key using public inputs only. Does not submit or execute code
from the JSON file. Limited to the 20..36-bit modulus sizes (early tiers only).
"""
import hashlib
import json
import math
import sys


def recover(document):
    public = document["challenge"]["public"] if "challenge" in document else document
    if public["scheme_id"] != "rsa-kem" or public["version"] not in {"1", "2", "3", "4"}:
        raise ValueError("This example implements Crucible RSA releases 1 through 4.")
    modulus, exponent = (int(public["parameters"][name], 16) for name in ("N", "e"))
    if not 20 <= modulus.bit_length() <= 36 or exponent != 65537:
        raise ValueError("Parameters exceed this example's resource limit.")
    width = (modulus.bit_length() + 7) // 8
    if public["encoding"]["integer_bytes"] != width:
        raise ValueError("Unexpected integer encoding")
    divisor = next((candidate for candidate in range(3, math.isqrt(modulus) + 1, 2)
                    if modulus % candidate == 0), None)
    if divisor is None:
        raise ValueError("No factor found")
    other = modulus // divisor
    private = pow(exponent, -1, (divisor - 1) * (other - 1))
    ciphertext = int(public["ciphertext"], 16)
    secret_integer = pow(ciphertext, private, modulus)
    fields = [public["profile_id"].encode("ascii")]
    fields += [modulus.to_bytes(width, "big"), exponent.to_bytes(3 if public["version"] == "4" else width, "big"),
               ciphertext.to_bytes(width, "big"), secret_integer.to_bytes(width, "big")]
    transcript = bytes.fromhex(public["kdf"]["domain_hex"])
    transcript += b"".join(len(field).to_bytes(4, "big") + field for field in fields)
    return hashlib.sha256(transcript).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1]) as source:
        print(recover(json.load(source)))
