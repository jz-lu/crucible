#!/usr/bin/env python3
"""Offline candidate search, not profile admission or a global optimality proof.

Keep q3329/rank2/CBD3-CBD2/native message entropy fixed; vary compression widths.
Exact rational bounds validate selected witnesses within their explicit model.
Implementing any tuple requires new encodings, versioned artifacts and review.
"""
from collections import Counter, defaultdict
from fractions import Fraction as F
from functools import lru_cache
from pathlib import Path
import argparse, hashlib, json, math, sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts import mlkem_correctness_certificate as old
Q=3329

@lru_cache(None)
def compression_counts(bits):
    if type(bits) is not int or not 1<=bits<=12:
        raise ValueError('Compression width must be an integer in 1..12')
    counts = Counter()
    for a in range(Q):
        c = ((2 * (1 << bits) * a + Q) // (2 * Q)) % (1 << bits)
        d = (2 * Q * c + (1 << bits)) // (2 * (1 << bits))
        counts[(d - a + Q // 2) % Q - Q // 2] += 1
    return dict(sorted(counts.items()))


@lru_cache(None)
def distributions(du, mode):
    if mode not in old.MODES:
        raise ValueError('Unknown correctness model')
    wide, small = old.cbd(3), old.cbd(2)
    first = old.combine(wide, wide, lambda a,b:a*b)
    if mode == 'conservative-compression':
        bound = max(map(abs, compression_counts(du)))
        shifted = defaultdict(F)
        for a,p in small.items():
            shifted[abs(a)+bound] += p
    else:
        rounding = {v:F(c,Q) for v,c in compression_counts(du).items()}
        shifted = old.combine(small, rounding, lambda a,b:a+b)
    return first, old.combine(wide, shifted, lambda a,b:a*b), small


def candidate(n, du, dv, mode, exact=False):
    if type(n) is not int or n not in old.DEGREES or mode not in old.MODES:
        raise ValueError('Unsupported candidate degree or correctness model')
    bu = max(map(abs, compression_counts(du)))
    bv = max(map(abs, compression_counts(dv)))
    support = 2*n*(9+3*(2+bu))+2+bv
    threshold = 832-bv
    out = {'n':n,'rank':2,'q':Q,'eta1':3,'eta2':2,'du':du,'dv':dv,
           'mode':mode,'ciphertext_bytes':n*(2*du+dv)//8,
           'u_rounding_bound':bu,'v_rounding_bound':bv,'residual_support_bound':support}
    if support < 832:
        return {**out,'argument':'deterministic-support','bits':None,'meets_target':True}
    if threshold <= 0:
        return {**out,'argument':'no-positive-tail-threshold','bits':0,'meets_target':False}
    first,second,add = distributions(du,mode)
    def objective(t):
        return 2*n*(old.log_mgf(first,t)+old.log_mgf(second,t))+old.log_mgf(add,t)-threshold*t
    # Keep exponent arguments bounded even for deliberately poor candidates.
    left,right=0.0,min(1.0,650/max(max(first),max(second),max(add)))
    for _ in range(55):
        a,b=left+(right-left)/3,right-(right-left)/3
        if objective(a)<objective(b):right=b
        else:left=a
    x=F(round(math.exp((left+right)/2)*1_000_000),1_000_000)
    bits=-(math.log(2*n)+objective(math.log(float(x))))/math.log(2)
    out.update(argument='chernoff-union-bound',threshold=threshold,
               proposed_witness=[x.numerator,x.denominator],bits=round(bits,9),meets_target=bits>=128)
    if exact:
        if x<=1:
            out.update(bits=0,meets_target=False)
            return out
        bound=2*n*(old.mgf(first,x)*old.mgf(second,x))**(2*n)*old.mgf(add,x)/x**threshold
        out.update(certified_exponent=max(0,old.dyadic_bits(min(F(1),bound))),
                   meets_target=(bound.numerator<<128)<=bound.denominator,
                   exact_bound_sha256=hashlib.sha256((hex(bound.numerator)+'/'+hex(bound.denominator)).encode()).hexdigest())
    return out


def compression_search():
    result=[]
    for n in (8,16,32,64,128,256):
        modes=[]
        for mode in old.MODES:
            rows=[candidate(n,du,dv,mode) for du in range(1,13) for dv in range(1,13)]
            accepted=[r for r in rows if r['meets_target']]
            best=min(r['ciphertext_bytes'] for r in accepted)
            finalists=[candidate(n,r['du'],r['dv'],mode,exact=True) for r in accepted if r['ciphertext_bytes']==best]
            assert all(r['meets_target'] for r in finalists)
            # Search ranking is numerical, not a global optimality certificate.
            # Exact arithmetic certifies the selected candidates only. A failed
            # bound at a numerical witness does not prove failure of a tuple.
            exclusions=[r for r in rows if r['ciphertext_bytes']<best]
            modes.append({'mode':mode,'minimum_ciphertext_bytes':best,
                          'minimal_candidates':finalists,
                          'cheaper_candidates_not_certified_in_numerical_search':len(exclusions),
                          'strongest_cheaper_bound_bits':max((r['bits'] or 0) for r in exclusions),
                          'baseline':candidate(n,10,4,mode,exact=True)})
        result.append({'n':n,'public_key_bytes':3*n+32,'modes':modes})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    rows=compression_search()
    document={'schema':'crucible-module-compression-candidates-v1',
        'status':'offline-candidates-not-admission',
        'basis_sha256':hashlib.sha256((ROOT/'scripts/mlkem_correctness_certificate.py').read_bytes()).hexdigest(),
        'search_domain':{'degrees':[8,16,32,64,128,256],'du':[1,12],'dv':[1,12],'q':3329,'rank':2,'eta1':3,'eta2':2},
        'search_qualification':'Floating optimization ranks candidates. Exact selected witnesses establish bounds; no global optimality or hardness theorem.',
        'correctness_qualification':'Conservative mode retains actual rounding dependence under independent ideal CBD draws. Modeled mode adds an unproved independent-uniform-u-compression bridge.',
        'configurations':rows}
    args.output.write_text(json.dumps(document,indent=2)+'\n')

if __name__=='__main__':main()
