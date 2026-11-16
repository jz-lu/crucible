"""Explicit code-based attack budgets, not best-known security estimates.

Prange's support-hit probability is exact. Its conditional rank-density premise
is deliberately a visible, unverified hypothesis for these structured matrices.
Executable small-instance kernels check the decoding path and probability
identity; they do not establish that premise at cryptographic dimensions.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
import hashlib
from math import comb

from . import common as ram


SOURCES = [
    {"title": "Classic McEliece specification (2022-10-23)",
     "url": "https://classic.mceliece.org/mceliece-spec-20221023.pdf"},
    {"title": "Classic McEliece security analysis: Prange and improved decoding",
     "url": "https://classic.mceliece.org/mceliece-security-20221023.pdf"},
    {"title": "HQC specification (2025-08-22), exact retained construction",
     "url": "https://pqc-hqc.org/doc/hqc_specifications_2025_08_22.pdf"},
]


def _dims(length, rows, weight):
    if any(type(x) is not int for x in (length, rows, weight)):
        raise ValueError("Code dimensions must be integers")
    if not 1 <= weight <= rows <= length or length > 2**20:
        raise ValueError("Unsupported code dimensions")


def support_hit_probability(length, rows, weight):
    """A uniformly selected rows-subset contains a FIXED weight-w support."""
    _dims(length, rows, weight)
    return Fraction(comb(rows, weight), comb(length, weight))


def prange_budget(length, rows, weight):
    """Finite budget under rank density >=1/8 on >=7/8 of target instances.

    The 64-attempt bounded Fisher-Yates sampler aborts a trial on exhaustion.
    Its per-trial abort probability is <=rows*2^-64<1/2 here. Conditional on
    success its selected subset is uniform. For a good target each trial then
    succeeds with probability >=hit/16. R=ceil(32/hit) gives failure<=exp(-2)
    <1/4, hence unconditional success >=21/32 before downstream failures.
    """
    _dims(length, rows, weight)
    address = (length * rows + length * 32 + rows * rows + 4096).bit_length()
    step = 8 + 4 * ram.loop_control(address) + 4 * ram.add_bits(address)
    # Initial integer index array, then bounded unbiased partial Fisher-Yates.
    sampler = length * (2 * address + ram.loop_control(address))
    sampler += rows * (64 * (address + 2 * ram.compare_bits(address)
                            + ram.loop_control(address))
                       + 8 * address + 4 * ram.add_bits(address)
                       + ram.loop_control(address))
    # Extract [H_J | syndrome], Gaussian elimination, explicit syndrome and
    # weight recheck. Each cell slot covers reads/writes, XOR, pointer arithmetic
    # and loop/control. The cubic census includes pivot scans and row swaps.
    extraction = rows * (rows + 1) * step
    elimination = (rows**3 + 4 * rows**2 + 4 * rows) * step
    verification = (rows * length + 4 * length) * step
    hit = support_hit_probability(length, rows, weight)
    trials = (32 * hit.denominator + hit.numerator - 1) // hit.numerator
    trial_control = ram.loop_control(trials.bit_length())
    per_trial = sampler + extraction + elimination + verification + trial_control
    return {
        "trials": trials,
        "per_trial": per_trial,
        "sampling": sampler,
        "extraction": extraction,
        "elimination": elimination,
        "verification": verification,
        "trial_control": trial_control,
        "work": trials * per_trial,
        "memory_bits": rows * length + rows * (rows + 1) + length * address
                       + 2 * rows * address + 4 * length + 8 * rows
                       + trials.bit_length() + 4096,
        "hit_numerator": hit.numerator,
        "hit_denominator": hit.denominator,
        "address_bits": address,
    }


def rank_density_assumption():
    return {
        "id": "prange-structured-rank-density-v1",
        "title": "Enough information sets remain invertible",
        "human": (
            "For at least seven eighths of honest targets, at least one eighth "
            "of the selected column sets that contain the true error support "
            "give an invertible matrix. This is a working hypothesis about the "
            "actual structured public matrices; it has not been established "
            "for every stage. The curve is conditional on it."
        ),
        "technical": (
            "Write H as an r-by-N binary matrix and e for the actual weight-t "
            "target. With probability at least7/8 over the honest target draw, "
            "Pr_J[rank(H_J)=r | supp(e) subset J] >=1/8, where J is uniform "
            "among r-subsets. This is a per-instance density condition on a "
            "7/8-measure good set, not an average success probability that can "
            "be amplified without justification. Fresh trials are independent "
            "conditional on H,e. Neither random-matrix heuristics nor toy checks "
            "prove this claim for binary Goppa or quasi-cyclic matrices."
        ),
    }


def _hqc_finish_cost(n, rs_length, rm_length, weight):
    """Account for cyclic multiply, nearest-RM, polynomial RS decoding and KDF."""
    a = (4 * n + 256 * rm_length + 64 * rs_length**2).bit_length()
    slot = 8 + 2 * ram.add_bits(a) + 2 * ram.loop_control(a)
    ring = n * weight * slot
    # Generate 256 affine RM codewords once; compare every symbol, with an
    # explicitly charged Hamming-distance accumulator and argmin comparator.
    rm_table = 256 * (128 * (16 + ram.loop_control(8)) + rm_length * 2)
    count_width = rm_length.bit_length()
    nearest = rs_length * 256 * (
        rm_length * (4 + ram.add_bits(count_width) + 2 * ram.add_bits(a)
                     + 2 * ram.loop_control(a))
        + ram.compare_bits(count_width) + ram.mux_bits(8)
    )
    # GF(256) multiplication: eight shift/XOR/reduction rounds, <=1024 bit ops.
    # Inversion x^254 takes <=16 such multiplications. Count syndromes,
    # Berlekamp-Massey, roots over all stored coordinates, Vandermonde error
    # solve, correction and syndrome recheck. The deliberately loose census
    # is 4L^3+32L^2+512L+4096 field/control slots. Each slot also pays indirect
    # transfer/address/loop overhead. This is a specified simple decoder,
    # not the optimized native decoder's measured runtime.
    rs_slots = 4 * rs_length**3 + 32 * rs_length**2 + 512 * rs_length + 4096
    rs = rs_slots * (1024 + slot)
    pk_bytes = 32 + (n + 7) // 8
    kdf = ram.sha3_256(pk_bytes + 1) + ram.sha3_512(32 + 16 + 16 + 1)
    return {"cyclic_multiply": ring, "rm_table": rm_table,
            "nearest_rm": nearest, "rs_decode": rs, "key_derivation": kdf}


def _hqc_unique_failure(n, weight):
    # For distinct equal-weight y,y', y+y' has even parity and degree<n-1.
    # Primitive-prime n makes it invertible on the (n-1)-dimensional factor.
    # Therefore h(y+y') is uniform in the even-parity subspace for uniform h.
    # Union-bound alternative (x',y') pairs of prescribed weight.
    candidates = comb(n, weight)
    return Fraction(candidates * (candidates - 1), 1 << (n - 1))


def _hqc_support_correctness(params):
    """Apply the existing October29 support proof to the actual tuple.

    Return (residual maximum, first possibly failing weight) only when the
    strict inequality is established. Missing or inconsistent codec/weight
    parameters leave the statistical correctness premise in place.
    """
    n = params.get("n")
    w = params.get("secret_weight", params.get("weight"))
    wr, we = params.get("encryption_weight"), params.get("error_weight")
    length, rm_length = params.get("rs_length"), params.get("rm_length")
    dimension = params.get("rs_dimension", 16)
    if any(type(value) is not int for value in (n, w, wr, we, length, rm_length, dimension)):
        return None
    if (dimension != 16 or length not in range(18, 47, 2)
            or rm_length not in (128, 256, 384)
            or not 0 <= w <= n or not 0 <= wr <= n or not 0 <= we <= n
            or length * rm_length > n):
        return None
    delta, repeats = (length - dimension) // 2, rm_length // 128
    if (params.get("rs_delta", delta) != delta
            or params.get("rm_repetitions", repeats) != repeats
            or params.get("code_length", length * rm_length) != length * rm_length):
        return None
    residual = 2 * w * wr + we
    threshold = 32 * repeats * (delta + 1)
    return (residual, threshold) if residual < threshold else None


def estimate(scheme_id, version, profile):
    """Return a complete, explicitly conditional primitive-to-KEM work budget."""
    params = profile.get("parameters", profile)
    pid = profile.get("profile_id", profile.get("id", ""))
    if (scheme_id, version) == ("classic-mceliece", "length-research-v1"):
        n, t, rows = params["n"], params["t"], params["m"] * params["t"]
        account = prange_budget(n, rows, t)
        setup = 2 * rows * n + ram.loop_control((rows * n).bit_length()) * rows
        kdf = ram.shake256(1 + (n + 7) // 8 + (rows + 7) // 8, 32)
        breakdown = {"public_matrix_setup": setup,
                     "bounded_prange_trials": account["work"], "key_derivation": kdf}
        return {
            "scheme_id": scheme_id, "version": version, "profile_id": pid,
            "algorithm": "Prange syndrome decoding with bounded sampling",
            "work_bound": sum(breakdown.values()),
            "memory_bits": account["memory_bits"] + 1600,
            "breakdown": breakdown, "accounting": account,
            "assumptions": [rank_density_assumption()],
            "success_lower_bound": "21/32 > 1/2, conditional on the stated rank-density hypothesis",
            "status": "conditional-analytic-upper-bound",
            "human": (
                "Select columns, solve a binary linear system, and check whether "
                "the result is the weight-64 ciphertext error. Once recovered, "
                "the error and ciphertext give the full shared secret through "
                "the specified SHAKE256 derivation. The curve includes all "
                "trials and their elimination work. It is a deliberately simple "
                "attack budget, not the cheapest known decoding attack or a "
                "security lower bound."
            ),
            "technical": MC_TECHNICAL,
            "sources": SOURCES[:2],
        }
    if (scheme_id, version) != ("hqc", "modeled-research-v3"):
        raise ValueError("Unsupported code-based release")
    n, w = params["n"], params.get("secret_weight", params.get("weight"))
    length = params["rs_length"]
    rm_length = params["rm_length"]
    account = prange_budget(2 * n, n, 2 * w)
    finish = _hqc_finish_cost(n, length, rm_length, w)
    seed_expansion = ram.shake256(33, (n + 7) // 8)
    a = (2 * n * n + n * 32).bit_length()
    setup = seed_expansion + 2 * n * n * (8 + 2 * ram.loop_control(a) + 2 * ram.add_bits(a))
    # Exhaust all supports y; the true support is included regardless of its
    # distribution. This avoids rank hypotheses at the easiest stages.
    support_count = comb(n, w)
    enum_step = n * (w + 2) * (8 + 2 * ram.loop_control(a) + 2 * ram.add_bits(a))
    enum_step += 4 * w * (ram.loop_control(a) + ram.add_bits(a) + 2 * a)
    enum_work = seed_expansion + support_count * enum_step
    prange_work = setup + account["work"]
    use_enum = enum_work <= prange_work
    attack_work = enum_work if use_enum else prange_work
    breakdown = {"key_recovery": attack_work, **finish}
    uniqueness_failure = _hqc_unique_failure(n, w)
    if uniqueness_failure >= Fraction(1, 64):
        raise ValueError("The unique-key bound does not support the stated success target")
    assumptions = [] if use_enum else [rank_density_assumption()]
    assumptions.append(
        {"id": "hqc-unique-key-transfer-v1", "title": "Almost every public key has a unique small secret",
         "human": "Under the actual seeded key generator, at most one in 64 public keys has a different pair of weight-w secrets that explains the same public relation. An ideal random-multiplier calculation strongly motivates this premise, but does not prove it for fixed SHAKE.",
         "technical": "The required concrete premise is Pr_actual[there exists another weight-(w,w) pair x',y' with s=x'+h*y']<=1/64. For an ideal h uniform and independent of x,y, primitive-prime n and fixed weight w<n/2 imply Pr[h(y+y')=x+x']<=2^-(n-1) for each alternative pair. Thus the ideal failure probability is <=binom(n,w)(binom(n,w)-1)2^-(n-1). This much smaller ideal bound is supporting rationale only. We do not assert a fixed SHAKE image of 256-bit seeds is literally uniform over n bits or transfer that tiny ideal bound without justification. The attack computes actual SHAKE and subtracts1/64, not the ideal-model probability, in its success claim."}
    )
    support_correctness = _hqc_support_correctness(params)
    if support_correctness is None:
        assumptions.append(
            {"id": "hqc-honest-decoding-transfer-v1", "title": "The stated decoding-failure bound applies to the actual scheme",
             "human": "The scheme's conditional correctness analysis must bound actual honest decoding failure by 2^-128, including correlated ring errors and the chosen tie rule. Its surrogate-to-real-noise premise is still unproved for these research stages.",
             "technical": "Use the retained per-profile conditional correctness certificate and its model-transfer premise to assert Pr[the concatenated nearest-RM/bounded-distance-RS decoder fails on an honest encapsulation]<=2^-128. The explicit attack decoder uses the same nearest-RM tie order (linear mask first, then constant) and the unique RS codeword within delta symbol errors. This transfer includes compatibility of those tie and decoding conventions. Subtract this failure probability by union bound; independence from rank or uniqueness events is not assumed."}
        )
        correctness_text = "\nFor this tuple the deterministic support criterion is not established; subtract2^-128 under the stated actual honest-decoding premise.\n"
    else:
        residual, threshold = support_correctness
        correctness_text = f"\nFor this tuple B=2*w*wr+we={residual} < (delta+1)*32*r={threshold}. The existing support proof therefore gives zero honest decoding failure, including deterministic ties, without an independent-noise or ideal-XOF premise. No correctness failure term is subtracted.\n"
    recovery_success = "1" if use_enum else "21/32"
    decoding_loss = " - 2^-128" if support_correctness is None else ""
    return {
        "scheme_id": scheme_id, "version": version, "profile_id": pid,
        "algorithm": "Exhaustive private-support recovery" if use_enum else "Prange recovery of the quasi-cyclic private key",
        "work_bound": sum(breakdown.values()),
        "memory_bits": (4 * n + 256 * rm_length + 64 * length**2 + 4096
                        if use_enum else account["memory_bits"] + 256 * rm_length + 64 * length**2 + 4096),
        "breakdown": breakdown, "accounting": account,
        "alternative_work_bounds": {"private_support_enumeration": enum_work + sum(finish.values()),
                                    "prange": prange_work + sum(finish.values())},
        "assumptions": assumptions,
        "success_lower_bound": f"{recovery_success} - 1/64{decoding_loss} > 1/2",
        "status": "conditional-analytic-upper-bound",
        "human": (
            "Recover the small private vector from the public relation s=x+hy, "
            "then decode the ciphertext and derive its 32-byte secret. The "
            "displayed budget uses the cheaper of full support enumeration and "
            "a simple information-set attack, with the relevant assumptions "
            "shown for that choice. It includes decoding and hashing. This "
            "baseline omits faster specialized attacks and is not estimated "
            "best-known security."
        ),
        "technical": HQC_TECHNICAL + correctness_text,
        "sources": [SOURCES[2], SOURCES[1]],
    }


def solve_selected_columns(matrix, syndrome, columns):
    """Executable binary Prange core; returns (candidate, elementary census).

    The census counts data-bit loads/stores/XOR only. Address, sampling and
    control are additionally charged by prange_budget. Small-instance tests
    verify correctness and compare this core census to the enclosing budget.
    """
    rows = len(matrix)
    length = len(matrix[0]) if rows else 0
    if len(syndrome) != rows or len(columns) != rows or len(set(columns)) != rows:
        raise ValueError("Invalid syndrome or information set")
    if any(len(row) != length or any(x not in (0, 1) for x in row) for row in matrix):
        raise ValueError("Expected a binary rectangular matrix")
    if any(type(j) is not int or not 0 <= j < length for j in columns):
        raise ValueError("Column out of range")
    a = [[matrix[i][j] for j in columns] + [syndrome[i]] for i in range(rows)]
    cost = 2 * rows * (rows + 1)
    for i in range(rows):
        pivot = None
        for j in range(i, rows):
            cost += 1
            if a[j][i]:
                pivot = j
                break
        if pivot is None:
            return None, cost
        if pivot != i:
            a[i], a[pivot] = a[pivot], a[i]
            cost += 4 * (rows + 1)
        for j in range(rows):
            cost += 1
            if j != i and a[j][i]:
                for k in range(i, rows + 1):
                    a[j][k] ^= a[i][k]
                    cost += 4
    candidate = [0] * length
    cost += length
    for i, j in enumerate(columns):
        candidate[j] = a[i][-1]
        cost += 2
    return candidate, cost


def bounded_uniform_columns(length, rows, rng):
    """Partial Fisher-Yates; abort after64 rejected draws at any position.

    Crucially each draw uses ceil(log2(remaining)), not the full RAM-address
    width. Its acceptance probability is then at least1/2. The account uses
    the larger address width as a conservative cost bound only.
    """
    if not 0 <= rows <= length <= 2**20:
        raise ValueError("Unsupported subset dimensions")
    indices = list(range(length))
    for i in range(rows):
        remaining = length - i
        width = (remaining - 1).bit_length()
        for _ in range(64):
            candidate = rng.getrandbits(width)
            if candidate < remaining:
                break
        else:
            return None
        j = i + candidate
        indices[i], indices[j] = indices[j], indices[i]
    return indices[:rows]


def recover_syndrome(matrix, target_syndrome, weight, rng, max_trials):
    """Executable bounded Prange algorithm for local reproducibility checks."""
    rows, length = len(matrix), len(matrix[0])
    if type(max_trials) is not int or not 1 <= max_trials <= 100000:
        raise ValueError("Local executable trial limit exceeded")
    _dims(length, rows, weight)
    for trial in range(1, max_trials + 1):
        columns = bounded_uniform_columns(length, rows, rng)
        if columns is None:
            continue
        candidate, _ = solve_selected_columns(matrix, target_syndrome, columns)
        if candidate is None or sum(candidate) != weight:
            continue
        checked = [sum(a * b for a, b in zip(row, candidate)) % 2 for row in matrix]
        if checked == list(target_syndrome):
            return candidate, trial
    return None, max_trials


def exact_rank_density(matrix, support):
    """Exhaustive tiny-instance diagnostic; never used as a big-stage premise."""
    rows, length = len(matrix), len(matrix[0])
    support = frozenset(support)
    if length > 20 or not support <= set(range(length)) or len(support) > rows:
        raise ValueError("Diagnostic is limited to valid tiny instances")
    invertible = total = 0
    for rest in combinations([i for i in range(length) if i not in support], rows - len(support)):
        columns = sorted(support | set(rest))
        candidate, _ = solve_selected_columns(matrix, [0] * rows, columns)
        total += 1
        invertible += candidate is not None
    return Fraction(invertible, total)


def gf256_mul(a, b):
    out = 0
    for _ in range(8):
        if b & 1:
            out ^= a
        b >>= 1
        a <<= 1
        if a & 256:
            a ^= 0x11D
    return out


def _gf_pow(x, exponent):
    out = 1
    while exponent:
        if exponent & 1:
            out = gf256_mul(out, x)
        x = gf256_mul(x, x)
        exponent >>= 1
    return out


def _gf_inverse(x):
    if not x:
        raise ValueError("Cannot invert zero")
    return _gf_pow(x, 254)


def _poly_eval(coeffs, x):
    out = 0
    for c in reversed(coeffs):
        out = gf256_mul(out, x) ^ c
    return out


def rs_encode(message, length):
    """Systematic shortened cyclic RS, roots alpha^1 ... alpha^(L-k)."""
    message = bytes(message)
    parity = length - len(message)
    if not 0 <= parity <= 254 or not len(message) <= length <= 255:
        raise ValueError("Invalid RS dimensions")
    generator = [1]
    for j in range(1, parity + 1):
        root = _gf_pow(2, j)
        result = [0] * (len(generator) + 1)
        for i, c in enumerate(generator):
            result[i] ^= gf256_mul(c, root)
            result[i + 1] ^= c
        generator = result
    dividend = [0] * parity + list(message)
    for pos in range(length - 1, parity - 1, -1):
        factor = dividend[pos]
        for j, c in enumerate(generator):
            dividend[pos - parity + j] ^= gf256_mul(factor, c)
    return bytes(dividend[:parity]) + message


def rs_decode(received, dimension=16):
    """BM locations + a GF256 Vandermonde solve; reject outside unique radius."""
    received = list(received)
    length = len(received)
    parity = length - dimension
    if not 1 <= dimension <= length <= 255 or parity % 2:
        raise ValueError("Unsupported RS dimensions")
    syndromes = [_poly_eval(received, _gf_pow(2, j)) for j in range(1, parity + 1)]
    if not any(syndromes):
        return bytes(received[-dimension:])
    c = [1] + [0] * parity
    b = list(c)
    ell, shift, previous = 0, 1, 1
    for step in range(parity):
        discrepancy = syndromes[step]
        for j in range(1, ell + 1):
            discrepancy ^= gf256_mul(c[j], syndromes[step - j])
        if discrepancy == 0:
            shift += 1
            continue
        old = list(c)
        scale = gf256_mul(discrepancy, _gf_inverse(previous))
        for j in range(parity + 1 - shift):
            c[j + shift] ^= gf256_mul(scale, b[j])
        if 2 * ell <= step:
            ell = step + 1 - ell
            b, previous, shift = old, discrepancy, 1
        else:
            shift += 1
    if ell > parity // 2:
        raise ValueError("Outside RS unique decoding radius")
    locations = [i for i in range(length)
                 if _poly_eval(c[:ell + 1], _gf_pow(2, (255 - i) % 255)) == 0]
    if len(locations) != ell:
        raise ValueError("Incomplete RS error locations")
    a = [[_gf_pow(2, (i * j) % 255) for i in locations] + [syndromes[j - 1]]
         for j in range(1, ell + 1)]
    for i in range(ell):
        pivot = next((j for j in range(i, ell) if a[j][i]), None)
        if pivot is None:
            raise ValueError("Singular error-value system")
        a[i], a[pivot] = a[pivot], a[i]
        inverse = _gf_inverse(a[i][i])
        a[i] = [gf256_mul(x, inverse) for x in a[i]]
        for j in range(ell):
            if j != i:
                factor = a[j][i]
                a[j] = [x ^ gf256_mul(factor, y) for x, y in zip(a[j], a[i])]
    for i, location in enumerate(locations):
        received[location] ^= a[i][-1]
    if any(_poly_eval(received, _gf_pow(2, j)) for j in range(1, parity + 1)):
        raise ValueError("RS syndrome check failed")
    return bytes(received[-dimension:])


def rm_word(symbol, repetitions=1):
    word = sum((((symbol >> 7) ^ ((symbol & 127) & i).bit_count()) & 1) << i
               for i in range(128))
    return sum(word << (128 * i) for i in range(repetitions))


def recover_hqc_from_y(public, y):
    """Public-input completion of a recovered private y; no hidden key oracle."""
    p = public["parameters"]
    n, length, rm_length = p["n"], p["rs_length"], p["rm_length"]
    repetitions = rm_length // 128
    code_length = length * rm_length
    nbytes = (n + 7) // 8
    pk, ct = bytes.fromhex(public["public_key"]), bytes.fromhex(public["ciphertext"])
    if len(pk) != nbytes + 32 or len(ct) != nbytes + code_length // 8 + 16:
        raise ValueError("Unexpected HQC encoding")
    if y < 0 or y >= 1 << n:
        raise ValueError("Private support out of range")
    u, v = int.from_bytes(ct[:nbytes], "little"), int.from_bytes(ct[nbytes:-16], "little")
    mask = (1 << n) - 1
    product = 0
    for i in range(n):
        if y >> i & 1:
            product ^= ((u << i) | (u >> (n - i))) & mask
    noisy = (v ^ product) & ((1 << code_length) - 1)
    words = [rm_word(i, repetitions) for i in range(256)]
    decoded = []
    for j in range(length):
        block = (noisy >> (j * rm_length)) & ((1 << rm_length) - 1)
        decoded.append(min(range(256), key=lambda x: ((block ^ words[x]).bit_count(), x & 127, x >> 7)))
    message = rs_decode(decoded, p.get("rs_dimension", 16))
    hashpk = hashlib.sha3_256(pk + b"\x01").digest()
    return hashlib.sha3_512(hashpk + message + ct[-16:] + b"\x00").digest()[:32]


MC_TECHNICAL = r"""## Attack specification: explicit Prange baseline

