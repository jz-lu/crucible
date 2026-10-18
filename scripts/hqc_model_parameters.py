#!/usr/bin/env python3
"""Freeze a bounded HQC research search; all acceptance uses rational arithmetic."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible.hqc_model_correctness import certificate, MODEL_ID
from crucible.hqc_growth_parameters import rs_generator

MAX_N = 17669
MAX_WEIGHT = 66
MAX_ENCAP_WEIGHT = 75


def prime_divisors(n):
    factors = []
    p = 2
    while p * p <= n:
        if n % p == 0:
            factors.append(p)
            while n % p == 0:
                n //= p
        p += 1
    if n > 1:
        factors.append(n)
    return factors


def primitive_prime(n):
    if n < 3 or any(n % d == 0 for d in range(2, int(n ** .5) + 1)):
        return False
    return all(pow(2, (n - 1) // q, n) != 1 for q in prime_divisors(n - 1))


def generate():
    candidates = []
    for repeat in (1, 2, 3):
        for delta in range(1, 16):
            code_length = (16 + 2 * delta) * 128 * repeat
            n = next(n for n in range(code_length + 1, MAX_N + 1) if primitive_prime(n))
            pk = 32 + (n + 7) // 8
            ct = (n + 7) // 8 + code_length // 8 + 16
            candidates.append((pk + ct, pk, ct, repeat, delta, n))
    candidates.sort()
    profiles = []
    for w in range(1, MAX_WEIGHT + 1):
        wr = (MAX_ENCAP_WEIGHT * w + MAX_WEIGHT - 1) // MAX_WEIGHT
        for total, pk, ct, repeat, delta, n in candidates:
            result = certificate(n, w, wr, wr, 16 + 2 * delta, repeat)
            if result['meets_target']:
                row = {'tier': w, 'n': n, 'weight': w, 'encryption_weight': wr,
                    'error_weight': wr, 'rs_length': 16 + 2 * delta,
                    'rs_dimension': 16, 'rs_delta': delta, 'rm_repetitions': repeat,
                    'rm_length': 128 * repeat, 'code_length': (16 + 2 * delta) * 128 * repeat,
                    'public_key_bytes': pk, 'ciphertext_bytes': ct,
                    'rs_generator': rs_generator(delta), 'barrett_mu': (1 << 32) // n,
                    'rejection_threshold': ((1 << 24) // n) * n,
                    'prime_order_factors': prime_divisors(n - 1),
                    'correctness_certificate': result}
                profiles.append(row)
                print(f"w={w}: n={n}, RS={16+2*delta}, RM={128*repeat}, bytes={total}, bound={result['log2_bound_display']:.6f}", flush=True)
                break
        else:
            raise RuntimeError(f'No admitted profile for weight{w}; expand only after review')
    source = ROOT/'crucible/hqc_model_correctness.py'
    return {'format': 'crucible-hqc-model-parameters-v1', 'model_id': MODEL_ID,
        'certificate_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'selection': {'weights': 'w=1..66; wr=we=ceil(75w/66)',
            'objective': 'Minimum public-key+ciphertext bytes at each fixed weight pair among45 supported codecs, each with the first primitive prime above its code length, under the exact conditional correctness acceptance rule; not optimization at an established security level',
            'tie_break': 'public-key bytes, ciphertext bytes, RM repetitions, RS delta, ring length',
            'max_ring_length': MAX_N, 'rs_dimension': 16, 'rs_delta': [1, 15],
            'rm_repetitions': [1, 2, 3], 'candidate_count_per_weight': len(candidates),
            'correctness': 'Exact rational Model1+deterministic tie correction and per-profile sampler-density upper bound; actual joint-noise transfer is an explicit conjectural premise',
            'security': 'No numeric security estimate or monotonic attack-cost theorem. Weights follow the2025HQC-1 balance; latest security calibration remains pending. Primitive prime ring and full native entropy are retained.'},
        'published_2025_anchor': {'n': 17669, 'weight': 66, 'encryption_weight': 75,
            'error_weight': 75, 'rs_length': 46, 'rm_repetitions': 3,
            'correctness_certificate': certificate(17669, 66, 75, 75, 46, 3)},
        'published_2026_comparison': {'n': 17669, 'weight': 67, 'encryption_weight': 76,
            'error_weight': 76, 'rs_length': 46, 'rm_repetitions': 3,
            'model1_certificate': certificate(17669, 67, 76, 76, 46, 3),
            'published_model2_log2_bound': -127.9,
            'meaning': 'Published conditional upper-bound analysis, not a rigorous lower bound or known contradiction of the actual scheme.'},
        'profiles': profiles}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = generate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
