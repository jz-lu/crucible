"""Offline deterministic DH parameter generation; never imported by the server.

Run ``python scripts/generate_dh_v4_primes.py > dh-v4-primes.json`` to recreate
the release-4 extension. Every result carries exact Pocklington certificates;
the small-prime sieve only discards proven composites. No probabilistic
primality test is used. The first certified pair in the published odd-r scan
is selected. At most two worker processes are used.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json


_SMALL_PRIMES = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53,
                 59, 61, 67, 71, 73, 79, 83, 89, 97)


def generate_pair(bits: int) -> dict[str, int]:
    if type(bits) is not int or not 37 <= bits <= 300:
        raise ValueError("The certificate ladder covers integer bit lengths 37..300")
    exponent = (bits + 1) // 2
    factor = 1 << exponent
    first_r = (1 << (bits - exponent - 1)) + 1
    r = first_r
    while True:
        q = r * factor + 1
        p = 2 * q + 1
        if q.bit_length() != bits:
            raise ValueError(f"No certified pair found within the {bits}-bit range")
        if all(q % prime and p % prime for prime in _SMALL_PRIMES):
            if pow(3, (q - 1) // 2, q) == q - 1 and pow(2, p - 1, p) == 1:
                # Known factor 2^exponent of q-1 exceeds sqrt(q); after q
                # is proved prime, q divides p-1 and exceeds sqrt(p).
                assert factor * factor > q and q * q > p and p % 3
                return {"bits": bits, "p": p, "q": q, "r": r,
                        "odd_candidates": (r - first_r) // 2 + 1}
        r += 2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=129)
    parser.add_argument("--stop", type=int, default=300)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if not 37 <= args.start <= args.stop <= 300:
        parser.error("Use 37 <= start <= stop <= 300")
    sizes = range(args.start, args.stop + 1)
    if args.workers == 1:
        records = [generate_pair(bits) for bits in sizes]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            records = list(pool.map(generate_pair, sizes))
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