For the actual binary Goppa public matrix H=[I_(mt)|T], N=n, r=mt=768,
t=64. The ciphertext is s=He. Select an r-column set J uniformly, solve
H_J z=s, extend z by zeros, and accept only a weight-t vector with syndrome s.
The code distance is at least2t+1, so two accepted weight-t vectors cannot
differ: their difference would be a nonzero codeword of weight<=2t. Thus an
accepted vector is the honest error. Derive the full secret by
SHAKE256(0x01 || little_endian_bits(e) || ciphertext,32).

For any fixed support, hit=binom(r,t)/binom(N,t). Rank is NOT independent of
the support hit and is NOT inferred from a uniform-matrix constant. The visible
rank-density hypothesis supplies rho>=1/8 on a target set of measure>=7/8.
Bounded partial Fisher-Yates uses up to64 rejection draws per index, each with
ceil(log2(N-i)) random bits when N-i elements remain. Thus each draw is accepted
with probability>=1/2. Its abort probability is <=r*2^-64<1/2.
Conditioned on not aborting, the resulting r-subset remains uniform. Therefore
each independent trial on a good target succeeds with probability>=hit/16.
R=ceil(32/hit) gives failure<exp(-2)<1/4. Overall success is >=21/32>1/2.

The bit-RAM budget includes the index-array initialization and bounded sampler,
matrix extraction, pivot search, row swaps/XOR elimination, support weight and
syndrome verification, and the KDF. Addresses use the declared bit width.
Store contiguous rows and maintain their base pointers by additions; do not
recompute row*stride with an uncharged integer multiply on every access. The
memory account includes the row-base pointer arrays. Extraction has r(r+1)
cell slots; elimination is bounded by
r^3+4r^2+4r slots; verification uses rN+4N slots. Each slot pays
8+4 LOOP(a)+4 ADD(a), covering bit transfers, Boolean work, address changes
and nested-loop control. A separate R.bit_length()-wide trial counter is charged
per trial, rather than truncating that counter to an address width. No
high-level Gaussian elimination is counted as one
operation. The matrix and scratch storage are also reported. Exact integer
budgets are preserved before taking logarithms.

