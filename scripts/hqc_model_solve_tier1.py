#!/usr/bin/env python3
"""Public-only example for Crucible HQC modeled research configuration 1."""
import hashlib
import json
from pathlib import Path
import sys


def solve(public):
    parameters = public.get('parameters', {})
    if (public.get('scheme_id'), public.get('version'), public.get('profile_id')) != ('hqc', 'modeled-research-v3', 'hqc-modeled-research-v3-tier-1'):
        raise ValueError('This example accepts only the exact modeled research configuration 1')
    if tuple(parameters.get(name) for name in ('secret_weight', 'encryption_weight', 'error_weight')) != (1, 2, 2):
        raise ValueError('Unexpected research weight distribution')
    n = parameters['n']; code_length = parameters['code_length']; rm_length = parameters['rm_length']
    n_bytes = (n+7)//8; mask = (1<<n)-1
    pk = bytes.fromhex(public['public_key']); ct = bytes.fromhex(public['ciphertext'])
    if len(pk) != 32+n_bytes or len(ct) != n_bytes+code_length//8+16:
        raise ValueError('Wrong public encoding')
    def rotate(value, shift):
        return ((value<<shift)|(value>>(n-shift)))&mask if shift else value
    h = int.from_bytes(hashlib.shake_256(pk[:32]+b'\x01').digest(n_bytes), 'little')&mask
    syndrome = int.from_bytes(pk[32:], 'little'); shifted = h
    for position in range(n):
        if (syndrome^shifted).bit_count() == 1:
            break
        shifted = rotate(shifted, 1)
    else:
        raise ValueError('No weight-one private support found')
    u = int.from_bytes(ct[:n_bytes], 'little')
    v = int.from_bytes(ct[n_bytes:-16], 'little')
    noisy = v ^ (rotate(u, position)&((1<<code_length)-1))
    words = []
    for symbol in range(256):
        word = sum((((symbol>>7)^((symbol&127)&i).bit_count())&1)<<i for i in range(128))
        words.append(sum(word<<(128*j) for j in range(parameters['rm_repetitions'])))
    decoded = []
    for block in range(parameters['rs_length']):
        received = (noisy>>(rm_length*block))&((1<<rm_length)-1)
        decoded.append(min(range(256), key=lambda symbol: (received^words[symbol]).bit_count()))
    # At weights (1,2,2), E<=2*1*2+2=6, below the RM unique-decoding
    # threshold 32. Thus each symbol and the systematic RS suffix are exact.
    message = bytes(decoded[-16:])
    hashpk = hashlib.sha3_256(pk+b'\x01').digest()
    return hashlib.sha3_512(hashpk+message+ct[-16:]+b'\x00').digest()[:32]

if __name__ == '__main__':
    value = json.loads(Path(sys.argv[1]).read_text())
    public = value.get('challenge', value).get('public', value)
    print(solve(public).hex())
