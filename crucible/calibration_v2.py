"""Versioned, deliberately limited classical rho/NFS reference calibration.

This is an initial comparison convention, not a certified security estimator or
a prediction of elapsed time. Frozen adapters embed their own parameter table;
changing this module must never silently change an issued challenge contract.
"""
import math


MIN_TIER = 1
MAX_TIER = 50
MIN_MODULUS_BITS = 32
MAX_MODULUS_BITS = 3072
MODEL_ID = "classical-baseline-v2"

MODEL = {
    "id": MODEL_ID,
    "name": "Initial classical rho/NFS reference",
    "status": "heuristic-reference",
    "tier_range": {"min": MIN_TIER, "max": MAX_TIER},
    "target_mapping": "Tiers 1–50 target 8+120(t−1)/49 reference bits, evenly spaced from 8 to 128. Tier 50 targets 128.",
    "modulus_mapping": "Choose the smallest integer modulus bit length n from 32 through 3072 for which min(n/4, G(n)) meets the target.",
    "formula": "G(n) = 128 F(n)/F(3072), where F(n) = (n ln 2)^(1/3) [ln(n ln 2)]^(2/3). RSA uses min(n/4, G(n)); DH uses min(log2(q)/2, G(n)) for the registered subgroup order q.",
    "anchor": {"tier": 50, "target_bits": 128, "modulus_bits": 3072},
    "scope": "Classical, single-target primitive-recovery reference including setup work. Public inputs only; no decapsulation oracle. RSA models factoring via rho and a normalized NFS leading term; DH models subgroup discrete logarithm via rho and the same normalized prime-field NFS term. This is not a general KEM confidentiality estimate.",
    "limitations": [
        "The normalized NFS expression and tier spacing are project conventions, not a NIST formula or a validated finite-size attack-cost estimator.",
        "RSA's n/4 term is a balanced-factor bit-length approximation. ECM, quadratic sieve, special-purpose attacks and algorithmic constants are not modeled; low-tier estimates are especially rough.",
        "Arithmetic-operation models differ between algorithms and parameter sizes. These values do not predict hardware cost, seconds, or what can be recovered within one hourly round.",
        "DH groups are reused. Group-specific preparation can be amortized across targets; the reference includes setup and is not an online-only cost estimate.",
        "The registered certified-prime generation distributions are explicit construction choices; the model does not prove their equivalence to uniformly sampled primes or exclude special-structure attacks.",
        "These classical primitive estimates do not establish IND-CPA or IND-CCA security, executable safety, or quantum resistance of the custom KEM constructions.",
        "Keep the original model and parameter mapping fixed after attacks. New evidence or a revised estimator must be separately versioned, without rewriting historical instances.",
    ],
    "references": [
        {"title": "NIST SP 800-57 Part 1 Rev. 5, Table 2: conventional 3072-bit / 128-bit reference", "url": "https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-57pt1r5.pdf"},
        {"title": "RFC 7919, Appendix A.2: a different 125-bit estimate for ffdhe3072", "url": "https://www.rfc-editor.org/rfc/rfc7919.html#appendix-A.2"},
        {"title": "Joux et al., The Number Field Sieve in the Medium Prime Case", "url": "https://iacr.org/archive/crypto2006/41170323/41170323.pdf"},
        {"title": "Pollard, A Monte Carlo Method for Factorization", "url": "https://pages.cs.wisc.edu/~cs812-1/pollardrho.pdf"},
        {"title": "Lenstra, Factoring Integers with Elliptic Curves (an omitted method)", "url": "https://annals.math.princeton.edu/1987/126-3/p09"},
        {"title": "Logjam: reusable prime-field discrete-log preparation", "url": "https://weakdh.org/logjam.html"},
    ],
}


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def target_bits(tier):
    """Return the common target, not an estimate of a particular instance."""
    tier = _integer(tier, MIN_TIER, MAX_TIER, "Tier")
    return 8.0 + 120.0 * (tier - 1) / 49


def _nfs_shape(modulus_bits):
    log_modulus = modulus_bits * math.log(2)
    return log_modulus ** (1 / 3) * math.log(log_modulus) ** (2 / 3)


_ANCHOR_SHAPE = _nfs_shape(MAX_MODULUS_BITS)


def _nfs_bits(modulus_bits):
    return 128.0 * _nfs_shape(modulus_bits) / _ANCHOR_SHAPE


def estimate_bits(modulus_bits):
    """Return the unrounded balanced-factor rho/NFS reference in this domain."""
    modulus_bits = _integer(modulus_bits, MIN_MODULUS_BITS, MAX_MODULUS_BITS, "Modulus bit length")
    return min(modulus_bits / 4.0, _nfs_bits(modulus_bits))


def modulus_bits_for_tier(tier):
    """Select the smallest integer bit length meeting this model's target."""
    target = target_bits(tier)
    lower, upper = MIN_MODULUS_BITS, MAX_MODULUS_BITS
    while lower < upper:
        middle = (lower + upper) // 2
        if estimate_bits(middle) >= target:
            upper = middle
        else:
            lower = middle + 1
    return lower


def profile_estimate(tier, modulus_bits, subgroup_order=None):
    """Keep target and parameter-derived reference separate, including rounding.

    For DH, the actual registered q supplies the generic discrete-log term.
    This helper does not validate primality, certificates, or q dividing p−1;
    the frozen adapter must do that. It never raises a weak estimate to target.
    """
    target = target_bits(tier)
    estimate = estimate_bits(modulus_bits)
    if subgroup_order is not None:
        if type(subgroup_order) is not int or subgroup_order <= 1 or subgroup_order.bit_length() > modulus_bits:
            raise ValueError("Subgroup order must be an integer greater than 1 within the modulus bit length")
        estimate = min(math.log2(subgroup_order) / 2, _nfs_bits(modulus_bits))
    return {"model_id": MODEL_ID, "bits": round(estimate, 6), "target_bits": round(target, 6)}
