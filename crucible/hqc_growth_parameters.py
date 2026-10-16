"""Frozen HQC-derived growth profiles; correctness is fixed, not estimated security."""
import hashlib
import json
from pathlib import Path

PARAMETERS_FILE = Path(__file__).with_name('data') / 'hqc_growth_profiles.json'
PARAMETERS_SHA256 = '0e358f4558c6cb68010e5f771fb16ad918d8eaeceb3913e89083ca3b0388a0a7'


def codec_for_weight(weight):
    """Smallest supported concatenated code meeting the worst-case bound.

    Tie-break toward fewer repeated RM blocks, then smaller RS degree.
    Domain deliberately stays inside the reference's existing table dimensions.
    """
    if type(weight) is not int or not 1 <= weight <= 27:
        raise ValueError('HQC growth weight must be an integer from 1 to 27')
    error = 2 * weight * weight + weight
    choices = [(128 * repeats * (16 + 2 * delta), repeats, delta)
               for repeats in (1, 2, 3) for delta in range(1, 16)
               if error < 32 * repeats * (delta + 1)]
    length, repeats, delta = min(choices)
    return {'rs_length': 16 + 2 * delta, 'rs_dimension': 16,
            'rs_delta': delta, 'rm_repetitions': repeats, 'rm_length': 128 * repeats,
            'code_length': length, 'worst_case_error_weight': error,
            'guaranteed_decoder_error_capacity': 32 * repeats * (delta + 1) - 1}


def gf256_mul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a <<= 1
        if a & 256:
            a ^= 0x11d
        b >>= 1
    return result


def rs_generator(delta):
    coefficients, root = [1], 1
    for _ in range(2 * delta):
        root = gf256_mul(root, 2)
        product = [0] * (len(coefficients) + 1)
        for index, value in enumerate(coefficients):
            product[index] ^= gf256_mul(value, root)
            product[index + 1] ^= value
        coefficients = product
    return coefficients


def read_profiles():
    raw = PARAMETERS_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARAMETERS_SHA256:
        raise ValueError('HQC growth parameter certificate differs from its pin')
    return json.loads(raw)['profiles']
