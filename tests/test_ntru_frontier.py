"""Bounded-model parameter selection, frozen native execution and honest decoding."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from crucible import ntru_frontier as kem
from crucible import ntru_reference as reference
from crucible.ntru_frontier_model import estimate
from scripts.ntru_frontier_parameters import choose
from scripts.ntru_frontier_solve_tier1 import solve


def test_finite_candidate_minima_and_nonhybrid_loophole_is_rejected():
    data=json.loads(kem.PARAMETERS.read_bytes())
    assert len(data['candidates'])==103 and len(data['profiles'])==27
    assert choose(data['candidates'])==data['profiles']
    for row in data['profiles']:
        assert row['q']>=16*row['w']+1 and 2*row['p']>=3*row['w']
        assert row['communication_bytes']==sum(kem._sizes(row)[i] for i in (0,2))
        target=row['target_reference_bits']
        if target is not None:
            choices=[r for r in data['candidates'] if r['reference']['bits'] is not None and r['reference']['bits']>=target]
            assert row['communication_bytes']==min(r['communication_bytes'] for r in choices)
    sparse=next(r for r in data['candidates'] if (r['p'],r['q'],r['w'])==(653,2311,144))
    assert sparse['reference']['nonhybrid_bits']>128
    assert sparse['reference']['bits']<121
    assert sparse['reference']['attack']=='mitm'
    endpoint=data['profiles'][-1]
    assert (endpoint['p'],endpoint['q'],endpoint['w'])==(653,4621,288)
    assert 129<endpoint['reference']['bits']<130
    assert (endpoint['public_key_bytes'],endpoint['ciphertext_bytes'])==(994,897)
    assert all(a['communication_bytes']<b['communication_bytes'] for a,b in zip(data['profiles'],data['profiles'][1:]))


def test_introductory_profile_screen_does_not_invent_security():
    unknown=[r for r in kem.PROFILES if r['reference']['bits'] is None]
    assert len(unknown)==13
    ceilings=[math.comb(r['p'],r['w'])*2**r['w'] for r in unknown]
    assert ceilings==sorted(set(ceilings))
    for r in unknown:
        assert kem.get_manifest(kem.profile_for_size(r['tier']))['initial_security_estimate'] is None
    assert 'not a security lower bound' in kem.INITIAL_SECURITY_MODEL['limitations'][0]


def test_native_sources_are_bound_and_old_release_remains_distinct():
    from crucible import ntru_tiers as old
    assert set(m['profile_id'] for m in old.manifests()).isdisjoint(m['profile_id'] for m in kem.manifests())
    artifact=kem.verify_artifact(kem.profile_for_size(1))
    assert artifact['scheme_version']=='3' and artifact['worker_sha256']==kem.WORKER_SHA256
    for name,pin in kem.FILE_PINS.items():
        assert hashlib.sha256((kem.ROOT/name).read_bytes()).hexdigest()==pin
    assert 'ntru_frontier' in str(kem.WORKER).replace('-','_')
    with pytest.raises(ValueError):kem.profile_for_size(True)
    with pytest.raises(ValueError):kem.profile_for_size(28)


@pytest.mark.parametrize('tier',range(1,28))
def test_every_profile_native_and_independent_python_agreement(tier):
    profile=kem.profile_for_size(tier);row=kem.PROFILES[tier-1]
    p,q,w=(row[k] for k in ('p','q','w'))
    public,private=kem.keygen(profile)
    ciphertext,key=kem.encapsulate(profile,public)
    assert kem.decapsulate(profile,private,ciphertext)==key
    assert reference.decapsulate(p,q,w,private.value,ciphertext)==key
    assert len(public)==row['public_key_bytes'] and len(ciphertext)==row['ciphertext_bytes']
    altered=ciphertext[:-1]+bytes([ciphertext[-1]^1])
    rejected=kem.decapsulate(profile,private,altered)
    assert rejected!=key and rejected==reference.decapsulate(p,q,w,private.value,altered)
    n=(p+3)//4
    f=reference.small_decode(private.value[:n],p)
    assert sum(v!=0 for v in f)==w


def test_published_653_known_answer_decapsulation():
    data=json.loads((Path(__file__).parent/'fixtures/sntrup653-kat.json').read_text())
    assert len(data['vectors'])==3
    for row in data['vectors']:
        key=kem.decapsulate(kem.profile_for_size(27),kem.PrivateKey(bytes.fromhex(row['sk'])),bytes.fromhex(row['ct']))
        assert key.hex()==row['ss'].lower()


def test_public_only_example_and_standalone_cli(tmp_path):
    generated=kem.generate(kem.profile_for_size(1))
    assert generated.diagnostics['shared_secret_matches']
    assert solve(generated.public)==generated.target
    path=tmp_path/'challenge.json';path.write_text(json.dumps({'challenge':{'public':generated.public}}))
    result=subprocess.run([sys.executable,str(kem.ROOT/'scripts/ntru_frontier_solve_tier1.py'),str(path)],
        capture_output=True,text=True,check=True,timeout=20)
    assert result.stdout.strip()==generated.target.hex()
    assert generated.target.hex() not in json.dumps(generated.public)
    assert generated.target.hex() not in repr(generated)


def test_worker_tampering_fails_closed(tmp_path,monkeypatch):
    fake=tmp_path/'wrong-worker';fake.write_bytes(b'wrong')
    monkeypatch.setattr(kem,'WORKER',fake)
    with pytest.raises(kem.NativeImplementationError,match='reviewed pin'):
        kem.generate(kem.profile_for_size(1))


def test_changed_artifact_after_native_call_is_not_released(monkeypatch):
    original=kem.verify_artifact;calls=[]
    def check(*args):
        calls.append(True)
        if len(calls)==2:
            raise kem.NativeImplementationError('changed after launch')
        return original(*args)
    monkeypatch.setattr(kem,'verify_artifact',check)
    with pytest.raises(kem.NativeImplementationError,match='after launch'):
        kem.generate(kem.profile_for_size(1))
    assert len(calls)==2


def test_correctness_mismatch_is_not_retried_or_retargeted(monkeypatch):
    calls=[];pk,_,ct=kem._sizes(kem.PROFILES[0])
    def run(*args):
        calls.append(True);return bytes(pk+ct)+b't'*32+b'd'*32
    monkeypatch.setattr(kem,'_run',run)
    generated=kem.generate(kem.profile_for_size(1))
    assert generated.target==b't'*32 and generated.diagnostics['shared_secret_matches'] is False
    assert calls==[True]


def test_official_nonhybrid_estimator_calibration():
    data=json.loads((kem.ROOT/'docs/ntru-frontier-estimator-calibration.json').read_text())
    checked=0
    for row in data['checks']:
        if not row['hybrid']:
            got=estimate(row['p'],row['q'],row['w'])['bits']
            assert got==pytest.approx(row['official_bits'],abs=1e-8);checked+=1
    assert checked==4


def test_hqc_and_mceliece_cost_audit_is_reproducible():
    from scripts.audit_code_parameter_frontiers import hqc_audit,mceliece_audit
    saved=json.loads((kem.ROOT/'docs/code-parameter-frontier-audit.json').read_text())
    assert hqc_audit()==saved['hqc']
    assert mceliece_audit()==saved['classic-mceliece']
    assert saved['hqc']['status']=='codec-cost-minimum-only'
    assert saved['classic-mceliece']['status']=='restricted-shortening-path-only'
