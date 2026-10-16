"""Independent offline Sage validation; run sage -python scripts/check_ec_parameters.py."""
from pathlib import Path
import hashlib
import json
import sys
from sage.all import GF, EllipticCurve, Integer, pari

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible import ec_research as ec

for row in ec._parameters():
    p, q = row["p"], row["q"]
    assert Integer(p).is_prime(proof=True) and Integer(q).is_prime(proof=True)
    curve = EllipticCurve(GF(p), [row["a"], row["b"]])
    assert int(curve.cardinality()) == q
    generator = curve(row["g"])
    for scalar in (1, 2, 17, q-1):
        point = scalar*generator
        assert ec._mul(p, row["a"], tuple(row["g"]), scalar) == (int(point[0]), int(point[1]))
    print(f"Tier {row['tier']}: Sage exact primality, group order and scalar arithmetic verified", flush=True)
print("parameter_sha256", hashlib.sha256(ec.PARAMETERS.read_bytes()).hexdigest())