This is a conservative upper bound for the specified Prange implementation.
It excludes faster ISD, structural Goppa attacks and quantum attacks; it cannot
be used as a lower bound on security. The tests exhaust tiny information sets,
verify the exact hit/rank product, and execute recovery; they do not validate
the structured-rank hypothesis for the current cryptographic profiles.
"""


HQC_TECHNICAL = r"""## Attack specification: private support recovery then full KEM completion

The retained HQC2025 public relation is s=x+h*y in F2[X]/(X^n-1), where
wt(x)=wt(y)=w. One baseline enumerates every weight-w y, forms x=s+h*y,
and accepts when wt(x)=w. Another applies the explicitly bounded Prange
algorithm to H=[I_n|rot(h)], N=2n, r=n and target weight2w, then checks the
individual half weights. The smaller documented work budget is selected;
its premise set is selected with it. Prange uses the same rank-density
hypothesis and R=ceil(32 binom(2n,2w)/binom(n,2w)) accounting as the McEliece
note. Exhaustive enumeration needs no rank hypothesis and no uniform-secret
assumption: the true support is always visited.

For motivation, first analyze an ideal independent uniform public multiplier.
In that auxiliary model another weight-(w,w) solution has probability at most
epsilon=binom(n,w)(binom(n,w)-1)/2^(n-1). To see this, for y'!=y,
y+y' has even parity. Since n is primitive prime and2w<n-1, this nonzero
polynomial is not divisible by the irreducible degree-(n-1) factor of X^n-1.
Multiplication by it maps uniform h uniformly onto the even-parity subspace.
Each fixed x' therefore satisfies the alternative equation with probability
at most2^-(n-1). Apply a union bound; no independence of those alternatives
is needed. The bound is checked per profile and is below1/64. For the actual
fixed SHAKE implementation the visible premise is only that alternative-key
probability is at most1/64. This is an unproved event-level transfer claim;
the tiny ideal-model bound is not transplanted to the finite seeded output
space. The attack explicitly computes SHAKE and pays its cost.

