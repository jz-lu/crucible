"""Fixed correctness, minimal supported costs, native conformance and public attack."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import pytest
from crucible import code_hqc_v2 as hqc, hqc_growth_parameters as parameters
from scripts.hqc_growth_solve_tier1 import solve

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('CRUCIBLE_HQC_TEST_BASE_NATIVE', ROOT/'.native-pq'))
SOURCE = BASE/'source'/('liboqs-'+hqc.COMMIT)
CODECS = {tuple(row[key] for key in ('rs_length', 'rm_length')): row for row in hqc.PROFILES}


def compile_harness(tmp_path, row, harness, directory=None, sanitize=False):
    directory = directory or ROOT/'.native-hqc-growth/source'/f"hqc-{row['tier']}"
    output = tmp_path/(harness + str(row['tier']) + ('-sanitized' if sanitize else ''))
    flags = ['-DCODE_FAMILY=1', '-DHQC_ARCH_REF=1', '-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_', '-DUSE_OQS_RANDOMBYTES']
    if sanitize:
        flags += ['-fsanitize=' + os.environ.get('CRUCIBLE_NATIVE_SANITIZERS', 'undefined'), '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
    command = ['cc', '-std=c99', '-O1', *flags, '-I', str(directory), '-I', str(BASE/'build/include'),
        '-I', str(SOURCE/'src/common/pqclean_shims'), str(ROOT/'scripts'/harness),
        *([str(SOURCE/'src/common/rand/rand_nist.c')] if harness == 'code_conformance.c' else []),
        *[str(p) for p in sorted(directory.glob('*.c'))], str(BASE/'build/lib/liboqs.a'), '-o', str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=45)
    return subprocess.run([str(output)], check=True, capture_output=True, timeout=25).stdout


@pytest.mark.parametrize('row', hqc.PROFILES, ids=lambda row: str(row['tier']))
def test_every_tier_exact_sizes_and_honest_agreement(row):
    identifier = hqc.profile_for_size(row['tier'])
    manifest = hqc.get_manifest(identifier); generated = hqc.generate(identifier)
    assert generated.diagnostics['honest_trials'] == 1
    assert generated.diagnostics['shared_secret_matches'] is True
    assert manifest['parameters'] == generated.public['parameters']
    assert len(bytes.fromhex(generated.public['public_key'])) == manifest['encoding']['public_key_bytes']
    assert len(bytes.fromhex(generated.public['ciphertext'])) == manifest['encoding']['ciphertext_bytes']
    assert len(generated.target) == 32
    assert generated.target.hex() not in json.dumps(generated.public)
    assert manifest['correctness']['failure_bound'] == '0'
    assert manifest['initial_security_estimate'] is None
    assert manifest['record_inheritance'] is False


def test_same_correctness_requirement_and_minimal_supported_code_at_every_weight():
    assert len(hqc.PROFILES) == 27
    lengths, rings, pks, cts = [], [], [], []
    for row in hqc.PROFILES:
        assert all(row[key] == value for key, value in parameters.codec_for_weight(row['weight']).items())
        assert row['worst_case_error_weight'] <= row['guaranteed_decoder_error_capacity']
        assert row['rs_generator'] == parameters.rs_generator(row['rs_delta'])
        assert row['n'] > row['code_length']
        assert row['barrett_mu'] == (1<<32)//row['n']
        assert row['rejection_threshold'] == ((1<<24)//row['n'])*row['n']
        enc = hqc.encoding(hqc.profile_for_size(row['tier']))
        lengths.append(row['code_length']); rings.append(row['n'])
        pks.append(enc['public_key_bytes']); cts.append(enc['ciphertext_bytes'])
    assert all(values == sorted(values) and values[0] < values[-1] for values in (lengths, rings, pks, cts))
    assert hqc.PROFILES[-1]['n'] == 17669
    assert len(CODECS) >= 15
    assert parameters.rs_generator(15) == [89,69,153,116,176,117,111,75,73,233,242,233,65,210,21,139,103,173,67,118,105,210,174,110,74,69,228,82,255,181,1]


@pytest.mark.parametrize('row', CODECS.values(), ids=lambda row: str(row['tier']))
def test_each_distinct_native_codec_at_guaranteed_boundary(tmp_path, row):
    output = compile_harness(tmp_path, row, 'hqc_growth_decoder_check.c', sanitize=True)
    assert f"256 codec fixtures passed at {row['guaranteed_decoder_error_capacity']}-error bound".encode() in output


def test_endpoint_conforms_to_previous_exact_hqc_weight27_profile(tmp_path):
    previous = Path(os.environ.get('CRUCIBLE_HQC_TEST_LEGACY_SOURCE', ROOT/'.native-code/source/hqc-27'))
    row = hqc.PROFILES[-1]
    expected = compile_harness(tmp_path, row, 'code_conformance.c', directory=previous)
    assert compile_harness(tmp_path, row, 'code_conformance.c') == expected


def test_public_only_initial_attack_and_freshness():
    a = hqc.generate(hqc.profile_for_size(1)); b = hqc.generate(hqc.profile_for_size(1))
    assert a.public['public_key'] != b.public['public_key']
    assert a.public['ciphertext'] != b.public['ciphertext']
    assert solve(a.public) == a.target
    assert solve(b.public) == b.target


def test_artifact_closure_and_tamper(monkeypatch):
    artifact = hqc.verify_artifact(); claim = artifact.pop('artifact_sha256')
    assert hashlib.sha256(hqc.canonical(artifact)).hexdigest() == claim
    assert '/Users/' not in json.dumps(artifact)
    original = hqc.sha
    monkeypatch.setattr(hqc, 'sha', lambda path: '0'*64 if str(path).endswith('hqc_growth_profiles.json') else original(path))
    with pytest.raises(hqc.Unavailable):
        hqc.generate(hqc.profile_for_size(1))


def test_no_correctness_conditioned_retry(monkeypatch):
    identifier = hqc.profile_for_size(1); enc = hqc.encoding(identifier)
    pk, ct = enc['public_key_bytes'], enc['ciphertext_bytes']; calls = []
    output = b'CPQ1'+struct.pack('>III',pk,ct,32)+b'\0'+b'p'*pk+b'c'*ct+b's'*32
    monkeypatch.setattr(hqc, '_execute', lambda *args: calls.append(args) or output)
    result = hqc.generate(identifier)
    assert result.diagnostics['shared_secret_matches'] is False
    assert len(calls) == 1


def test_profile_ids_remain_distinct_from_legacy():
    assert hqc.profile_for_size(1) == 'hqc-growth-research-v2-tier-1'
    for value in (0, 28, True, 1.0, '1'):
        with pytest.raises(ValueError):
            hqc.profile_for_size(value)
    with pytest.raises(ValueError):
        hqc.get_manifest('hqc-weight-research-v1-tier-1')
