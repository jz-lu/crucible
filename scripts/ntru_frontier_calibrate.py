#!/usr/bin/env python3
"""Explicit offline comparison against the pinned NTRU authors' Sage estimator.

Run with SageMath Python and a separately downloaded, reviewed estimator file.
The given file is executed only after its exact source digest is checked.
This command has no network access, server activation or credentials.
"""
import argparse
import collections
import collections.abc
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from crucible.ntru_frontier_model import SOURCE_SHA256,estimate,estimate_with_hybrid


def main():
    import sage.all as sage
    from sage.repl.preparse import preparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--estimator',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raw=args.estimator.read_bytes()
    if len(raw)>100000 or hashlib.sha256(raw).hexdigest()!=SOURCE_SHA256:
        raise SystemExit('Estimator differs from the reviewed source pin')
    base=raw.decode().split('for line in sys.stdin:')[0]
    # Compatibility changes only: collections ABC moved in Python3; symbolic
    # NaN comparison is replaced by a numeric predicate to avoid Maxima startup.
    collections.Hashable=collections.abc.Hashable
    base=base.replace('assert lgcost != NaN','assert not __import__("math").isnan(float(lgcost))')
    marker="  for quantum,eors,memcost,lgsvp in estimates:\n    for hybrid in ['nonh','hybrid']:"
    if base.count(marker)!=1:
        raise SystemExit('Upstream return marker changed')
    base=base.replace(marker,'  return best\n'+marker)
    checks=[]
    for hybrid,tuples in [(False,[(653,4621,245),(653,4621,288),(761,4591,286),(617,12829,231)]),
            (True,[(653,2311,144),(653,4621,288)])]:
        source=base if hybrid else base.replace('if m%40 == 0 and force == 0:','if False:')
        namespace={name:getattr(sage,name) for name in dir(sage)}
        namespace.update(sys=sys,collections=collections)
        exec(preparse(source),namespace)
        # The hybrid check needs only the classical sieving/free-memory model.
        # Non-hybrid checks retain the original full model list/force search.
        if hybrid:
            namespace['estimates']=(('nonq','sieving','free',namespace['sieve']),)
        for p,q,w in tuples:
            reference=namespace['run'](sage.ZZ(p),sage.ZZ(q),sage.ZZ(w),True)
            kind='hybrid' if hybrid else 'nonh'
            bits,notes=reference[kind,'nonq','sieving','free']
            local=(estimate_with_hybrid if hybrid else estimate)(p,q,w)
            difference=abs(float(bits)-local['bits'])
            if difference>1e-8:
                raise SystemExit(f'Numerical mismatch for{(p,q,w)}: {difference}')
            checks.append({'p':p,'q':q,'w':w,'hybrid':hybrid,'official_bits':float(bits),
                'local_bits':local['bits'],'absolute_difference':difference,'official_search_notes':notes})
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps({'format':'crucible-ntru-estimator-calibration-v1',
                'upstream_source_sha256':SOURCE_SHA256,'sage':sage.version(),
                'compatibility_changes':['collections.Hashable alias','Numeric NaN predicate','Return result map instead of printing'],
                'scope':'Selected reproducibility checks, not independent validation of heuristic attack models','checks':checks},indent=2)+'\n')
            print(f'Compared{kind} {(p,q,w)}: {float(bits):.9f}; difference{difference:.2g}',flush=True)

if __name__=='__main__':main()
