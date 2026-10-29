#!/usr/bin/env python3
"""Small exact structure checks; no KEM admission or deployed-tuple DFR claim.

The noise witness is deliberately bounded to n11,w2,wr3,we3. The ring check
covers only current module degrees. Results are mathematical inspection aids.
"""
from collections import defaultdict
from itertools import combinations
from pathlib import Path
import argparse,json
Q=3329

def fwht(a):
    a=a[:]; h=1
    while h<len(a):
        for start in range(0,len(a),2*h):
            for j in range(start,start+h):
                x,y=a[j],a[j+h];a[j],a[j+h]=x+y,x-y
        h*=2
    return a


def hqc_correlation_witness():
    n,w,wr,we=11,2,3,3
    # Exact complete distribution for a deliberately tiny explanatory ring.
    # Not a failure-rate computation and not a deployed parameter tuple.
    def supports(weight):
        return [sum(1<<i for i in c) for c in combinations(range(n),weight)]
    xs,rs,es=supports(w),supports(wr),supports(we)
    mask=(1<<n)-1
    product=[0]*(1<<n)
    for x in xs:
        for r in rs:
            v=0
            for j in range(n):
                if x>>j&1:v^=((r<<j)|(r>>(n-j)))&mask
            product[v]+=1
    error_set=set(es)
    ef=[int(i in error_set) for i in range(1<<n)]
    pf=fwht(product);ef=fwht(ef)
    values=fwht([p*p*e for p,e in zip(pf,ef)])
    assert all(v%(1<<n)==0 for v in values)
    values=[v//(1<<n) for v in values]
    denominator=(len(xs)*len(rs))**2*len(es)
    assert sum(values)==denominator
    by_weight=defaultdict(list)
    for pattern,count in enumerate(values):by_weight[pattern.bit_count()].append((count,pattern))
    differing=[]
    for weight,rows in by_weight.items():
        low,high=min(rows),max(rows)
        if low[0]!=high[0]:
            differing.append({'weight':weight,'low_count':low[0],'low_pattern':format(low[1],f'0{n}b'),
                              'high_count':high[0],'high_pattern':format(high[1],f'0{n}b')})
    assert differing
    assert all(not c or p.bit_count()%2==we%2 for p,c in enumerate(values))
    return {'n':n,'w':w,'wr':wr,'we':we,'denominator':denominator,
            'primitive_prime_ring':pow(2,n-1,n)==1 and all(pow(2,j,n)!=1 for j in range(1,n-1)),
            'same_weight_different_probability':differing,
            'scope':'Exact algebraic joint-noise witness, NOT a deployed-tuple DFR refutation.'}


def ring_factors():
    result=[]
    for n in (8,16,32,64,128,256):
        degree=1
        while pow(Q,degree,2*n)!=1:degree+=1
        roots=[a for a in range(Q) if pow(a,n,Q)==Q-1]
        assert len(roots)==(n if degree==1 else 0)
        result.append({'n':n,'irreducible_factor_degree':degree,'factor_count':n//degree,
                       'linear_roots':len(roots),'message_entropy_bits':n,
                       'generic_full_message_enumeration_candidates':str(1<<n)})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result={'schema':'crucible-hqc-module-structure-checks-v1',
            'hqc_small_ring_dependence':hqc_correlation_witness(),
            'module_factorization':ring_factors()}
    args.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
