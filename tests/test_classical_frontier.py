"""Equivalent representations and explicitly modeled exponent optimization."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random

import pytest
from crucible import dh_v6, dh_v7, rsa_v3, rsa_v4, ec_research, ec_compressed


def example(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1]/'scripts'/name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dh_interval_choice_preserves_reference_and_fixed_correctness():
    assert len(dh_v7.manifests()) == 50
    for old, new in zip(dh_v6.manifests(), dh_v7.manifests()):
        p = dh_v7._profile(new['id']); upper = dh_v7.exponent_upper_bound(p)
        assert all(new['parameters'][key] == old['parameters'][key] for key in ('p','q','g'))
        baseline = dh_v7._INITIAL_ESTIMATES[p.instance_size]['bits']
        chosen = math.ceil(2*baseline + 32)
        assert upper == min(p.q-1, (1 << chosen)-1)
        assert new['initial_security_estimate']['bits'] >= baseline-0.00002
        if upper < p.q-1:
            assert chosen-1 < 2*baseline+32 <= chosen
        assert new['correctness']['failure_bound'] == '0'
        for private in (1, upper):
            pk=dh_v7._encode(p, pow(p.g,private,p.p))
            ct,key=dh_v7.encapsulate(p.id,pk)
            assert dh_v7.decapsulate(p.id,private,ct) == key
        with pytest.raises(ValueError): dh_v7.decapsulate(p.id,upper+1,ct)
    assert dh_v7.exponent_upper_bound(dh_v7._profile('dh-v7-tier-50')).bit_length()==288
    assert dh_v6._profile('dh-v6-tier-50').q.bit_length()==1537


def test_dh_public_example_and_rng_uses_declared_interval(monkeypatch):
    seen=[]
    def draw(bound): seen.append(bound);return bound-1
    monkeypatch.setattr(dh_v7.secrets,'randbelow',draw)
    generated=dh_v7.generate('dh-v7-tier-1')
    assert seen==[dh_v7.exponent_upper_bound(dh_v7._profile('dh-v7-tier-1'))]*2
    assert example('solve_teaching_frontier.py').recover(generated.public)==generated.target.hex()
    assert generated.diagnostics['shared_secret_matches']


@pytest.mark.parametrize('tier',[1,25,50])
def test_rsa_compact_encoding_honest_and_malformed(tier, monkeypatch):
    # Conformance is conditional on successful bounded generation. Exercise the
    # encoding with headroom for a busy host; resource limits have their own tests.
    monkeypatch.setattr(rsa_v4, 'MAX_GENERATION_SECONDS', 60.0)
    profile=rsa_v4._profile(f'rsa-v4-tier-{tier}')
    pk,sk=rsa_v4.keygen(profile.id);ct,key=rsa_v4.encapsulate(profile.id,pk)
    assert len(pk)==profile.width+3 and pk[-3:]==b'\x01\x00\x01'
    assert rsa_v4.decapsulate(profile.id,sk,ct)==key
    for bad in (pk+b'\x00', pk[:-1], pk[:-3]+b'\x01\x00\x03', b'\x00'*len(pk)):
        with pytest.raises(ValueError):rsa_v4.encapsulate(profile.id,bad)
    # RSA correctness includes nonunits, not only likely random draws.
    for z in (0,sk.p,sk.q,sk.p*sk.q-1):
        c=rsa_v4._encode(profile,pow(z,65537,sk.p*sk.q))
        assert rsa_v4.decapsulate(profile.id,sk,c)==rsa_v4._kdf(profile,pk,c,z)


def test_rsa_frontier_costs_and_public_only_example():
    for old,new in zip(rsa_v3.manifests(),rsa_v4.manifests()):
        width=(new['parameter_bits']+7)//8
        assert new['parameter_bits']==old['parameter_bits']
        assert new['initial_security_estimate']==old['initial_security_estimate']
        assert new['encoding']['public_key_bytes']==width+3<old['encoding']['public_key_bytes']
        assert new['encoding']['ciphertext_bytes']==old['encoding']['ciphertext_bytes']
    generated=rsa_v4.generate('rsa-v4-tier-1')
    assert example('solve_rsa_frontier.py').recover(generated.public)==generated.target.hex()


@pytest.mark.parametrize('tier',range(1,51))
def test_ec_compression_retains_the_exact_point_and_shared_group_element(tier):
    row=ec_compressed._profile(f'ec-compressed-v2-tier-{tier}')
    assert row==ec_research._profile(f'ec-research-v1-tier-{tier}')
    for scalar in (1,2,row['q']-1):
        point=ec_research._mul(row['p'],row['a'],tuple(row['g']),scalar)
        encoded=ec_compressed._encode(row,point)
        assert ec_compressed._decode(row,encoded)==point
        assert len(encoded)==1+(row['p'].bit_length()+7)//8
        assert len(encoded)<len(ec_research._encode(row,point))
        opposite=bytes([encoded[0]^1])+encoded[1:]
        assert ec_compressed._decode(row,opposite)==(point[0],-point[1]%row['p'])
    pk,sk=ec_compressed.keygen(f'ec-compressed-v2-tier-{tier}')
    ct,key=ec_compressed.encapsulate(f'ec-compressed-v2-tier-{tier}',pk)
    assert ec_compressed.decapsulate(f'ec-compressed-v2-tier-{tier}',sk,ct)==key
    width=(row['p'].bit_length()+7)//8
    for bad in (b'\x00',b'\x04'+pk[1:],pk+b'\x00',b'\x02'+row['p'].to_bytes(width,'big')):
        with pytest.raises(ValueError):ec_compressed._decode(row,bad)


def test_ec_square_root_rejects_nonresidue_and_has_unique_zero_encoding():
    for p in (5,13,17,41,97,103):
        for value in range(p):
            squares={x*x%p for x in range(p)}
            if value in squares:
                root=ec_compressed._square_root(value,p);assert root*root%p==value
            else:
                with pytest.raises(ValueError):ec_compressed._square_root(value,p)
    artificial={'p':5,'a':0,'b':0}
    assert ec_compressed._decode(artificial,b'\x02\x00')==(0,0)
    with pytest.raises(ValueError):ec_compressed._decode(artificial,b'\x03\x00')
