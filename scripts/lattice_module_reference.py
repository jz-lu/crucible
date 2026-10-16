#!/usr/bin/env sage-python
"""Independent Sage polynomial-ring reference for frozen research KATs.

This intentionally does not import the challenge adapter. It specifies the
research construction; these are NOT NIST ML-KEM vectors.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sage.all import GF, PolynomialRing


def vector(q,seed,rank=3):
    ring=PolynomialRing(GF(q),'x');x=ring.gen();quotient=ring.quotient(x**256+1)
    width=(q.bit_length()+7)//8
    def coefficients(poly):
        values=[int(a) for a in poly.lift().list()]
        return values+[0]*(256-len(values))
    def serialize(polys):return b''.join(v.to_bytes(width,'little') for poly in polys for v in coefficients(poly))
    def noise(sigma,nonce):
        data=hashlib.shake_256(sigma+bytes([nonce])).digest(128)
        bits=[(value>>j)&1 for value in data for j in range(8)]
        return quotient([bits[4*i]+bits[4*i+1]-bits[4*i+2]-bits[4*i+3] for i in range(256)])
    def matrix(seed_a,row,column):
        domain=b'Crucible-Module-LWE-v1/A'+q.to_bytes(16,'little')+seed_a+bytes([row,column])
        data=hashlib.shake_128(domain).digest(width*256*32)
        values=[]
        for offset in range(0,len(data),width):
            value=int.from_bytes(data[offset:offset+width],'little')%(2**q.bit_length())
            if value<q:values.append(value)
            if len(values)==256:return quotient(values)
        raise RuntimeError('Sampling bound exceeded')
    key_coins=hashlib.shake_256(b'key'+seed.to_bytes(2,'little')).digest(64)
    message=hashlib.shake_256(b'message'+seed.to_bytes(2,'little')).digest(32)
    rho_sigma=hashlib.sha3_512(key_coins[:32]+bytes([rank])).digest();rho,sigma=rho_sigma[:32],rho_sigma[32:]
    a=[[matrix(rho,i,j) for j in range(rank)] for i in range(rank)]
    s=[noise(sigma,i) for i in range(rank)];e=[noise(sigma,rank+i) for i in range(rank)]
    t=[sum((a[i][j]*s[j] for j in range(rank)),quotient.zero())+e[i] for i in range(rank)]
    pk=b'MLW1'+q.to_bytes(16,'little')+rho+serialize(t)
    centered=[v if v<=q//2 else v-q for p in s for v in coefficients(p)]
    sk=key_coins[32:]+bytes(v%256 for v in centered)+pk+hashlib.sha3_256(pk).digest()
    derived=hashlib.sha3_512(message+hashlib.sha3_256(pk).digest()).digest();coins=derived[32:]
    sp=[noise(coins,i) for i in range(rank)];ep=[noise(coins,rank+i) for i in range(rank)];epp=noise(coins,2*rank)
    u=[sum((a[j][i]*sp[j] for j in range(rank)),quotient.zero())+ep[i] for i in range(rank)]
    bits=[(byte>>j)&1 for byte in message for j in range(8)]
    v=sum((t[i]*sp[i] for i in range(rank)),quotient.zero())+epp+quotient([b*((q+1)//2) for b in bits])
    ct=serialize(u+[v])
    return {'q':str(q),'seed':seed,'key_randomness':key_coins.hex(),'encapsulation_message':message.hex(),
        'pk_sha256':hashlib.sha256(pk).hexdigest(),'sk_sha256':hashlib.sha256(sk).hexdigest(),
        'ct_sha256':hashlib.sha256(ct).hexdigest(),'shared_secret':derived[:32].hex()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--calibration',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    profiles=json.loads(args.calibration.read_text())['profiles']
    values=[vector(int(profiles[t-1]['q']),seed,1 if t==1 else 3) for t,seed in ((1,0),(2,1),(12,2),(25,3),(37,4),(50,5))]
    result={'format':'crucible-independent-research-kat-v1','authority':'Independent SageMath10.1 finite-field polynomial quotient implementation; not published NIST vectors',
        'reference_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'vectors':values}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
