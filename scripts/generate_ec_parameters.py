"""Offline Sage 10.1 generator: public prime-order EC research parameters.

Run with sage -python. No Sage dependency is needed to serve challenges.
The seed/search is public; this does not claim uniform sampling over curves.
"""
from pathlib import Path
import hashlib
import json
import math
import sys
from sage.all import Integer, pari, factor

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "crucible" / "ec_parameters.json"


def lucas(n):
    n = int(n)
    if n < 65536:
        assert Integer(n).is_prime(proof=True)
        return {"n": n}
    factors = [(int(p), int(e)) for p, e in factor(Integer(n - 1), proof=True)]
    for a in range(2, 10000):
        if pow(a, n - 1, n) == 1 and all(math.gcd(pow(a, (n - 1) // p, n) - 1, n) == 1 for p, _ in factors):
            return {"n": n, "a": a, "factors": [[lucas(p), e] for p, e in factors]}
    raise RuntimeError("No small Lucas witness")


def certificate(n):
    n = int(n)
    if n < 2**64:
        return {"lucas": lucas(n)}
    raw = pari(n).primecert()
    assert int(pari.primecertisvalid(raw)) == 1
    chain = [[int(row[0]), int(row[1]), int(row[2]), int(row[3]), [int(row[4][0]), int(row[4][1])]] for row in raw]
    last = chain[-1]
    terminal = (last[0] + 1 - last[1]) // last[2]
    return {"ecpp": chain, "terminal": lucas(terminal)}


def build(tier):
    # Ceiling uses integers; a p of this bit length places rho at or above
    # the target (subject to q, checked explicitly below).
    twice_target = 16 + 240 * (tier - 1) / 49
    field_bits = math.ceil(twice_target) + 1
    seed = f"crucible/ec-research-v1/setup/tier-{tier}".encode()
    p = (1 << (field_bits - 1)) + int.from_bytes(hashlib.shake_256(seed + b"/field").digest(40), "big") % (1 << (field_bits - 2))
    p = p - p % 4 + 3
    while not Integer(p).is_prime(proof=True):
        p += 4
    for attempt in range(100000):
        b = int.from_bytes(hashlib.shake_256(seed + b"/curve/" + str(attempt).encode()).digest(40), "big") % p
        if b == 0 or (27 * b * b - 108) % p == 0:
            continue
        curve = pari.ellinit([-3, b], p)
        # SEA early-aborts on a small factor of the group order.
        q = int(pari.ellsea(curve, 1)) if field_bits > 30 else int(pari.ellcard(curve))
        if q <= 0 or q == p or not Integer(q).is_prime(proof=True) or math.log2(q) < twice_target:
            continue
        if any(pow(p, k, q) == 1 for k in range(1, 101)):
            continue
        for x in range(p):
            rhs = (x*x*x - 3*x + b) % p
            y = pow(rhs, (p + 1)//4, p)
            if y*y % p == rhs and y != 0:
                y = min(y, p-y)
                break
        assert list(pari.ellmul(curve, [x, y], q)) == [0]
        return {"tier": tier, "p": p, "a": p-3, "b": b, "q": q, "g": [x, y],
                "attempt": attempt, "p_certificate": certificate(p), "q_certificate": certificate(q)}
    raise RuntimeError("Curve search exhausted")


if __name__ == "__main__":
    # Keep completed public setup after interruptions; verify independently.
    records = json.loads(OUT.read_text()) if OUT.exists() else []
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    for tier in range(len(records)+1, target+1):
        row = build(tier)
        records.append(row)
        OUT.write_text(json.dumps(records, indent=2) + "\n")
        print(f"Tier {tier}: field {row['p'].bit_length()} bits, rho {math.log2(row['q'])/2:.3f}, curve attempts {row['attempt']+1}", flush=True)
