#!/usr/bin/env python3
"""Reproduce the public DH6 setup (never a secret-key generator).

Usage: python scripts/generate_dh_v6_parameters.py --tier 1 --output params.json
Omit --tier to regenerate all 50 profiles. Uses fixed public SHAKE256 seeds,
exact certificates, and the frozen modulus table; it does not modify adapters.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
# Frozen linear target: 8 + 120 * (tier - 1) / 49 classical reference bits.
_MODULUS_BITS = (32, 42, 52, 62, 72, 81, 91, 101, 111, 121, 141, 164, 190, 217, 247, 279, 313, 350, 389, 430, 474, 521, 571, 623, 678, 735, 796, 859, 925, 994, 1067, 1142, 1221, 1302, 1387, 1475, 1566, 1661, 1759, 1861, 1966, 2074, 2186, 2302, 2421, 2544, 2670, 2801, 2935, 3072)

MAX_PRIME_BITS=3072
MAX_CANDIDATES=131072

class PublicStream:
    def __init__(self,tier):
        self.seed=f"crucible/dh-v6/setup/v1/tier-{tier}".encode("ascii")
        self.counter=0

    def randbelow(self,upper):
        bits=(upper-1).bit_length()
        width=(bits+7)//8
        while True:
            self.counter+=1
            value=int.from_bytes(hashlib.shake_256(self.seed+self.counter.to_bytes(8,"big")).digest(width),"big") & ((1<<bits)-1)
            if value<upper:
                return value

class PrimeGenerationLimit(ValueError):
    """A bounded search exhausted its allowance; no instance was generated."""

@dataclass
class _GenerationBudget:
    remaining: int = MAX_CANDIDATES

    def spend(self):
        if self.remaining <= 0:
            raise PrimeGenerationLimit("The bounded prime-generation search was exhausted")
        self.remaining -= 1

@dataclass(frozen=True, repr=False)
class PrimeCertificate:
    value: int
    factor: PrimeCertificate | None = None
    multiplier: int = 0

    def __repr__(self):
        return "PrimeCertificate(<public parameter>)"

def _trial_is_prime(value):
    if type(value) is not int or not 2 <= value < (1 << 16):
        return False
    if value % 2 == 0:
        return value == 2
    return all(value % divisor for divisor in range(3, math.isqrt(value) + 1, 2))

_SMALL_PRIMES=tuple(value for value in range(3,1000,2) if _trial_is_prime(value))

def _prove_prime(bits, budget=None, rng=None):
    """Sample a structured prime, with an exact public primality certificate.

    Base case: uniform odd k-bit candidates, exact trial division (k<=16).
    Otherwise draw one certified q of ceil(k/2)+1 bits, then uniform R in the
    exact interval making p=2Rq+1 k bits. Reject small factors or a failed
    base-2 Pocklington certificate. q^2>p, Fermat, and the gcd condition prove
    primality; there is no probable-prime error probability. All levels share
    a finite candidate budget. Uniformity over all k-bit primes is not claimed.
    """
    if type(bits) is not int or not 2 <= bits <= MAX_PRIME_BITS:
        raise ValueError("Unsupported certified prime bit length")
    budget = _GenerationBudget() if budget is None else budget
    if bits <= 16:
        while True:
            budget.spend()
            value = (1 << (bits - 1)) | (rng.randbelow(1 << (bits - 2)) << 1) | 1
            if _trial_is_prime(value):
                return PrimeCertificate(value)
    factor = _prove_prime((bits + 1) // 2 + 1, budget, rng)
    step = 2 * factor.value
    minimum = ((1 << (bits - 1)) - 1 + step - 1) // step
    maximum = ((1 << bits) - 2) // step
    while True:
        budget.spend()
        multiplier = minimum + rng.randbelow(maximum - minimum + 1)
        value = step * multiplier + 1
        if any(value % prime == 0 for prime in _SMALL_PRIMES):
            continue
        if (factor.value * factor.value > value and pow(2, value - 1, value) == 1
                and math.gcd(pow(2, 2 * multiplier, value) - 1, value) == 1):
            return PrimeCertificate(value, factor, multiplier)

def _validate_prime_certificate(certificate, *, _depth=0):
    """Check a bounded proof tree exactly; return its proved prime value."""
    if not isinstance(certificate, PrimeCertificate) or _depth > 16:
        raise ValueError("Invalid public prime certificate")
    value = certificate.value
    if type(value) is not int or not 2 <= value or value.bit_length() > MAX_PRIME_BITS:
        raise ValueError("Invalid certified prime value")
    if value.bit_length() <= 16:
        if (certificate.factor is not None or type(certificate.multiplier) is not int
                or certificate.multiplier != 0 or not _trial_is_prime(value)):
            raise ValueError("Invalid trial-division prime certificate")
        return value
    factor = _validate_prime_certificate(certificate.factor, _depth=_depth + 1)
    multiplier = certificate.multiplier
    if (type(multiplier) is not int or multiplier <= 0
            or multiplier.bit_length() > MAX_PRIME_BITS
            or factor.bit_length() != (value.bit_length() + 1) // 2 + 1
            or value != 2 * multiplier * factor + 1 or factor * factor <= value
            or pow(2, value - 1, value) != 1
            or math.gcd(pow(2, 2 * multiplier, value) - 1, value) != 1):
        raise ValueError("Invalid Pocklington prime certificate")
    return value

def _encode_proof(proof):
    return {"value":str(proof.value),"multiplier":proof.multiplier,
            "factor":_encode_proof(proof.factor) if proof.factor else None}


def generate(tier):
    if type(tier) is not int or not 1<=tier<=50:
        raise ValueError("Tier must be an integer from 1 through 50")
    stream=PublicStream(tier)
    proof=_prove_prime(_MODULUS_BITS[tier-1],rng=stream)
    _validate_prime_certificate(proof)
    p,q=proof.value,proof.factor.value
    h=2
    while (g:=pow(h,(p-1)//q,p))==1:
        h+=1
    return {"tier":tier,"modulus_bits":p.bit_length(),"p":str(p),"q":str(q),"g":str(g),
            "generator_base":h,"public_seed":stream.seed.decode("ascii"),"draws":stream.counter,
            "certificate":_encode_proof(proof)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier",type=int,choices=range(1,51))
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    tiers=[args.tier] if args.tier else range(1,51)
    rows=[]
    for tier in tiers:
        rows.append(generate(tier))
        print(f"Verified public setup for Tier {tier}",file=sys.stderr)
    args.output.write_text(json.dumps(rows,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
