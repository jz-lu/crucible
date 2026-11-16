"""Explicit classical recovery baselines in Crucible's bit-operation RAM model.

These are conservative attainable-work bounds, NOT security lower bounds or a
claim of optimal cryptanalysis. Integer arithmetic in the small executable
references uses Python; the meter charges the separately specified primitive
upper bounds. It does not purport to count Python's implementation instructions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math

from .common import (MODEL_ID, add_bits, sub_bits, compare_bits, mul_mod,
                     pow_mod, mul_bits, divmod_bits, sha256, loop_control)


SOURCES = [
    {"title": "Menezes, van Oorschot and Vanstone, Handbook of Applied Cryptography, Chapter 3: discrete logarithms and factoring",
     "url": "https://cacr.uwaterloo.ca/hac/about/chap3.pdf"},
    {"title": "Handbook of Applied Cryptography, Chapter 14: integer and modular arithmetic",
     "url": "https://cacr.uwaterloo.ca/hac/about/chap14.pdf"},
]


def _positive_int(value, label):
    if type(value) is not int or value < 1:
        raise ValueError(f"Invalid {label}")
    return value


def _ceil_sqrt(value):
    root = math.isqrt(value)
    return root + (root * root < value)


def _width(value):
    return max(1, int(value).bit_length())


def _profile_id(profile):
    return profile.get("profile_id", profile.get("id"))


@dataclass
class Meter:
    """Instrumented macro-operation upper charges, not measured gate executions."""
    charges: dict[str, int] = field(default_factory=dict)

    def charge(self, name, work):
        if type(work) is not int or work < 0:
            raise ValueError("Invalid work charge")
        self.charges[name] = self.charges.get(name, 0) + work

    @property
    def work(self):
        return sum(self.charges.values())


def _table_shape(upper, key_bits):
    # Include exponent zero, although honest samplers use 1..upper.
    count = upper + 1
    baby = _ceil_sqrt(count)
    giants = (count + baby - 1) // baby
    levels = max(1, (baby - 1).bit_length())
    index_bits = _width(baby - 1)
    record_bits = key_bits + index_bits
    # Padded power-of-two slots make a bit address the concatenation of the
    # record index and a public fixed bit offset. Reading an unrolled record
    # therefore needs no uncharged variable-width address multiplication.
    slot_bits = 1 << (record_bits - 1).bit_length()
    address_bits = _width(2 * baby * slot_bits)
    # Bound all input/output pointer updates and merge/lookup branches, rather
    # than charging only a single loop increment per compound table operation.
    control = 8 * loop_control(address_bits)
    compare_cost = 2 * record_bits + compare_bits(key_bits) + control
    move_cost = 2 * record_bits + control
    lookup_cost = record_bits + compare_bits(key_bits) + 3 * control
    return dict(baby=baby, giants=giants, levels=levels,
                record_bits=record_bits, slot_bits=slot_bits, address_bits=address_bits,
                control=control, compare_cost=compare_cost,
                move_cost=move_cost, lookup_cost=lookup_cost)


def _table_work(upper, key_bits, group_work):
    shape = _table_shape(upper, key_bits)
    m, g, levels = shape["baby"], shape["giants"], shape["levels"]
    # Bottom-up mergesort has <=m*levels comparisons and <=2m*levels
    # record transfers; the two-array allowance also pays for odd tails.
    breakdown = {
        "baby_steps": (m - 1) * group_work + m * (shape["record_bits"] + shape["control"]),
        "table_sort": m * levels * (shape["compare_cost"] + 2 * shape["move_cost"]),
        "giant_steps": (g - 1) * group_work + g * ((levels + 1) * shape["lookup_cost"] + shape["control"]),
    }
    return shape, breakdown


def _ec_add_work(bits):
    # Affine addition, including infinity/equality checks; inverse is z^(p-2).
    # Six products and twenty linear-width operations safely cover either the
    # distinct-x or doubling formula after the inversion.
    return pow_mod(bits, bits) + 6 * mul_mod(bits) + 20 * add_bits(bits + 1)


def _ec_scalar_work(bits, scalar_bits):
    return 2 * scalar_bits * _ec_add_work(bits) + scalar_bits * loop_control(scalar_bits)


def _dh_parameters(profile):
    values = profile["parameters"]
    # Adapter manifests encode these integers as fixed-width hexadecimal.
    p, q, g = (int(values[name], 16) for name in ("p", "q", "g"))
    upper = int(values["exponent_upper_bound"], 16)
    if not 1 <= upper < q < p or not 1 < g < p:
        raise ValueError("Invalid finite-field attack parameters")
    return p, q, g, upper


def _base_result(scheme_id, version, profile, algorithm, label, breakdown,
                 memory_bits, human, technical, parameters):
    return {
        "model_id": MODEL_ID,
        "scheme_id": scheme_id, "version": version,
        "profile_id": _profile_id(profile),
        "algorithm": algorithm, "label": label,
        "work_bound": sum(breakdown.values()), "memory_bits": memory_bits,
        "breakdown": breakdown, "parameters": parameters,
        "success_lower_bound": "1", "success_status": "proved-for-honest-inputs",
        "assumptions": [], "human": human, "technical": technical,
        "sources": SOURCES,
        "evidence_status": "analytic-upper-bound-with-small-executable-checks",
        "not_best_known": True,
    }


def estimate(scheme_id, version, profile):
    """Return an integer work bound for one exact current registered profile."""
    if scheme_id == "teaching-dh-kem" and version == "7":
        return _dh_estimate(profile)
    if scheme_id == "dhkem-x25519" and version == "curve-compressed-v2":
        return _ec_estimate(profile)
    if scheme_id == "rsa-kem" and version == "4":
        return _rsa_estimate(profile)
    raise ValueError("Unsupported classical release")


def _dh_estimate(profile):
    p, q, generator, upper = _dh_parameters(profile)
    bits, exponent_bits = p.bit_length(), upper.bit_length()
    shape, breakdown = _table_work(upper, bits, mul_mod(bits))
    m = shape["baby"]
    # g^(-m): first compute g^m, then its Fermat inverse.
    breakdown["setup"] = pow_mod(bits, m.bit_length()) + pow_mod(bits, bits)
    breakdown["recover_and_check"] = 2 * pow_mod(bits, exponent_bits)
    width = (bits + 7) // 8
    domain = b"crucible/teaching-dh-kem/v7/shared-secret\0"
    payload_bytes = len(domain) + 7 * 4 + len(_profile_id(profile)) + 6 * width
    breakdown["transcript_hash"] = sha256(payload_bytes)
    breakdown["input_output_and_control"] = 8 * (2 * width + payload_bytes + 32) + 64 * (bits + exponent_bits + 1) ** 2
    memory = 2 * shape["baby"] * shape["slot_bits"] + 256 * (bits + exponent_bits + 1) + 16384
    human = (
        "Baby-step–giant-step searches the actual published exponent interval. "
        "It recovers the recipient exponent, computes the shared group element, "
        "and applies the scheme's exact transcript hash. The displayed work is a "
        "conservative upper bound with success one for honest inputs. No random-walk "
        "assumption is needed. The large table is explicitly included. This is a "
        "reproducible baseline, not the cheapest known finite-field attack; "
        "number-field-sieve and reusable-precomputation improvements are absent."
    )
    technical = _bsgs_proof(upper, bits, shape) + (
        "\n\nFor this release the public certificates prove prime p,q and ord(g)=q; "
        "the honest x,r lie in [1,M] with M<q. Thus there is a unique x in that interval. "
        "The recovered x gives c^x=g^(rx). The final SHA-256 input has "
        f"{payload_bytes} bytes including all uint32 framing, profile, p,q,g, X,C,Z. "
        "No hash collision-resistance hypothesis is needed to compute the same hash. "
        "The profile-specific public constants are part of the input specification; "
        "all baby-table preparation is charged again for each target."
    )
    return _base_result("teaching-dh-kem", "7", profile, "interval-bsgs-v1", "Interval baby-step–giant-step",
                        breakdown, memory, human, technical,
                        {"field_bits": bits, "exponent_upper_bound": str(upper), "baby_steps": str(m), "giant_steps": str(shape["giants"]), "hash_input_bytes": payload_bytes})


def _ec_estimate(profile):
    values = profile["parameters"]
    p, q = int(values["p"]), int(values["q"])
    upper, bits = q - 1, p.bit_length()
    if p % 4 != 3 or not 1 < q < 2 * p:
        raise ValueError("Invalid curve attack parameters")
    exponent_bits = upper.bit_length()
    key_bits = 2 * bits + 1  # infinity flag plus uncompressed affine x,y
    shape, breakdown = _table_work(upper, key_bits, _ec_add_work(bits))
    breakdown["setup"] = _ec_scalar_work(bits, shape["baby"].bit_length()) + sub_bits(bits)
    # Two honest compressed points: Legendre check and p=3 mod4 square root,
    # followed by parity correction and curve polynomial evaluation.
    breakdown["point_decoding"] = 4 * pow_mod(bits, bits) + 8 * mul_mod(bits) + 24 * add_bits(bits + 1)
    breakdown["recover_and_check"] = 2 * _ec_scalar_work(bits, exponent_bits)
    width = 1 + (bits + 7) // 8
    domain_parameters = json.dumps({k: values[k] for k in ("p", "a", "b", "q", "g")}, sort_keys=True, separators=(",", ":")).encode()
    payload_bytes = len(b"crucible/ec-compressed-v2/shared-secret\0") + 5 * 4 + len(_profile_id(profile)) + len(domain_parameters) + 3 * width
    breakdown["transcript_hash"] = sha256(payload_bytes)
    breakdown["input_output_and_control"] = 8 * (2 * width + payload_bytes + 32) + 64 * (bits + exponent_bits + 1) ** 2
    memory = 2 * shape["baby"] * shape["slot_bits"] + 256 * (bits + exponent_bits + 1) + 16384
    human = (
        "Baby-step–giant-step recovers the scalar of the public point on the exact "
        "registered curve, then reconstructs the shared point and transcript hash. "
        "The work bound includes point decoding, affine field arithmetic, sorting, "
        "table lookup and hashing. It succeeds for every honest input without a "
        "random-walk premise. Its table is large, and its elementary affine arithmetic "
        "is intentionally unoptimized. This is an attainable-work baseline, not a "
        "claim that every attack is this expensive."
    )
    technical = _bsgs_proof(upper, key_bits, shape) + (
        "\n\nThe published prime/order certificates establish that G has prime order q "
        "and the entire nonsingular curve has order q. Table keys retain both affine "
        "coordinates and an infinity flag, so x-coordinate sign collisions are not "
        "discarded. All denominators in nonexceptional additions are invertible; "
        "identity, equality and opposite-point branches are handled explicitly. "
        "Use inversion z^(p-2), charged by binary exponentiation, and the usual "
        "affine formulas. Each group addition is bounded by one inversion, six "
        "modular products and twenty linear-width operations. Two compressed points "
        "are decoded using the actual p=3 mod4 rule. Then xC=rX and the same "
        f"{payload_bytes}-byte framed input produces the same SHA-256 output. "
        "The curve's canonical public JSON is a fixed public profile constant. "
        "No unproved curve-hardness or hash-hardness premise is used by this attack."
    )
    return _base_result("dhkem-x25519", "curve-compressed-v2", profile, "curve-bsgs-v1", "Curve baby-step–giant-step",
                        breakdown, memory, human, technical,
                        {"field_bits": bits, "group_order": str(q), "baby_steps": str(shape["baby"]), "giant_steps": str(shape["giants"]), "hash_input_bytes": payload_bytes})


def _bsgs_proof(upper, key_bits, shape):
    return (
        "Write every candidate x in [0,M] uniquely as x=im+j, with "
        "m=ceil(sqrt(M+1)), 0<=j<m and 0<=i<ceil((M+1)/m). "
        "Store (g^j,j), or (jG,j) for a curve, in a bottom-up mergesorted table. "
        "A giant-step lookup compares the target times g^(-im), or target-imG, "
        "against the table. Equality yields x; check x<=M and verify the public key. "
        "No probabilistic collision model or constant-time hash-table premise is used. "
        "The table has distinct group elements because m<=q. Binary search uses at "
        "most ceil(log2(m))+1 comparisons. Sorting uses at most m*ceil(log2(m)) "
        "comparisons and twice that many record transfers. Each comparison reads "
        "both full records and pays a bitwise comparison plus address/control work. "
        "Two table arrays are reserved in the memory bound. Records occupy padded "
        "power-of-two bit slots. Each bit address concatenates the record index "
        "with a fixed, unrolled offset; this avoids treating a variable address "
        "multiplication or an entire RAM word transfer as free. All padding is "
        "included in the storage bound. Eight counter-update/compare allowances "
        "per table control step cover the input/output pointers and branches. "
        f"Here M={upper}, m={shape['baby']}, giant iterations={shape['giants']}, "
        f"key width={key_bits}, record width={shape['record_bits']} bits. "
        "All quantities are exact integers until the final log2 display. The work "
        "bound covers success probability one, hence also the shared 50% target. "
        "This does not assert the budget is minimal for achieving that target."
    )


def _rsa_estimate(profile):
    bits = _positive_int(profile["parameter_bits"], "RSA modulus bits")
    if bits < 32:
        raise ValueError("Unsupported RSA modulus width")
    factor_bits = bits // 2
    trials = 1 << (factor_bits - 2)
    exponent = 65537
    breakdown = {
        "trial_division": trials * (divmod_bits(bits) + compare_bits(bits) + loop_control(factor_bits)),
        "factor_and_private_exponent": (3 * divmod_bits(bits + 17) + 2 * mul_bits(bits + 17)
                                        + pow_mod(17, 17) + 8 * add_bits(bits + 17)),
        "rsa_inversion_and_check": pow_mod(bits, bits) + pow_mod(bits, 17),
    }
    width = (bits + 7) // 8
    payload_bytes = len(b"crucible/rsa-kem/v4/shared-secret\0") + 5 * 4 + len(_profile_id(profile)) + 3 * width + 3
    breakdown["transcript_hash"] = sha256(payload_bytes)
    breakdown["input_output_and_control"] = 8 * (2 * width + 3 + payload_bytes + 32) + 64 * (bits + 18) ** 2
    human = (
        "This deliberately elementary baseline tries every odd candidate for the "
        "smaller certified RSA factor, reconstructs the private exponent, and recovers "
        "the encapsulated integer and transcript hash. It is guaranteed on honest "
        "inputs. It is substantially slower than established factoring attacks, "
        "including Pollard rho and the number field sieve. A high point on this "
        "curve is therefore not evidence of strong RSA security. Its purpose is to "
        "provide a complete, reproducible RAM accounting baseline until better "
        "factoring analyses have been admitted under the same model."
    )
    technical = (
        "For an n-bit modulus the adapter samples a factor q of exactly k=floor(n/2) "
        "bits. All such factors are odd. Try every d=2^(k-1)+1,2^(k-1)+3,...,2^k-1. "
        f"There are exactly 2^(k-2)={trials} candidates. Restoring binary division "
        "finds q (or another prime factor) with certainty, independent of the "
        "nonuniform Pocklington sampler. Set p=N/q and phi=(p-1)(q-1). To avoid an "
        "unaccounted large-integer inverse, exploit the fixed prime e=65537: set "
        "u=(phi mod e)^(e-2) mod e and t=(-u) mod e. Then d=(1+t*phi)/e is an "
        "integer with ed=1 mod phi and d<phi. The sampler enforces gcd(e,phi)=1. "
        "Compute z=c^d mod N, including z that is not a unit; Fermat and CRT prove "
        "recovery for every honest z. Verify z^e=c, then apply the exact framed "
        f"{payload_bytes}-byte SHA-256 transcript. All primitive costs and loop "
        "control are charged; O(n) workspace suffices. This upper bound is neither "
        "a best-known factoring estimate nor a hardness premise."
    )
    return _base_result("rsa-kem", "4", profile, "rsa-trial-division-v1", "Trial-division recovery baseline",
                        breakdown, 256 * (bits + 18) + 16384, human, technical,
                        {"modulus_bits": bits, "factor_bits": factor_bits, "trial_candidates": str(trials), "public_exponent": exponent, "hash_input_bytes": payload_bytes})


def _merge_sort(rows, shape, meter):
    width = 1
    while width < len(rows):
        out = []
        for start in range(0, len(rows), 2 * width):
            left, right = rows[start:start+width], rows[start+width:start+2*width]
            i = j = 0
            while i < len(left) and j < len(right):
                meter.charge("sort", shape["compare_cost"])
                if left[i][0] <= right[j][0]:
                    out.append(left[i]); i += 1
                else:
                    out.append(right[j]); j += 1
                meter.charge("sort", 2 * shape["move_cost"])
            for row in left[i:] + right[j:]:
                out.append(row)
                meter.charge("sort", 2 * shape["move_cost"])
        rows = out
        width *= 2
    return rows


def _lookup(rows, key, shape, meter):
    lo, hi = 0, len(rows)
    while lo < hi:
        meter.charge("lookup", shape["lookup_cost"])
        mid = (lo + hi) // 2
        value, index = rows[mid]
        if key == value:
            return index
        if key < value:
            hi = mid
        else:
            lo = mid + 1
    return None


def _bsgs(target, generator, identity, combine, inverse, scalar,
          encode_key, upper, key_bits, meter):
    shape = _table_shape(upper, key_bits)
    m = shape["baby"]
    rows, value = [], identity
    for j in range(m):
        rows.append((encode_key(value), j))
        meter.charge("table_write", shape["record_bits"] + shape["control"])
        if j + 1 < m:
            value = combine(value, generator)
    rows = _merge_sort(rows, shape, meter)
    stride = inverse(scalar(generator, m))
    value = target
    for i in range(shape["giants"]):
        meter.charge("giant_control", shape["control"])
        j = _lookup(rows, encode_key(value), shape, meter)
        if j is not None and i*m+j <= upper:
            return i*m+j
        if i+1 < shape["giants"]:
            value = combine(value, stride)
    raise ValueError("Target is outside the specified exponent interval")


def recover_dh(profile_id, public_key, ciphertext):
    """Small-instance end-to-end reference; never invoked by the website."""
    from .. import dh_v7
    profile = dh_v7._profile(profile_id)
    upper = dh_v7.exponent_upper_bound(profile)
    if upper > 1 << 24:
        raise ValueError("Executable demonstration is restricted to small instances")
    bits, p = profile.p.bit_length(), profile.p
    meter = Meter()
    def combine(a, b):
        meter.charge("field_products", mul_mod(bits))
        return a*b % p
    def scalar(a, e):
        meter.charge("field_exponentiation", pow_mod(bits, e.bit_length()))
        return pow(a, e, p)
    def inverse(a):
        meter.charge("field_inverse", pow_mod(bits, bits))
        return pow(a, p-2, p)
    x = _bsgs(int.from_bytes(public_key, "big"), profile.g, 1, combine, inverse, scalar,
              lambda a: a, upper, bits, meter)
    if scalar(profile.g, x) != int.from_bytes(public_key, "big"):
        raise AssertionError("Recovered exponent does not match public key")
    shared = scalar(int.from_bytes(ciphertext, "big"), x)
    manifest = dh_v7.get_manifest(profile_id)
    result = estimate("teaching-dh-kem", "7", manifest)
    meter.charge("hash", result["breakdown"]["transcript_hash"])
    meter.charge("io", result["breakdown"]["input_output_and_control"])
    return dh_v7._kdf(profile, public_key, ciphertext, shared), meter


def recover_ec(profile_id, public_key, ciphertext):
    from .. import ec_compressed
    row = ec_compressed._profile(profile_id)
    p, q = row["p"], row["q"]
    if q > 1 << 24:
        raise ValueError("Executable demonstration is restricted to small instances")
    bits, meter = p.bit_length(), Meter()
    def combine(a, b):
        meter.charge("point_addition", _ec_add_work(bits))
        return ec_compressed._add(p, row["a"], a, b)
    def scalar(a, e):
        meter.charge("scalar_multiplication", _ec_scalar_work(bits, e.bit_length()))
        return ec_compressed._mul(p, row["a"], a, e)
    def inverse(a):
        meter.charge("point_negation", sub_bits(bits))
        return None if a is None else (a[0], (-a[1]) % p)
    def encode(a):
        return 0 if a is None else (1 << (2*bits)) | (a[0] << bits) | a[1]
    target, ephemeral = ec_compressed._decode(row, public_key), ec_compressed._decode(row, ciphertext)
    manifest = ec_compressed.get_manifest(profile_id)
    result = estimate("dhkem-x25519", "curve-compressed-v2", manifest)
    meter.charge("decode", result["breakdown"]["point_decoding"])
    x = _bsgs(target, tuple(row["g"]), None, combine, inverse, scalar, encode,
              q-1, 2*bits+1, meter)
    if scalar(tuple(row["g"]), x) != target:
        raise AssertionError("Recovered scalar does not match public key")
    shared = scalar(ephemeral, x)
    meter.charge("hash", result["breakdown"]["transcript_hash"])
    meter.charge("io", result["breakdown"]["input_output_and_control"])
    return ec_compressed._kdf(profile_id, row, public_key, ciphertext, shared), meter


def recover_rsa(profile_id, public_key, ciphertext):
    from .. import rsa_v4
    profile = rsa_v4._profile(profile_id)
    bits = profile.parameter_bits
    if bits > 40:
        raise ValueError("Executable demonstration is restricted to small instances")
    n, e = rsa_v4._public_values(profile, public_key)
    c = rsa_v4._ciphertext_value(profile, n, ciphertext)
    k, meter = bits//2, Meter()
    for candidate in range((1 << (k-1))+1, 1 << k, 2):
        meter.charge("division", divmod_bits(bits) + compare_bits(bits) + loop_control(k))
        if n % candidate == 0:
            break
    else:
        raise ValueError("No factor in the profile's certified range")
    p, q = candidate, n//candidate
    phi = (p-1)*(q-1)
    inverse = pow(phi % e, e-2, e)
    d = (1 + ((-inverse) % e) * phi)//e
    z = pow(c, d, n)
    if pow(z, e, n) != c:
        raise AssertionError("Recovered RSA integer does not match ciphertext")
    result = estimate("rsa-kem", "4", rsa_v4.get_manifest(profile_id))
    for name in ("factor_and_private_exponent", "rsa_inversion_and_check", "transcript_hash", "input_output_and_control"):
        meter.charge(name, result["breakdown"][name])
    return rsa_v4._kdf(profile, public_key, ciphertext, z), meter
