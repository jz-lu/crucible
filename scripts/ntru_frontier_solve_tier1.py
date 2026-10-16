#!/usr/bin/env python3
"""Standalone bounded public-only NTRU v3 input-enumeration demonstration.

Usage: python solve.py challenge.json
No reference module execution, account access, network, private key or secret
randomness is used. The original2020 mixed-radix/hash encoding is reproduced.
"""
from hashlib import sha512
from itertools import combinations, product


def center(x, q):
    return (x + (q - 1) // 2) % q - (q - 1) // 2


def multiply(a, b, q):
    p = len(a)
    if p != len(b) or p < 2:
        raise ValueError("Mismatched ring polynomials")
    out = [0] * (2 * p - 1)
    for i, u in enumerate(a):
        for j, v in enumerate(b):
            out[i + j] += u * v
    for i in range(2 * p - 2, p - 1, -1):
        out[i - p] += out[i]
        out[i - p + 1] += out[i]
    return [center(x, q) for x in out[:p]]


def encode(values, moduli):
    if len(values) != len(moduli) or not values:
        raise ValueError("Invalid encoding dimensions")
    if any(type(r) is not int or not 0 <= r < m < 16384 for r, m in zip(values, moduli)):
        raise ValueError("Invalid mixed-radix coefficient")
    if len(values) == 1:
        r, m, out = values[0], moduli[0], bytearray()
        while m > 1:
            out.append(r & 255)
            r >>= 8
            m = (m + 255) >> 8
        return bytes(out)
    out, next_values, next_moduli = bytearray(), [], []
    for i in range(0, len(values) - 1, 2):
        r, m = values[i] + values[i + 1] * moduli[i], moduli[i] * moduli[i + 1]
        while m >= 16384:
            out.append(r & 255)
            r >>= 8
            m = (m + 255) >> 8
        next_values.append(r)
        next_moduli.append(m)
    if len(values) % 2:
        next_values.append(values[-1])
        next_moduli.append(moduli[-1])
    return bytes(out) + encode(next_values, next_moduli)


def encoded_length(p, modulus):
    return len(encode([0] * p, [modulus] * p))


def decode(data, moduli):
    """The total decoder, including upstream's reduction of invalid encodings."""
    def step(offset, ms):
        if len(ms) == 1:
            width = 0 if ms[0] == 1 else 1 if ms[0] <= 256 else 2
            if offset + width > len(data):
                raise ValueError("Short mixed-radix encoding")
            return [int.from_bytes(data[offset:offset + width], "little") % ms[0]], offset + width
        bottoms, next_ms = [], []
        for i in range(0, len(ms) - 1, 2):
            m = ms[i] * ms[i + 1]
            width = 2 if m > 256 * 16383 else 1 if m >= 16384 else 0
            if offset + width > len(data):
                raise ValueError("Short mixed-radix encoding")
            bottoms.append((int.from_bytes(data[offset:offset + width], "little"), 256**width))
            offset += width
            for _ in range(width):
                m = (m + 255) >> 8
            next_ms.append(m)
        if len(ms) % 2:
            next_ms.append(ms[-1])
        upper, offset = step(offset, next_ms)
        out = []
        for j, (bottom, scale) in enumerate(bottoms):
            value = bottom + scale * upper[j]
            out.extend((value % ms[2 * j], (value // ms[2 * j]) % ms[2 * j + 1]))
        if len(ms) % 2:
            out.append(upper[-1])
        return out, offset
    result, consumed = step(0, moduli)
    if consumed != len(data):
        raise ValueError("Wrong mixed-radix encoding length")
    return result


def small_encode(values):
    if any(x not in (-1, 0, 1) for x in values):
        raise ValueError("Not a small polynomial")
    out = bytearray((len(values) + 3) // 4)
    for i, x in enumerate(values):
        out[i // 4] |= (x + 1) << (2 * (i % 4))
    return bytes(out)


def hash_prefix(domain, data):
    return sha512(bytes([domain]) + data).digest()[:32]


def hide(p, q, w, public_key, r):
    if len(r) != p or sum(x != 0 for x in r) != w or any(x not in (-1, 0, 1) for x in r):
        raise ValueError("Candidate must be a full fixed-weight polynomial")
    h = [r0 - (q - 1) // 2 for r0 in decode(public_key, [q] * p)]
    c = [x - center(x, 3) for x in multiply(h, r, q)]
    raw = encode([(x + (q - 1) // 2) // 3 for x in c], [(q + 2) // 3] * p)
    encoded = small_encode(r)
    ciphertext = raw + hash_prefix(2, hash_prefix(3, encoded) + hash_prefix(4, public_key))
    return ciphertext, hash_prefix(1, hash_prefix(3, encoded) + ciphertext)


def recover_low_tier(public, *, max_candidates=100000):
    """Public-only exhaustive encapsulation-input attack; intentionally bounded."""
    from math import comb
    p, q, w = (public["parameters"][k] for k in ("p", "q", "w"))
    if comb(p, w) * 2**w > max_candidates:
        raise ValueError("Public exhaustive attack allowance exceeded")
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    for support in combinations(range(p), w):
        for signs in product((-1, 1), repeat=w):
            r = [0] * p
            for position, sign in zip(support, signs):
                r[position] = sign
            candidate, key = hide(p, q, w, pk, r)
            if candidate == ct:
                return key
    raise ValueError("No candidate matched the complete public ciphertext")


def solve(public):
    if public.get("scheme_id") != "sntrup" or public.get("version") != "3":
        raise ValueError("This example expects the NTRU frontier release3")
    if not isinstance(public.get("parameters"),dict):
        raise ValueError("Missing exact public parameters")
    p,q,w=(public["parameters"].get(k) for k in ("p","q","w"))
    if not all(type(value) is int for value in (p,q,w)) or not (0<w<=p<=17 and 2<q<16384):
        raise ValueError("Parameters exceed this example's explicit small-instance allowance")
    return recover_low_tier(public)


if __name__ == "__main__":
    import json,sys
    from pathlib import Path
    if len(sys.argv)!=2:
        raise SystemExit("Usage: python solve.py challenge.json")
    value=json.loads(Path(sys.argv[1]).read_text())
    public=value.get("challenge",value).get("public",value.get("challenge",value))
    print(solve(public).hex())
