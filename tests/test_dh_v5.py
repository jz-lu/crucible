"""The calibrated DH release has proved parameters and independent recovery."""
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from crucible import calibration, dh_v5
from scripts.generate_dh_v5_parameters import generate as reproduce_setup
from scripts.solve_teaching import recover


def test_all_fifty_certified_profiles_match_the_frozen_calibration():
    profiles=dh_v5.manifests()
    assert [p['instance_size'] for p in profiles]==list(range(1,51))
    for manifest in profiles:
        tier=manifest['instance_size']
        profile=dh_v5._profile(manifest['id'])
        dh_v5.validate_profile(profile)
        assert profile.p.bit_length()==calibration.modulus_bits_for_tier(tier)
        assert profile.q.bit_length()==(profile.p.bit_length()+1)//2+1
        assert profile.q**2>profile.p and (profile.p-1)%profile.q==0
        assert profile.parameter_bits==profile.q.bit_length()!=tier
        assert manifest['initial_security_estimate']==calibration.profile_estimate(tier,profile.p.bit_length(),profile.q)
        assert manifest['encoding']['public_key_bytes']==(profile.p.bit_length()+7)//8
        pk,sk=dh_v5.keygen(profile.id)
        ct,key=dh_v5.encapsulate(profile.id,pk)
        assert len(key)==32 and dh_v5.decapsulate(profile.id,sk,ct)==key
    assert profiles[-1]['modulus_bits']==3072
    assert profiles[-1]['initial_security_estimate']['bits']==128


@pytest.mark.parametrize('tier',[1,10,25])
def test_public_setup_is_reproducible(tier):
    record=reproduce_setup(tier)
    profile=dh_v5._profile(f'dh-v5-tier-{tier}')
    assert tuple(int(record[k]) for k in ('p','q','g'))==(profile.p,profile.q,profile.g)


def test_independent_public_only_solver_and_freshness():
    first=dh_v5.generate('dh-v5-tier-1')
    second=dh_v5.generate('dh-v5-tier-1')
    assert first.public['version']=='5'
    assert first.public['instance_size']==1 and first.public['parameter_bits']==17
    assert first.public['parameters']==second.public['parameters']
    assert (first.public['public_key'],first.public['ciphertext'])!=(second.public['public_key'],second.public['ciphertext'])
    assert bytes.fromhex(recover(first.public))==first.target
    assert first.diagnostics['shared_secret_matches'] is True
    assert set(first.public)=={'scheme_id','version','profile_id','instance_size','parameter_bits','modulus_bits','initial_security_estimate','parameters','public_key','ciphertext','encoding','kdf','implementation_sha256'}
    assert 'target' not in first.public and 'private' not in first.public
    assert first.target.hex() not in repr(first)
    assert dh_v5.IMPLEMENTATION_SHA256==hashlib.sha256(Path(dh_v5.__file__).read_bytes()).hexdigest()


def test_invalid_prime_and_generator_proofs_fail():
    profile=dh_v5._profile('dh-v5-tier-1')
    for altered in (
        replace(profile,g=1),replace(profile,q=profile.q+2),
        replace(profile,certificate=replace(profile.certificate,multiplier=profile.certificate.multiplier+1)),
        replace(profile,parameter_bits=profile.parameter_bits+1),replace(profile,instance_size=50),
    ):
        with pytest.raises(ValueError): dh_v5.validate_profile(altered)


@pytest.mark.parametrize('profile_id',['dh-20','dh-v5-tier-0','dh-v5-tier-51',None,1])
def test_release_rejects_unknown_profiles(profile_id):
    with pytest.raises(ValueError):dh_v5.get_manifest(profile_id)


def test_malformed_inputs_and_private_exponents_are_rejected():
    profile=dh_v5._profile('dh-v5-tier-1')
    pk,sk=dh_v5.keygen(profile.id)
    ct,key=dh_v5.encapsulate(profile.id,pk)
    malformed=[b'',ct+b'\0',dh_v5._encode(profile,0),dh_v5._encode(profile,1),dh_v5._encode(profile,profile.p-1)]
    for bad in malformed:
        with pytest.raises(ValueError):dh_v5.encapsulate(profile.id,bad)
        with pytest.raises(ValueError):dh_v5.decapsulate(profile.id,sk,bad)
    for bad in (True,0,-1,profile.q,1.5):
        with pytest.raises(ValueError):dh_v5.decapsulate(profile.id,bad,ct)
