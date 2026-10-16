#!/usr/bin/env sage-python
"""Independent Sage quotient-ring conformance reference; no adapter imports.

These reproducible research vectors are not NIST ML-KEM vectors. All eleven
profiles and native plaintext lengths are covered, using independent polynomial
arithmetic, bit sampling, encoding and honest-message recovery.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sage.all import GF, PolynomialRing


def vector(n, rank, q, seed):
    polynomial_ring = PolynomialRing(GF(q), "x")
    x = polynomial_ring.gen()
    ring = polynomial_ring.quotient(x**n + 1)
    binding = n.to_bytes(2, "little") + bytes([rank]) + q.to_bytes(4, "little")
    width = (q.bit_length() + 7) // 8

    def coefficients(poly):
        values = [int(a) for a in poly.lift().list()]
        return values + [0] * (n - len(values))

    def serialize(polys):
        return b"".join(value.to_bytes(width, "little") for poly in polys for value in coefficients(poly))

    def noise(sigma, nonce):
        domain = b"Crucible-Module-LWE-v2/noise" + binding + sigma + bytes([nonce])
        data = hashlib.shake_256(domain).digest(n // 2)
        bits = [(value >> j) & 1 for value in data for j in range(8)]
        return ring([bits[4*i] + bits[4*i+1] - bits[4*i+2] - bits[4*i+3] for i in range(n)])

    def matrix(rho, row, column):
        domain = b"Crucible-Module-LWE-v2/A" + binding + rho + bytes([row, column])
        data = hashlib.shake_128(domain).digest(width * n * 32)
        values = []
        for offset in range(0, len(data), width):
            value = int.from_bytes(data[offset:offset + width], "little") % (2**q.bit_length())
            if value < q:
                values.append(value)
            if len(values) == n:
                return ring(values)
        raise RuntimeError("Matrix sampling resource bound exceeded")

    key_coins = hashlib.shake_256(b"key-v2" + seed.to_bytes(2, "little")).digest(64)
    message = hashlib.shake_256(b"message-v2" + seed.to_bytes(2, "little")).digest(n // 8)
    rho_sigma = hashlib.sha3_512(b"Crucible-Module-LWE-v2/keygen" + binding + key_coins[:32]).digest()
    rho, sigma = rho_sigma[:32], rho_sigma[32:]
    a = [[matrix(rho, i, j) for j in range(rank)] for i in range(rank)]
    s = [noise(sigma, i) for i in range(rank)]
    e = [noise(sigma, rank + i) for i in range(rank)]
    t = [sum((a[i][j] * s[j] for j in range(rank)), ring.zero()) + e[i] for i in range(rank)]
    pk = b"MLD2" + binding + rho + serialize(t)
    centered = [value if value <= q//2 else value-q for poly in s for value in coefficients(poly)]
    sk = key_coins[32:] + bytes(v % 256 for v in centered) + pk + hashlib.sha3_256(pk).digest()
    derived = hashlib.sha3_512(b"Crucible-Module-LWE-v2/encap" + binding + message + hashlib.sha3_256(pk).digest()).digest()
    coins = derived[32:]
    r = [noise(coins, i) for i in range(rank)]
    e1 = [noise(coins, rank + i) for i in range(rank)]
    e2 = noise(coins, 2 * rank)
    u = [sum((a[j][i] * r[j] for j in range(rank)), ring.zero()) + e1[i] for i in range(rank)]
    bits = [(byte >> j) & 1 for byte in message for j in range(8)]
    v = sum((t[i] * r[i] for i in range(rank)), ring.zero()) + e2 + ring([bit * ((q + 1)//2) for bit in bits])
    ct = serialize(u + [v])
    residual = v - sum((s[j] * u[j] for j in range(rank)), ring.zero())
    # Independent nearest-center decoder, using integer circular distances.
    recovered_bits = []
    for value in coefficients(residual):
        distance0 = min(value, q-value)
        difference1 = abs(value-(q+1)//2)
        distance1 = min(difference1, q-difference1)
        recovered_bits.append(int(distance1 < distance0))
    recovered = bytes(sum(recovered_bits[8*i+j] << j for j in range(8)) for i in range(n//8))
    assert recovered == message
    return {"n": n, "rank": rank, "q": str(q), "seed": seed, "key_randomness": key_coins.hex(),
            "encapsulation_message": message.hex(), "pk_sha256": hashlib.sha256(pk).hexdigest(),
            "sk_sha256": hashlib.sha256(sk).hexdigest(), "ct_sha256": hashlib.sha256(ct).hexdigest(),
            "shared_secret": derived[:32].hex(), "independent_honest_message_recovered": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profiles = json.loads(args.calibration.read_text())["profiles"]
    values = [vector(p["n"], p["rank"], int(p["q"]), i) for i, p in enumerate(profiles)]
    result = {"format": "crucible-independent-research-kat-v1",
              "authority": "Independent SageMath10.1 finite-field quotient-ring implementation; not NIST vectors",
              "reference_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "vectors": values}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
