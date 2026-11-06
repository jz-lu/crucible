"""Versioned classical bit-RAM accounting for explicit attack upper bounds.

These functions count an identified implementation, never the fastest possible
implementation. Python execution time is not the unit. Fixed bit-register wiring
is free; Boolean functions of <=2 register bits cost one; an indirect bit-memory
read/write costs one. Address arithmetic and control are charged separately.
No vector instruction, hash, field operation, or unbounded integer op is unit cost.
"""
from __future__ import annotations

import math

MODEL_ID = "crucible-bit-ram-v1"
MODEL_VERSION = 1
MODEL = {
    "id": MODEL_ID,
    "label": "Classical bit-RAM · v1",
    "unit": "log2 bit operations",
    "status": "research-draft",
    "experiment": "one-fresh-honest-kem-shared-secret-v1",
    "success_target": "1/2",
    "preprocessing": "Single target; all attack setup charged. No amortization or target-specific advice.",
    "memory": "Unbounded capacity; each indirect one-bit read/write costs one operation. Peak storage is reported separately.",
    "semantics": [
        "One Boolean operation of at most two bit-register inputs costs one; fixed wiring, constants and bit permutations are free.",
        "Indirect reads and writes cost one per transferred bit, independently of memory size. Address construction is additional work.",
        "Register layouts and executable program are fixed by the public configuration, never by an unknown target answer.",
        "Integer/field arithmetic, comparisons, branches and hashes are expanded into the declared elementary algorithms.",
        "Every reported cost is a conservative work budget, not expected Python runtime or a lower bound on all attacks.",
    ],
    "sources": [
        {"title": "NIST IR 8413-upd1, Appendix B: bit-operation RAM", "url": "https://nvlpubs.nist.gov/nistpubs/ir/2022/NIST.IR.8413-upd1.pdf#page=90"},
        {"title": "NIST Kyber512 FAQ (December 2023): gate-count and RAM conventions", "url": "https://csrc.nist.gov/csrc/media/Projects/post-quantum-cryptography/documents/faq/Kyber-512-FAQ.pdf"},
        {"title": "Bernstein–Chou: executable attacks, cost and success predictors", "url": "https://iacr.org/submit/files/slides/2024/crypto/crypto2024/407/slides.pdf"},
    ],
}


def _positive(value):
    if type(value) is not int or value < 1:
        raise ValueError("A positive integer width is required")
    return value


def _length(value):
    if type(value) is not int or value < 0:
        raise ValueError("A nonnegative integer length is required")
    return value


def add_bits(width):
    # Ripple full adder: 2 XOR + 2 AND + OR =5 per bit; 8 also covers
    # carry setup and fixed flag/selection bookkeeping.
    return 8 * _positive(width)


sub_bits = add_bits
compare_bits = add_bits


def mux_bits(width):
    return 3 * _positive(width)  # b XOR (s AND (a XOR b))


def loop_control(index_bits):
    # One increment + comparison at the declared public index width.
    return 16 * (_length(index_bits) + 1)


def memory_read(bits):
    return _length(bits)


memory_write = memory_read


def mod_add(width):
    # Inputs [0,q), q<2^w. (w+1)-bit sum, subtract q, select by borrow;
    # 8+8+3=19 per bit, padded to24 to include flags/control.
    return 24 * (_positive(width) + 1)


def mul_bits(width):
    # Schoolbook AND rows and ripple additions, w rounds, with loop/control.
    return 16 * (_positive(width) + 1) ** 2


def divmod_bits(width):
    # Restoring long division, w iterations: compare/subtract/select on w+1
    # bits (<=19(w+1)) plus index increment/comparison and flags.
    return 64 * (_positive(width) + 1) ** 2


def mul_mod(width):
    # w double-and-add rounds: two modular adds, one selector and control.
    # The 80(w+1)^2 bound also covers initialization/final copy.
    return 80 * (_positive(width) + 1) ** 2


def pow_mod(width, exponent_bits):
    width, exponent_bits = _positive(width), _positive(exponent_bits)
    return exponent_bits * (2 * mul_mod(width) + mux_bits(width) + loop_control(exponent_bits)) + 2 * width


