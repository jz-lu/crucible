#!/usr/bin/env python3
"""Public-only attack on HQC low-weight research tier1 (w=wr=we=1).
No generation seeds, private keys, server target or native library required.
"""
import hashlib
import json
from pathlib import Path
import sys

N=17669
MASK=(1<<N)-1

def rotate(value,amount):
    return ((value<<amount)|(value>>(N-amount)))&MASK if amount else value

def solve(public):
    if public.get('parameters',{}).get('secret_weight')!=1 or public['parameters'].get('encryption_weight')!=1:
        raise ValueError('This example solves only the actual weight-one research profile')
    pk=bytes.fromhex(public['public_key']);ct=bytes.fromhex(public['ciphertext'])
    if len(pk)!=2241 or len(ct)!=4433:raise ValueError('Wrong public encoding')
    h=int.from_bytes(hashlib.shake_256(pk[:32]+b'\x01').digest(2209),'little')&MASK
    syndrome=int.from_bytes(pk[32:],'little');shifted=h;position=None
    for j in range(N):
        if (syndrome^shifted).bit_count()==1:
            position=j;break
        shifted=rotate(shifted,1)
    if position is None:raise ValueError('No weight-one private support found')
    u=int.from_bytes(ct[:2209],'little');v=int.from_bytes(ct[2209:4417],'little')
    noisy=v^(rotate(u,position)&((1<<17664)-1))
    # Actual duplicated RM(1,7), not a repetition-code substitute.
    words=[]
    for symbol in range(256):
        word=sum((((symbol>>7)^((symbol&127)&i).bit_count())&1)<<i for i in range(128))
        words.append(word|(word<<128)|(word<<256))
    decoded=[]
    for block in range(46):
        received=(noisy>>(384*block))&((1<<384)-1)
        decoded.append(min(range(256),key=lambda symbol:(received^words[symbol]).bit_count()))
    # At weight1 total noise≤3, hence all RM blocks decode exactly and the
    # systematic last16 symbols directly give the original128-bit message.
    message=bytes(decoded[-16:])
    hashpk=hashlib.sha3_256(pk+b'\x01').digest()
    return hashlib.sha3_512(hashpk+message+ct[-16:]+b'\x00').digest()[:32]

if __name__=='__main__':
    value=json.loads(Path(sys.argv[1]).read_text());public=value.get('challenge',value).get('public',value)
    print(solve(public).hex())
