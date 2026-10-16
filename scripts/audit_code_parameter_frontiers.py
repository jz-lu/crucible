#!/usr/bin/env python3
"""Bounded cost audit, never a cryptographic security estimator or activator."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from crucible.hqc_growth_parameters import read_profiles
from crucible.code_parameters import MC_LENGTHS


def prime(n):
    return n>=2 and all(n%d for d in range(2,math.isqrt(n)+1))


def primitive_ring(n):
    if not prime(n):
        return False
    remaining=n-1; factors=[]; d=2
    while d*d<=remaining:
        if remaining%d==0:
            factors.append(d)
            while remaining%d==0:
                remaining//=d
        d+=1
    if remaining>1:
        factors.append(remaining)
    return all(pow(2,(n-1)//factor,n)!=1 for factor in factors)


def first_ring(length):
    return next(n for n in range(length+1,length+4096) if primitive_ring(n))


def hqc_audit():
    codecs=[]
    for repeats in (1,2,3):
        for delta in range(1,16):
            length=(16+2*delta)*128*repeats
            n=first_ring(length)
            pk=32+(n+7)//8;ct=(n+7)//8+length//8+16
            codecs.append({'repetitions':repeats,'delta':delta,'length':length,'n':n,
                'capacity':32*repeats*(delta+1)-1,'public_key_bytes':pk,
                'ciphertext_bytes':ct,'communication_bytes':pk+ct})
    results=[]
    for row in read_profiles():
        weight=row['weight'];residual=2*weight*weight+weight
        choices=[c for c in codecs if c['capacity']>=residual]
        selected=min(choices,key=lambda c:(c['communication_bytes'],c['repetitions'],c['delta']))
        assert (selected['n'],selected['length'],selected['delta'],selected['repetitions'])==(row['n'],row['code_length'],row['rs_delta'],row['rm_repetitions'])
        results.append({'tier':row['tier'],'weight':weight,'qualifying_codec_count':len(choices),**selected})
    plateaus=[]
    for result in results:
        if plateaus and plateaus[-1]['communication_bytes']==result['communication_bytes']:
            plateaus[-1]['tiers'].append(result['tier'])
        else:
            plateaus.append({'communication_bytes':result['communication_bytes'],'tiers':[result['tier']]})
    return {'status':'codec-cost-minimum-only','candidate_codec_count':len(codecs),
        'objective':'Minimize canonical public-key plus ciphertext bytes at each fixed equal weight, within supported RS/RM domain',
        'fixed_constraints':'16-byte message, delta1..15, RM repetitions1..3, prime primitive ring, equal weights, deterministic disagreement0',
        'security_frontier':'Unresolved: no qualified complete QCSD/DOOM attack estimate for reduced tuples or search over unequal weights/message/code families',
        'timing':'No measured runtime/memory optimum; higher weights can increase sampler work at equal bytes',
        'plateaus':[p for p in plateaus if len(p['tiers'])>1],'profiles':results}


def mceliece_audit():
    rows=[]
    for tier,n in enumerate(MC_LENGTHS,1):
        target=8+(math.log2(math.comb(3488,64))-math.log2(math.comb(768,64))-8)*(tier-1)/49
        reference=math.log2(math.comb(n,64))-math.log2(math.comb(768,64))
        lower=math.log2(math.comb(n-8,64))-math.log2(math.comb(768,64)) if n-8>768 else None
        assert reference>=target-1e-10 and (lower is None or lower<target)
        rows.append({'tier':tier,'n':n,'m':12,'t':64,'public_key_bytes':768*((n-768+7)//8),
            'ciphertext_bytes':96,'prange_log2_trials':reference,'nominal_prange_target':target})
    diagnostic={'m':7,'n':120,'t':4,'public_key_bytes':28*((120-28+7)//8),'ciphertext_bytes':4,
        'prange_log2_trials':math.log2(math.comb(120,4))-math.log2(math.comb(28,4)),
        'status':'Unimplemented broader-domain algebraically admissible diagnostic, not an equal-security replacement',
        'qualification':'Same elementary Prange trial proxy says more expensive to attack, but polynomial elimination, improved decoding and low-degree structural key recovery are unmodeled. This does NOT establish actual security dominance.'}
    assert diagnostic['prange_log2_trials']>rows[0]['prange_log2_trials']
    return {'status':'restricted-shortening-path-only','objective':'Minimum byte-aligned n at each elementary Prange trial target with m12,t64 fixed',
        'security_frontier':'Unresolved: varying m,n,t requires qualified improved-ISD and structural estimates plus general finite-field/decoder implementation review',
        'witness_against_global_proxy_optimality':diagnostic,'profiles':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result={'format':'crucible-code-frontier-audit-v1','hqc':hqc_audit(),'classic-mceliece':mceliece_audit()}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Verified45HQCcodec candidates at27weights and50restrictedMcEliece length minima; no security-optimum claim.')

if __name__=='__main__':main()
