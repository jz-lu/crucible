"""Conditional-certificate, native-conformance and fail-closed HQC v3 checks."""
from fractions import Fraction
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import struct
import subprocess

import pytest
from crucible import code_hqc_v3 as hqc, hqc_model_profiles as profiles
from crucible.hqc_model_correctness import certificate, marginal, rm_counts_twice
from scripts.hqc_model_parameters import primitive_prime
from scripts.hqc_model_solve_tier1 import solve

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('CRUCIBLE_HQC_TEST_BASE_NATIVE', ROOT/'.native-pq'))
SOURCE = BASE/'source'/('liboqs-'+hqc.COMMIT)
NATIVE = Path(os.environ.get('CRUCIBLE_HQC_MODEL_TEST_ROOT', ROOT/'.local/native-hqc-model'))
CODECS = {tuple(row[key] for key in ('rs_length', 'rm_length')): row for row in hqc.PROFILES}


def compile_harness(tmp_path, row, harness, directory=None, sanitize=False):
    directory = directory or NATIVE/'source'/f"hqc-{row['tier']}"
    output = tmp_path/(harness + str(row['tier']) + ('-sanitized' if sanitize else ''))
    flags = ['-DCODE_FAMILY=1', '-DHQC_ARCH_REF=1', '-DPQCHQC_NAMESPACE_PREFIX=PQCHQC_HQC1_C_', '-DUSE_OQS_RANDOMBYTES']
    if sanitize:
        flags += ['-fsanitize=undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
    command = ['cc', '-std=c99', '-O1', *flags, '-I', str(directory), '-I', str(BASE/'build/include'),
        '-I', str(SOURCE/'src/common/pqclean_shims'), str(ROOT/'scripts'/harness),
        *([str(SOURCE/'src/common/rand/rand_nist.c')] if harness == 'code_conformance.c' else []),
        *[str(p) for p in sorted(directory.glob('*.c'))], str(BASE/'build/lib/liboqs.a'), '-o', str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=45)
    return subprocess.run([str(output)], check=True, capture_output=True, timeout=25).stdout


def test_exact_marginal_against_small_cyclic_support_enumeration():
    n, w, wr, we = 5, 1, 2, 2
    supports = lambda weight: [set(s) for s in combinations(range(n), weight)]
    products = [sum(((0-i) % n) in b for i in a) % 2
                for a in supports(w) for b in supports(wr)]
    failures = sum(a ^ b ^ (0 in e) for a in products for b in products for e in supports(we))
    assert marginal(n, w, wr, we) == Fraction(failures, len(products)**2 * len(supports(we)))


def test_current_analysis_distinguishes_published_versions():
    old = certificate(17669,66,75,75,46,3)
    new = certificate(17669,67,76,76,46,3)
    assert old['meets_target'] and old['certified_model_bound_exponent'] == 132
    assert not new['meets_target'] and new['certified_model_bound_exponent'] == 117
    assert old['log2_tie_correction_display'] < -148
    # The latter is failure to certify under Model1, not an actual DFR lower bound.
    assert 'conditional' in old['evidence_class']


def test_frozen_parameter_certificates_and_endpoint():
    assert len(hqc.PROFILES) == 66
    totals = []
    for row in hqc.PROFILES:
        assert row['encryption_weight'] == row['error_weight'] == (75*row['weight']+65)//66
        assert primitive_prime(row['n'])
        assert all(not primitive_prime(n) for n in range(row['code_length']+1,row['n']))
        assert certificate(row['n'],row['weight'],row['encryption_weight'],row['error_weight'],
                           row['rs_length'],row['rm_repetitions']) == row['correctness_certificate']
        assert row['correctness_certificate']['meets_target']
        totals.append(row['public_key_bytes']+row['ciphertext_bytes'])
    assert totals == sorted(totals)
    row = hqc.PROFILES[-1]
    assert (row['n'],row['weight'],row['encryption_weight'],row['rs_length'],row['rm_length']) == (17669,66,75,46,384)
    assert (row['public_key_bytes'],row['ciphertext_bytes']) == (2241,4433)


@pytest.mark.parametrize('row', hqc.PROFILES, ids=lambda row: str(row['tier']))
def test_every_tier_exact_sizes_and_unconditioned_honest_agreement(row):
    identifier = hqc.profile_for_size(row['tier'])
    manifest = hqc.get_manifest(identifier); generated = hqc.generate(identifier)
    assert generated.diagnostics['honest_trials'] == 1
    assert generated.diagnostics['shared_secret_matches'] is True
    assert manifest['parameters'] == generated.public['parameters']
    assert len(bytes.fromhex(generated.public['public_key'])) == manifest['encoding']['public_key_bytes']
    assert len(bytes.fromhex(generated.public['ciphertext'])) == manifest['encoding']['ciphertext_bytes']
    assert len(generated.target) == 32 and generated.target.hex() not in json.dumps(generated.public)
    assert manifest['correctness']['kind'] == 'statistical'
    assert manifest['correctness']['failure_bound_exponent'] == 128
    assert manifest['initial_security_estimate'] is None
    assert manifest['record_inheritance'] is False


@pytest.mark.parametrize('row', CODECS.values(), ids=lambda row: str(row['tier']))
def test_each_distinct_native_codec_at_guaranteed_boundary(tmp_path, row):
    output = compile_harness(tmp_path,row,'hqc_growth_decoder_check.c',sanitize=True)
    capacity = 32*row['rm_repetitions']*(row['rs_delta']+1)-1
    assert f'256 codec fixtures passed at {capacity}-error bound'.encode() in output


def test_endpoint_byte_conformance_to_unmodified_published_2025(tmp_path):
    expected = compile_harness(tmp_path,hqc.PROFILES[-1],'code_conformance.c',
                               directory=SOURCE/'src/kem/hqc/pqc-hqc_hqc-1_ref')
    assert compile_harness(tmp_path,hqc.PROFILES[-1],'code_conformance.c') == expected


def test_public_only_tier_one_recovery_and_freshness():
    a = hqc.generate(hqc.profile_for_size(1)); b = hqc.generate(hqc.profile_for_size(1))
    assert a.public['public_key'] != b.public['public_key']
    assert a.public['ciphertext'] != b.public['ciphertext']
    assert solve(a.public) == a.target and solve(b.public) == b.target


def test_artifact_closure_and_table_tamper_fail_closed(monkeypatch):
    artifact = hqc.verify_artifact(); claim = artifact.pop('artifact_sha256')
    assert hashlib.sha256(hqc.canonical(artifact)).hexdigest() == claim
    assert '/Users/' not in json.dumps(artifact)
    original = hqc.sha
    monkeypatch.setattr(hqc,'sha',lambda path:'0'*64 if str(path).endswith('hqc_model_profiles.json') else original(path))
    with pytest.raises(hqc.Unavailable):
        hqc.generate(hqc.profile_for_size(1))


def test_honest_mismatch_is_reported_once_without_resampling(monkeypatch):
    identifier = hqc.profile_for_size(1); enc = hqc.encoding(identifier)
    pk, ct = enc['public_key_bytes'], enc['ciphertext_bytes']; calls = []
    output = b'CPQ1'+struct.pack('>III',pk,ct,32)+b'\0'+b'p'*pk+b'c'*ct+b's'*32
    monkeypatch.setattr(hqc,'_execute',lambda *args:calls.append(args) or output)
    result = hqc.generate(identifier)
    assert result.diagnostics['shared_secret_matches'] is False and len(calls) == 1


def test_native_fault_is_not_a_mathematical_disagreement(monkeypatch):
    def fail(*args):
        raise hqc.Unavailable('Native generation did not complete')
    monkeypatch.setattr(hqc,'_execute',fail)
    with pytest.raises(hqc.Unavailable):
        hqc.generate(hqc.profile_for_size(1))


def test_historical_release_identity_is_separate():
    assert hqc.profile_for_size(1) == 'hqc-modeled-research-v3-tier-1'
    with pytest.raises(ValueError):
        hqc.get_manifest('hqc-growth-research-v2-tier-1')
    for invalid in (0,67,True,1.,'1'):
        with pytest.raises(ValueError):
            hqc.profile_for_size(invalid)


@pytest.mark.parametrize('decap_status', [0, -1])
def test_actual_worker_separates_api_fault_from_honest_mismatch(tmp_path, decap_status):
    # Compile the real isolation/serialization worker against deterministic mock
    # native entrypoints: no mathematical-failure assertion relies on a mocked
    # Python exception or on the real primitive ever suffering an API fault.
    (tmp_path/'api.h').write_text('''
#define CRYPTO_PUBLICKEYBYTES 1
#define CRYPTO_SECRETKEYBYTES 1
#define CRYPTO_CIPHERTEXTBYTES 1
static int crypto_kem_keypair(uint8_t *pk,uint8_t *sk){pk[0]=1;sk[0]=1;return 0;}
static int crypto_kem_enc(uint8_t *ct,uint8_t *ss,const uint8_t *pk){(void)pk;ct[0]=2;memset(ss,3,32);return 0;}
static int crypto_kem_dec(uint8_t *ss,const uint8_t *ct,const uint8_t *sk){(void)ct;(void)sk;memset(ss,4,32);return DECAP_STATUS;}
'''.replace('DECAP_STATUS',str(decap_status)))
    output = tmp_path/'worker'
    subprocess.run(['cc','-std=c99','-O1','-DCODE_FAMILY=1','-I',str(tmp_path),
                    '-I',str(BASE/'build/include'),str(ROOT/'scripts/hqc_model_worker.c'),
                    str(BASE/'build/lib/liboqs.a'),'-o',str(output)],check=True,capture_output=True)
    result = subprocess.run([str(output),'generate'],capture_output=True,timeout=22)
    if decap_status:
        assert result.returncode == 71 and result.stdout == b''
    else:
        assert result.returncode == 0 and result.stdout[:4] == b'CPQ1'
        assert result.stdout[16] == 0 and result.stdout[-32:] == b'\3'*32
    assert subprocess.run([str(output),'unknown'],capture_output=True).returncode == 64
