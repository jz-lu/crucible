"""Independent representation/model checks; not an optimality/security proof.

The first EC curve is small enough to check its entire compressed wire domain
against a directly enumerated square table, without using Tonelli-Shanks as
its own oracle. The frozen source hashes protect pre-revision contracts.
"""
from decimal import Decimal, localcontext
import hashlib
import math
from pathlib import Path

import pytest

from crucible import dh_v7, ec_compressed, rsa_v4


ROOT = Path(__file__).resolve().parents[1]
FROZEN = {
    'crucible/dh_v6.py': '8701162e1415ca5892308a463385bc9428c2fb06b7ad66087c6e2ca0e80fe9d3',
    'crucible/rsa_v3.py': '679b213ee8d2e873aea2ad01a983d126e48941a8fe9c96d11209b7dde6727ba0',
    'crucible/ec_research.py': '389f6c80eacac92e99fec1e9b146197321f3f573dd57065dd213e1905a1266a0',
    'crucible/ec_parameters.json': '3a33802acf992bbebc7bd9cac5e9056a278143c9becbf6db0f32b83f8d8b6e1a',
}


def test_archived_classical_sources_and_parameter_contracts_remain_frozen():
    for relative, expected in FROZEN.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_dh_public_interval_and_estimate_agree_with_independent_decimal_formula():
    with localcontext() as context:
        context.prec = 65
        two = Decimal(2)
        ln2 = two.ln()

        def nfs_shape(bits):
            value = Decimal(bits) * ln2
            return (value.ln() / 3 + Decimal(2) * value.ln().ln() / 3).exp()

        anchor = nfs_shape(3072)
        for item in dh_v7.manifests():
            p = int(item['parameters']['p'], 16)
            q = int(item['parameters']['q'], 16)
            upper = int(item['parameters']['exponent_upper_bound'], 16)
            baseline = min(Decimal(q).ln() / ln2 / 2, Decimal(128) * nfs_shape(p.bit_length()) / anchor)
            # The source freezes six-decimal model values; compare within that
            # declared precision, not an invented exact security statement.
            reference = min(baseline, Decimal(upper).ln() / ln2 / 2)
            shown = Decimal(str(item['initial_security_estimate']['bits']))
            assert abs(shown - reference) < Decimal('0.0000011')
            frozen_bits = dh_v7._INITIAL_ESTIMATES[item['instance_size']]['bits']
            chosen = math.ceil(2 * frozen_bits + 32)
            assert 1 <= upper < q and upper == min(q - 1, (1 << chosen) - 1)
            if upper != q - 1:
                # Minimal whole bit-length within the chosen 32-exponent-bit
                # margin convention; no global parameter optimum is implied.
                assert chosen - 1 < 2 * frozen_bits + 32 <= chosen
                assert math.log2(upper) / 2 >= frozen_bits + 16 - 1e-10
            # Public clients can reconstruct the chosen interval from the
            # published profile, without consulting private server data.
            assert upper == min(q - 1, (1 << math.ceil(2 * float(shown) + 32)) - 1)


def test_entire_first_ec_compressed_domain_matches_direct_square_enumeration():
    row = ec_compressed._profile('ec-compressed-v2-tier-1')
    p, a, b, q = (row[key] for key in ('p', 'a', 'b', 'q'))
    roots = {}
    for y in range(p):
        roots.setdefault(y * y % p, {})[y & 1] = y
    width = (p.bit_length() + 7) // 8
    accepted = 0
    for x in range(p):
        expected = roots.get((x * x * x + a * x + b) % p, {})
        for parity in (0, 1):
            wire = bytes([2 | parity]) + x.to_bytes(width, 'big')
            if parity in expected:
                point = ec_compressed._decode(row, wire)
                assert point == (x, expected[parity])
                assert ec_compressed._encode(row, point) == wire
                accepted += 1
            else:
                with pytest.raises(ValueError):
                    ec_compressed._decode(row, wire)
    # Exactly the q-1 affine points: no infinity representation or duplicated
    # negative-zero encoding is accepted.
    assert accepted == q - 1


def test_compact_rsa_public_fields_and_transcript_are_independently_reproducible(monkeypatch):
    profile = rsa_v4._profile('rsa-v4-tier-1')
    public, private = rsa_v4.keygen(profile.id)
    modulus = int.from_bytes(public[:-3], 'big')
    assert public[-3:] == bytes.fromhex('010001')
    assert modulus == private.p * private.q
    # Exercise a nonunit and independently frame the compact exponent. This
    # checks the new wire/KDF representation rather than replaying _kdf.
    z = private.p
    monkeypatch.setattr(rsa_v4.secrets, 'randbelow', lambda bound: z)
    ciphertext, key = rsa_v4.encapsulate(profile.id, public)
    assert int.from_bytes(ciphertext, 'big') == pow(z, 65537, modulus)
    fields = (profile.id.encode('ascii'), modulus.to_bytes(profile.width, 'big'),
              bytes.fromhex('010001'), ciphertext, z.to_bytes(profile.width, 'big'))
    framed = b'crucible/rsa-kem/v4/shared-secret\x00' + b''.join(
        len(value).to_bytes(4, 'big') + value for value in fields)
    assert key == hashlib.sha256(framed).digest()
    assert rsa_v4.decapsulate(profile.id, private, ciphertext) == key
