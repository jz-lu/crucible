"""Independent public conformance vectors and algebraic/validation boundaries."""
import hashlib
import json
from pathlib import Path
import random

import pytest

from crucible import module_compressed_v4_core as core

FIXTURE = Path(__file__).parent / "fixtures/module-compressed-v4-acvp.json"
PARAMETERS = {"ML-KEM-512": core.ML_KEM_512, "ML-KEM-768": core.ML_KEM_768}
GROUPS = json.loads(FIXTURE.read_text())["groups"]
CASES = [(group, case) for group in GROUPS for case in group["tests"]]


@pytest.mark.parametrize("group,case", CASES,
    ids=[f"{group['parameter_set']}-{group['function']}-{case['tcId']}" for group,case in CASES])
def test_independent_nist_acvp(group, case):
    p = PARAMETERS[group["parameter_set"]]
    value = lambda name: bytes.fromhex(case[name])
    operation = group["function"]
    if operation == "keygen":
        pk, sk = core.keygen_internal(p, value("d"), value("z"))
        assert pk == value("ek") and sk == value("dk")
    elif operation == "encapsulation":
        ct, key = core.encapsulate_internal(p, value("ek"), value("m"))
        assert ct == value("c") and key == value("k")
    elif operation == "decapsulation":
        assert core.decapsulate(p, value("dk"), value("c")) == value("k")
    elif operation in ("encapsulationKeyCheck", "decapsulationKeyCheck"):
        check = core.validate_public_key if operation == "encapsulationKeyCheck" else core.validate_private_key
        key = value("ek" if operation == "encapsulationKeyCheck" else "dk")
        if case["testPassed"]:
            check(p, key)
        else:
            with pytest.raises(ValueError):
                check(p, key)
    else:
        pytest.fail("Unrecognized NIST fixture category")


def schoolbook(a, b):
    # Independent signed convolution in Z[x]/(x^n+1), without NTT helpers.
    n, output = len(a), [0] * len(a)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            degree = i+j
            output[degree % n] += (1 if degree < n else -1) * x*y
    return [x % core.Q for x in output]


@pytest.mark.parametrize("n", [8, 16, 32, 64, 128, 256])
def test_generalized_ntt_is_exact_ring_arithmetic(n):
    rng = random.Random(n)
    for _ in range(3):
        a = [rng.randrange(core.Q) for _ in range(n)]
        b = [rng.randrange(core.Q) for _ in range(n)]
        assert core.inverse_ntt(core.ntt(a)) == a
        assert core.inverse_ntt(core.multiply_ntts(core.ntt(a), core.ntt(b))) == schoolbook(a,b)


@pytest.mark.parametrize("bits,maximum", [(10, 2), (4, 104)])
def test_exact_compression_error_support(bits, maximum):
    errors = []
    for value in range(core.Q):
        recovered = core.decompress(core.compress(value,bits),bits)
        difference = (recovered-value+core.Q//2) % core.Q-core.Q//2
        errors.append(abs(difference))
    assert max(errors) == maximum


@pytest.mark.parametrize("n", [8, 16, 32, 64, 128, 256])
def test_native_message_space_encoding_and_single_honest_roundtrip(n):
    p = core.Parameters(n,2,3)
    d, z = bytes(range(32)), bytes(range(32,64))
    pk, sk = core.keygen_internal(p,d,z)
    assert len(pk) == p.public_key_bytes
    assert len(sk) == p.private_key_bytes
    for message in (bytes(p.message_bytes), b"\xff"*p.message_bytes):
        ct, key = core.encapsulate_internal(p,pk,message)
        assert len(ct) == p.ciphertext_bytes
        assert core.decapsulate(p,sk,ct) == key
        mutated = bytes([ct[0]^1])+ct[1:]
        expected = hashlib.shake_256(core.domain(p,"rejection")+z+mutated).digest(32)
        assert core.decapsulate(p,sk,mutated) == expected
    with pytest.raises(ValueError):
        core.encapsulate_internal(p,pk,bytes(p.message_bytes+1))


def test_public_key_modulus_check_and_private_hash_validation():
    p = core.ML_KEM_512
    pk, sk = core.keygen_internal(p,bytes(32),bytes(32))
    malformed = bytes([255, pk[1] | 15]) + pk[2:]
    with pytest.raises(ValueError, match="Noncanonical"):
        core.encapsulate_internal(p,malformed,bytes(32))
    ct,_ = core.encapsulate_internal(p,pk,bytes(32))
    with pytest.raises(ValueError,match="hash"):
        core.decapsulate(p,sk[:-64]+bytes([sk[-64]^1])+sk[-63:],ct)
    with pytest.raises(ValueError):
        core.decapsulate(p,sk,ct[:-1])


@pytest.mark.parametrize("values", [(True,2,3),(7,2,3),(512,2,3),(256,4,2),(256,2,4)])
def test_unregistered_arithmetic_dimensions_rejected(values):
    with pytest.raises(ValueError):
        core.Parameters(*values)


def test_reduced_profiles_separate_every_hash_purpose_and_parameter_tuple():
    domains = [core.domain(core.Parameters(n,2,3), role)
               for n in (8,16,32,64,128)
               for role in ("matrix","noise","keygen","public-key-hash","encapsulation","rejection")]
    assert all(domains) and len(domains) == len(set(domains))
    assert core.domain(core.ML_KEM_512,"keygen") == b""
    assert core.domain(core.ML_KEM_768,"encapsulation") == b""
    with pytest.raises(ValueError):
        core.domain(core.Parameters(8,2,3),"unknown")
