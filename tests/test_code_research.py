"""Real parameter variation, honest correctness and public-only first-tier attacks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

import pytest
from crucible import code_hqc,code_mceliece,code_native,code_parameters
from scripts.code_solve_hqc_tier1 import solve as solve_hqc
from scripts.code_solve_mceliece_tier1 import solve as solve_mceliece
ROOT=Path(__file__).resolve().parents[1]
NATIVE=Path(os.environ.get('CRUCIBLE_CODE_TEST_BASE_NATIVE',ROOT/'.native-pq'))
SOURCE=NATIVE/'source'/('liboqs-'+code_native.COMMIT)
CODE_ROOT=Path(os.environ.get('CRUCIBLE_CODE_TEST_ROOT',ROOT/'.native-code'))
MODULES=(code_hqc,code_mceliece)
PROFILES=[(module,identifier) for module in MODULES for identifier in module._adapter.profiles]

@pytest.mark.parametrize('module,identifier',PROFILES)
def test_every_research_profile_has_actual_parameters_and_honest_agreement(module,identifier):
    manifest=module.get_manifest(identifier);result=module.generate(identifier)
    assert result.diagnostics['shared_secret_matches'] is True
    assert result.diagnostics['honest_trials']==1
    assert result.public['parameters']==manifest['parameters']
    assert len(result.target)==32
    assert len(bytes.fromhex(result.public['public_key']))==manifest['encoding']['public_key_bytes']
    assert len(bytes.fromhex(result.public['ciphertext']))==manifest['encoding']['ciphertext_bytes']
    assert manifest['profile_kind']=='research-tier'
    assert manifest['profile_label']==f"Tier {manifest['instance_size']}"
    assert result.public['profile_label']==manifest['profile_label']
    assert manifest['record_inheritance'] is result.public['record_inheritance'] is False
    assert 'initial_security_estimate' not in manifest
    assert manifest['correctness']['failure_bound']=='0'
    assert manifest['implementation']['source_sha256']==hashlib.sha256(module.source_bytes()).hexdigest()
    assert result.target.hex() not in json.dumps(result.public)
    assert not {'private_key','seed','target'}&set(result.public)

def test_no_invented_fifty_hqc_steps_or_security_normalization():
    profiles=code_hqc.manifests();assert len(profiles)==27
    assert [p['parameters']['secret_weight'] for p in profiles]==list(range(1,28))
    assert all(p['parameters']['worst_case_error_weight']<16*96 for p in profiles)
    assert code_parameters.hqc_worst_case_errors(28)>1535
    mc=code_mceliece.manifests();assert len(mc)==50
    assert len({p['parameters']['n'] for p in mc})==50
    assert mc[0]['parameters']['n']==840 and mc[-1]['parameters']['n']==3488
    assert mc[-1]['work_reference']['log2_expected_trials']>142
    assert all(p['parameters']['m']==12 and p['parameters']['t']==64 for p in mc)

def test_frozen_prange_mapping_uses_actual_unscaled_trial_count():
    end=code_parameters.prange_log2_trials(3488)
    for tier,n in enumerate(code_parameters.MC_LENGTHS,1):
        target=8+(end-8)*(tier-1)/49
        assert code_parameters.prange_log2_trials(n)+1e-10>=target
        assert n==776 or code_parameters.prange_log2_trials(n-8)<target

def test_mceliece_sampler_matches_published_general_parameter_rule():
    assert code_parameters.mceliece_sampling_batch(840)==512
    assert code_parameters.mceliece_sampling_batch(1024)==256
    assert code_parameters.mceliece_sampling_batch(2048)==128
    assert code_parameters.mceliece_sampling_batch(3488)==128
    for tier,n in enumerate(code_parameters.MC_LENGTHS,1):
        tau=code_parameters.mceliece_sampling_batch(n)
        assert 4096*64/tau<=n<4096*128/tau
        source=(CODE_ROOT/'source'/f'mceliece-{tier}'/'encrypt.c').read_text()
        assert 'SYS_T * 2' not in source
        assert f'uint16_t nums[ {tau} ]' in source

@pytest.mark.parametrize('module',MODULES)
def test_fresh_full_native_sampling(module):
    identifier=next(iter(module._adapter.profiles));a=module.generate(identifier);b=module.generate(identifier)
    assert a.public['public_key']!=b.public['public_key']
    assert a.public['ciphertext']!=b.public['ciphertext']
    assert a.target!=b.target
    with pytest.raises(ValueError):module.generate(identifier+'-fake')

def test_hqc_tier1_public_attack_recovers_complete_secret():
    identifier=next(iter(code_hqc._adapter.profiles))
    for _ in range(3):
        generated=code_hqc.generate(identifier)
        assert solve_hqc(generated.public)==generated.target

def test_mceliece_tier1_public_prange_attack_recovers_complete_secret():
    generated=code_mceliece.generate(next(iter(code_mceliece._adapter.profiles)))
    recovered,trials=solve_mceliece(generated.public,timeout=90)
    assert recovered==generated.target and trials>=1

@pytest.mark.parametrize('module',MODULES)
def test_artifact_is_hash_bound_and_public(module):
    artifact=module.verify_artifact();claimed=artifact.pop('artifact_sha256')
    assert hashlib.sha256(code_native.canonical(artifact)).hexdigest()==claimed
    assert '/Users/' not in json.dumps(artifact)
    assert artifact['source_archive_sha256']==code_native.ARCHIVE_SHA256

@pytest.mark.parametrize('module',MODULES)
def test_honest_mismatch_is_not_resampled(module,monkeypatch):
    identifier=next(iter(module._adapter.profiles));encoding=module._adapter.encoding(identifier)
    pk,ct=encoding['public_key_bytes'],encoding['ciphertext_bytes'];calls=[]
    output=b'CPQ1'+struct.pack('>III',pk,ct,32)+b'\0'+b'p'*pk+b'c'*ct+b's'*32
    monkeypatch.setattr(code_native,'_execute',lambda *args:calls.append(args) or output)
    result=module.generate(identifier)
    assert result.diagnostics['shared_secret_matches'] is False and len(calls)==1

def test_runtime_common_source_tamper_blocks_generation(monkeypatch):
    code_hqc.artifact_manifest();original=code_native.sha
    monkeypatch.setattr(code_native,'sha',lambda p:'0'*64 if str(p).endswith('code_parameters.py') else original(p))
    with pytest.raises(code_native.Unavailable):code_hqc.generate(next(iter(code_hqc._adapter.profiles)))

def compile_test(tmp_path,family,tier,harness):
    directory=CODE_ROOT/'source'/f'{family}-{tier}'
    flags=['-DCODE_FAMILY=1','-DHQC_ARCH_REF=1','-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_','-DUSE_OQS_RANDOMBYTES'] if family=='hqc' else ['-DCODE_FAMILY=2','-Dold_gas_syntax']
    output=tmp_path/harness
    command=['cc','-std=c99','-O1',*flags,'-I',str(directory),'-I',str(NATIVE/'build/include'),'-I',str(SOURCE/'src/common/pqclean_shims'),str(ROOT/'scripts'/harness),*([str(SOURCE/'src/common/rand/rand_nist.c')] if harness=='code_conformance.c' else []),*[str(p) for p in sorted(directory.glob('*.c'))],str(NATIVE/'build/lib/liboqs.a'),'-o',str(output)]
    subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    return subprocess.run([str(output)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20).stdout

def test_mceliece_endpoint_exactly_matches_published_known_answer(tmp_path):
    official=subprocess.run([str(NATIVE/'build/tests/kat_kem'),'Classic-McEliece-348864'],check=True,capture_output=True,timeout=20).stdout.decode()
    fields=dict(line.split(' = ',1) for line in official.splitlines() if ' = ' in line)
    expected=b''.join(bytes.fromhex(fields[name]) for name in ('pk','sk','ct','ss'))
    assert compile_test(tmp_path,'mceliece',50,'code_conformance.c')==expected

def test_unchanged_hqc_decoder_meets_deterministic_boundary(tmp_path):
    assert b'256 codec fixtures passed' in compile_test(tmp_path,'hqc',27,'code_hqc_decoder_check.c')
