"""Real native round trips plus boundary tests; build_pq_backend.py is prerequisite."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys
import time

import pytest
from crucible import pq_native,pq_mlkem,pq_frodokem,pq_hqc,pq_mceliece
MODULES=(pq_mlkem,pq_frodokem,pq_hqc,pq_mceliece)
PROFILES=[(m,identifier) for m in MODULES for identifier in m._adapter.profiles]

@pytest.mark.parametrize('module,profile_id',PROFILES)
def test_published_profile_honest_generation(module,profile_id):
    manifest=module.get_manifest(profile_id)
    result=module.generate(profile_id)
    assert result.diagnostics['shared_secret_matches'] is True
    assert result.diagnostics['honest_trials']==1
    assert len(result.target)==manifest['shared_secret_bytes']
    assert len(bytes.fromhex(result.public['public_key']))==manifest['encoding']['public_key_bytes']
    assert len(bytes.fromhex(result.public['ciphertext']))==manifest['encoding']['ciphertext_bytes']
    assert module.parse_shared_secret(result.target.hex(),profile_id)==result.target
    assert result.target.hex() not in repr(result)
    assert set(result.public)=={'scheme_id','version','profile_id','profile_label','profile_kind','instance_size','parameter_bits','parameters','public_key','ciphertext','encoding','kdf','implementation_sha256','artifact_sha256'}
    assert 'initial_security_estimate' not in manifest
    assert manifest['profile_kind']=='standard-parameter-set'
    assert manifest['parameter_bits']==0
    assert manifest['profile_order']==manifest['instance_size']
    assert manifest['admission']['official_ranked'] is False
    assert manifest['implementation']['source_sha256']==hashlib.sha256(module.source_bytes()).hexdigest()

@pytest.mark.parametrize('module',MODULES)
def test_fresh_instances_and_exact_registered_ids(module):
    profile=next(iter(module._adapter.profiles))
    first=module.generate(profile);second=module.generate(profile)
    assert first.public['public_key']!=second.public['public_key']
    assert first.public['ciphertext']!=second.public['ciphertext']
    assert first.target!=second.target
    with pytest.raises(ValueError): module.generate(profile+'-reduced')
    with pytest.raises(ValueError): module.get_manifest(True)

@pytest.mark.parametrize('value',['','a'*31,'a'*33,'00 '*16,'zz'*16,123,None])
def test_secret_parser_rejects_wrong_length_or_noncanonical(value):
    profile=next(iter(pq_frodokem._adapter.profiles))
    with pytest.raises(ValueError): pq_frodokem.parse_shared_secret(value,profile)

def test_secret_length_is_profile_specific():
    profiles=list(pq_frodokem._adapter.profiles)
    for identifier,length in zip(profiles,(16,24,32)):
        assert pq_frodokem.parse_shared_secret('AB'*length,identifier)==b'\xab'*length
        with pytest.raises(ValueError): pq_frodokem.parse_shared_secret('ab'*(length+1),identifier)

def test_artifact_is_public_closed_and_hash_bound():
    artifact=pq_mlkem.artifact_manifest();claimed=artifact.pop('artifact_sha256')
    assert claimed==hashlib.sha256(pq_native._canonical(artifact)).hexdigest()
    assert artifact['native_build']['source_commit']==pq_native.COMMIT
    assert artifact['native_build']['source_archive_sha256']==pq_native.ARCHIVE_SHA256
    assert '/Users/' not in json.dumps(artifact)
    assert '/private/' not in json.dumps(artifact)
    assert artifact['native_build']['cmake_options'][1]=='-DOQS_USE_OPENSSL=OFF'
    assert len(artifact['native_build']['kat_sha256'])==14
    assert sum(artifact['native_build']['nist_acvp']['cases_passed'].values())==180
    # Copies cannot mutate the frozen artifact.
    original=pq_mlkem.verify_artifact()
    artifact['native_build']['binary_sha256']='0'*64
    assert pq_mlkem.artifact_manifest()==original

def test_mismatch_returned_once_without_resampling(monkeypatch):
    module=pq_mlkem;identifier=next(iter(module._adapter.profiles));p=module._adapter.profiles[identifier]
    calls=[]
    payload=b'CPQ1'+struct.pack('>III',p['pk'],p['ct'],p['ss'])+b'\0'+b'p'*p['pk']+b'c'*p['ct']+b's'*p['ss']
    monkeypatch.setattr(pq_native,'_execute',lambda *args:(calls.append(args) or payload))
    result=module.generate(identifier)
    assert result.diagnostics['shared_secret_matches'] is False
    assert result.diagnostics['honest_trials']==1 and len(calls)==1
    assert result.target==b's'*p['ss']

@pytest.mark.parametrize('mutation',['magic','length','status','short','extra'])
def test_invalid_worker_output_rejected(monkeypatch,mutation):
    module=pq_mlkem;identifier=next(iter(module._adapter.profiles));p=module._adapter.profiles[identifier]
    payload=bytearray(b'CPQ1'+struct.pack('>III',p['pk'],p['ct'],p['ss'])+b'\1'+b'p'*p['pk']+b'c'*p['ct']+b's'*p['ss'])
    if mutation=='magic':payload[0]=0
    elif mutation=='length':payload[4]=1
    elif mutation=='status':payload[16]=2
    elif mutation=='short':payload=payload[:-1]
    else:payload+=b'x'
    monkeypatch.setattr(pq_native,'_execute',lambda *args:bytes(payload))
    with pytest.raises(pq_native.Unavailable):module.generate(identifier)

def test_runtime_binary_and_manifest_tampering_blocked(tmp_path,monkeypatch):
    directory=tmp_path/'backend';directory.mkdir()
    original=pq_mlkem._adapter._directory()
    for name in ('build-manifest.json','crucible-pq-worker'):shutil.copyfile(original/name,directory/name)
    monkeypatch.setenv('CRUCIBLE_PQ_BACKEND',str(directory))
    clone=pq_native.Adapter(pq_mlkem.__file__,pq_mlkem.SCHEME_ID,pq_mlkem.SCHEME_VERSION,pq_mlkem._DEFINITION)
    clone.artifact_manifest()
    (directory/'crucible-pq-worker').write_bytes(b'changed')
    with pytest.raises(pq_native.Unavailable,match='changed'):clone._verify_artifact()
    shutil.copyfile(original/'crucible-pq-worker',directory/'crucible-pq-worker')
    (directory/'build-manifest.json').write_text('{}')
    with pytest.raises(pq_native.Unavailable,match='changed'):clone._verify_artifact()

def test_missing_backend_has_no_automatic_download(tmp_path,monkeypatch):
    monkeypatch.setenv('CRUCIBLE_PQ_BACKEND',str(tmp_path))
    clone=pq_native.Adapter(pq_mlkem.__file__,pq_mlkem.SCHEME_ID,pq_mlkem.SCHEME_VERSION,pq_mlkem._DEFINITION)
    with pytest.raises(pq_native.Unavailable):clone.artifact_manifest()
    assert list(tmp_path.iterdir())==[]

def test_worker_deadline_is_hard_and_child_reaped(tmp_path):
    worker=tmp_path/'slow';worker.write_text('#!/bin/sh\nsleep 20\n');worker.chmod(0o700)
    start=time.monotonic()
    with pytest.raises(pq_native.GenerationLimit):pq_native._execute(worker,'ML-KEM-512',timeout=.08)
    assert time.monotonic()-start<2

def test_worker_stdout_bound(tmp_path):
    worker=tmp_path/'large';worker.write_text('#!'+sys.executable+'\nimport os\nos.write(1,b"x"*1500000)\n');worker.chmod(0o700)
    with pytest.raises(pq_native.Unavailable,match='bound'):pq_native._execute(worker,'ML-KEM-512',timeout=2)

def test_worker_refuses_unknown_algorithm_and_no_inputs():
    worker=pq_mlkem._adapter._verify_artifact()
    with pytest.raises(pq_native.Unavailable):pq_native._execute(worker,'ML-KEM-513')

def test_historical_hqc_and_mceliece_status_are_explicit():
    hqc=pq_hqc.manifests()[0];mc=pq_mceliece.manifests()[0]
    assert '2025-08-22' in hqc['security_claim'] and 'not the newer 2026-10-12' in hqc['security_claim']
    assert 'historical' in hqc['correctness']['failure_bound']
    assert 'inactive' in mc['implementation']['limitations']
    assert mc['correctness']['kind']=='perfect'

def test_common_helper_source_tampering_prevents_execution(monkeypatch):
    pq_mlkem.artifact_manifest()
    original=pq_native._sha
    monkeypatch.setattr(pq_native,'_sha',lambda path: '0'*64 if Path(path)==Path(pq_native.__file__) else original(path))
    monkeypatch.setattr(pq_native,'_execute',lambda *args:pytest.fail('Must not execute changed code'))
    with pytest.raises(pq_native.Unavailable,match='changed'):
        pq_mlkem.verify_artifact()
    with pytest.raises(pq_native.Unavailable,match='changed'):
        pq_mlkem.generate(next(iter(pq_mlkem._adapter.profiles)))
