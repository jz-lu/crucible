"""Complete input-enumeration attacks for the exact lattice research releases.

These are intentionally elementary upper bounds, not best-known lattice attack
estimates. They use the common bit-RAM primitive account and include all setup,
candidate generation, complete ciphertext matching and shared-key derivation.
The Frodo alternative additionally searches bounded secret columns. No Core-SVP
number is converted into a bit-operation count.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import product
from math import comb

from . import common as ram


FIPS = {"title": "FIPS 203, Algorithms 14 and 17: deterministic ML-KEM encapsulation", "url": "https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.203.pdf"}
FRODO = {"title": "Glabush et al., FrodoKEM: salted construction and finite noise law", "url": "https://cic.iacr.org/p/2/3/25/pdf"}
NTRU = {"title": "NTRU Prime specification, 2020-10-07, including perfect correctness", "url": "https://ntruprime.cr.yp.to/nist/ntruprime-20201007/Supporting_Documentation/doc.pdf"}

ENUMERATION_PROOF = r"""### Why enumeration recovers the complete shared secret

Fix an honest key and any public encapsulation salt. Let F(x) be the deterministic
ciphertext produced by native input x and K(x) its derived key. Enumerate every
input and return K(x) for the first exact full-ciphertext match. This algorithm
does not receive a correctness oracle or the challenge target key.

Suppose an honest PKE decoder D fails to return x on at most a fraction delta of
uniform native inputs. The good inputs are injective under F: D(F(x)) cannot
equal two different x. A first-match attack can be wrong on at most all bad
inputs, plus one good input for each bad first representative. There are at most
as many such good inputs as bad inputs. Hence its key-recovery success is at
least 1-2*delta. The statement also holds after averaging over keys and salts.
It uses message recovery, not merely agreement of KEM outputs. No hash collision
resistance or random-oracle premise is needed for this combinatorial lemma.

This is a full-space, worst-case work budget that achieves at least 1/2 success;
it is not the minimum possible budget for 1/2 success. Early stopping only saves
work. Where every input decodes, success is exactly one in the mathematical
implementation. Operational faults are not a cryptanalytic assumption.
"""

LAYOUT_ACCOUNT = r"""
### Memory layout and control conventions

