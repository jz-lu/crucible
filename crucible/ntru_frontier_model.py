"""A scoped classical non-hybrid Core-SVP reference, never a security proof.

Equations/search from the NTRU Prime authors' estimate-20200927.sage,
https://ntruprime.cr.yp.to/estimate-20200927.sage. The reference deliberately
excludes hybrid/dual/structural attacks and memory costs. Small dimensions
hitting the block-size floor are unsupported, not assigned a security value.
"""
from functools import lru_cache
import math

MODEL_ID = 'ntruprime-20200927-hybrid-classical-free-memory-v1'
SOURCE_SHA256 = 'f22b040c7ecc6bee5a94fd81921cd4bf0bd43094b7074613465fbf773fdd2158'

@lru_cache(maxsize=None)
def _logdelta(block):
    return (math.log(block) + math.log(math.pi * block) / block - math.log(2 * math.pi * math.e)) / (2 * (block - 1))

@lru_cache(maxsize=None)
def _logcomb(n, k):
    if not 0 <= k <= n:
        return -math.inf
    return math.log(math.comb(n, k))


def estimate(p, q, w):
    """Official non-hybrid search, with binary64 arithmetic; see calibration.

    Cost is 0.29248125036*beta plus forced-zero success penalty. All official
    force/sample/scale search bounds are retained. This is an estimate for
    this attack model alone, not the minimum of all attack costs.
    """
    if not all(type(v) is int for v in (p, q, w)) or not 0 < w <= p or q <= 2:
        raise ValueError('Invalid NTRU model parameters')
    if p < 40:
        return {'bits': None, 'status': 'outside-model-domain', 'reason': 'Fewer than 40 available lattice samples'}
    best, lastneeded = None, 0
    for force in range(p):
        if force > 20 and force > 2 * lastneeded:
            break
        if force > 20 and force % 10:
            continue
        if force > 100 and force % 20:
            continue
        if force > 400 and force % 40:
            continue
        logprob = _logcomb(p-force, w) - _logcomb(p, w)
        if not math.isfinite(logprob):
            continue
        probability = math.exp(logprob)
        amplified = 1.0 if probability >= 1 else -math.expm1(p * math.log1p(-probability))
        if amplified <= 0:
            continue
        penalty = -math.log2(amplified)
        spositions = p - force
        for m in range(40, p+1, 10):
            dimension = spositions + m
            for scale in (1.0, math.sqrt(2*p/(3*w)), math.sqrt(2*m/(3*w))):
                target = math.log(w*scale*scale + 2*m/3)/2 - (spositions*math.log(scale) + m*math.log(q))/dimension
                for block in range(40, m+1):
                    cost = 0.29248125036 * block + penalty
                    if best is not None and cost >= best['raw_bits']:
                        break
                    rhs = _logdelta(block) * (2*block-dimension) + math.log(dimension/block)/2
                    if target <= rhs:
                        best = {'raw_bits': cost, 'block_size': block, 'force': force,
                            'samples': m, 'scale': scale, 'force_penalty_bits': penalty}
                        lastneeded = force
                        break
    if best is None:
        return {'bits': None, 'status': 'outside-model-domain', 'reason': 'No successful attack found inside the bounded estimator search'}
    if best['block_size'] == 40:
        return {'bits': None, 'status': 'block-size-floor', **best,
            'reason': 'The asymptotic estimator reaches its minimum block size; this is not an estimated security level'}
    return {'bits': best['raw_bits'], 'status': 'scoped-reference', **best}


def _amplified_log2(logprob, count):
    if logprob >= 0:
        return 0.0
    if logprob < -700:
        return (logprob + math.log(count)) / math.log(2)
    probability = math.exp(logprob)
    return math.log2(-math.expm1(count * math.log1p(-probability)))


def _log2add(a, b):
    high, low = max(a, b), min(a, b)
    return high + math.log2(1 + 2**(low-high))


def estimate_with_hybrid(p, q, w):
    """Official classical sieving/free-memory hybrid search in stable floats.

    Only an offline calibration tool: requires SciPy. Includes the official
    simple and meet-in-the-middle searches, its nearest-plane success model,
    candidate weight ranges, and attack-parameter search restrictions. Remains
    an incomplete heuristic model with the upstream documented limitations.
    """
    import numpy as np
    from scipy.special import betainc
    nonhybrid=estimate(p,q,w)
    if nonhybrid['bits'] is None:
        return nonhybrid | {'nonhybrid_bits': None}
    best={'raw_bits':nonhybrid['bits'],'attack':'non-hybrid',
        'block_size':nonhybrid['block_size'],'parameters':nonhybrid}
    denominator=math.comb(p,w)
    tables={}
    for sigma in range(40,p,40):
        half=sigma//2
        c0=[math.comb(sigma,i)*2**i for i in range(sigma+1)]
        c1=[math.comb(half,i)*2**i for i in range(half+1)]
        suffix=[math.comb(p-sigma,w-i) if 0<=w-i<=p-sigma else 0 for i in range(sigma+1)]
        prob0=[math.comb(sigma,i)*suffix[i]/denominator for i in range(sigma+1)]
        ch=[math.comb(half,i) for i in range(half+1)]
        prob_l=[]
        for j in range(half+1):
            numerator=sum(ch[i]*ch[j]*suffix[i+j] for i in range(j+1))
            numerator+=sum(ch[j]*ch[i]*suffix[j+i] for i in range(j))
            prob_l.append(numerator/denominator)
        from itertools import accumulate
        tables[sigma]=(list(accumulate(c0)),list(accumulate(c1)),
            list(accumulate(prob0)),list(accumulate(prob_l)))
    for m in range(40,p+1,40):
        dimension=p+m
        for sigma,(cost0,cost1,prob0,prob_l) in tables.items():
            minid=dimension-sigma
            b=p-sigma
            vector_length=math.sqrt(b*w/p+2*m/3)
            for block in range(40,minid,40):
                delta=_logdelta(block)
                k=min(minid,math.floor(math.sqrt(b*math.log(q)/delta)))
                logs=np.concatenate((np.full(minid-k,math.log(q)),
                    (1-b/k)*math.log(q)+delta*(k-1-2*np.arange(k))))
                r2=np.minimum(1.0,np.exp(np.minimum(0.0,2*(logs-math.log(2*vector_length)))))
                factors=betainc(0.5,(minid-1)/2,r2)
                if np.any(factors<=0):
                    continue
                logprob=_amplified_log2(float(np.log(factors).sum()),p)
                lattice=0.29248125036*block
                for attack,costs,probs in [('simple',cost0,prob0),('mitm',[2*c for c in cost1],prob_l)]:
                    for imax in range(0,len(probs),5):
                        probability=probs[imax]
                        if probability<=0:
                            continue
                        cost=_log2add(lattice,math.log2(costs[imax]))-logprob-math.log2(probability)
                        if cost < best['raw_bits']:
                            best={'raw_bits':cost,'attack':attack,'block_size':block,
                                'parameters':{'samples':m,'guessed_positions':sigma,'imax':imax,
                                    'nearest_plane_log2_probability':logprob,
                                    'search_probability':probability}}
                        if probability>0.99:
                            break
    if best['block_size']==40:
        return {'bits':None,'status':'block-size-floor','nonhybrid_bits':nonhybrid['bits'],**best,
            'reason':'The heuristic hybrid model reaches the minimum block size; no security estimate is assigned'}
    return {'bits':best['raw_bits'],'status':'scoped-reference','nonhybrid_bits':nonhybrid['bits'],**best}
