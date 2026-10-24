"""Exact upper bounds in HQC's explicitly conditional 2026 Model 1.

This proves arithmetic inside the model, not that independent Bernoulli noise
conservatively bounds the decoder failure of actual correlated HQC ring products. See docs/hqc-model-v3.txt.
No sampling, native execution or parameter search occurs on import.
"""
from fractions import Fraction
from functools import lru_cache
from math import comb, log2

MODEL_ID = 'hqc-20261012-model1-ties-bias-v1'
SPEC_URL = 'https://pqc-hqc.org/doc/hqc_specifications_2026_10_12.pdf'
P_BITS = 64
BOUND_BITS = 256


def choose(n, k):
    return comb(n, k) if 0 <= k <= n else 0


def ceil_dyadic(value, bits):
    """Round nonnegative rational upward, with no floating-point decisions."""
    if value < 0:
        raise ValueError('A probability bound must be nonnegative')
    return Fraction((value.numerator * (1 << bits) + value.denominator - 1)
                    // value.denominator, 1 << bits)


def marginal(n, w, wr, we):
    """Exact one-coordinate marginal for independent uniform fixed supports."""
    if not all(type(v) is int for v in (n, w, wr, we)) or not 1 <= w <= wr <= n or not 1 <= we <= n:
        raise ValueError('Invalid HQC support parameters')
    denominator = comb(n, wr)
    odd = sum(choose(w, j) * choose(n - w, wr - j)
              for j in range(1, min(w, wr) + 1, 2))
    even = denominator - odd
    return Fraction(2 * odd * even * (n - we) + (odd * odd + even * even) * we,
                    denominator * denominator * n)


@lru_cache(maxsize=3)
def rm_counts_twice(repetitions):
    """Twice A_omega in the 2026 specification's Proposition 6.1.4.

    Includes the two-way and multiway tie terms. The all-one word is covered
    by the trivial cap at weights >=d, exactly as in that proposition.
    """
    if repetitions not in (1, 2, 3):
        raise ValueError('This reviewed codec domain has repetitions 1..3')
    d = 64 * repetitions
    length = 2 * d
    result = []
    for weight in range(length + 1):
        ties = 255 * choose(d, d // 2) * choose(d, weight - d // 2)
        closer = 510 * sum(choose(d, j) * choose(d, weight - j)
                           for j in range(d // 2 + 1, d + 1))
        multiway = choose(255, 2) * sum(choose(d // 2, j) ** 3
                                      * choose(d // 2, weight - d + j)
                                      for j in range(d // 2 + 1))
        result.append(min(2 * choose(length, weight), ties + closer + multiway))
    # Finite exact verification permits monotone upward rounding of p.
    if not all(result[j] * choose(length, j + 1) <= result[j + 1] * choose(length, j)
               for j in range(length)):
        raise ValueError('Inner error majorant is not increasing with weight')
    return tuple(result)


def inner_bounds(p, repetitions):
    """Bounds for decoding failure and any tie with the transmitted word."""
    if not 0 <= p <= Fraction(1, 2):
        raise ValueError('Only the reviewed p<=1/2 regime is supported')
    p = ceil_dyadic(p, P_BITS)
    a, b = p.numerator, p.denominator
    q = b - a
    d = 64 * repetitions
    length = 2 * d
    ap = [pow(a, j) for j in range(length + 1)]
    qp = [pow(q, j) for j in range(length + 1)]
    failure = Fraction(sum(c * ap[j] * qp[length - j]
                           for j, c in enumerate(rm_counts_twice(repetitions))),
                       2 * pow(b, length))
    # 254 weight-d words plus the weight-2d word. Its central tie probability
    # is no larger than the weight-d one for p<=1/2; hence the factor255.
    tie = min(Fraction(1), Fraction(255 * choose(d, d // 2) * pow(a * q, d // 2),
                                   pow(b, d)))
    return ceil_dyadic(failure, BOUND_BITS), ceil_dyadic(tie, BOUND_BITS), p


def binomial_tail(trials, first, p):
    if first > trials:
        return Fraction(0)
    a, b = p.numerator, p.denominator
    q = b - a
    return Fraction(sum(choose(trials, j) * pow(a, j) * pow(q, trials - j)
                        for j in range(first, trials + 1)), pow(b, trials))


def model_bound(n, w, wr, we, rs_length, repetitions):
    """Conservative bound under Model1, including the retained native sampler.

    Scope: average honest failure over uniform128-bit messages and independent
    ideal expanded key/encapsulation noise, followed by the Model1 surrogate.
    The transfer of the surrogate bound to actual correlated ring products and
    finite-seed SHAKE remains an explicit conjectural premise, not established by this function.
    """
    if type(rs_length) is not int or not 18 <= rs_length <= 46 or rs_length % 2:
        raise ValueError('Reviewed RS lengths are18,20,...,46 with dimension16')
    if 128 * repetitions * rs_length >= n:
        raise ValueError('Ring must strictly contain the concatenated code')
    p = marginal(n, w, wr, we)
    inner, tie, rounded_p = inner_bounds(p, repetitions)
    delta = (rs_length - 16) // 2
    outer = binomial_tail(rs_length, delta + 1, inner)
    # Any16 distinct RS coordinates of a uniform message are independent.
    # Current spec6.1.5 accounts for deterministic tie-rule coupling this way.
    tie_correction = min(Fraction(1), choose(rs_length, 17) * tie ** 17)
    # The published fixed-work sampler has density ratio at most this value.
    # Each of its32-bit bounded draws has ratio <=1+n/2^32; x,y are uniform.
    bias = Fraction((1 << 32) + n, 1 << 32) ** (2 * wr + we)
    bound = min(Fraction(1), (outer + tie_correction) * bias)
    return {'bound': bound, 'marginal': p, 'rounded_marginal': rounded_p,
            'inner_bound': inner, 'tie_bound': tie, 'outer_bound': outer,
            'deterministic_tie_correction': tie_correction, 'sampler_density_ratio': bias}


def exponent_floor(bound):
    """Largest nonnegative integer b such that bound <=2^-b, exactly."""
    if not 0 < bound <= 1:
        raise ValueError('Expected a strictly positive probability bound')
    result = max(0, bound.denominator.bit_length() - bound.numerator.bit_length())
    if bound.numerator << result > bound.denominator:
        result -= 1
    return result


def log2_display(value):
    """Presentation only. All admission/comparison decisions use rationals."""
    return None if not value else log2(value.numerator) - log2(value.denominator)


def certificate(n, w, wr, we, rs_length, repetitions):
    result = model_bound(n, w, wr, we, rs_length, repetitions)
    return {'model_id': MODEL_ID, 'target_failure_bound_exponent': 128,
            'certified_model_bound_exponent': exponent_floor(result['bound']),
            'meets_target': result['bound'].numerator << 128 <= result['bound'].denominator,
            'log2_bound_display': log2_display(result['bound']),
            'log2_outer_bound_display': log2_display(result['outer_bound']),
            'log2_tie_correction_display': log2_display(result['deterministic_tie_correction']),
            'marginal_display': float(result['marginal']),
            'rounded_marginal': {'numerator': result['rounded_marginal'].numerator,
                                 'denominator': result['rounded_marginal'].denominator},
            'source': SPEC_URL, 'sections': ['6.1.1', 'Proposition6.1.4', 'Theorem6.1', '6.1.5', '6.2.3'],
            'evidence_class': 'exact arithmetic conditional on stated noise/model and implementation premises'}
