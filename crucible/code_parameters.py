"""Frozen research parameters; navigation tiers are not comparable security bits."""
from math import comb,log2

def prange_log2_trials(n):
    return log2(comb(n,64))-log2(comb(768,64))

def mceliece_sampling_batch(n):
    """Exact FixedWeight tau from Classic McEliece 2022-10-23, section 5.4."""
    if type(n) is not int or not 768 < n <= 3488:
        raise ValueError('Unsupported McEliece code length')
    tau, threshold = 64, 4096
    while n < threshold:
        tau *= 2
        threshold //= 2
    return tau

# Fixed m=12,t=64; shortest byte-aligned n reaching each evenly spaced
# log2 expected-trial reference from8 to the actual348864 Prange reference.
# This count excludes elimination cost and all improved/structural attacks.
MC_LENGTHS = (840, 864, 888, 912, 944, 968, 992, 1024, 1056, 1088, 1120, 1152, 1184, 1216, 1256, 1288, 1328, 1368, 1408, 1448, 1496, 1536, 1584, 1632, 1680, 1728, 1776, 1832, 1888, 1944, 2000, 2056, 2120, 2184, 2248, 2312, 2384, 2456, 2528, 2600, 2680, 2760, 2840, 2928, 3016, 3104, 3200, 3296, 3392, 3488)
HQC_WEIGHTS = tuple(range(1,28))
HQC_N=17669
HQC_DECODER_GUARANTEED_ERRORS=1535

def hqc_worst_case_errors(weight):
    return 2*weight*weight+weight

assert len(set(MC_LENGTHS))==50 and MC_LENGTHS[-1]==3488
assert all(768<n<=3488 and n%8==0 for n in MC_LENGTHS)
assert all(hqc_worst_case_errors(w)<=HQC_DECODER_GUARANTEED_ERRORS for w in HQC_WEIGHTS)
