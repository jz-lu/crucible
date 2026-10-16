#!/usr/bin/env python3
"""Offline bounded NTRU candidate certification and communication frontier.

Run with SageMath Python. No server calls this search. Domain, objective,
estimator omissions and discarded alternatives are written into the report.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from crucible.ntru_reference import encoded_length
from crucible.ntru_frontier_model import estimate_with_hybrid, MODEL_ID, SOURCE_SHA256


def build_candidates():
    from sage.all import GF, PolynomialRing, ZZ, prime_range, version
    prior=json.loads((ROOT/'crucible/data/ntru_tier_profiles.json').read_text())['profiles']
    candidates={}
    for old in prior:
        p=old['p']
        if p>653:
            continue
        qs={old['q']}
        # The degree domain is explicitly inherited from certified research
        # settings. Different target weights broaden each degree's modulus
        # choices; these finite fractions do not claim exhaustive optimization.
        for numerator in (1,2,3,4):
            requested=max(1,p*numerator//8)
            for prime in prime_range(16*requested+1,min(16384,16*(2*p//3)+2)):
                q=int(prime)
                if q%6!=1:
                    continue
                ring=PolynomialRing(GF(q),'x'); x=ring.gen()
                if (x**p-x-1).is_irreducible():
                    qs.add(q)
                    break
        for q in sorted(qs):
            ring=PolynomialRing(GF(q),'x'); x=ring.gen()
            assert ZZ(p).is_prime(proof=True) and ZZ(q).is_prime(proof=True)
            assert (x**p-x-1).is_irreducible()
            # Preserve the old weight as an explicit alternative, and test
            # full use of the exact deterministic correctness capacity.
            weights={min((q-1)//16,2*p//3)}
            if q==old['q']:
                weights.add(old['w'])
            for w in sorted(weights):
                assert 0<w<=p and 2*p>=3*w and q>=16*w+1
                pk=encoded_length(p,q);ct=encoded_length(p,(q+2)//3)+32
                candidates[p,q,w]={'p':p,'q':q,'w':w,'public_key_bytes':pk,
                    'ciphertext_bytes':ct,'communication_bytes':pk+ct,
                    'reference':estimate_with_hybrid(p,q,w)}
        print(f'certified degree {p}: {len(candidates)} cumulative candidates',flush=True)
    return list(candidates.values()),version()


def choose(candidates):
    # Uncalibrated small settings remain visibly uncalibrated. They form an
    # introductory degree path, not rows advertised at fabricated bit targets.
    selected=[]
    modeled=[r for r in candidates if r['reference']['bits'] is not None]
    first=min(r['reference']['bits'] for r in modeled)
    first_target=max(16,4*int(first//4))
    first_cost=min(r['communication_bytes'] for r in modeled if r['reference']['bits']>=first_target)
    # At the estimator floor, don't invent bits. Keep only introductory rows
    # below the first calibrated cost whose exact input-enumeration ceiling
    # improves as bytes grow. This is a bounded proxy screen, NOT a security
    # Pareto claim; structural attacks may rank these small tuples differently.
    intro=[r for r in candidates if r['reference']['bits'] is None and r['communication_bytes']<first_cost]
    ceiling=0
    for row in sorted(intro,key=lambda r:(r['communication_bytes'],r['public_key_bytes'],-(math.comb(r['p'],r['w'])*2**r['w']))):
        count=math.comb(row['p'],row['w'])*2**row['w']
        if count<=ceiling:
            continue
        if selected and row['communication_bytes']==selected[-1]['communication_bytes']:
            continue
        selected.append(dict(row,target_reference_bits=None))
        ceiling=count
    # 4-bit model reference increments give actionable initial targets; when
    # one tuple already satisfies adjacent targets it appears only once.
    first=min(r['reference']['bits'] for r in candidates if r['reference']['bits'] is not None)
    targets=list(range(max(16,4*int(first//4)),129,4))
    for target in targets:
        qualifying=[r for r in candidates if r['reference']['bits'] is not None and r['reference']['bits']>=target]
        if not qualifying:
            raise ValueError(f'No candidate reaches reference target {target}')
        best=min(qualifying,key=lambda r:(r['communication_bytes'],r['public_key_bytes'],r['p'],r['q'],-r['w']))
        key=tuple(best[k] for k in ('p','q','w'))
        if any(tuple(r[k] for k in ('p','q','w'))==key for r in selected):
            continue
        selected.append(dict(best,target_reference_bits=target))
    # Do not silently normalize the anchor to128: its actual scoped estimate
    # remains explicit. The final selected tuple need not be the named anchor.
    return [dict(r,tier=i+1) for i,r in enumerate(selected)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reuse-candidates',type=Path)
    args=parser.parse_args()
    if args.reuse_candidates:
        previous=json.loads(args.reuse_candidates.read_text());candidates=previous['candidates'];sage=previous['certificate']['tool']
        for row in candidates:
            row['reference']=estimate_with_hybrid(row['p'],row['q'],row['w'])
    else:
        candidates,sage=build_candidates()
    profiles=choose(candidates)
    result={'format':'crucible-ntru-frontier-parameters-v1','profiles':profiles,'candidates':candidates,
        'selection':{'objective':'Minimize canonical public-key plus one ciphertext bytes; ties: public-key bytes, p, q, decreasing w',
            'fixed_constraints':['Perfect honest agreement: q>=16w+1 and 2p>=3w','Full uniform ideal-model samplers and the 2020 transform/encodings','p,q prime; p mod4=1; q mod6=1; x^p-x-1 irreducible modulo q','32-byte output; no padding or forced timing'],
            'domain':'Inherited certified degrees through653; at each p, old q plus first admissible q above16*floor(a*p/8)+1 for a=1,2,3,4, bounded by q<16384 and q<=16*floor(2p/3)+1. Test maximum correctness-admissible weight at each q and the old weight at oldq.',
            'scope':'Minimum only among the listed finite candidates under the scoped reference, not a global or full-security optimum',
            'timing':'No runtime/memory dominance claimed; increased weight can increase sampler work',
            'targets':'Uncalibrated introductory tuples screened for increasing bytes and exact input-enumeration ceilings below the first calibrated cost; then distinct minimum-communication tuples at4-bit classical hybrid-inclusive reference targets through128',
            'model':MODEL_ID,'upstream_estimator_sha256':SOURCE_SHA256,
            'omissions':'Dual/structural attacks, memory cost, side channels, full-KEM proof losses and concrete small-dimension reliability. The hybrid model includes simple and meet-in-the-middle guessing; neither this estimate nor Core-SVP is a security lower bound.',
            'input_enumeration':'2^w*C(p,w) candidate input-polynomial re-encryptions, reported separately from Core-SVP units'},
        'certificate':{'tool':sage,'prime_proof':True,'irreducibility':'SageMath GF(q)[x]: (x^p-x-1).is_irreducible() for every candidate','honest_failure_bound':'0','meaning':'Algebra/encoding conditions, not a confidentiality certificate'}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(f'Wrote {len(profiles)} selected profiles from {len(candidates)} candidates',flush=True)

if __name__=='__main__':
    main()
