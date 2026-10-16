#!/usr/bin/env python3
"""Exact finite-field experiments for a REJECTED network/subspace KEM idea.

Standard library only; deterministic random.Random is for reproducibility, not
cryptographic use. The public-input attack deliberately receives no secret word,
secret matrix, setup conjugator, or random-number generator used by the KEM.
Historical work completed before the user requested construction-only research;
this script is not part of the revised candidate's reproduction commands.
Run from the repository root: python3 scripts/kem_discovery_networks.py --help
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import platform
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path


Matrix = list[list[int]]


def eye(n: int) -> Matrix:
    return [[int(i == j) for j in range(n)] for i in range(n)]


def transpose(a: Matrix) -> Matrix:
    return [list(row) for row in zip(*a)]


def mul(a: Matrix, b: Matrix, p: int) -> Matrix:
    bt = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) % p for col in bt] for row in a]


def rref(a: Matrix, p: int) -> tuple[Matrix, list[int]]:
    out = [[x % p for x in row] for row in a]
    if not out:
        return out, []
    pivots = []
    for j in range(len(out[0])):
        k = len(pivots)
        pivot = next((i for i in range(k, len(out)) if out[i][j]), None)
        if pivot is None:
            continue
        out[k], out[pivot] = out[pivot], out[k]
        scale = pow(out[k][j], -1, p)
        out[k] = [scale * x % p for x in out[k]]
        for i in range(len(out)):
            if i != k and out[i][j]:
                scale = out[i][j]
                out[i] = [(x - scale * y) % p for x, y in zip(out[i], out[k])]
        pivots.append(j)
        if len(pivots) == len(out):
            break
    return out, pivots


def nullspace(a: Matrix, nvars: int, p: int) -> Matrix:
    reduced, pivots = rref(a, p)
    pivot_set = set(pivots)
    basis = []
    for free in range(nvars):
        if free in pivot_set:
            continue
        v = [0] * nvars
        v[free] = 1
        for i, pivot in enumerate(pivots):
            v[pivot] = -reduced[i][free] % p
        basis.append(v)
    return basis


def inverse(a: Matrix, p: int) -> Matrix:
    n = len(a)
    reduced, pivots = rref([row + unit for row, unit in zip(a, eye(n))], p)
    if pivots[:n] != list(range(n)):
        raise ValueError("singular matrix")
    return [row[n:] for row in reduced]


def canonical(u: Matrix, p: int) -> Matrix:
    """Canonical column basis: transpose of the nonzero rows of RREF(U^T)."""
    reduced, pivots = rref(transpose(u), p)
    if not pivots:
        return [[] for _ in u]
    return transpose(reduced[:len(pivots)])


def rank(a: Matrix, p: int) -> int:
    return len(rref(a, p)[1])


def linear_combination(coeffs: list[int], basis: list[Matrix], p: int) -> Matrix:
    d = len(basis[0])
    return [[sum(c * m[i][j] for c, m in zip(coeffs, basis)) % p
             for j in range(d)] for i in range(d)]


def symmetric(n: int, p: int, rng: random.Random) -> Matrix:
    a = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            a[i][j] = a[j][i] = rng.randrange(p)
    return a


def shear(v: list[int], t: int, upper: bool, p: int) -> Matrix:
    n = len(v)
    out = eye(2 * n)
    for i in range(n):
        for j in range(n):
            out[i if upper else n + i][n + j if upper else j] = t * v[i] * v[j] % p
    return out


def power_shear(g: Matrix, t: int, p: int) -> Matrix:
    """g = I + N, N^2 = 0, so g^t = I + tN, including after conjugation."""
    return [[(int(i == j) + t * (x - int(i == j))) % p
             for j, x in enumerate(row)] for i, row in enumerate(g)]


def symplectic(a: Matrix, p: int) -> bool:
    d = len(a)
    n = d // 2
    form = [[0] * d for _ in range(d)]
    for i in range(n):
        form[i][n + i] = 1
        form[n + i][i] = p - 1
    return mul(mul(transpose(a), form, p), a, p) == form


def isotropic(u: Matrix, p: int) -> bool:
    n = len(u) // 2
    pairing = mul(transpose(u[:n]), u[n:], p)
    return pairing == transpose(pairing)


@dataclass(frozen=True)
class PublicParameters:
    p: int
    n: int
    word_length: int
    base: Matrix
    alice_generators: list[Matrix]
    bob_generators: list[Matrix]

    def identifier(self) -> bytes:
        obj = dict(p=self.p, n=self.n, word_length=self.word_length, base=self.base,
                   alice_generators=self.alice_generators, bob_generators=self.bob_generators)
        encoded = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).digest()


def setup(n: int, p: int, word_length: int, rng: random.Random) -> PublicParameters:
    """Public conjugation mixes the coordinates; no setup trapdoor is retained."""
    conjugator = eye(2 * n)
    for _ in range(4 * n):
        v = [rng.randrange(p) for _ in range(n)]
        step = shear(v, rng.randrange(p), bool(rng.randrange(2)), p)
        conjugator = mul(step, conjugator, p)
    inv = inverse(conjugator, p)
    families: list[list[Matrix]] = [[], []]
    for i in range(n):
        v = [int(i == j) for j in range(n)]
        for upper in (False, True):
            g = mul(mul(conjugator, shear(v, 1, upper, p), p), inv, p)
            families[int(i >= n // 2)].append(g)
    base = canonical(eye(n) + symmetric(n, p, rng), p)
    params = PublicParameters(p, n, word_length, base, families[0], families[1])
    assert all(symplectic(g, p) for family in families for g in family)
    assert all(mul(a, b, p) == mul(b, a, p) for a in families[0] for b in families[1])
    assert all(any(mul(a, b, p) != mul(b, a, p) for a in family for b in family)
               for family in families)
    return params


def sample_word(generators: list[Matrix], length: int, p: int, rng: random.Random) -> Matrix:
    out = eye(len(generators[0]))
    for _ in range(length):
        g = power_shear(rng.choice(generators), rng.randrange(p), p)
        out = mul(g, out, p)
    return out


def encode(u: Matrix, params: PublicParameters) -> bytes:
    width = (params.p.bit_length() + 7) // 8
    return b"".join(x.to_bytes(width, "big") for row in transpose(canonical(u, params.p)) for x in row)


def decode(data: bytes, params: PublicParameters) -> Matrix:
    n, p = params.n, params.p
    width = (p.bit_length() + 7) // 8
    if len(data) != 2 * n * n * width:
        raise ValueError("wrong encoding length")
    values = [int.from_bytes(data[i:i + width], "big") for i in range(0, len(data), width)]
    if any(x >= p for x in values):
        raise ValueError("noncanonical field element")
    u = transpose([values[i:i + 2 * n] for i in range(0, len(values), 2 * n)])
    if rank(u, p) != n or canonical(u, p) != u or not isotropic(u, p):
        raise ValueError("not a canonical Lagrangian subspace")
    return u


def derive(params: PublicParameters, pk: bytes, ct: bytes, shared: Matrix) -> bytes:
    return hashlib.shake_256(b"crucible-rejected-network-subspace-v1\x00" + params.identifier()
                            + pk + ct + encode(shared, params)).digest(32)


def keygen(params: PublicParameters, rng: random.Random) -> tuple[bytes, Matrix]:
    a = sample_word(params.alice_generators, params.word_length, params.p, rng)
    return encode(mul(a, params.base, params.p), params), a


def encaps(params: PublicParameters, pk: bytes, rng: random.Random) -> tuple[bytes, bytes]:
    ua = decode(pk, params)
    b = sample_word(params.bob_generators, params.word_length, params.p, rng)
    ct = encode(mul(b, params.base, params.p), params)
    return ct, derive(params, pk, ct, mul(b, ua, params.p))


def decaps(params: PublicParameters, pk: bytes, a: Matrix, ct: bytes) -> bytes | None:
    try:
        ub = decode(ct, params)
    except ValueError:
        return None
    return derive(params, pk, ct, mul(a, ub, params.p))


def centralizer(generators: list[Matrix], p: int) -> list[Matrix]:
    """Solve XG = GX. Unknowns are the d^2 entries of X, in row order."""
    d = len(generators[0])
    equations = []
    for g in generators:
        for i in range(d):
            for j in range(d):
                row = [0] * (d * d)
                for k in range(d):
                    row[i * d + k] += g[k][j]
                    row[k * d + j] -= g[i][k]
                row = [x % p for x in row]
                if any(row):
                    equations.append(row)
    return [[v[i:i + d] for i in range(0, d * d, d)]
            for v in nullspace(equations, d * d, p)]


def witness_space(base: Matrix, ua: Matrix, commutant: list[Matrix], p: int) -> list[Matrix]:
    """Find all centralizer elements X satisfying X U0 contained in UA."""
    annihilator = nullspace(transpose(ua), len(ua), p)
    images = [mul(mul(annihilator, c, p), base, p) for c in commutant]
    equations = [[image[i][j] for image in images]
                 for i in range(len(annihilator)) for j in range(len(base[0]))]
    coefficients = nullspace(equations, len(commutant), p)
    return [linear_combination(v, commutant, p) for v in coefficients]


def recover_shared(witnesses: list[Matrix], ub: Matrix, p: int) -> Matrix:
    """Sum all X_i UB. No search for an invertible X_i is necessary."""
    if not witnesses:
        raise ValueError("empty witness space")
    images = [mul(w, ub, p) for w in witnesses]
    joined = [list(itertools.chain.from_iterable(image[i] for image in images))
              for i in range(len(ub))]
    return canonical(joined, p)


def recover_key(params: PublicParameters, pk: bytes, ct: bytes,
                witnesses: list[Matrix]) -> bytes:
    shared = recover_shared(witnesses, decode(ct, params), params.p)
    if len(shared[0]) != params.n:
        raise ValueError("public transcript does not meet the attack's hypotheses")
    return derive(params, pk, ct, shared)


def network_checks(rng: random.Random) -> dict:
    # Exhaust all nonzero three-edge stars over F_7, retaining singular counts.
    p = 7
    valid = singular = 0
    for a, b, c in itertools.product(range(1, p), repeat=3):
        weights = [a, b, c]
        s = sum(weights) % p
        if s == 0:
            singular += 1
            continue
        inv = pow(s, -1, p)
        response = [[(int(i == j) * weights[i] - weights[i] * weights[j] * inv) % p
                     for j in range(3)] for i in range(3)]
        triangle = [[0] * 3 for _ in range(3)]
        for i in range(3):
            for j in range(i + 1, 3):
                w = weights[i] * weights[j] * inv % p
                triangle[i][i] = (triangle[i][i] + w) % p
                triangle[j][j] = (triangle[j][j] + w) % p
                triangle[i][j] = triangle[j][i] = -w % p
        assert response == triangle
        valid += 1
    # Verify the rational response formula against its pole-free subspace lift.
    trials = 500
    poles = regular = 0
    for _ in range(trials):
        n = 3
        s = symmetric(n, p, rng)
        v = [rng.randrange(p) for _ in range(n)]
        t = rng.randrange(p)
        sv = [sum(x * y for x, y in zip(row, v)) % p for row in s]
        denominator = (1 + t * sum(x * y for x, y in zip(v, sv))) % p
        transformed = mul(shear(v, t, True, p), eye(n) + s, p)
        assert rank(transformed, p) == n and isotropic(transformed, p)
        if denominator == 0:
            assert rank(transformed[:n], p) < n
            poles += 1
        else:
            direct = [[(s[i][j] - t * sv[i] * sv[j] * pow(denominator, -1, p)) % p
                       for j in range(n)] for i in range(n)]
            assert canonical(eye(n) + direct, p) == canonical(transformed, p)
            regular += 1
    return dict(star_field=7, stars_total=216, stars_regular=valid,
                stars_singular=singular, rational_trials=trials,
                rational_regular=regular, rational_poles=poles)


def exhaustive_check() -> dict:
    """All 24 x 24 local SL(2,3) secrets, for a graph and a nongraph base."""
    p, n = 3, 2
    group = [[[a, b], [c, d]] for a, b, c, d in itertools.product(range(p), repeat=4)
             if (a * d - b * c) % p == 1]
    assert len(group) == 24

    def embed(g: Matrix, i: int) -> Matrix:
        out = eye(4)
        for row in range(2):
            for col in range(2):
                out[i + row * n][i + col * n] = g[row][col]
        return out

    alice = [embed(g, 0) for g in group]
    bob = [embed(g, 1) for g in group]
    bg = [shear([0, 1], 1, upper, p) for upper in (False, True)]
    commutant = centralizer(bg, p)
    bases = [eye(n) + [[1, 1], [1, 2]], [[0, 0], [0, 1], [1, 0], [0, 0]]]
    cases = 0
    for base in bases:
        assert rank(base, p) == n and isotropic(base, p)
        for a in alice:
            ua = canonical(mul(a, base, p), p)
            witnesses = witness_space(base, ua, commutant, p)
            for b in bob:
                ub = canonical(mul(b, base, p), p)
                expected = canonical(mul(a, ub, p), p)
                assert expected == canonical(mul(b, ua, p), p)
                assert recover_shared(witnesses, ub, p) == expected
                cases += 1
    # An individual nullspace basis vector need not have full rank on the base.
    base = eye(2) + [[0, 0], [0, 0]]
    commutant = centralizer([eye(4)], p)
    witnesses = witness_space(base, base, commutant, p)
    individual_ranks = [rank(mul(w, base, p), p) for w in witnesses]
    assert max(individual_ranks) == 1
    assert recover_shared(witnesses, base, p) == base
    # A noncommuting control: upper and lower shears on the same coordinate.
    base = eye(2) + eye(2)
    a = shear([1, 0], 1, True, p)
    b = shear([1, 0], 1, False, p)
    ab = canonical(mul(a, mul(b, base, p), p), p)
    ba = canonical(mul(b, mul(a, base, p), p), p)
    assert ab != ba
    return dict(field=p, local_group_size=len(group), bases=len(bases),
                exhaustive_pairs=cases, recovered=cases,
                singular_basis_example=dict(witness_dimension=len(witnesses),
                    maximum_individual_image_rank=max(individual_ranks), recovered_rank=2),
                noncommuting_control_agrees=False)


def parser_checks(params: PublicParameters, pk: bytes, sk: Matrix, ct: bytes) -> int:
    width = (params.p.bit_length() + 7) // 8
    bad_range = params.p.to_bytes(width, "big") + ct[width:]
    bad = [b"", ct[:-1], ct + b"\x00", bytes(len(ct)), bad_range]
    # Same subspace, noncanonical basis: the encoding must be rejected.
    rows = transpose(decode(ct, params))
    rows[0], rows[1] = rows[1], rows[0]
    bad.append(b"".join(x.to_bytes(width, "big") for row in rows for x in row))
    # Full rank, canonical graph of a nonsymmetric matrix: not isotropic.
    nonsymmetric = [[0] * params.n for _ in range(params.n)]
    nonsymmetric[0][1] = 1
    bad.append(encode(eye(params.n) + nonsymmetric, params))
    assert all(decaps(params, pk, sk, value) is None for value in bad)
    return len(bad)


def experiments(seed: int, trials: int, dimensions: list[int], primes: list[int],
                lengths: list[int], ciphertexts: int) -> dict:
    rng = random.Random(seed)
    result = dict(format="crucible-kem-discovery-network-v1", status="rejected-classical-break",
                  seed=seed, python=platform.python_version(), platform=platform.platform(),
                  randomness="random.Random; experiments only, not a production KEM",
                  network=network_checks(rng), exhaustive=exhaustive_check(), profiles=[])
    for p, n, length in itertools.product(primes, dimensions, lengths):
        started = time.perf_counter()
        params = setup(n, p, length, rng)
        setup_seconds = time.perf_counter() - started
        started = time.perf_counter()
        commutant = centralizer(params.bob_generators, p)
        precompute_seconds = time.perf_counter() - started
        key_seconds, attack_seconds, honest_seconds, witness_dimensions = [], [], [], []
        success = correctness = nongraph_pk = nongraph_ct = invalid_rejections = 0
        first_example = None
        for trial in range(trials):
            pk, sk = keygen(params, rng)
            ua = decode(pk, params)
            nongraph_pk += int(rank(ua[:n], p) < n)
            started = time.perf_counter()
            witnesses = witness_space(params.base, ua, commutant, p)
            key_seconds.append(time.perf_counter() - started)
            witness_dimensions.append(len(witnesses))
            for _ in range(ciphertexts):
                started = time.perf_counter()
                ct, key = encaps(params, pk, rng)
                decapsulated = decaps(params, pk, sk, ct)
                honest_seconds.append(time.perf_counter() - started)
                correctness += int(decapsulated == key)
                assert decapsulated == key
                ub = decode(ct, params)
                nongraph_ct += int(rank(ub[:n], p) < n)
                started = time.perf_counter()
                recovered = recover_key(params, pk, ct, witnesses)
                attack_seconds.append(time.perf_counter() - started)
                success += int(recovered == key)
                assert recovered == key
                if first_example is None:
                    first_example = dict(parameters=dict(p=p, n=n, word_length=length,
                        base=params.base, alice_generators=params.alice_generators,
                        bob_generators=params.bob_generators), pk=pk.hex(), ct=ct.hex(),
                        key=key.hex(), recovered=recovered.hex())
            if trial == 0:
                invalid_rejections += parser_checks(params, pk, sk, ct)
        result["profiles"].append(dict(p=p, n=n, ambient_dimension=2 * n, word_length=length,
            public_keys=trials, ciphertexts_per_key=ciphertexts, samples=trials * ciphertexts,
            correct=correctness, recovered=success, public_key_bytes=len(pk),
            ciphertext_bytes=len(ct), key_bytes=32, centralizer_dimension=len(commutant),
            witness_dimensions=sorted(set(witness_dimensions)), nongraph_public_keys=nongraph_pk,
            nongraph_ciphertexts=nongraph_ct, malformed_ciphertexts_rejected=invalid_rejections,
            seconds=dict(setup=setup_seconds, public_precomputation=precompute_seconds,
                per_key_median=statistics.median(key_seconds),
                per_ciphertext_median=statistics.median(attack_seconds),
                honest_encaps_decaps_median=statistics.median(honest_seconds)),
            first_example=first_example))
    result["totals"] = dict(samples=sum(p["samples"] for p in result["profiles"]),
                            correct=sum(p["correct"] for p in result["profiles"]),
                            recovered=sum(p["recovered"] for p in result["profiles"]))
    return result


def is_prime(p: int) -> bool:
    return p >= 3 and p % 2 == 1 and all(p % d for d in range(3, __import__("math").isqrt(p) + 1, 2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--trials", type=int, default=8, help="independent public keys per profile")
    parser.add_argument("--ciphertexts", type=int, default=3, help="encapsulations per public key")
    parser.add_argument("--dimensions", type=int, nargs="+", default=[2, 3, 4])
    parser.add_argument("--primes", type=int, nargs="+", default=[7, 101])
    parser.add_argument("--lengths", type=int, nargs="+", default=[8, 64])
    parser.add_argument("--output", type=Path, help="optional JSON evidence file")
    args = parser.parse_args()
    if args.trials < 1 or args.ciphertexts < 1 or min(args.dimensions) < 2 or min(args.lengths) < 1:
        parser.error("positive trial, ciphertext, and length counts; dimensions at least two")
    if not all(is_prime(p) and p <= 65521 for p in args.primes):
        parser.error("experiments accept odd primes no larger than 65521")
    result = experiments(args.seed, args.trials, args.dimensions, args.primes,
                         args.lengths, args.ciphertexts)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "profiles"}, indent=2))
    for profile in result["profiles"]:
        print(json.dumps({key: value for key, value in profile.items() if key != "first_example"}))


if __name__ == "__main__":
    main()