Polynomial and matrix scalar slots and matrix row strides are padded to powers
of two. An indexed slot address is the concatenation of the public-width index
and a fixed intra-slot bit offset; bit transfers inside a scalar are unrolled.
The storage bound includes that padding. Loop/control allowances cover up to
eight counter increment/comparison pairs per compound step. Arithmetic
subroutines use the common register-level bounds; array reads/writes are charged
separately. The fixed13-entry Frodo CDF scan is unrolled. None of these costs
are measured CPython instruction counts. The bounds specify an elementary
implementation; they are deliberately less optimized than production kernels.
"""


def first_match_success_lower_bound(message_failure: Fraction) -> Fraction:
    """Combinatorial lemma for a uniform finite native-input distribution."""
    if not isinstance(message_failure, Fraction) or not 0 <= message_failure <= 1:
        raise ValueError("Supply an exact failure probability in [0,1]")
    return max(Fraction(0), 1 - 2 * message_failure)


def _identity(scheme_id, version, profile, algorithm):
    labels={"module-native-message-enumeration-v1":"Native-message enumeration",
            "frodo-native-message-enumeration-v1":"Native-message enumeration",
            "frodo-bounded-secret-column-enumeration-v1":"Bounded-secret column search",
            "ntru-fixed-weight-input-enumeration-v1":"Fixed-weight input enumeration"}
    return {"scheme_id": scheme_id, "version": version,
            "profile_id": profile.get("profile_id", profile.get("id")),
            "algorithm": algorithm, "label":labels[algorithm], "model_id": ram.MODEL_ID}


def _result(identity, breakdown, memory_bits, assumptions, human, technical,
            sources, success, *, candidates=None):
    result = {**identity, "work_bound": sum(breakdown.values()),
              "memory_bits": memory_bits, "breakdown": breakdown,
              "assumptions": assumptions, "human": human, "technical": technical+LAYOUT_ACCOUNT,
              "sources": sources, "success_probability_lower_bound": success,
              "success_lower_bound": success,
              "success_status": "conditional-analytical-bound" if assumptions else "analytical-bound",
              "success_target": "1/2", "evidence_status": "conditional-upper-bound" if assumptions else "analytical-upper-bound",
              "claim": "Explicit attack work upper bound; not best-known attack cost or a security lower bound.",
              "not_best_known": True,
              "implementation": "crucible/attack_models/lattice.py"}
    if candidates is not None:
        result["candidate_count"] = candidates
    return result


def _controls(iterations, width):
    # Up to eight independent index/loop-counter updates per compound step.
    # Polynomial/matrix rows and scalar slots have power-of-two padded strides;
    # the bit address is fixed concatenation of indices and an unrolled offset.
    return 8 * iterations * ram.loop_control(width)


def _io(bits):
    return ram.memory_read(bits) + ram.memory_write(bits)


def _module_parts(n):
    """Bound a fixed NTT-based re-encryption program, with public A cached."""
    # Domain lengths follow the exact reduced-degree source; endpoint has none.
    def domain(role):
        return 0 if n == 256 else len(b"Crucible/module-compressed-v4/") + len(role) + 1 + 7
    width, rank = 12, 2
    pk_bytes, ct_bytes = 3*n+32, 3*n
    butterflies = (n//2) * (n.bit_length()-2)
    ntt = butterflies * (ram.mul_mod(width) + 2*ram.mod_add(width))
    inverse = ntt + n*ram.mul_mod(width)
    poly_product = (n//2)*(5*ram.mul_mod(width)+2*ram.mod_add(width))
    # The frozen sampler's maximum successful prefix is <=65536 bytes. Expand
    # that many once and inspect every 12-bit candidate; admitted inputs finish.
    matrix = 4*(ram.shake128(domain("matrix")+34, 65536)
                + (2*(65536//3))*(ram.compare_bits(12)+_io(12)+8*ram.loop_control(17)))
    setup = {
        "public_matrix_expansion_and_rejection": matrix,
        "public_key_hash": ram.sha3_256(domain("public-key-hash")+pk_bytes),
        "public_parsing_and_root_generation": _io(8*pk_bytes)+n*ram.pow_mod(12,12),
    }
    noise_hashes = (2*ram.shake256(domain("noise")+33, 6*n//8)
                    +3*ram.shake256(domain("noise")+33, 4*n//8))
    noise_decode = n*(2*(6*ram.add_bits(12)+ram.mod_add(12))
                      +3*(4*ram.add_bits(12)+ram.mod_add(12)))
    arithmetic = 2*ntt + 3*inverse + 6*poly_product + 7*n*ram.mod_add(12)
    compression = 3*n*(ram.add_bits(24)+ram.divmod_bits(24))
    # Dynamic accesses and loop indices are charged separately from register
    # arithmetic. Each butterfly/product reads <=6 and writes <=2 coefficients.
    coefficient_visits = 8*(5*butterflies+3*n+6*n) + 40*n
    candidate = {
        "message_transform": ram.sha3_512(domain("encapsulation")+n//8+32),
        "noise_expansion": noise_hashes,
        "noise_decode": noise_decode,
        "ntt_and_ring_arithmetic": arithmetic,
        "compression": compression,
        "memory_and_controls": _io(coefficient_visits*12+8*ct_bytes+n)
             +_controls(5*butterflies+64*n, max(17,n.bit_length()+6))
             +ram.loop_control(n),
        "full_ciphertext_compare_and_key_output": ram.compare_bits(8*ct_bytes)+_io(256),
    }
    memory = 8*65536 + 64*n*16 + 8*(pk_bytes+ct_bytes) + 8192 + n
    return setup, candidate, memory


def _module(profile):
    params = profile["parameters"]
    n = int(params["n"])
    if (n not in (8,16,32,64,128,256) or int(params.get("q",3329)) != 3329
            or int(params.get("rank",2)) != 2 or int(params.get("eta1",3)) != 3
            or int(params.get("eta2",2)) != 2 or int(params.get("du",10)) != 10
            or int(params.get("dv",4)) != 4):
        raise ValueError("This account covers only the frozen compressed module v4 tuples")
    setup, candidate, memory = _module_parts(n)
    candidates = 1 << n
    assumptions = []
    delta = Fraction(0)
    if n >= 32:
        bits = {32:367,64:193,128:236,256:128}[n]
        delta = Fraction(1,8)
        assumptions.append({"id":"module-v4-message-correctness",
            "title":"At most one eighth of honest messages fail to decode",
            "human": r"For the actual seeded scheme, require \(\Pr[D(F(m))\ne m]\le 1/8\). This is enough for the enumeration attack to recover the key with probability at least \(3/4\). The transfer from the idealized correctness analysis remains unproved."
                +(" The supporting analysis at this degree also uses a uniform u-rounding model." if n>=128 else ""),
            "technical":f"The assumed event concerns the real fixed SHA3/SHAKE implementation, averaged over its stated key/message distribution: Pr[D(F(m)) != m] <= 1/8. The local conditional message-residual calculation gives the much stronger 2^(-{bits}) under independent expanded CBD noise,"
                +(" and an additional independent uniform u-rounding model." if n>=128 else " keeping compression conservatively bounded.")
                +" That idealized calculation motivates, but does not prove, this weaker actual-distribution premise. It is a message-decoding statement, not just KEM key agreement; literal information-theoretic independence of long expansions from fixed finite seeds is not asserted."})
    breakdown = {"setup__"+key:value for key,value in setup.items()}
    breakdown.update({"all_candidates__"+key:candidates*value for key,value in candidate.items()})
    return _result(_identity("ml-kem","compressed-research-v4",profile,"module-native-message-enumeration-v1"),
        breakdown,memory,assumptions,
        f"Try every {n}-bit native message, re-create its complete ciphertext, and derive the shared key from the matching message. The plot counts the actual transform and polynomial work as bit operations. Faster lattice or structural attacks are outside this baseline.",
        ENUMERATION_PROOF+f"\n### Exact module account\nUse q=3329, n={n}, rank2, CBD3/CBD2, du10/dv4. Cache public A and H(pk), then run the documented NTT program for all 2^{n} messages. A butterfly has one modular product and two modular additions; each quadratic NTT base product uses five modular products and two additions. Count two forward transforms, three inverse transforms and six polynomial base products per message. Compress all 3n coefficients via bounded-width integer division. G and all five noise XOF calls use the exact parameter/purpose domains. Compare all ciphertext bits and output the 32-byte K already produced by G. The matrix expansion uses a deliberately conservative 65536-byte bound per polynomial. The exact registered key generator, including its degree256 endpoint, calls the same capped SampleNTT routine; its maximum successful prefix is49152 bytes. Thus every issued public key has its matrix reproduced within this bound. This argument concerns successful generation by the registered release and does not claim a finite worst-case cap for arbitrary uncapped FIPS203 key generation. Arithmetic and control counts are upper bounds for this specified program, not operation timings of CPython.\n",
        [FIPS], str(first_match_success_lower_bound(delta)), candidates=candidates)


def _frodo_parts(n):
    pk_bytes, ct_bytes = 16+15*n, 152+15*n
    samples = 16*n+64
    products = 8*n*n+64*n
    setup = {"public_matrix": n*ram.shake128(18,2*n)+_io(15*n*n),
             "public_key_hash_and_parse": ram.shake128(pk_bytes,16)+_io(8*pk_bytes)}
    candidate = {
        "message_and_noise_expansion": ram.shake128(64,48)+ram.shake128(33,32*n+128),
        "finite_noise_sampling": samples*(13*(ram.compare_bits(15)+ram.add_bits(4))+ram.sub_bits(15)+ram.mux_bits(15)+_io(16)),
        "matrix_products": products*(ram.mul_mod(15)+ram.mod_add(15)),
        "matrix_initialization":_io(15*(8*n+64)+16*samples),
        "matrix_memory_and_control": products*(ram.memory_read(45)+ram.memory_write(15)+8*ram.loop_control(2*n.bit_length()+8)),
        "encoding_and_comparison": 64*ram.mod_add(15)+_io(8*ct_bytes)+ram.compare_bits(8*ct_bytes)
             +_controls(samples+8*n+64,2*n.bit_length()+8)+ram.loop_control(128),
        "final_shared_key_hash": ram.shake128(ct_bytes+16,16)+_io(128),
    }
    padded_n=1<<(n-1).bit_length()
    memory = 32*padded_n*padded_n + 1024*padded_n + 8*(pk_bytes+ct_bytes+32*n+128) + 32768
    return setup,candidate,memory


def _frodo_correctness(n):
    if n == 8:
        return Fraction(0), []
    return Fraction(1,8), [{"id":"frodo-v3-message-correctness",
        "title":"At most one eighth of honest messages fail to decode",
        "human":r"For the actual seeded scheme, require \(\Pr[D(F(m))\ne m]\le 1/8\). The ideal-noise correctness analysis supports this conservative threshold, but its transfer to fixed SHAKE remains a premise.",
        "technical":"Assume the actual seeded key/message/salt distribution has message-decoding failure probability at most1/8. The local exact rational Chernoff/union certificate bounds the64 residuals E''+sum(S'E−E'S), with threshold4096, by2^-133 under independent finite-CDF noise. That stronger ideal-model statement motivates, but does not prove, the weaker actual-event bound used here. No literal independence of a long deterministic SHAKE expansion is claimed."}]


def frodo_false_secret_bound(n):
    if type(n) is not int or n < 1:
        raise ValueError("Positive dimension required")
    return min(Fraction(1), Fraction(8*(25**n-1),1<<(10*n)))


def _frodo_message(profile):
    n = int(profile["parameters"]["n"])
    setup,candidate,memory = _frodo_parts(n)
    delta,assumptions = _frodo_correctness(n)
    candidates = 1<<128
    breakdown = {"setup__"+key:value for key,value in setup.items()}
    breakdown.update({"all_candidates__"+key:candidates*value for key,value in candidate.items()})
    return _result(_identity("frodokem","anchor-research-v3",profile,"frodo-native-message-enumeration-v1"),
        breakdown,memory,assumptions,
        "Try every 128-bit native message while keeping the observed public salt fixed; reproduce the full ciphertext and final shared key. The nearly flat ceiling comes from the real fixed message space. It is not evidence that lattice attacks are equally expensive.",
        ENUMERATION_PROOF+f"\n### Exact salted Frodo account\nFor n={n}, q=32768 and nbar8, precompute all n public SHAKE128 rows and H(pk). For each of 2^128 messages use the observed 32-byte ciphertext salt. Expand 16n+64 noise values; a linear scan of the 13 CDF thresholds reproduces bisect_left exactly. Compute S'A and S'B with 8n²+64n modular multiply-adds. Encode, compare the entire ciphertext including its salt, and compute SHAKE128(ct||k,16). Native message search does not guess the public salt and does not truncate any seed. Final shared-key hashing is charged for every candidate, a conservative overhead.\n",
        [FRODO],str(first_match_success_lower_bound(delta)),candidates=candidates)


FRODO_COLUMN_PROOF = r"""### Bounded-column recovery and success proof

