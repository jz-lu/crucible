"""Release checks for bounded parameter efficiency, without altering archives."""
import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
from crucible import lattice_frodo_v2 as frodo
from crucible import lattice_module_v3 as module

ROOT=Path(__file__).parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_bounded_search_evidence_reproduces_exactly_and_unknown_is_not_dominated():
    evidence=load('scripts/lattice_parameter_frontier.py','frontier_evidence').build()
    assert evidence==json.loads((ROOT/'docs/lattice-parameter-frontier.json').read_text())
    assert evidence['frodo']['selected_endpoint']['n']==592
    assert frodo.DIMENSIONS==tuple(range(8,593,8))
    assert frodo.ESTIMATED_BITS[:16]==(None,)*16
    assert frodo.ESTIMATED_BITS[-1]==128
    for row,m in zip(evidence['frodo']['candidates'][:74],frodo.manifests()):
        if row['planning_reference_bits'] is None:
            assert 'initial_security_estimate' not in m
        else:
            assert m['initial_security_estimate']['bits']==round(row['planning_reference_bits'],6)
    assert all(row['selected'] for row in evidence['frodo']['candidates'][:74])
    assert not any(row['selected'] for row in evidence['frodo']['candidates'][74:])


def test_independent_sage_packed_module_vectors():
    fixture=json.loads((ROOT/'tests/fixtures/module-lwe-packed-v3.json').read_text())
    assert fixture['reference_source_sha256']==hashlib.sha256((ROOT/'scripts/lattice_module_v3_reference.py').read_bytes()).hexdigest()
    for v in fixture['vectors']:
        pk,sk=module.keygen_parameters(v['n'],v['rank'],bytes.fromhex(v['key_randomness']))
        ct,key=module.encapsulate_parameters(v['n'],v['rank'],pk,bytes.fromhex(v['encapsulation_message']))
        assert [hashlib.sha256(x).hexdigest() for x in (pk,sk.value,ct)]==[v['pk_sha256'],v['sk_sha256'],v['ct_sha256']]
        assert key.hex()==v['shared_secret']
        assert module.decapsulate_parameters(v['n'],v['rank'],sk,ct)==key


@pytest.mark.parametrize('tier',range(1,12))
def test_packed_module_all_issued_profiles_have_correct_shape_and_fixed_correctness(tier):
    profile=f'module-lwe-v3-tier-{tier}';m=module.get_manifest(profile);result=module.generate(profile)
    assert result.diagnostics['shared_secret_matches'] and result.diagnostics['honest_trials']==1
    assert result.target.hex() not in json.dumps(result.public)
    assert m['correctness']['failure_bound']=='0'
    assert len(bytes.fromhex(result.public['public_key']))==m['encoding']['public_key_bytes']
    assert len(bytes.fromhex(result.public['ciphertext']))==m['encoding']['ciphertext_bytes']
    row=json.loads((ROOT/'docs/lattice-parameter-frontier.json').read_text())['module']['profiles'][tier-1]
    assert row['public_key_bytes']<row['old_public_key_bytes']
    assert row['ciphertext_bytes']<row['old_ciphertext_bytes']


@pytest.mark.parametrize('tier',range(1,75))
def test_frodo_each_natural_profile_roundtrips_in_bounded_worker(tier):
    profile=f'frodo-lwe-v2-tier-{tier}';m=frodo.get_manifest(profile);result=frodo.generate(profile)
    assert result.diagnostics['shared_secret_matches'] and result.diagnostics['honest_trials']==1
    assert result.target.hex() not in json.dumps(result.public)
    assert len(bytes.fromhex(result.public['public_key']))==m['encoding']['public_key_bytes']
    assert len(bytes.fromhex(result.public['ciphertext']))==m['encoding']['ciphertext_bytes']
    assert 'initial_security_estimate' in m if tier>16 else 'initial_security_estimate' not in m


def test_pinned_native_frodo_conformance_is_preserved_despite_new_profile_mapping():
    v=json.loads((ROOT/'tests/fixtures/frodo-research-anchor.json').read_text())
    pk,sk=frodo.keygen_dimension(640,bytes.fromhex(v['keygen_randomness']))
    ct,key=frodo.encapsulate_dimension(640,pk,bytes.fromhex(v['encaps_randomness']))
    assert (pk.hex(),sk.value.hex(),ct.hex(),key.hex())==(v['pk'],v['sk'],v['ct'],v['ss'])
    assert frodo.decapsulate_dimension(640,sk,ct)==key


