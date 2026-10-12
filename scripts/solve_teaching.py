#!/usr/bin/env python3
"""Offline public-input BSGS example for Crucible's deliberately weak teaching KEM.

Usage: python3 scripts/solve_teaching.py downloaded-challenge.json
Prints the full candidate key. Makes no submission and uses no private server data.
This example is deliberately transparent; supplying it is not a security claim.
"""
import hashlib
import json
import math
import sys


def recover(document):
    public = document["challenge"]["public"] if "challenge" in document else document
    if public["scheme_id"] != "teaching-dh-kem" or public["version"] not in {"1", "2"}:
        raise ValueError("This example only implements Teaching DH-KEM versions 1 and 2.")
    p, q, g = (int(public["parameters"][name], 16) for name in ("p", "q", "g"))
    if q.bit_length() > 36:
        raise ValueError("Parameter exceeds this teaching example's resource limit.")
    pk, c = (int(public[name], 16) for name in ("public_key", "ciphertext"))
    width = public["encoding"]["group_element_bytes"]
    step = math.isqrt(q) + 1
    baby, value = {}, 1
    for j in range(step):
        baby.setdefault(value, j)
        value = value * g % p
    inverse = pow(pow(g, step, p), -1, p)
    giant = pk
    exponent = None
    for i in range(step + 1):
        if giant in baby:
            x = i * step + baby[giant]
            if 0 < x < q and pow(g, x, p) == pk:
                exponent = x
                break
        giant = giant * inverse % p
    if exponent is None:
        raise ValueError("No discrete logarithm found in this profile.")
    shared = pow(c, exponent, p)
    fields = [public["profile_id"].encode("ascii")] + [value.to_bytes(width, "big") for value in (p, q, g, pk, c, shared)]
    data = bytes.fromhex(public["kdf"]["domain_hex"]) + b"".join(len(value).to_bytes(4, "big") + value for value in fields)
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1]) as source:
        print(recover(json.load(source)))