Enumerate every s in {-12,...,12}^n separately for each of the eight columns of
B. Retain the first vector whose residual b-A*s, centered modulo 32768, lies
in {-12,...,12}^n. The actual secret column always passes. The finite sampler's
support is a fact of the specified CDF, not a Gaussian approximation.

Condition on the true S,E and assume A is an independent uniform matrix over
Z/(2^15). For a false candidate, d=s-s_true is nonzero with coordinates bounded
by24. Its coordinate gcd with2^15 is2^v with v<=4. For one uniform row of A,
A*d is uniform on multiples of2^v. Among any25 consecutive residual targets,
at most ceil(25/2^v) lie in this subgroup. For v=0,...,4 the probability is at
most1/1024. Independent rows give at most2^(-10n). A union bound over eight
columns and at most25^n-1 false vectors gives 8(25^n-1)2^(-10n). No independence
between candidate events or between the eight columns is asserted or needed.

After recovery of all eight genuine columns, compute Decode(C-B'*S), reencrypt
with the observed salt and derive the final key. In the ideal model the success
bound is1 - 8(25^n-1)2^(-10n) - delta. For the actual seeded scheme the displayed
curve instead assumes only that the false-candidate event has probability at
most1/8. Combined with the separate actual message-decoding premise delta<=1/8,
this gives success at least3/4. At n=8 decoding is perfect and the bound is7/8.
Early matches can only save work; the budget scans all25^n vectors per column.

Public A is generated from a128-bit seed by fixed SHAKE. Literal uniformity over
all large matrices is impossible for that finite image. The visible premise is
the actual bad-event probability bound, not literal uniformity of the expansion.
The ideal theorem supplies evidence/rationale only. Do not transplant its tiny
tail probabilities into the actual seeded distribution without a new argument.
"""


def _frodo_columns(profile):
    n = int(profile["parameters"]["n"])
    setup,candidate,memory = _frodo_parts(n)
    delta,assumptions = _frodo_correctness(n)
    assumptions = [{"id":"frodo-v3-independent-public-matrix",
        "title":"False bounded-secret candidates occur with probability at most one eighth",
        "human":r"Under the actual seeded generator, require \(\Pr[\exists\text{ a false bounded secret column passing the residual test}]\le1/8\). An ideal uniform-matrix calculation motivates this bound; it has not been proved for fixed SHAKE.",
        "technical":"The event is: for some column j and s' != S_j in{-12,...,12}^n, every centered coordinate of B_j-A*s' is in{-12,...,12}. Assume its probability over actual fresh key generation is at most1/8. Conditional on actual S,E, replacing A by an independent uniform matrix yields the explicit union bound8(25^n-1)2^(-10n); that replacement is only an ideal analysis and is not asserted to describe the literal finite seeded matrix distribution."}, *assumptions]
    candidates = 8*25**n
    width = 2*n.bit_length()+8
    column = n*n*(ram.mul_mod(15)+ram.mod_add(15)+ram.memory_read(45)
                  +ram.memory_write(15)+8*ram.loop_control(width))
    column += n*(ram.mod_add(15)+ram.sub_bits(15)+2*ram.compare_bits(15)+ram.memory_read(30))
    column += n*(ram.add_bits(5)+ram.compare_bits(5)+_io(5)+8*ram.loop_control(width))
    breakdown = {"setup__"+key:value for key,value in setup.items()}
    breakdown["all_column_candidates"] = candidates*column
    breakdown["decrypt_recovered_message"] = 64*n*(ram.mul_mod(15)+ram.mod_add(15)+_io(60)+8*ram.loop_control(width)) +64*ram.add_bits(15)
    breakdown.update({"one_final_encapsulation__"+key:value for key,value in candidate.items()})
    return _result(_identity("frodokem","anchor-research-v3",profile,"frodo-bounded-secret-column-enumeration-v1"),
        breakdown,memory+256*(1<<(n-1).bit_length()),assumptions,
        "Search each short secret column and check its public residuals. This elementary attack is cheaper than message enumeration at the smallest dimensions. Its false-match bound uses the explicitly stated public-matrix premise; faster lattice attacks remain outside this baseline.",
        FRODO_COLUMN_PROOF+f"\nAt n={n}, scan 8·25^{n} candidate columns; each performs n² modular multiply-adds, n residual checks and bounded-base counter advancement. The one-time public-matrix expansion, final message decoding, complete reencryption check and final shared-key hash are charged.\n",
        [FRODO],str(1-Fraction(1,8)-delta),candidates=candidates)


def _ntru(profile):
    from ..ntru_reference import encoded_length
    from ..ntru_frontier import PROFILES
    params = profile["parameters"]
    p,q,w = (int(params[key]) for key in ("p","q","w"))
    if (not 0 < w <= p or not 2 < q < 16384 or q < 16*w+1
            or not any((row["p"],row["q"],row["w"])==(p,q,w) for row in PROFILES)):
        raise ValueError("Tuple outside the certified NTRU enumeration account")
    width = q.bit_length()
    pk_bytes = encoded_length(p,q)
    ct_bytes = encoded_length(p,(q+2)//3)+32
    small_bytes = (p+3)//4
    candidates = comb(p,w)*(1<<w)
    control = max(1,p.bit_length())
    setup = {"public_key_hash":ram.sha512(1+pk_bytes),
             "public_key_decode":p*(3*ram.divmod_bits(28)+ram.mul_bits(28)+2*ram.add_bits(28)+_io(112)+8*ram.loop_control(control))}
    candidate = {
        "ring_convolution":p*p*(ram.mul_mod(width)+ram.mod_add(width)+ram.memory_read(3*width)+ram.memory_write(width)+8*ram.loop_control(2*control)),
        "coefficient_initialization":_io(2*p*width+4*p),
        "ring_reduction_and_rounding":2*(p-1)*(ram.mod_add(width)+_io(3*width))
            +p*(ram.divmod_bits(width+2)+3*ram.add_bits(width+2)+2*ram.compare_bits(width+2)+ram.mux_bits(width+2)+_io(4*(width+2))),
        "mixed_radix_and_small_encoding":p*(ram.mul_bits(28)+ram.add_bits(28)+_io(112)+8*ram.loop_control(control)) +_io(8*(small_bytes+ct_bytes)),
        "input_generation":p*(3*ram.add_bits(control)+3*ram.compare_bits(control)+_io(control+2)+8*ram.loop_control(control)) + w*(ram.add_bits(2)+_io(2)),
        "confirmation_and_shared_key":ram.sha512(1+small_bytes)+ram.sha512(65)+ram.sha512(33+ct_bytes),
        "complete_ciphertext_match":ram.compare_bits(8*ct_bytes)+_io(256),
    }
    breakdown = {"setup__"+key:value for key,value in setup.items()}
    breakdown.update({"all_candidates__"+key:candidates*value for key,value in candidate.items()})
    padded_p=1<<(p-1).bit_length()
    memory = 8*(pk_bytes+ct_bytes+small_bytes)+16*padded_p*32+8192
    return _result(_identity("sntrup","3",profile,"ntru-fixed-weight-input-enumeration-v1"),
        breakdown,memory,[],
        "Enumerate every allowed fixed-weight ternary encapsulation input, reproduce the rounded polynomial and confirmation hash, then derive the shared key. Perfect message recovery makes this complete search unambiguous. Lattice and hybrid attacks can be much faster than this baseline.",
        ENUMERATION_PROOF+f"\n### Exact NTRU account\nFor p={p},q={q},w={w}, enumerate all C(p,w)·2^w supports/signs without repetition. Decode h and cache Hash4(pk). Schoolbook multiplication computes h*r modulo(q,x^p−x−1), followed by the specified centered rounding and mixed-radix encoding. Each mixed-radix merge has inputs<2^14 and product<2^28; a28-bit account suffices. Hash3(smallencode(r)), Hash2(...||Hash4(pk)) and Hash1(...||ciphertext) use the pinned SHA512-prefix transform, with full hash cost counted despite truncation to32bytes. The certified q>=16w+1 bound and exact ring arithmetic give perfect native-r recovery, hence injectivity of the honest ciphertext map. No hash-collision premise is required.\n",
        [NTRU],"1",candidates=candidates)


def estimates_for(scheme_id, version, profile):
    """Return all explicitly accounted baselines, never legacy estimator data."""
    if (scheme_id,version)==("ml-kem","compressed-research-v4"):
        return [_module(profile)]
    if (scheme_id,version)==("frodokem","anchor-research-v3"):
        n = profile["parameters"].get("n")
        q = profile["parameters"].get("q")
        if type(n) is not int or not 8<=n<=640 or n%8 or q!=32768:
            raise ValueError("This account covers only the exact Frodo v3 progression")
        return [_frodo_message(profile),_frodo_columns(profile)]
    if (scheme_id,version)==("sntrup","3"):
        return [_ntru(profile)]
    raise ValueError("Unsupported lattice release for this RAM account")


def estimate(scheme_id, version, profile):
    return min(estimates_for(scheme_id,version,profile),key=lambda record:record["work_bound"])


def recover_bounded_lwe_column(matrix, public_column, *, q=32768, bound=12, max_candidates=100000):
    """Small-instance executable of the specified full-support column search.

    No private key or noise is accepted. The limit is for local evidence runs;
    the analytical attack budget explicitly accounts for the entire space.
    """
    n = len(public_column)
    if not n or len(matrix)!=n or any(len(row)!=n for row in matrix):
        raise ValueError("Square public system required")
    if type(bound) is not int or bound<0 or q<=2*bound:
        raise ValueError("Invalid finite-support system")
    if (2*bound+1)**n>max_candidates:
        raise ValueError("Enumeration exceeds the explicit local evidence allowance")
    for secret in product(range(-bound,bound+1),repeat=n):
        residuals=[(b-sum(a*x for a,x in zip(row,secret))+q//2)%q-q//2
                   for row,b in zip(matrix,public_column)]
        if all(-bound<=value<=bound for value in residuals):
            return secret
    raise ValueError("No bounded secret candidate")
