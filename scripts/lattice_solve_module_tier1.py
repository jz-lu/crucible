#!/usr/bin/env sage-python
"""Experimental public-only lattice solver for large-modulus research Tier1.

Sage's fpylll dependency supplies LLL and nearest-plane. No completed solve
has been verified in the bounded local demonstration; this is a research sketch. This script never
reads a server target, private key, generation seed or account credential.
"""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from crucible import lattice_module as kem


def solve(public):
    from fpylll import IntegerMatrix, LLL, CVP
    if public.get('scheme_id')!=kem.SCHEME_ID or public.get('version')!=kem.SCHEME_VERSION or public.get('profile_id')!='module-lwe-v1-tier-1':
        raise ValueError('This example attacks only Module-LWE research Tier1')
    q=kem.MODULI[0];pk=bytes.fromhex(public['public_key']);ct=bytes.fromhex(public['ciphertext'])
    rho,t=kem._decode_pk(pk,q);rank=kem._rank(q);n=kem.N*rank
    a=[[kem.uniform_poly(rho,q,i,j) for j in range(rank)] for i in range(rank)]
    basis=IntegerMatrix(2*n,2*n)
    for i in range(n):basis[i,i]=q;basis[n+i,n+i]=1
    for i in range(rank):
        for j in range(rank):
            for h in range(kem.N):
                for k in range(kem.N):
                    coefficient=a[i][j][(k-h)%kem.N]
                    basis[n+j*kem.N+h,i*kem.N+k]=coefficient if k>=h else -coefficient
    LLL.reduction(basis,delta=.75,method="heuristic",float_type="mpfr",precision=256)
    target=[x for poly in t for x in poly]+[0]*n
    closest=CVP.babai(basis,target)
    secret=list(closest[n:])
    if any(abs(x)>kem.ETA for x in secret):
        raise RuntimeError('Nearest plane did not recover a CBD2 secret; no result claimed')
    s=[secret[i*kem.N:(i+1)*kem.N] for i in range(rank)]
    polynomials=kem.unpack(ct,rank+1,q)
    product=kem._sum_products(s,polynomials[:rank],[0]*kem.N,q)
    values=[(x-y)%q for x,y in zip(polynomials[-1],product)]
    message=sum((((2*x+q//2)//q)&1)<<i for i,x in enumerate(values)).to_bytes(32,'little')
    derived=hashlib.sha3_512(message+hashlib.sha3_256(pk).digest()).digest()
    if kem._encrypt(q,pk,message,derived[32:])!=ct:raise RuntimeError('Candidate failed full reencryption')
    return derived[:32]


if __name__=='__main__':
    value=json.loads(Path(sys.argv[1]).read_text());public=value.get('challenge',value).get('public',value)
    print(solve(public).hex())
