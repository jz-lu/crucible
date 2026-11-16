#!/usr/bin/env python3
"""Offline finite sanity checks for the authored code-family argument notes.

No challenge, account or note is created. These checks do not verify hardness,
random-oracle transfer, rare-event transfer, or full native correctness.
"""
from __future__ import annotations
import hashlib
from itertools import combinations, product
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible.code_parameters import MC_LENGTHS, mceliece_sampling_batch
from scripts.hqc_support_review import load_reviewed_profiles, supplemental_evidence


def integer_ring_product(a, b):
    p = len(a)
    raw = [0] * (2*p-1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            raw[i+j] += x*y
    for i in range(2*p-2, p-1, -1):
        raw[i-p] += raw[i]
        raw[i-p+1] += raw[i]
    return raw[:p]


def prime(n):
    return n >= 2 and all(n % d for d in range(2, int(n**0.5)+1))


def gf_mul(a,b):
    result = 0
    while b:
        if b & 1: result ^= a
        b >>= 1
        a <<= 1
        if a & 8: a ^= 0b1011
    return result


def gf_inv(a):
    if not a: raise ValueError('Zero has no inverse')
    return next(b for b in range(1,8) if gf_mul(a,b)==1)


def check():
    ntru_path = ROOT/'crucible/data/ntru_frontier_profiles.json'
    ntru = json.loads(ntru_path.read_text())['profiles']
    for row in ntru:
        p,q,w = row['p'],row['q'],row['w']
        assert prime(p) and prime(q) and 0 < w <= p and 2*p >= 3*w
        assert 16*w < q and q < 16384 and q % 6 == 1
    # Exact exhaustive small-ring check of the coefficient bound used in the
    # written all-degree folding argument. No probabilistic sampling.
    products = 0
    p = 5
    for w in (1,2):
        for positions in combinations(range(p),w):
            for signs in product((-1,1),repeat=w):
                a=[0]*p
                for i,s in zip(positions,signs):a[i]=s
                for b in product((-1,0,1),repeat=p):
                    assert max(map(abs,integer_ring_product(a,b))) <= 2*w
                    products += 1
    for n in MC_LENGTHS:
        assert 768 < n <= 3488 and n % 8 == 0
        tau = mceliece_sampling_batch(n)
        assert 4096*64//tau <= n < 4096*128//tau
    # Binary Goppa toy instance over GF(8), g=X^2+X+1, all eight support points.
    # g has no root in GF(8); inverse(X+a)=(X+a+1)/(a^2+a+1) mod g.
    columns=[]
    for a in range(8):
        denom = gf_mul(a,a)^a^1
        assert denom
        inv=gf_inv(denom)
        columns.append((gf_mul(a^1,inv),inv))
    def syndrome(support):
        x=y=0
        for i in support:x ^= columns[i][0];y ^= columns[i][1]
        return x,y
    weights=[]
    for mask in range(1,256):
        support=[i for i in range(8) if mask >> i & 1]
        if syndrome(support)==(0,0):weights.append(len(support))
    assert weights and min(weights)>=5
    errors=list(combinations(range(8),2))
    assert len({syndrome(e) for e in errors}) == len(errors)
    hqc_path=ROOT/'crucible/data/hqc_model_profiles.json'
    hqc=load_reviewed_profiles(hqc_path)
    support=[supplemental_evidence(row) for row in hqc]
    assert [row['tier'] for row,e in zip(hqc,support) if e['deterministic_guarantee_established']]==[1,2,3,4]
    assert [e['residual_weight_maximum'] for e in support[:5]]==[6,15,28,45,66]
    assert all(e['failure_bound']=='0' for e in support[:4])
    sources=['crucible/ntru_reference.py','crucible/ntru_frontier.py',
             'crucible/data/ntru_frontier_profiles.json','crucible/code_parameters.py',
             'scripts/code_build_backend.py','crucible/code_native.py','crucible/code_hqc_v3.py',
             'crucible/hqc_model_correctness.py','crucible/data/hqc_model_profiles.json']
    return {'format':'crucible-scheme-code-argument-checks-v1',
      'scope':'Finite arithmetic/domain checks; not formal proofs, hardness validation, oracle-model validation or source-code refinement',
      'ntru_profiles_checked':len(ntru),'ntru_tiny_ring_products_exhausted':products,
      'mceliece_lengths_checked':len(MC_LENGTHS),'goppa_toy_nonzero_codewords':len(weights),
      'goppa_toy_minimum_distance':min(weights),'goppa_toy_weight_two_unique_syndromes':len(errors),
      'hqc_current_profiles_checked':len(hqc),'hqc_deterministic_stages':[1,2,3,4],
      'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}}

if __name__=='__main__':
    print(json.dumps(check(),indent=2,sort_keys=True))
