from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import pytest
from crucible import lattice_module as kem


def test_independent_sage_full_construction_vectors():
    source=Path(__file__).parents[1]/'scripts/lattice_module_reference.py'
    fixture=json.loads((Path(__file__).parent/'fixtures/module-lwe-research.json').read_text())
    assert fixture['reference_source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    for v in fixture['vectors']:
        q=int(v['q']);pk,sk=kem.keygen_modulus(q,bytes.fromhex(v['key_randomness']))
        ct,ss=kem.encapsulate_modulus(q,pk,bytes.fromhex(v['encapsulation_message']))
        assert hashlib.sha256(pk).hexdigest()==v['pk_sha256']
        assert hashlib.sha256(sk.value).hexdigest()==v['sk_sha256']
        assert hashlib.sha256(ct).hexdigest()==v['ct_sha256']
        assert ss.hex()==v['shared_secret']
        assert kem.decapsulate_modulus(q,sk,ct)==ss


@pytest.mark.parametrize('tier',range(1,51))
def test_each_real_modulus_with_complete_public_contract(tier):
    profile=f'module-lwe-v1-tier-{tier}';m=kem.get_manifest(profile);r=kem.generate(profile)
    assert r.diagnostics['shared_secret_matches'] is True
    assert r.diagnostics['honest_trials']==1
    assert r.public['parameters']['q']==str(kem.MODULI[tier-1])
    assert len(bytes.fromhex(r.public['public_key']))==m['encoding']['public_key_bytes']
    assert len(bytes.fromhex(r.public['ciphertext']))==m['encoding']['ciphertext_bytes']
    assert len(r.target)==32 and r.target.hex() not in repr(r) and r.target.hex() not in json.dumps(r.public)
    assert m['record_inheritance'] is False and m['educational'] is False
    assert m['correctness']['kind']=='perfect' and m['correctness']['failure_bound']=='0'
    assert m['admission']['official_ranked'] is False


def test_cbd2_exhaustive_four_bit_distribution():
    counts=Counter(kem.cbd(bytes(range(128))))
    # Test each of16 possible nibbles in both positions without relying on PRF.
    values=[kem.cbd(bytes([n|(n<<4)])*128)[0] for n in range(16)]
    assert Counter(values)==Counter({-2:1,-1:4,0:6,1:4,2:1})
    assert all(-2<=x<=2 for x in counts)
    assert kem.noise(bytes(32),0)!=kem.noise(bytes(32),1)


def test_public_uniform_rejection_does_not_reduce_modulo_q(monkeypatch):
    q=kem.MODULI[0];width=(q.bit_length()+7)//8
    class Stream:
        def digest(self,length):
            block=q.to_bytes(width,'little')+(q-1).to_bytes(width,'little')
            return (block*((length+len(block)-1)//len(block)))[:length]
    monkeypatch.setattr(kem.hashlib,'shake_128',lambda _data:Stream())
    assert kem.uniform_poly(bytes(32),q,0,0)==[q-1]*256


def test_rejection_sampler_resource_abort_is_not_biased_fallback(monkeypatch):
    class BadStream:
        def digest(self,length):return b'\xff'*length
    monkeypatch.setattr(kem.hashlib,'shake_128',lambda _data:BadStream())
    with pytest.raises(kem.GenerationLimit):kem.uniform_poly(bytes(32),kem.MODULI[0],0,0)


def test_negacyclic_wrap_sign_and_fixed_width_canonical_encoding():
    q=kem.MODULI[-1];a=[0]*256;b=[0]*256;a[255]=2;b[1]=3
    expected=[q-6]+[0]*255
    assert kem.poly_mul(a,b,q)==expected
    assert kem.unpack(kem.pack([expected],q),1,q)==[expected]
    width=(q.bit_length()+7)//8
    invalid=q.to_bytes(width,'little')+bytes(255*width)
    with pytest.raises(ValueError):kem.unpack(invalid,1,q)


def test_perfect_correctness_has_actual_support_margin_all_profiles():
    assert len(kem.MODULI)==len(set(kem.MODULI))==50
    assert all(a>b for a,b in zip(kem.MODULI,kem.MODULI[1:]))
    assert min(q//4 for q in kem.MODULI)>2*kem.N*kem.K*kem.ETA**2+kem.ETA==6146
    data=json.loads((Path(__file__).parents[1]/'docs/lattice-module-calibration.json').read_text())
    assert tuple(int(r['q']) for r in data['profiles'])==kem.MODULI
    for tier,row in enumerate(data['profiles'],1):
        manifest=kem.get_manifest(f'module-lwe-v1-tier-{tier}')
        if row['bits'] is None:assert 'initial_security_estimate' not in manifest
        else:assert manifest['initial_security_estimate']['bits']==round(row['bits'],6)


def test_complete_implicit_rejection_and_malformed_coefficients():
    q=kem.MODULI[-1];pk,sk=kem.keygen_modulus(q);ct,ss=kem.encapsulate_modulus(q,pk)
    for position in (0,len(ct)//2,len(ct)-1):
        modified=ct[:position]+bytes([ct[position]^1])+ct[position+1:]
        assert kem.decapsulate_modulus(q,sk,modified)==hashlib.shake_256(sk.value[:32]+modified).digest(32)
    width=(q.bit_length()+7)//8;noncanonical=q.to_bytes(width,'little')+ct[width:]
    assert kem.decapsulate_modulus(q,sk,noncanonical)==hashlib.shake_256(sk.value[:32]+noncanonical).digest(32)
    with pytest.raises(ValueError):kem.decapsulate_modulus(q,sk,ct[:-1])
    with pytest.raises(ValueError):kem.encapsulate_modulus(q,pk[:-1])


def test_fresh_full_randomness_and_profile_binding():
    q=kem.MODULI[-1];pk,sk=kem.keygen_modulus(q);other,_=kem.keygen_modulus(q)
    assert pk!=other
    ct,ss=kem.encapsulate_modulus(q,pk);ct2,ss2=kem.encapsulate_modulus(q,pk)
    assert ct!=ct2 and ss!=ss2
    with pytest.raises(ValueError):kem.encapsulate_modulus(kem.MODULI[0],pk)
    with pytest.raises(ValueError):kem.keygen_modulus(True)
    assert repr(sk)=='PrivateKey(<redacted>)'


def test_resource_deadline_and_source_identity(monkeypatch):
    monkeypatch.setattr(kem,'IMPLEMENTATION_SHA256','0'*64)
    with pytest.raises(kem.GenerationUnavailable):kem.generate('module-lwe-v1-tier-1')
    monkeypatch.undo();monkeypatch.setattr(kem,'GENERATION_TIMEOUT_SECONDS',.00001)
    start=time.monotonic()
    with pytest.raises(kem.GenerationLimit):kem.generate('module-lwe-v1-tier-1')
    assert time.monotonic()-start<2
