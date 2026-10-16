#!/usr/bin/env python3
"""Reproduce the bounded lattice parameter/encoding comparison, without a server.

Uses frozen independently calibrated evidence, checked by its source digest.
Re-estimate from scratch with scripts/lattice_calibrate_frodo.py and the pinned
estimator/archive when the model changes; this script never downloads inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    raw = (ROOT / 'docs/lattice-frodo-calibration.json').read_bytes()
    data = json.loads(raw)
    expected = '2cc09becc57cc5f72e67d6e8c7ec9b6f9e10b0c2c6b14ca677accf2348096449'
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Frozen Frodo calibration evidence changed')
    rows = data['candidate_estimates']
    candidates = []
    for row in rows:
        supported = row.get('bits') is not None and all(c['beta'] > 40 for c in row.get('attacks', {}).values())
        raw_bits = min((c['log2_rop'] for c in row.get('attacks', {}).values()), default=None) if supported else None
        candidates.append({'n': row['n'], 'modeled_lattice_log2_rop': raw_bits,
                           'native_message_bits': 128,
                           'planning_reference_bits': min(128, raw_bits) if supported else None,
                           'public_key_bytes': 16 + 15*row['n'],
                           'ciphertext_bytes': 152 + 15*row['n'],
                           'matrix_coefficients': row['n']**2,
                           'selected': row['n'] <= 592,
                           'selection_reason': ('Unestimated natural warmup; unknown is not dominated' if not supported else
                                                'Increasing reference, retained' if row['n'] < 592 else
                                                'First candidate meeting128 planning target' if row['n'] == 592 else
                                                'Extra cost after fixed128 planning ceiling; archived, not a claim about published security')})
    supported = [c for c in candidates if c['planning_reference_bits'] is not None]
    endpoint = min((c for c in supported if c['planning_reference_bits'] >= 128), key=lambda c:c['n'])
    assert endpoint['n'] == 592
    selected = [c for c in candidates if c['selected'] and c['planning_reference_bits'] is not None]
    assert all(a['planning_reference_bits'] < b['planning_reference_bits'] for a,b in zip(selected,selected[1:]))
    module_raw = (ROOT/'docs/lattice-module-v2-calibration.json').read_bytes()
    if hashlib.sha256(module_raw).hexdigest() != '91028db2e57f0ce39cc0cd2cac0b7a38093087bc0ac9edcb13e76b8f015e74ad':
        raise ValueError('Frozen module calibration evidence changed')
    module = json.loads(module_raw)
    module_rows = []
    for row in module['profiles']:
        n,k,q = row['n'],row['rank'],int(row['q'])
        dim = n*k
        alternatives = []
        for degree in (8,16,32,64,128,256):
            if dim%degree or not 1 <= dim//degree <= 127: continue
            rank = dim//degree
            alternatives.append({'n':degree,'rank':rank,'native_message_bits':degree,
                                 'packed_public_key_bytes':43+dim*q.bit_length()//8,
                                 'packed_ciphertext_bytes':(dim+degree)*q.bit_length()//8,
                                 'expanded_matrix_coefficients':rank*rank*degree,
                                 'schoolbook_encapsulation_products':dim*dim+dim*degree,
                                 'retains_current_native_message_capacity':degree>=n})
        module_rows.append({'tier':row['tier'],'n':n,'rank':k,'q':q,'reference_bits':row['bits'],
                            'coefficient_bits':q.bit_length(),'prior_coefficient_bits':16,
                            'old_public_key_bytes':43+2*dim,'public_key_bytes':43+dim*q.bit_length()//8,
                            'old_ciphertext_bytes':2*(dim+n),'ciphertext_bytes':(dim+n)*q.bit_length()//8,
                            'same_flattened_dimension_alternatives':alternatives})
    return {'format':'crucible-bounded-parameter-review-v1','frodo':{
        'evidence_sha256':expected,'model_id':data['model_id'],
        'construction_invariants':'q32768, nbar8, exact Frodo640 CDF,128-bit native message, salted transform, full seeds; fixed correctness envelope2^-133',
        'search_space':'All80 natural matrix dimensions n8..640 in steps of8. No joint q/noise/nbar optimization.',
        'objective':'Minimize public-key bytes, ciphertext bytes and matrix coefficient count at each supported planning reference.',
        'limitations':'Two heuristic LWE attack families, Gaussian approximation, a separate message-enumeration ceiling with different operation units; not an optimum under complete security analysis. Published n640 has security margins outside this narrow objective.',
        'selected_endpoint':endpoint,'candidates':candidates},'module':{
        'search_space':'The11 registered mathematical profiles; canonical fixed-width coefficient encodings. Bounded n/rank alternatives are displayed, not asserted security-equivalent.',
        'objective':'Remove wasted binary coefficient bits while preserving every mathematical parameter and native plaintext capacity; no lossy compression.',
        'limitations':'ceil(log2 q) is minimal per-coefficient fixed-width binary storage, not an information-theoretic minimum for a whole vector. Changing ring degree/rank changes message capacity, structure and matrix expansion; flattened LWE equivalence is not full KEM equivalence.',
        'profiles':module_rows}}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.write_text(json.dumps(build(),indent=2)+'\n')