def test_frodo_common_correctness_envelope_covers_smaller_endpoint():
    certificate=load('scripts/lattice_calibrate_frodo.py','frodo_certificate_frontier')
    assert certificate.correctness_certificate(592)['bound_exponent']>=133
    assert certificate.correctness_certificate(640)['bound_exponent']==133


def test_new_module_lossless_encodings_reject_noncanonical_coefficients():
    for p in module.PROFILES:
        polys=[[0,1,p.q-1]+[0]*(p.n-3)]
        packed=module.pack(polys,p)
        integer=sum(value << (i*p.q.bit_length()) for i,value in enumerate(polys[0]))
        assert packed==integer.to_bytes(p.n*p.q.bit_length()//8,'little')
        assert module.unpack(packed,1,p)==polys
        invalid=p.q.to_bytes(len(packed),'little')
        with pytest.raises(ValueError):module.unpack(invalid,1,p)
        with pytest.raises(ValueError):module.unpack(packed+b'\0',1,p)
        with pytest.raises(ValueError):module.pack([[p.q]*p.n],p)
    p=module.PROFILES[0];pk,sk=module.keygen_parameters(p.n,p.rank)
    ct,key=module.encapsulate_parameters(p.n,p.rank,pk)
    changed=p.q.to_bytes(len(ct),'little')
    assert module.decapsulate_parameters(p.n,p.rank,sk,changed)==hashlib.shake_256(sk.value[:32]+changed).digest(32)
    with pytest.raises(ValueError):module.encapsulate_parameters(p.n,p.rank,pk,bytes(2))


def test_full_native_message_space_and_public_only_solver():
    solver=load('scripts/lattice_solve_module_v3_tier1.py','module_v3_solver')
    p=module.PROFILES[0];pk,sk=module.keygen_parameters(p.n,p.rank,bytes(range(64)))
    seen=set()
    for value in range(256):
        ct,key=module.encapsulate_parameters(p.n,p.rank,pk,bytes([value]))
        assert module.decapsulate_parameters(p.n,p.rank,sk,ct)==key
        seen.add(ct)
    assert len(seen)==256
    issued=module.generate('module-lwe-v3-tier-1')
    assert solver.solve(issued.public,module)==issued.target


@pytest.mark.parametrize('kem,profile',[(module,'module-lwe-v3-tier-1'),(frodo,'frodo-lwe-v2-tier-1')])
def test_source_identity_is_checked_before_generation(kem,profile,monkeypatch):
    monkeypatch.setattr(kem,'IMPLEMENTATION_SHA256','0'*64)
    with pytest.raises(kem.GenerationUnavailable):kem.generate(profile)


@pytest.mark.parametrize('kem,profile',[(module,'module-lwe-v3-tier-1'),(frodo,'frodo-lwe-v2-tier-1')])
def test_worker_attests_source_actually_executed_after_spawn_race(kem,profile,tmp_path,monkeypatch):
    import sys
    copy=tmp_path/'candidate.py';copy.write_bytes(kem.source_bytes())
    spec=importlib.util.spec_from_file_location('frontier_race_candidate',copy)
    candidate=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,spec.name,candidate);spec.loader.exec_module(candidate)
    real_popen=candidate.subprocess.Popen
    def mutate_after_preflight(*args,**kwargs):
        copy.write_text(copy.read_text()+'\n# Changed after preflight, before child execution.\n')
        return real_popen(*args,**kwargs)
    monkeypatch.setattr(candidate.subprocess,'Popen',mutate_after_preflight)
    with pytest.raises(candidate.GenerationUnavailable,match='different registered source'):
        candidate.generate(profile)


@pytest.mark.parametrize('kem,profile',[(module,'module-lwe-v3-tier-1'),(frodo,'frodo-lwe-v2-tier-1')])
def test_hard_generation_deadline_is_not_a_math_disagreement(kem,profile,monkeypatch):
    import time
    monkeypatch.setattr(kem,'GENERATION_TIMEOUT_SECONDS',0.00001)
    started=time.monotonic()
    with pytest.raises(kem.GenerationLimit):kem.generate(profile)
    assert time.monotonic()-started<2
