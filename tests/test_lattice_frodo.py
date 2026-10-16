import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import time

import pytest
from crucible import lattice_frodo as kem


def test_independent_pinned_native_anchor_all_operations():
    v = json.loads((Path(__file__).parent/"fixtures/frodo-research-anchor.json").read_text())
    pk, sk = kem.keygen_dimension(640, bytes.fromhex(v["keygen_randomness"]))
    ct, target = kem.encapsulate_dimension(640, pk, bytes.fromhex(v["encaps_randomness"]))
    assert pk.hex() == v["pk"]
    assert sk.value.hex() == v["sk"]
    assert ct.hex() == v["ct"]
    assert target.hex() == v["ss"]
    assert kem.decapsulate_dimension(640, sk, ct) == target


@pytest.mark.parametrize("tier", range(1, 51))
def test_every_actual_dimension_round_trip_and_public_isolation(tier):
    p = f"frodo-lwe-v1-tier-{tier}"
    m = kem.get_manifest(p)
    result = kem.generate(p)
    assert result.diagnostics["shared_secret_matches"] is True
    assert result.diagnostics["honest_trials"] == 1
    assert len(bytes.fromhex(result.public["public_key"])) == m["encoding"]["public_key_bytes"]
    assert len(bytes.fromhex(result.public["ciphertext"])) == m["encoding"]["ciphertext_bytes"]
    assert len(result.target) == 16
    assert result.target.hex() not in json.dumps(result.public)
    assert result.target.hex() not in repr(result)
    assert m["record_inheritance"] is False
    assert m["educational"] is False and m["admission"]["official_ranked"] is False
    assert m["profile_kind"] == "research-tier"


def test_exact_sampler_exhaustive_distribution_and_signed_boundaries():
    from collections import Counter
    observed = Counter(kem.sample_words(struct.pack("<"+"H"*65536, *range(65536))))
    assert observed[0] == 9288
    assert observed[12] == observed[-12] == 1
    assert sum(observed.values()) == 65536
    assert sum(x*x*count for x,count in observed.items()) == 519160
    for x in range(1,13):
        assert observed[x] == observed[-x]
    for boundary in kem.CDF[:-1]:
        assert kem.sample_words(struct.pack("<HH", 2*boundary, 2*(boundary+1)))[1] == kem.sample_words(struct.pack("<H", 2*boundary))[0]+1


def test_bit_encodings_and_correct_rounding_edges():
    values = [0,1,2,3,32764,32765,32766,32767]
    encoded = kem.pack(values)
    assert int.from_bytes(encoded,"big") == sum(v << (15*(7-i)) for i,v in enumerate(values))
    assert kem.unpack(encoded,8) == values
    message = bytes(range(16))
    centers = kem.encode(message)
    for delta in (-4095,0,4095):
        assert kem.decode([(v+delta)%kem.Q for v in centers]) == message
    assert kem.decode([(v+4096)%kem.Q for v in centers]) != message


def test_complete_implicit_rejection_including_salt():
    pk,sk=kem.keygen_dimension(8)
    ct,key=kem.encapsulate_dimension(8,pk)
    for position in (0,119,120,239,240,271):
        changed=ct[:position]+bytes([ct[position]^1])+ct[position+1:]
        recovered=kem.decapsulate_dimension(8,sk,changed)
        assert recovered == hashlib.shake_128(changed+sk.value[:16]).digest(16)
        assert recovered != key


def test_no_mismatch_resampling(monkeypatch):
    n=8;pk=bytes(136);ct=bytes(272);target=b'a'*16;other=b'b'*16;calls=[]
    monkeypatch.setattr(kem,"_worker",lambda dimension:(calls.append(dimension) or b'FLW1\0'+pk+ct+target+other))
    result=kem.generate('frodo-lwe-v1-tier-1')
    assert calls==[8] and result.target==target and result.diagnostics['shared_secret_matches'] is False


@pytest.mark.parametrize("value",[None,True,1,'frodo-lwe-v1-tier-0','frodo-lwe-v1-tier-51'])
def test_invalid_profiles_rejected_before_worker(value,monkeypatch):
    monkeypatch.setattr(kem,"_worker",lambda *a:pytest.fail('invalid input reached worker'))
    with pytest.raises(ValueError):kem.generate(value)


def test_malformed_crypto_inputs():
    with pytest.raises(ValueError):kem.keygen_dimension(7)
    with pytest.raises(ValueError):kem.keygen_dimension(8,b'x'*63)
    with pytest.raises(ValueError):kem.encapsulate_dimension(8,b'x'*135)
    with pytest.raises(ValueError):kem.encapsulate_dimension(8,b'x'*136,b'x'*47)
    with pytest.raises(ValueError):kem.decapsulate_dimension(8,kem.PrivateKey(b'x'*295),b'x'*272)
    for s in ('ab'*15,'ab '*16,'xx'*16,None):
        with pytest.raises(ValueError):kem.parse_shared_secret(s,'frodo-lwe-v1-tier-1')


def test_frozen_mapping_matches_reproducible_evidence():
    root=Path(__file__).parents[1]
    data=json.loads((root/'docs/lattice-frodo-calibration.json').read_text())
    assert tuple(row['n'] for row in data['profiles']) == kem.DIMENSIONS
    assert len(set(kem.DIMENSIONS))==50 and kem.DIMENSIONS[0]==8 and kem.DIMENSIONS[-1]==640
    assert kem.ESTIMATED_BITS[:2] == (None,None)
    for tier,row in enumerate(data['profiles'][2:],3):
        assert kem.get_manifest(f'frodo-lwe-v1-tier-{tier}')['initial_security_estimate']['bits']==round(row['bits'],6)


def test_exact_integer_correctness_certificate():
    path=Path(__file__).parents[1]/'scripts/lattice_calibrate_frodo.py'
    spec=importlib.util.spec_from_file_location('frodo_certificate',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.correctness_certificate(8)['bound']=='0'
    assert module.correctness_certificate(640)['bound_exponent']==133
    assert module.correctness_certificate(320)['bound_exponent']>133


def test_source_mutation_and_deadline_are_not_math_failures(monkeypatch):
    monkeypatch.setattr(kem,'IMPLEMENTATION_SHA256','0'*64)
    with pytest.raises(kem.GenerationUnavailable):kem.generate('frodo-lwe-v1-tier-1')
    monkeypatch.undo()
    monkeypatch.setattr(kem,'GENERATION_TIMEOUT_SECONDS',.00001)
    start=time.monotonic()
    with pytest.raises(kem.GenerationLimit):kem.generate('frodo-lwe-v1-tier-50')
    assert time.monotonic()-start<2
