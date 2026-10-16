#!/usr/bin/env python3
"""Construction/correctness prototype for a retained, UNVALIDATED KEM lead.

No cryptanalytic routines. Public maps are expanded coefficient lists; the
factorization used to create them is not passed to Encaps or Decaps. Random is
seeded for reproducible mathematics and is NOT cryptographic randomness.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import platform
import random
import statistics
import time
from pathlib import Path


Poly = dict[tuple[int, ...], int]
Point = tuple[int, ...]
Word = list[tuple[int, int]]


def add(a: Poly, b: Poly, p: int) -> Poly:
    out = dict(a)
    for e, c in b.items():
        out[e] = (out.get(e, 0) + c) % p
        if out[e] == 0:
            del out[e]
    return out


def scale(a: Poly, c: int, p: int) -> Poly:
    return {e: c * v % p for e, v in a.items() if c * v % p}


def product(a: Poly, b: Poly, p: int) -> Poly:
    out: Poly = {}
    for ea, ca in a.items():
        for eb, cb in b.items():
            e = tuple(x + y for x, y in zip(ea, eb))
            out[e] = (out.get(e, 0) + ca * cb) % p
    return {e: c for e, c in out.items() if c}


def variables(count: int) -> list[Poly]:
    return [{tuple(int(i == j) for j in range(count)): 1} for i in range(count)]


def dot_poly(weights: list[int], values: list[Poly], p: int) -> Poly:
    out: Poly = {}
    for c, value in zip(weights, values):
        out = add(out, scale(value, c, p), p)
    return out


def evaluate_map(polys: list[Poly], z: Point, t: int, p: int) -> Point:
    values = (*z, t)
    # Reuse monomial evaluations across output coordinates.
    cache: dict[tuple[int, ...], int] = {}
    result = []
    for poly in polys:
        total = 0
        for exponents, coefficient in poly.items():
            if exponents not in cache:
                value = 1
                for x, power in zip(values, exponents):
                    if power:
                        value = value * pow(x, power, p) % p
                cache[exponents] = value
            total += coefficient * cache[exponents]
        result.append(total % p)
    return tuple(result)


# Each linear layer is a symplectic rank-one shear (upper, vector, scalar).
Layer = tuple[bool, list[int], int]


def linear_layers(n: int, p: int, rng: random.Random) -> list[Layer]:
    return [(bool(rng.randrange(2)), [rng.randrange(p) for _ in range(n)], rng.randrange(p))
            for _ in range(2 * n)]


def apply_linear_poly(z: list[Poly], layers: list[Layer], p: int, inverse: bool = False) -> list[Poly]:
    n = len(z) // 2
    out = list(z)
    for upper, v, t in (reversed(layers) if inverse else layers):
        source = out[n:] if upper else out[:n]
        delta = scale(dot_poly(v, source, p), -t if inverse else t, p)
        for i in range(n):
            j = i if upper else n + i
            out[j] = add(out[j], scale(delta, v[i], p), p)
    return out


def apply_linear_point(z: Point, layers: list[Layer], p: int, inverse: bool = False) -> Point:
    n = len(z) // 2
    out = list(z)
    for upper, v, t in (reversed(layers) if inverse else layers):
        source = out[n:] if upper else out[:n]
        delta = (-t if inverse else t) * sum(a * b for a, b in zip(v, source)) % p
        for i in range(n):
            j = i if upper else n + i
            out[j] = (out[j] + v[i] * delta) % p
    return tuple(out)


def cubic_potential(n: int, p: int, rng: random.Random) -> Poly:
    out = {}
    for indices in itertools.combinations_with_replacement(range(n), 3):
        e = tuple(indices.count(i) for i in range(n))
        c = rng.randrange(p)
        if c:
            out[e] = c
    # Exact sampler conditions on a nonzero cubic, counted by retrying here.
    return out or cubic_potential(n, p, rng)


def gradient_poly(f: Poly, ys: list[Poly], p: int) -> list[Poly]:
    n = len(ys)
    out: list[Poly] = [{} for _ in range(n)]
    zero = (0,) * (2 * n + 1)
    for e, c in f.items():
        for i in range(n):
            if not e[i]:
                continue
            term = {zero: c * e[i] % p}
            for j in range(n):
                for _ in range(e[j] - int(i == j)):
                    term = product(term, ys[j], p)
            out[i] = add(out[i], term, p)
    return out


def gradient_point(f: Poly, y: Point, p: int) -> Point:
    out = [0] * len(y)
    for e, c in f.items():
        for i in range(len(y)):
            if e[i]:
                term = c * e[i]
                for j in range(len(y)):
                    term = term * pow(y[j], e[j] - int(i == j), p) % p
                out[i] = (out[i] + term) % p
    return tuple(out)


def nonlinear_poly(z: list[Poly], f: Poly, p: int, inverse: bool = False) -> list[Poly]:
    n = len(z) // 2
    gradient = gradient_poly(f, z[n:], p)
    return [add(z[i], scale(gradient[i], -1 if inverse else 1, p), p) for i in range(n)] + z[n:]


def nonlinear_point(z: Point, f: Poly, p: int, inverse: bool = False) -> Point:
    n = len(z) // 2
    gradient = gradient_point(f, z[n:], p)
    return tuple((z[i] + (-1 if inverse else 1) * gradient[i]) % p for i in range(n)) + z[n:]


def compile_generator(n: int, i: int, upper: bool, first: list[Layer], last: list[Layer],
                      potential: Poly, p: int) -> list[Poly]:
    """Expand phi E_i(t) phi^-1, phi = last o nonlinear o first."""
    all_variables = variables(2 * n + 1)
    z = apply_linear_poly(all_variables[:-1], last, p, inverse=True)
    z = nonlinear_poly(z, potential, p, inverse=True)
    z = apply_linear_poly(z, first, p, inverse=True)
    source, target = (n + i, i) if upper else (i, n + i)
    cube = product(product(z[source], z[source], p), z[source], p)
    z[target] = add(z[target], product(all_variables[-1], cube, p), p)
    z = apply_linear_poly(z, first, p)
    z = nonlinear_poly(z, potential, p)
    return apply_linear_poly(z, last, p)


def factorized_generator(z: Point, t: int, i: int, upper: bool, first: list[Layer],
                         last: list[Layer], potential: Poly, p: int) -> Point:
    """Private setup witness, used ONLY to verify coefficient expansion."""
    z = apply_linear_point(z, last, p, inverse=True)
    z = nonlinear_point(z, potential, p, inverse=True)
    z = list(apply_linear_point(z, first, p, inverse=True))
    n = len(z) // 2
    source, target = (n + i, i) if upper else (i, n + i)
    z[target] = (z[target] + t * pow(z[source], 3, p)) % p
    z = apply_linear_point(tuple(z), first, p)
    z = nonlinear_point(z, potential, p)
    return apply_linear_point(z, last, p)


def serialize_maps(maps: list[list[Poly]]) -> list:
    return [[[[[*e], c] for e, c in sorted(poly.items())] for poly in g] for g in maps]


def public_wire(params: dict) -> bytes:
    obj = {k: v for k, v in params.items() if k != "maps"}
    obj["maps"] = serialize_maps(params["maps"])
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("ascii")


def restore_public(data: bytes) -> dict:
    """Reload our generated evidence format; not an untrusted-parameter validator."""
    obj = json.loads(data)
    obj["base"] = [tuple(point) for point in obj["base"]]
    obj["maps"] = [[{tuple(e): c for e, c in poly} for poly in g] for g in obj["maps"]]
    return obj


def public_identifier(params: dict) -> bytes:
    return hashlib.sha256(public_wire(params)).digest()


def encode(points: list[Point], params: dict) -> bytes:
    width = (params["p"].bit_length() + 7) // 8
    return b"".join(x.to_bytes(width, "big") for point in sorted(points) for x in point)


def decode(data: bytes, params: dict) -> list[Point]:
    p, d, count = params["p"], 2 * params["n"], params["point_count"]
    width = (p.bit_length() + 7) // 8
    if len(data) != d * count * width:
        raise ValueError("wrong point-set length")
    values = [int.from_bytes(data[i:i + width], "big") for i in range(0, len(data), width)]
    if any(x >= p for x in values):
        raise ValueError("noncanonical field element")
    points = [tuple(values[i:i + d]) for i in range(0, len(values), d)]
    if points != sorted(set(points)):
        raise ValueError("point set is not strictly sorted")
    return points


def setup(n: int, p: int, length: int, count: int, rng: random.Random) -> tuple[dict, dict]:
    started = time.perf_counter()
    first, last = linear_layers(n, p, rng), linear_layers(n, p, rng)
    f = cubic_potential(n, p, rng)
    maps, expansion_checks = [], 0
    for i in range(n):
        for upper in (False, True):
            g = compile_generator(n, i, upper, first, last, f, p)
            maps.append(g)
            for _ in range(10):
                z = tuple(rng.randrange(p) for _ in range(2 * n))
                t = rng.randrange(p)
                expected = factorized_generator(z, t, i, upper, first, last, f, p)
                actual = evaluate_map(g, z, t, p)
                assert actual == expected
                assert evaluate_map(g, actual, -t % p, p) == z
                assert evaluate_map(g, z, 0, p) == z
                expansion_checks += 1
    # A public sample of a nonlinear boundary relation y = grad(F0)(x).
    # F0 = 1/2 sum(x_i^2) + sum(x_i^2 x_(i+1)^2), using adjacent pairs once.
    xs: set[Point] = set()
    draws = 0
    while len(xs) < count:
        xs.add(tuple(rng.randrange(p) for _ in range(n)))
        draws += 1
    base = []
    for x in sorted(xs):
        y = list(x)
        for i in range(n - 1):
            y[i] = (y[i] + 2 * x[i] * x[i + 1] ** 2) % p
            y[i + 1] = (y[i + 1] + 2 * x[i + 1] * x[i] ** 2) % p
        base.append((*x, *y))
    params = dict(p=p, n=n, word_length=length, point_count=count, base=base, maps=maps)
    # Only expanded maps and public points leave this function. No conjugator.
    coeffs = sum(len(poly) for g in maps for poly in g)
    exponents = [e for g in maps for poly in g for e in poly]
    diagnostics = dict(expansion_and_inverse_checks=expansion_checks,
        sampled_base_draws=draws, coefficient_count=coeffs,
        maximum_coordinate_degree=max(sum(e[:-1]) for e in exponents),
        maximum_parameter_degree=max(e[-1] for e in exponents),
        public_map_json_bytes=len(json.dumps(serialize_maps(maps), separators=(",", ":")).encode("ascii")),
        setup_seconds=time.perf_counter() - started)
    return params, diagnostics


def sample_word(params: dict, alice: bool, rng: random.Random) -> Word:
    cut = 2 * (params["n"] // 2)
    indices = range(cut) if alice else range(cut, 2 * params["n"])
    return [(rng.choice(indices), rng.randrange(params["p"])) for _ in range(params["word_length"])]


def apply_word(points: list[Point], word: Word, params: dict) -> list[Point]:
    out = list(points)
    for index, t in word:
        out = [evaluate_map(params["maps"][index], point, t, params["p"]) for point in out]
    assert len(set(out)) == len(points), "a purported permutation lost a point"
    return sorted(out)


def derive(params: dict, pk: bytes, ct: bytes, shared: list[Point], identifier: bytes) -> bytes:
    return hashlib.shake_256(b"crucible-nonlinear-candidate-v1\x00" + identifier
                            + pk + ct + encode(shared, params)).digest(32)


def keygen(params: dict, rng: random.Random) -> tuple[bytes, Word]:
    a = sample_word(params, True, rng)
    return encode(apply_word(params["base"], a, params), params), a


def encaps(params: dict, pk: bytes, identifier: bytes, rng: random.Random) -> tuple[bytes, bytes]:
    points = decode(pk, params)
    b = sample_word(params, False, rng)
    ct = encode(apply_word(params["base"], b, params), params)
    return ct, derive(params, pk, ct, apply_word(points, b, params), identifier)


def decaps(params: dict, pk: bytes, word: Word, ct: bytes, identifier: bytes) -> bytes | None:
    try:
        points = decode(ct, params)
    except ValueError:
        return None
    return derive(params, pk, ct, apply_word(points, word, params), identifier)


def run(seed: int, n: int, p: int, length: int, count: int, trials: int) -> dict:
    rng = random.Random(seed)
    params, diagnostics = setup(n, p, length, count, rng)
    identifier = public_identifier(params)
    wire = public_wire(params)
    params = restore_public(wire)
    assert public_identifier(params) == identifier
    timings, correct, example = [], 0, None
    pk, sk = keygen(params, rng)
    for _ in range(trials):
        started = time.perf_counter()
        ct, key = encaps(params, pk, identifier, rng)
        decapsulated = decaps(params, pk, sk, ct, identifier)
        timings.append(time.perf_counter() - started)
        assert key == decapsulated
        correct += 1
        if example is None:
            example = dict(parameter_id=identifier.hex(), pk=pk.hex(), ct=ct.hex(),
                           encapsulated_key=key.hex(), decapsulated_key=decapsulated.hex())
    # Public encoding behavior only; no key-recovery or distinguishing experiments.
    width = (p.bit_length() + 7) // 8
    malformed = [ct[:-1], ct + b"\x00", bytes(len(ct)), p.to_bytes(width, "big") + ct[width:]]
    assert all(decaps(params, pk, sk, c, identifier) is None for c in malformed)
    # Encoding does not preserve the construction order of a set of points.
    points = decode(ct, params)
    assert encode(list(reversed(points)), params) == ct
    return dict(format="crucible-kem-discovery-nonlinear-v1", status="retained-unvalidated-candidate",
        scope="construction and honest correctness only; no cryptanalysis",
        python=platform.python_version(), seed=seed,
        parameters=dict(n=n, p=p, word_length=length, point_count=count),
        diagnostics=diagnostics, public_keys=1, ciphertexts=trials, correct=correct,
        public_key_point_bytes=len(pk), ciphertext_bytes=len(ct), key_bytes=32,
        public_parameters_bytes=len(wire), public_key_with_parameters_bytes=len(wire) + len(pk),
        serialized_public_parameters_reloaded=True,
        malformed_encodings_rejected=len(malformed),
        median_encaps_decaps_seconds=statistics.median(timings), example=example,
        public_parameters=dict(p=p, n=n, word_length=length, point_count=count,
                               base=params["base"], maps=serialize_maps(params["maps"])))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261015)
    parser.add_argument("--n", type=int, default=2)
    parser.add_argument("--p", type=int, default=7)
    parser.add_argument("--length", type=int, default=4)
    parser.add_argument("--points", type=int, default=8)
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true",
                        help="omit expanded public maps from evidence; reproduce them from the seed")
    args = parser.parse_args()
    if (args.n < 2 or args.p < 5 or args.p > 65521
            or any(args.p % d == 0 for d in range(2, math.isqrt(args.p) + 1))
            or not 2 <= args.points <= args.p ** args.n or args.length < 1 or args.trials < 1):
        parser.error("n >= 2; prime 5 <= p <= 65521; 2 <= points <= p^n; positive length/trials")
    result = run(args.seed, args.n, args.p, args.length, args.points, args.trials)
    if args.summary_only:
        del result["public_parameters"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, separators=(",", ":")) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "public_parameters"}, indent=2))


if __name__ == "__main__":
    main()
