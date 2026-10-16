#!/usr/bin/env sage-python
"""Freeze prime moduli from the pinned, classical rough lattice estimator.

No network access. Prime proofs and estimator results are computed offline;
neither Sage nor the estimator is needed by the challenge service.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

COMMIT='53da5982597709ba0fdf94ea37a84d822310fd84'
ARCHIVE_SHA256='4236adc38031c139c8a2721804d8962d6cacd015fae37f6f5215d2ac7f011060'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--estimator',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if hashlib.sha256(args.archive.read_bytes()).hexdigest()!=ARCHIVE_SHA256:parser.error('Wrong estimator archive')
    with tarfile.open(args.archive) as archive:
        for member in archive.getmembers():
            if member.isfile():
                relative=Path(*Path(member.name).parts[1:])
                if (args.estimator/relative).read_bytes()!=archive.extractfile(member).read():parser.error('Estimator checkout changed')
    sys.path.insert(0,str(args.estimator))
    from estimator import LWE,ND
    from sage.all import QQ, RealField, log, next_prime, ZZ
    real=RealField(100)
    cache={}
    def evaluate(q):
        q=int(q)
        if q not in cache:
            params=LWE.Parameters(n=768,q=q,Xs=ND.CenteredBinomial(2),Xe=ND.CenteredBinomial(2),m=768)
            attacks=LWE.estimate.rough(params,quiet=True,catch_exceptions=False)
            values={name:{'log2_rop':float(log(cost['rop'],2)),'beta':int(cost.get('beta',0))} for name,cost in attacks.items()}
            cache[q]={'q':str(q),'bits':min(c['log2_rop'] for c in values.values()),'attacks':values}
        return cache[q]
    # Coarse half-bit exponent samples, followed by a fresh real estimate at
    # each interpolated candidate. Interpolation SELECTS q; it is never the
    # claimed cost. Sage next_prime and is_prime use proof=True.
    grid=[]
    for half in range(31,127):
        exponent=QQ(half)/2
        q=next_prime(ZZ((real(2)**exponent).ceil()),proof=True)
        grid.append((float(exponent),evaluate(q)))
    largest=2**127-1
    assert ZZ(largest).is_prime(proof=True)
    rows=[{'tier':1,'rank':1,'q':str(largest),'bits':None,'reason':'Uncalibrated rank1 instance; no validated attack-time estimate'}]
    for tier in range(2,51):
        target=8+120*(tier-1)/49
        if target<=11.68:
            exponent=63.0
        else:
            bracket=next(((a,b) for a,b in zip(grid,grid[1:]) if a[1]['bits']>=target>=b[1]['bits']),None)
            if bracket is None:raise RuntimeError('Target is outside the sampled model range')
            left,right=bracket
            fraction=(left[1]['bits']-target)/(left[1]['bits']-right[1]['bits'])
            exponent=left[0]+fraction*(right[0]-left[0])
        q=int(next_prime(ZZ((real(2)**real(exponent)).ceil()),proof=True))
        row={'tier':tier,'rank':3,'target_bits':round(target,6),**evaluate(q)}
        if min(c['beta'] for c in row['attacks'].values())<=40:
            row['bits']=None;row['reason']='Estimator beta40 floor; numerical security claim omitted'
        assert 4*6146<q<int(rows[-1]['q'])
        rows.append(row)
    result={'model_id':'module-lwe-core-svp-v1','estimator_commit':COMMIT,'archive_sha256':ARCHIVE_SHA256,
        'sage_version':'10.1','method':'LWE.estimate.rough, ADPS16 classical Core-SVP/GSA; primal uSVP and dual hybrid',
        'parameters':'Tiers2-50: flattened n768,m768,CBD2 secret and error, rank3 over degree256 negacyclic ring. Tier1: rank1, no numerical estimate.',
        'prime_verification':'All registered q pass Sage is_prime(proof=True); largest is the Mersenne prime2^127-1',
        'correctness':'Perfect: 2*k*n*eta^2+eta=6146<floor(q/4) for every profile; no lossy compression',
        'limitations':['Not the full module-LWE/KEM security problem: additional structure and ciphertext information omitted.',
            'Core-SVP heuristic estimates are not measured operations, elapsed time, or security lower bounds.',
            'Tiny-cost floor profiles have no numerical security estimate; full256-bit secrets/messages remain.'],
        'profiles':rows,'coarse_search':[{'log2_q_grid':x,**row} for x,row in grid]}
    args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
