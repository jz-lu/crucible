"""The Frodo ladder retains a real anchor without treating a graph cap as security."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from crucible import lattice_frodo_v2 as old
from crucible import lattice_frodo_v3 as kem

ROOT = Path(__file__).parents[1]
OLD_SOURCE_SHA256 = "2ff06a05019bb80709d6214bf95e8eb5bf9b522808486fab5318bf592c899051"


def load(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_frozen_raw_attack_references_are_not_message_entropy_or_a_kem_security_claim():
    rows = json.loads((ROOT / 'docs/lattice-frodo-calibration.json').read_text())['candidate_estimates']
    assert kem.DIMENSIONS == tuple(range(8, 641, 8))
    assert len(kem.manifests()) == 80
    assert kem.ESTIMATED_BITS[:16] == (None,) * 16
    assert kem.ESTIMATED_BITS[73] == 129.064
    assert kem.ESTIMATED_BITS[-1] == 141.912
    assert 'not full-KEM security bits' in kem.INITIAL_SECURITY_MODEL['scope']
    assert kem.INITIAL_SECURITY_MODEL['id'] == 'frodo-lwe-anchor-core-svp-v3'
    for row, manifest in zip(rows, kem.manifests()):
        assert manifest['parameters']['n'] == row['n']
        assert manifest['parameters']['message_entropy_bits'] == 128
        if row['n'] <= 128:
            assert 'initial_security_estimate' not in manifest
        else:
            estimate = manifest['initial_security_estimate']
            assert estimate['bits'] == round(min(a['log2_rop'] for a in row['attacks'].values()), 6)
            assert estimate['reference_kind'] == 'lattice-attack-cost'
            assert estimate['message_entropy_bits'] == 128


@pytest.mark.parametrize('tier', range(1, 81))
def test_all_natural_dimensions_complete_bounded_worker_roundtrip(tier):
    profile = f'frodo-lwe-v3-tier-{tier}'
    manifest = kem.get_manifest(profile)
    result = kem.generate(profile)
    assert result.public['version'] == 'anchor-research-v3'
    assert result.public['profile_id'] == profile
    assert result.public['parameters']['n'] == tier * 8
    assert result.diagnostics['shared_secret_matches'] is True
    assert result.diagnostics['honest_trials'] == 1
    assert len(bytes.fromhex(result.public['public_key'])) == manifest['encoding']['public_key_bytes']
    assert len(bytes.fromhex(result.public['ciphertext'])) == manifest['encoding']['ciphertext_bytes']
    assert len(result.target) == 16
    assert result.target.hex() not in json.dumps(result.public)
    assert result.target.hex() not in repr(result)
    assert manifest['record_inheritance'] is False


def test_published_640_anchor_matches_independent_pinned_native_fixture():
    vector = json.loads((ROOT / 'tests/fixtures/frodo-research-anchor.json').read_text())
    pk, sk = kem.keygen_dimension(640, bytes.fromhex(vector['keygen_randomness']))
    ct, secret = kem.encapsulate_dimension(640, pk, bytes.fromhex(vector['encaps_randomness']))
    assert (pk.hex(), sk.value.hex(), ct.hex(), secret.hex()) == (
        vector['pk'], vector['sk'], vector['ct'], vector['ss'])
    assert kem.decapsulate_dimension(640, sk, ct) == secret
    manifest = kem.get_manifest('frodo-lwe-v3-tier-80')
    assert manifest['encoding']['public_key_bytes'] == 9616
    assert manifest['encoding']['ciphertext_bytes'] == 9752


@pytest.mark.parametrize('n', [8, 136, 320, 592])
def test_overlap_preserves_actual_byte_distribution_and_algorithms(n):
    key_coins = bytes(range(64))
    encaps_coins = bytes(range(48))
    old_pk, old_sk = old.keygen_dimension(n, key_coins)
    pk, sk = kem.keygen_dimension(n, key_coins)
    old_ct, old_secret = old.encapsulate_dimension(n, old_pk, encaps_coins)
    ct, secret = kem.encapsulate_dimension(n, pk, encaps_coins)
    assert (pk, sk.value, ct, secret) == (old_pk, old_sk.value, old_ct, old_secret)
    assert kem.decapsulate_dimension(n, sk, ct) == secret


def test_archived_source_and_profiles_remain_immutable():
    assert hashlib.sha256((ROOT / 'crucible/lattice_frodo_v2.py').read_bytes()).hexdigest() == OLD_SOURCE_SHA256
    assert len(old.manifests()) == 74 and old.DIMENSIONS[-1] == 592
    assert old.ESTIMATED_BITS[-1] == 128
    assert old.SCHEME_VERSION == 'frontier-research-v2'


def test_fixed_correctness_acceptance_envelope_includes_restored_endpoint():
    certificate = load('scripts/lattice_calibrate_frodo.py', 'frodo_anchor_certificate')
    assert certificate.correctness_certificate(8)['bound'] == '0'
    assert certificate.correctness_certificate(592)['bound_exponent'] >= 133
    assert certificate.correctness_certificate(640)['bound_exponent'] == 133
    for manifest in kem.manifests():
        if manifest['parameters']['n'] == 8:
            assert manifest['correctness']['failure_bound'] == '0'
        else:
            assert 'At most 2^-133' in manifest['correctness']['failure_bound']
            assert 'Actual per-profile failure bounds may differ' in manifest['correctness']['argument']


def test_full_salted_ciphertext_implicit_rejection_and_canonical_inputs():
    pk, sk = kem.keygen_dimension(8, bytes(range(64)))
    ct, target = kem.encapsulate_dimension(8, pk, bytes(range(48)))
    for position in (0, 119, 120, 239, 240, 271):
        altered = ct[:position] + bytes([ct[position] ^ 1]) + ct[position + 1:]
        assert kem.decapsulate_dimension(8, sk, altered) == hashlib.shake_128(altered + sk.value[:16]).digest(16)
        assert kem.decapsulate_dimension(8, sk, altered) != target
    for value in (b'', pk[:-1], pk + b'\0'):
        with pytest.raises(ValueError):
            kem.encapsulate_dimension(8, value)
    for value in (b'', ct[:-1], ct + b'\0'):
        with pytest.raises(ValueError):
            kem.decapsulate_dimension(8, sk, value)
    for value in ('ab' * 15, 'ab ' * 16, 'xx' * 16, None):
        with pytest.raises(ValueError):
            kem.parse_shared_secret(value, 'frodo-lwe-v3-tier-1')


@pytest.mark.parametrize('profile', [None, True, 1, 'frodo-lwe-v3-tier-0', 'frodo-lwe-v3-tier-81', 'frodo-lwe-v2-tier-1'])
def test_invalid_profiles_never_reach_worker(profile, monkeypatch):
    monkeypatch.setattr(kem, '_worker', lambda *a: pytest.fail('Invalid profile reached worker'))
    with pytest.raises(ValueError):
        kem.generate(profile)


def test_source_identity_is_checked_before_generation(monkeypatch):
    monkeypatch.setattr(kem, 'IMPLEMENTATION_SHA256', '0' * 64)
    with pytest.raises(kem.GenerationUnavailable):
        kem.generate('frodo-lwe-v3-tier-1')


def test_worker_attests_source_executed_after_spawn_race(tmp_path, monkeypatch):
    copy = tmp_path / 'candidate.py'
    copy.write_bytes(kem.source_bytes())
    spec = importlib.util.spec_from_file_location('frodo_anchor_race_candidate', copy)
    candidate = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, candidate)
    spec.loader.exec_module(candidate)
    real_popen = candidate.subprocess.Popen

    def mutate_after_preflight(*args, **kwargs):
        copy.write_text(copy.read_text() + '\n# Changed after preflight, before child execution.\n')
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(candidate.subprocess, 'Popen', mutate_after_preflight)
    with pytest.raises(candidate.GenerationUnavailable, match='different registered source'):
        candidate.generate('frodo-lwe-v3-tier-1')


def test_hard_generation_deadline_is_not_mathematical_failure(monkeypatch):
    monkeypatch.setattr(kem, 'GENERATION_TIMEOUT_SECONDS', .00001)
    start = time.monotonic()
    with pytest.raises(kem.GenerationLimit):
        kem.generate('frodo-lwe-v3-tier-80')
    assert time.monotonic() - start < 2


def test_no_conditioned_resampling_on_mathematical_disagreement(monkeypatch):
    pk, ct, target, other, calls = bytes(136), bytes(272), b'a' * 16, b'b' * 16, []
    record = b'FLA3\0' + bytes.fromhex(kem.IMPLEMENTATION_SHA256) + pk + ct + target + other
    monkeypatch.setattr(kem, '_worker', lambda n: (calls.append(n) or record))
    result = kem.generate('frodo-lwe-v3-tier-1')
    assert calls == [8]
    assert result.target == target and result.diagnostics['shared_secret_matches'] is False


def workspace_package(tmp_path):
    reference = tmp_path / 'reference'
    reference.mkdir()
    (reference / 'protocol.py').write_bytes(kem.source_bytes())
    (reference / 'implementation-artifacts.json').write_text(json.dumps(kem.artifact_manifest()))
    result = kem.generate('frodo-lwe-v3-tier-1')
    package = {
        'challenge': {'scheme_id': kem.SCHEME_ID, 'scheme_version': kem.SCHEME_VERSION, 'public': result.public},
        'scheme_manifest': kem.get_manifest('frodo-lwe-v3-tier-1'),
    }
    return package, result.target


def test_fresh_workspace_solver_verifies_release_source_and_artifact_pins(tmp_path):
    solver = load('scripts/lattice_solve_frodo_v3_tier1.py', 'frodo_anchor_solver')
    package, _ = workspace_package(tmp_path)
    module = solver._workspace_kem(package, tmp_path)
    assert module.SCHEME_VERSION == kem.SCHEME_VERSION
    assert module.IMPLEMENTATION_SHA256 == kem.IMPLEMENTATION_SHA256
    (tmp_path / 'reference/protocol.py').write_text('# tampered\n')
    with pytest.raises(ValueError, match='differs from the challenge pin'):
        solver._workspace_kem(package, tmp_path)


def test_fresh_workspace_public_only_sage_solver(tmp_path):
    sage = Path('/private/var/tmp/sage-10.1-current/venv/bin/sage')
    if not sage.is_file():
        pytest.skip('Sage is required for the independent public-only LLL/Babai example')
    package, target = workspace_package(tmp_path)
    challenge = tmp_path / 'challenge.json'
    challenge.write_text(json.dumps(package))
    attack = tmp_path / 'attack'
    attack.mkdir()
    shutil.copyfile(ROOT / 'scripts/lattice_solve_frodo_v3_tier1.py', attack / 'solve.py')
    # Deliberately outside the source checkout: only challenge/public source/artifact are present.
    result = subprocess.run([str(sage), '-python', str(attack / 'solve.py'), str(challenge)],
                            cwd=tmp_path, env={**os.environ, 'DOT_SAGE': str(tmp_path / 'sage')},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == target.hex()
