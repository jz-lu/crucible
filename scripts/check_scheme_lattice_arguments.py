#!/usr/bin/env python3
"""Reproduce lattice argument arithmetic; never certify unproved premises.

Only bounded deterministic local checks run. No attack, native build, network,
database, publication or scheme admission is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible import module_compressed_v4_core as module
from crucible import lattice_frodo_v3 as frodo

OUTPUT = ROOT / 'docs/evidence/scheme-lattice-arguments-20261105.json'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def product(left, right):
    """Independent schoolbook negacyclic multiplication, no NTT helpers."""
    n, out = len(left), [0] * len(left)
    for i, x in enumerate(left):
        for j, y in enumerate(right):
            out[(i+j) % n] += x*y*(1 if i+j < n else -1)
    return [value % module.Q for value in out]


def plus(*values):
    return [sum(items) % module.Q for items in zip(*values)]


def module_equations(n):
    p = module.Parameters(n, 2, 3)
    d, z, coins = bytes(range(32)), bytes(reversed(range(32))), bytes([19])*32
    message = bytes([173]) * p.message_bytes
    pk, sk = module.keygen_internal(p, d, z)
    width = n*12//8
    rho, sigma = hashlib.sha3_512(module.domain(p, 'keygen') + d + bytes([2])).digest()[:32], hashlib.sha3_512(module.domain(p, 'keygen') + d + bytes([2])).digest()[32:]
    matrix = [[module.inverse_ntt(module._sample_ntt(rho, i, j, p)) for j in range(2)] for i in range(2)]
    s = [module._noise(sigma, i, 3, p) for i in range(2)]
    e = [module._noise(sigma, i+2, 3, p) for i in range(2)]
    t = [module.inverse_ntt(module.decode(pk[i*width:(i+1)*width], 12, n)) for i in range(2)]
    assert all(t[i] == plus(product(matrix[i][0], s[0]), product(matrix[i][1], s[1]), e[i]) for i in range(2))
    y = [module._noise(coins, i, 3, p) for i in range(2)]
    e1 = [module._noise(coins, i+2, 2, p) for i in range(2)]
    e2 = module._noise(coins, 4, 2, p)
    u = [plus(product(matrix[0][i], y[0]), product(matrix[1][i], y[1]), e1[i]) for i in range(2)]
    encoded = [module.decompress(bit, 1) for bit in module.decode(message, 1, n)]
    v = plus(product(t[0], y[0]), product(t[1], y[1]), e2, encoded)
    expected = b''.join(module.encode([module.compress(x, 10) for x in values], 10) for values in u)
    expected += module.encode([module.compress(x, 4) for x in v], 4)
    assert expected == module._encrypt(p, pk, message, coins)
    assert module._decrypt(p, sk[:p.pke_private_key_bytes], expected) == message
    return {'n': n, 'public_key_equations': True, 'ciphertext_equations': True,
            'fixed_fixture_message_decodes': True, 'support_bound': 42*n+106}


def build():
    ml = load('module_certificate', 'scripts/mlkem_correctness_certificate.py')
    fr = load('frodo_certificate', 'scripts/lattice_calibrate_frodo.py')
    saved = json.loads((ROOT / 'docs/mlkem-correctness-candidates.json').read_text())
    ml.verify(saved)
    selected = []
    for n in (8,16,32,64,128,256):
        row = next(r for r in saved['configurations'] if r['candidate_anchor'] == 'ML-KEM-512' and r['n'] == n and r['rank'] == 2)
        mode = 'conservative-compression' if n <= 64 else 'independent-uniform-u-compression'
        selected.append(next(item for item in row['evidence'] if item['mode'] == mode))
    # Exhaustively compare every16-bit sampler input to its certificate counts.
    words = b''.join(i.to_bytes(2,'little') for i in range(65536))
    assert Counter(frodo.sample_words(words)) == fr.distribution()
    frodo_rows = [fr.correctness_certificate(n) for n in range(8,641,8)]
    assert frodo_rows[0]['bound'] == '0'
    assert all(row.get('bound_exponent', 999) >= 133 for row in frodo_rows)
    assert frodo_rows[-1]['bound_exponent'] == 133
    paths = ('scripts/check_scheme_lattice_arguments.py', 'scripts/mlkem_correctness_certificate.py',
             'scripts/lattice_calibrate_frodo.py', 'crucible/module_compressed_v4_core.py',
             'crucible/lattice_module_v4.py', 'crucible/lattice_frodo_v3.py')
    return {'format': 'crucible-scheme-lattice-argument-evidence-v1',
            'scope': 'Exact certificate arithmetic and selected implementation-equation checks. No verification of seeded hardness, random-oracle transfer, noise-model transfer, or all-input code correctness.',
            'module_registered_certificates': selected,
            'module_all_saved_certificates_verified': 60,
            'module_equation_fixtures': [module_equations(n) for n in (8,16,32,64,128,256)],
            'frodo_all_sampler_words_verified': 65536,
            'frodo_registered_certificates': frodo_rows,
            'source_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    rendered = json.dumps(build(), indent=2, sort_keys=True) + '\n'
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            raise SystemExit('Lattice argument evidence differs; inspect before regenerating.')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(rendered)
    print('Verified 60 module certificates, six schoolbook equation fixtures, all 65536 Frodo sampler inputs and 80 Frodo certificates; unproved premises remain unproved.')
