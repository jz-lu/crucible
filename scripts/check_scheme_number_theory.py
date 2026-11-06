"""Deterministic corroboration of the local arguments; not a security proof.

Run with the project environment from the repository root. No DB/network use.
The RSA residue experiment uses small test moduli, not live challenge records.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible import dh_v7 as dh, ec_compressed as ec


def frame(domain, fields):
    return domain + b"".join(len(field).to_bytes(4, "big") + field for field in fields)


def check():
    result = {"format": "crucible-number-theory-argument-checks-v1", "scope": "Algebra/encoding conformance samples, not a formal proof or hardness evidence."}
    for stage in range(1, 51):
        p = dh._profile(f"dh-v7-tier-{stage}")
        maximum = dh.exponent_upper_bound(p)
        for x, r in ((1, maximum), (2, max(1, maximum-1)), (maximum, maximum)):
            public, ciphertext = dh._encode(p, pow(p.g, x, p.p)), dh._encode(p, pow(p.g, r, p.p))
            z = pow(int.from_bytes(public, "big"), r, p.p)
            assert z == pow(int.from_bytes(ciphertext, "big"), x, p.p)
            fields = [p.id.encode("ascii")] + [v.to_bytes(p.width, "big") for v in (p.p, p.q, p.g)] + [public, ciphertext, z.to_bytes(p.width, "big")]
            expected = hashlib.sha256(frame(dh.DOMAIN, fields)).digest()
            assert dh.decapsulate(p.id, x, ciphertext) == expected
        row = ec._profile(f"ec-compressed-v2-tier-{stage}")
        generator = tuple(row["g"])
        for x, r in ((1, row["q"]-1), (2, row["q"]-2)):
            public_point = ec._mul(row["p"], row["a"], generator, x)
            cipher_point = ec._mul(row["p"], row["a"], generator, r)
            shared = ec._mul(row["p"], row["a"], public_point, r)
            assert shared == ec._mul(row["p"], row["a"], cipher_point, x)
            public, ciphertext = ec._encode(row, public_point), ec._encode(row, cipher_point)
            assert ec._decode(row, public) == public_point
            parameters = json.dumps({k: row[k] for k in ("p", "a", "b", "q", "g")}, sort_keys=True, separators=(",", ":")).encode()
            fields = [f"ec-compressed-v2-tier-{stage}".encode(), parameters, public, ciphertext, ec._encode(row, shared)]
            expected = hashlib.sha256(frame(ec.DOMAIN, fields)).digest()
            assert ec.decapsulate(f"ec-compressed-v2-tier-{stage}", ec.PrivateKey(f"ec-compressed-v2-tier-{stage}", x), ciphertext) == expected
    result.update(dh_profiles=50, dh_boundary_pairs=150, ec_profiles=50, ec_boundary_pairs=100)
    residues = 0
    for p, q in ((3, 5), (5, 7), (11, 17), (31, 37)):
        n = p*q; exponent = 65537; phi = (p-1)*(q-1)
        assert math.gcd(exponent, phi) == 1
        d = pow(exponent, -1, phi)
        ciphertexts = [pow(z, exponent, n) for z in range(n)]
        assert len(set(ciphertexts)) == n
        assert all(pow(c, d, n) == z for z, c in enumerate(ciphertexts))
        assert sum(math.gcd(z, n) != 1 for z in range(n)) == p+q-1
        residues += n
    result.update(rsa_small_moduli=4, rsa_exhaustive_residues=residues, passed=True)
    return result


if __name__ == "__main__":
    print(json.dumps(check(), indent=2))