Once y is recovered, compute v+u*y and truncate to the code length. For each
repeated RM(1,7) block, compare all256 affine words and choose nearest,
breaking ties by linear-mask order and then the constant bit. Decode the
shortened cyclic RS code using syndromes, Berlekamp-Massey, locator roots and
a GF256 Vandermonde solve for error values; recheck syndromes. Extract the
systematic16-byte message. The full secret is the first32 bytes of
SHA3-512(SHA3-256(pk||0x01)||message||salt||0x00).

The primitive-recovery success is at least1-1/64 for full support enumeration,
or at least21/32-1/64 for Prange. The existing October29 support proof bounds
the honest residual weight by B=2*w*wr+we. A repeated RM block can decode
incorrectly only after at least32*r errors, counting ties as potentially wrong;
RS(16+2*delta,16) corrects delta wrong symbols. Thus B<(delta+1)*32*r proves
zero honest decoding failure for every supported draw, without an ideal-XOF
or independent-noise premise. The actual tuple is checked against this strict
criterion; among the current66 profiles exactly stages1-4 satisfy it. See
docs/hqc-module-progression-review-20261029.txt and
docs/hqc-support-review-20261029.json. Otherwise subtract the actual honest
decoding-failure probability<=2^-128 using the explicit conditional premise.
No independence between correctness, rank and uniqueness events is assumed.

The budget charges SHAKE expansion, public-matrix construction (Prange only),
all finite trials/support candidates, cyclic multiplication, an explicit256-word
nearest-RM decoder, polynomial RS decoding and both final SHA3 evaluations.
GF256 multiplication uses eight shift/XOR/reduction rounds (<=1024 bit ops),
and inversion by x^254 costs<=16 multiplications. RS work is bounded by
4L^3+32L^2+512L+4096 field/control slots, each paying1024 plus the address/
transfer/control allowance; this covers the cubic error-value solve, all
syndrome evaluations, BM, root evaluation and initialization. The reference
Python kernel executes this path but its wall-clock runtime is not the unit.
The implementation deliberately overcounts rather than claiming optimized
native timings. Source parameters and byte formats are those of the exact
modeled-research-v3 adapter; the2026 HQC parameter changes are not imported.

These baselines do not represent the cheapest known HQC attacks. In particular,
quasi-cyclic improvements, modern ISD and public-message enumeration may be
cheaper. The conditional upper-bound curve should not be labelled a lower
bound on security or evidence that a NIST category is met.
"""