def _sha2(nbytes, word, rounds):
    nbytes = _length(nbytes)
    blockbytes = 16 * word // 8
    lengthbytes = word // 4
    blocks = (nbytes + 1 + lengthbytes + blockbytes - 1) // blockbytes
    # Schedule: two small sigmas (4 XOR/bit total), three adds.
    schedule = (rounds - 16) * (4 * word + 3 * add_bits(word))
    # Round: two big sigmas; Ch<=3/bit, Maj<=4/bit, seven adds.
    round_cost = rounds * (11 * word + 7 * add_bits(word) + loop_control(rounds.bit_length()))
    feedforward = 8 * add_bits(word)
    return blocks * (schedule + round_cost + feedforward + 2 * blockbytes * 8) + 8 * nbytes + 8 * word


def sha256(nbytes):
    return _sha2(nbytes, 32, 64)


def sha512(nbytes):
    return _sha2(nbytes, 64, 80)


def _keccak(nbytes, outbytes, rate):
    nbytes, outbytes = _length(nbytes), _length(outbytes)
    absorbs = nbytes // rate + 1  # domain byte + final padding bit fit in same byte
    squeezes = max(0, (outbytes + rate - 1) // rate - 1)
    # Per round: theta3200, chi4800, iota<=64; rho/pi fixed wiring.
    # 24*8064=193536;200000 also covers round bookkeeping.
    return (absorbs + squeezes) * 200000 + absorbs * 16 * rate + 8 * outbytes


def shake256(nbytes, outbytes):
    return _keccak(nbytes, outbytes, 136)


def shake128(nbytes, outbytes):
    return _keccak(nbytes, outbytes, 168)


def sha3_256(nbytes):
    return shake256(nbytes, 32)


def sha3_512(nbytes):
    return _keccak(nbytes, 64, 72)


def log2_int(value):
    """Finite log2 without converting an exponentially large int to float."""
    value = _positive(value)
    shift = max(0, value.bit_length() - 53)
    return math.log2(value >> shift) + shift


TECHNICAL_MODEL = r"""## Shared computation model: crucible-bit-ram-v1

The program is uniform in public configuration inputs; no answer-dependent
advice is permitted. Its fixed-address bit registers support arbitrary Boolean
functions of one or two bits at unit cost. Constants, fixed wiring and fixed bit
permutations are free. A dynamically addressed bit read or write costs one;
computing that address is additional charged arithmetic. This is a bit-RAM
abstraction inspired by NIST's classical gate-count discussions, not the CAT
pure circuit metric and not a NIST-approved scheme classification.

The baseline has no memory-cap constraint. It measures neither physical latency
nor energy. A pointer of b bits must be manipulated with the b-bit arithmetic
account. Loops use a public counter width and charge increment/comparison.
Input generation for the honest challenger is outside the attack budget; all
attacker initialization, candidate generation, verification and key derivation
are inside. Randomness and any rejection sampling need explicit budgets or
their own success-loss treatment. A counter on high-level Python operations is
validation of the operation census, not a trace of exact elementary gates.

Primitive upper bounds, for width w: ADD/SUB/CMP=8w; MUX=3w;
MODADD=24(w+1); MUL=16(w+1)^2; DIVMOD=64(w+1)^2;
MODMUL=80(w+1)^2; LOOP(b)=16(b+1).
MODPOW(w,e)=e(2 MODMUL(w)+MUX(w)+LOOP(e))+2w.
All bounds refer to schoolbook/ripple, restoring-division and double-and-add
algorithms. They deliberately upper-bound a particular implementation.

SHA-256/SHA-512 follow FIPS180-4. The schedule uses two small sigmas and three
word additions per expanded word. Each round uses two large sigmas, Ch, Maj,
and seven additions. Eight final additions update the chaining value. Padding
and block input/output are charged. SHA3/SHAKE follow FIPS202: Keccak-f[1600]
uses at most193536 Boolean operations (24 rounds of8064), padded to200000
for control. Absorption and output transfers are additional. These are
algorithmic upper bounds, not claims about optimized hardware.

Every profile's complete attack is stopped within its reported budget. Its
success statement names the probability space and any unproved premises.
The resulting curve is a documented attack upper bound, not a proven security
level and not necessarily the cheapest known attack. No 50%-success result is
inferred simply by dividing cost by average success probability.

Primary specifications:
- https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf
- https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.202.pdf
"""
