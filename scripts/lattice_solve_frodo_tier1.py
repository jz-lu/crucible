#!/usr/bin/env sage-python
"""Public-input-only LLL/Babai attack for the n=8 Frodo research profile.

Run with Sage: sage -python scripts/lattice_solve_frodo_tier1.py challenge.json
No generation randomness, private key, account credential or server target is used.
"""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from crucible import lattice_frodo as kem


def solve(public):
    from sage.all import ZZ, QQ, matrix, vector, block_matrix, identity_matrix, zero_matrix
    if public.get("scheme_id") != kem.SCHEME_ID or public.get("parameters", {}).get("n") != 8:
        raise ValueError("This example attacks only the n=8 research profile")
    n = 8
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    kem._bytes(pk, 136, "Public key")
    kem._bytes(ct, 272, "Ciphertext")
    a = matrix(ZZ, kem.public_matrix(n, pk[:16]))
    # Lattice vectors (s, A*s+q*z); closest to (0,b) reveals (s,-e).
    basis = block_matrix([[identity_matrix(ZZ, n), a.transpose()],
                          [zero_matrix(ZZ, n), kem.Q*identity_matrix(ZZ, n)]])
    reduced = basis.LLL()
    orthogonal, _ = reduced.gram_schmidt()
    b = kem.unpack(pk[16:], n*8)
    columns = []
    for j in range(8):
        target = vector(ZZ, [0]*n+b[j::8])
        remainder = vector(QQ, target)
        for i in reversed(range(2*n)):
            coefficient = (remainder.dot_product(orthogonal[i])/orthogonal[i].dot_product(orthogonal[i])).round()
            remainder -= coefficient*reduced[i]
        closest = target-remainder
        s = list(closest[:n])
        residual = [(int(b[i*8+j])-sum(int(a[i,k])*int(s[k]) for k in range(n))) % kem.Q for i in range(n)]
        if any(abs(x) > 12 for x in s) or any(min(x, kem.Q-x) > 12 for x in residual):
            raise RuntimeError("This Babai run did not recover a bounded secret; no result claimed")
        columns.append(s)
    bp, c = kem.unpack(ct[:15*n], n*8), kem.unpack(ct[15*n:15*n+120], 64)
    noise_message = [(c[i*8+j]-sum(int(x)*int(y) for x,y in zip(bp[i*n:(i+1)*n], columns[j]))) % kem.Q
                     for i in range(8) for j in range(8)]
    mu = kem.decode(noise_message)
    reconstructed, k = kem._encrypt(n, pk, mu, ct[-32:])
    if reconstructed != ct:
        raise RuntimeError("Recovered message did not reproduce the ciphertext")
    return hashlib.shake_128(ct+k).digest(16)


if __name__ == "__main__":
    value = json.loads(Path(sys.argv[1]).read_text())
    public = value.get("challenge", value).get("public", value)
    print(solve(public).hex())
