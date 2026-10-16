#!/usr/bin/env python3
"""Public-only Prange decoder for the small-length McEliece research profile.
Classical information-set trials, bounded wall time; no private generation data.
"""
import hashlib
import json
from pathlib import Path
import random
import sys
import time

def solve(public,timeout=120):
    n=public['parameters']['n'];m=public['parameters']['m'];t=public['parameters']['t'];r=m*t
    if (m,t)!=(12,64) or not 768<n<=1024 or n%8:raise ValueError('Example limited to the shortest research lengths')
    k=n-r;pk=bytes.fromhex(public['public_key']);cipher=bytes.fromhex(public['ciphertext'])
    if len(pk)!=r*k//8 or len(cipher)!=r//8:raise ValueError('Wrong public encoding')
    rows=[int.from_bytes(pk[i*k//8:(i+1)*k//8],'little') for i in range(r)]
    columns=[sum(((rows[i]>>j)&1)<<i for i in range(r)) for j in range(k)]
    syndrome=int.from_bytes(cipher,'little');deadline=time.monotonic()+timeout;trials=0;rng=random.SystemRandom()
    while time.monotonic()<deadline:
        trials+=1;selected=set(rng.sample(range(n),r));basis=[None]*r
        # Insert identity columns first: mathematically the same random basis,
        # with substantially less Python elimination work at these short n.
        for j in selected:
            if j<r:basis[j]=(1<<j,1<<j)
        rank=sum(item is not None for item in basis)
        for j in sorted(selected):
            if j<r:continue
            vector=columns[j-r];combination=1<<j
            while vector:
                pivot=vector.bit_length()-1
                if basis[pivot] is None:
                    basis[pivot]=(vector,combination);rank+=1;break
                vector^=basis[pivot][0];combination^=basis[pivot][1]
        if rank!=r:continue
        vector=syndrome;error=0
        while vector:
            pivot=vector.bit_length()-1;vector^=basis[pivot][0];error^=basis[pivot][1]
        if error.bit_count()==t:
            shared=hashlib.shake_256(b'\x01'+error.to_bytes(n//8,'little')+cipher).digest(32)
            return shared,trials
    raise TimeoutError(f'No recovery in {trials} bounded public trials')

if __name__=='__main__':
    value=json.loads(Path(sys.argv[1]).read_text());public=value.get('challenge',value).get('public',value)
    secret,trials=solve(public);print(secret.hex());print(f'Prange trials: {trials}',file=sys.stderr)
