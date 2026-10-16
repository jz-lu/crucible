#!/usr/bin/env python3
"""Offline exact Sage certification; never invoked by the web server."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible.hqc_growth_parameters import codec_for_weight, rs_generator


def main():
    from sage.all import GF, Mod, PolynomialRing, ZZ, next_prime, version
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binary = PolynomialRing(GF(2), 'x'); x = binary.gen()
    field = GF(256, name='a', modulus=x**8+x**4+x**3+x**2+1); a = field.gen()
    ring = PolynomialRing(field, 'z'); z = ring.gen()
    assert a.multiplicative_order() == 255
    rm_weights = [sum(((symbol >> 7) ^ ((symbol & 127) & i).bit_count()) & 1
                     for i in range(128)) for symbol in range(1, 256)]
    assert min(rm_weights) == 64
    profiles = []
    for weight in range(1, 28):
        codec = codec_for_weight(weight)
        n = int(next_prime(codec['code_length'], proof=True))
        while Mod(2, n).multiplicative_order() != n - 1:
            n = int(next_prime(n, proof=True))
        assert ZZ(n).is_prime(proof=True)
        coefficients = [int(c.to_integer()) for c in
                        (ring.prod(z + a**i for i in range(1, 2*codec['rs_delta']+1))).list()]
        assert coefficients == rs_generator(codec['rs_delta'])
        assert codec['worst_case_error_weight'] <= codec['guaranteed_decoder_error_capacity']
        profiles.append({'tier': weight, 'n': n, 'weight': weight, **codec,
                         'rs_generator': coefficients,
                         'barrett_mu': (1 << 32) // n,
                         'rejection_threshold': ((1 << 24) // n) * n})
    result = {'format': 'crucible-hqc-growth-parameters-v1', 'profiles': profiles,
              'certificate': {'tool': version(), 'prime_proof': True,
                  'ring': 'n is the smallest prime above code length for which ord_n(2)=n-1',
                  'codec': 'Exhaustive RM(1,7) distance 64; RS consecutive roots a^1..a^(2*delta) in GF(256), modulus 0x11d',
                  'selection': 'Minimum concatenated length over RS(16+2*delta,16), delta=1..15, RM repetitions 1..3, satisfying 2*w*w+w < 32*repetitions*(delta+1)',
                  'meaning': 'Algebra and correctness certificate, not an attack-cost estimate'}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Certified 27 HQC growth profiles with fixed zero honest disagreement')

if __name__ == '__main__':
    main()
