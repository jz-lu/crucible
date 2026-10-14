"""Certified fresh RSA factors and a separate, frozen calibrated tier ladder."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import inspect
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible import rsa, rsa_v2, rsa_v3


def independent_reference_bits(bits):
    def shape(n):
        logarithm = n * math.log(2)
        return logarithm ** (1 / 3) * math.log(logarithm) ** (2 / 3)
    return min(bits / 4, 128 * shape(bits) / shape(3072))


def independently_check_certificate(certificate):
    """Verify the prime proof by its mathematical conditions, not the adapter."""
    value = certificate.value
    if certificate.factor is None:
        assert 2 <= value < 65536
        assert all(value % divisor for divisor in range(2, math.isqrt(value) + 1))
        return value
    factor = independently_check_certificate(certificate.factor)
    assert (value - 1) % factor == 0
    assert factor * factor > value
    assert pow(2, value - 1, value) == 1
    assert math.gcd(pow(2, (value - 1) // factor, value) - 1, value) == 1
    # Pocklington: every prime divisor is 1 mod factor > sqrt(value),
    # so two prime divisors cannot fit into value.
    return value


def known_private():
    return rsa_v3.PrivateKey(65521, 65519, 1475213633,
                            rsa_v3.PrimeCertificate(65521), rsa_v3.PrimeCertificate(65519))


def test_every_calibrated_tier_matches_the_independent_reference_model_without_generation(monkeypatch):
    monkeypatch.setattr(rsa_v3, '_prove_prime', lambda *args: pytest.fail('Manifests must not sample primes'))
    manifests = rsa_v3.manifests()
    assert len(manifests) == 50
    assert [item['instance_size'] for item in manifests] == list(range(1, 51))
    for archived in (rsa, rsa_v2):
        assert set(item['id'] for item in manifests).isdisjoint(item['id'] for item in archived.manifests())
    for tier, manifest in enumerate(manifests, 1):
        target = 8 + 120 * (tier - 1) / 49
        bits = next(n for n in range(32, 3073) if independent_reference_bits(n) >= target)
        assert independent_reference_bits(bits) >= target
        if bits > 32:
            assert independent_reference_bits(bits - 1) < target
        assert manifest['id'] == manifest['profile_id'] == f'rsa-v3-tier-{tier}'
        assert manifest['version'] == '3'
        assert manifest['parameter_bits'] == manifest['modulus_bits'] == bits
        assert manifest['initial_security_estimate'] == {
            'bits': round(independent_reference_bits(bits), 6),
            'target_bits': round(target, 6), 'model_id': 'classical-baseline-v2'}
        assert manifest['encoding']['integer_bytes'] == (bits + 7) // 8
        assert manifest['encoding']['public_key_bytes'] == 2 * ((bits + 7) // 8)
        assert manifest['encoding']['ciphertext_bytes'] == (bits + 7) // 8
        assert manifest['shared_secret_bytes'] == 32
    assert manifests[0]['parameter_bits'] == 32
    assert manifests[-1]['parameter_bits'] == 3072
    assert manifests[-1]['initial_security_estimate'] == {
        'bits': 128.0, 'target_bits': 128.0, 'model_id': 'classical-baseline-v2'}
    assert 'omits ECM' in manifests[-1]['security_claim']
    assert 'not best-known security estimates' in manifests[-1]['security_claim']
    assert 'not uniform over all primes' in manifests[-1]['distribution']
    assert 'conditioned on successful bounded key generation' in manifests[-1]['correctness']['scope']
    assert manifests[-1]['correctness']['failure_bound'] == '0'


@pytest.mark.parametrize('bits', [2, 3, 10, 16, 17, 18, 31, 32, 64, 129, 256, 768, 1536])
def test_random_prime_certificates_prove_exact_bit_lengths(bits):
    proof = rsa_v3._prove_prime(bits)
    assert proof.value.bit_length() == bits
    assert independently_check_certificate(proof) == proof.value
    assert rsa_v3._validate_prime_certificate(proof) == proof.value
    assert repr(proof) == 'PrimeCertificate(<private>)'


@pytest.mark.parametrize('bits', [True, 1, 0, -1, 1537, 32.0, '32'])
def test_prime_generator_refuses_unbounded_or_coerced_parameters(bits):
    with pytest.raises(ValueError, match='bit length'):
        rsa_v3._prove_prime(bits)


@pytest.mark.parametrize('malformation', ['non-certificate', 'bool', 'composite-leaf', 'leaf-child',
                                       'leaf-multiplier', 'bool-multiplier', 'changed-value',
                                       'changed-factor', 'changed-multiplier', 'oversized-value', 'cycle'])
def test_certificate_validation_rejects_forged_or_malformed_proofs(malformation):
    leaf = rsa_v3.PrimeCertificate(65521)
    proof = rsa_v3._prove_prime(96)
    if malformation == 'non-certificate': proof = {'value': leaf.value}
    elif malformation == 'bool': proof = rsa_v3.PrimeCertificate(True)
    elif malformation == 'composite-leaf': proof = rsa_v3.PrimeCertificate(32151)
    elif malformation == 'leaf-child': proof = rsa_v3.PrimeCertificate(leaf.value, leaf)
    elif malformation == 'leaf-multiplier': proof = rsa_v3.PrimeCertificate(leaf.value, None, 1)
    elif malformation == 'bool-multiplier': proof = rsa_v3.PrimeCertificate(leaf.value, None, False)
    elif malformation == 'changed-value': proof = replace(proof, value=proof.value + 2)
    elif malformation == 'changed-factor': proof = replace(proof, factor=replace(proof.factor, value=proof.factor.value + 2))
    elif malformation == 'changed-multiplier': proof = replace(proof, multiplier=proof.multiplier + 1)
    elif malformation == 'oversized-value': proof = replace(proof, value=1 << 1537)
    else: object.__setattr__(proof, 'factor', proof)
    with pytest.raises(ValueError):
        rsa_v3._validate_prime_certificate(proof)


@pytest.mark.parametrize('tier', [1, 2, 25, 50])
def test_boundary_keys_have_fresh_certified_balanced_factors_and_exact_encodings(tier):
    identifier = f'rsa-v3-tier-{tier}'
    manifest = rsa_v3.get_manifest(identifier)
    public_key, private = rsa_v3.keygen(identifier)
    bits, width = manifest['parameter_bits'], manifest['encoding']['integer_bytes']
    assert private.p != private.q
    assert private.p.bit_length() == (bits + 1) // 2
    assert private.q.bit_length() == bits // 2
    assert independently_check_certificate(private.p_certificate) == private.p
    assert independently_check_certificate(private.q_certificate) == private.q
    assert (private.p * private.q).bit_length() == bits
    assert rsa_v3.EXPONENT * private.d % ((private.p - 1) * (private.q - 1)) == 1
    assert len(public_key) == 2 * width
    ciphertext, secret = rsa_v3.encapsulate(identifier, public_key)
    assert len(ciphertext) == width and len(secret) == 32
    assert rsa_v3.decapsulate(identifier, private, ciphertext) == secret
    public = rsa_v3.serialize(identifier, public_key, ciphertext)
    assert public['instance_size'] == tier and public['parameter_bits'] == bits
    assert public['initial_security_estimate'] == manifest['initial_security_estimate']
    assert set(public['parameters']) == {'N', 'e'}
    assert int(public['parameters']['N'], 16) == private.p * private.q
    assert int(public['parameters']['e'], 16) == 65537
    assert len(bytes.fromhex(public['public_key'])) == manifest['encoding']['public_key_bytes']
    assert repr(private) == 'PrivateKey(<redacted>)'


def test_fixed_vector_and_nonunits_use_the_complete_new_transcript_domain():
    identifier = 'rsa-v3-tier-1'
    private = known_private()
    public_key = bytes.fromhex('ffe000ff00010001')
    with patch.object(rsa_v3.secrets, 'randbelow', return_value=42):
        ciphertext, secret = rsa_v3.encapsulate(identifier, public_key)
    assert ciphertext.hex() == '2e558fdb'
    assert secret.hex() == '6fe18af600b895643bf3f859b21c0065da853188f66a8abeb29eff0a9b59e5d3'
    assert rsa_v3.decapsulate(identifier, private, ciphertext) == secret
    for value in (0, 1, private.p, private.q, private.p * private.q - 1):
        with patch.object(rsa_v3.secrets, 'randbelow', return_value=value):
            ciphertext, secret = rsa_v3.encapsulate(identifier, public_key)
        assert rsa_v3.decapsulate(identifier, private, ciphertext) == secret
    assert rsa_v3.DOMAIN not in (rsa.DOMAIN, rsa_v2.DOMAIN)


@pytest.mark.parametrize('change', ['wrong-type', 'bool-factor', 'equal-factors', 'wrong-inverse',
                                  'unbounded-inverse', 'wrong-certificate', 'wrong-length-factor'])
def test_private_key_validation_cannot_be_bypassed(change):
    private = known_private()
    if change == 'wrong-type': private = (private.p, private.q, private.d)
    elif change == 'bool-factor': private = replace(private, p=True)
    elif change == 'equal-factors': private = replace(private, q=private.p)
    elif change == 'wrong-inverse': private = replace(private, d=private.d + 1)
    elif change == 'unbounded-inverse': private = replace(private, d=private.d + (private.p - 1) * (private.q - 1))
    elif change == 'wrong-certificate': private = replace(private, p_certificate=private.q_certificate)
    else: private = replace(private, p=17, p_certificate=rsa_v3.PrimeCertificate(17))
    with pytest.raises(ValueError):
        rsa_v3.decapsulate('rsa-v3-tier-1', private, b'\0' * 4)


def test_public_key_and_ciphertext_validation_is_exact_and_bounded():
    identifier = 'rsa-v3-tier-1'
    public_key = bytes.fromhex('ffe000ff00010001')
    private = known_private()
    bad_keys = [b'', public_key[:-1], public_key + b'\0', bytearray(public_key),
                b'\0' * 4 + public_key[4:], bytes.fromhex('ffe000fe00010001'), public_key[:4] + b'\0' * 4]
    for bad in bad_keys:
        with pytest.raises(ValueError):
            rsa_v3.encapsulate(identifier, bad)
        with pytest.raises(ValueError):
            rsa_v3.serialize(identifier, bad, b'\0' * 4)
    for bad in (b'', b'\0' * 3, b'\0' * 5, bytearray(4), (private.p * private.q).to_bytes(4, 'big'), b'\xff' * 4):
        with pytest.raises(ValueError):
            rsa_v3.decapsulate(identifier, private, bad)
        with pytest.raises(ValueError):
            rsa_v3.serialize(identifier, public_key, bad)
    for identifier in ('rsa-32', 'rsa-v3-tier-0', 'rsa-v3-tier-51', 'rsa-v3-tier-1.0', True, []):
        with pytest.raises(ValueError):
            rsa_v3.generate(identifier)


def test_candidate_and_pair_searches_have_hard_bounds(monkeypatch):
    budget = rsa_v3._GenerationBudget(remaining=3)
    monkeypatch.setattr(rsa_v3.secrets, 'randbelow', lambda _: 0)
    # k=4 with draw0 always produces9, so all three candidates fail exactly.
    with pytest.raises(rsa_v3.PrimeGenerationLimit):
        rsa_v3._prove_prime(4, budget)
    assert budget.remaining == 0
    calls = []
    def same_factor(bits, budget):
        calls.append(bits)
        return rsa_v3.PrimeCertificate(65521)
    monkeypatch.setattr(rsa_v3, '_sample_prime', same_factor)
    with pytest.raises(rsa_v3.PrimeGenerationLimit, match='key-pair'):
        rsa_v3.keygen('rsa-v3-tier-1')
    assert len(calls) == 2 * rsa_v3.MAX_PAIR_ATTEMPTS


def test_fresh_sampling_is_not_replaced_by_cached_private_keys(monkeypatch):
    real_sample = rsa_v3._sample_prime
    calls = []
    def capture(bits, budget):
        calls.append(bits)
        return real_sample(bits, budget)
    monkeypatch.setattr(rsa_v3, '_sample_prime', capture)
    rsa_v3.keygen('rsa-v3-tier-1')
    previous = len(calls)
    rsa_v3.keygen('rsa-v3-tier-1')
    assert previous >= 2 and len(calls) >= previous + 2


def test_generation_deadline_uses_a_monotonic_clock_and_stops_before_a_new_candidate(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(rsa_v3.time, 'monotonic', lambda: now[0])
    budget = rsa_v3._GenerationBudget()
    assert budget.deadline == 105.0
    budget.spend()
    now[0] = 105.0
    monkeypatch.setattr(rsa_v3.secrets, 'randbelow', lambda _: pytest.fail('Expired budget must not sample'))
    with pytest.raises(rsa_v3.PrimeGenerationLimit, match='time allowance'):
        rsa_v3._prove_prime(64, budget)
    assert budget.remaining == rsa_v3.MAX_CANDIDATES - 1


@pytest.mark.parametrize('operation', ['keygen', 'generate'])
def test_expiry_during_last_prime_does_not_return_a_key_or_recovery_target(monkeypatch, operation):
    now = [100.0]
    monkeypatch.setattr(rsa_v3.time, 'monotonic', lambda: now[0])
    budgets = []
    def sample(bits, budget):
        budgets.append(budget)
        budget.spend()
        if len(budgets) == 2:
            now[0] = 105.0
        return rsa_v3.PrimeCertificate(65521 if len(budgets) == 1 else 65519)
    monkeypatch.setattr(rsa_v3, '_sample_prime', sample)
    monkeypatch.setattr(rsa_v3, 'encapsulate', lambda *args: pytest.fail('Expired generation must not encapsulate'))
    with pytest.raises(rsa_v3.PrimeGenerationLimit, match='time allowance'):
        getattr(rsa_v3, operation)('rsa-v3-tier-1')
    assert len(budgets) == 2 and budgets[0] is budgets[1]
    assert budgets[0].deadline == 105.0


def test_generation_preserves_a_correctness_mismatch_without_retrying(monkeypatch):
    count = []
    def mismatch(*args):
        count.append(True)
        return b'\xff' * 32
    monkeypatch.setattr(rsa_v3, 'decapsulate', mismatch)
    generated = rsa_v3.generate('rsa-v3-tier-1')
    assert count == [True]
    assert generated.diagnostics['shared_secret_matches'] is False
    assert generated.diagnostics['honest_trials'] == 1
    assert set(generated.diagnostics) == {'correctness_checked', 'shared_secret_matches', 'honest_trials', 'implementation_sha256'}
    assert tuple(inspect.signature(rsa_v3.serialize).parameters) == ('profile_id', 'public_key', 'ciphertext')
    assert '<redacted>' in repr(generated)


def test_manifests_are_independent_and_sources_remain_separately_pinned():
    before = rsa_v3.get_manifest('rsa-v3-tier-1')
    copy = deepcopy(before)
    copy['size_parameter']['mapping'] = 'altered'
    copy['initial_security_estimate']['bits'] = 999
    assert rsa_v3.get_manifest('rsa-v3-tier-1') == before
    assert hashlib.sha256(Path(rsa_v3.__file__).read_bytes()).hexdigest() == rsa_v3.IMPLEMENTATION_SHA256
    assert before['implementation']['source'] == 'crucible/rsa_v3.py'
    assert hashlib.sha256(Path(rsa.__file__).read_bytes()).hexdigest() == 'fbe8d0162eeeeed291d7347b284cec8ba781b42af382ccae5851ce04955f1809'

    assert hashlib.sha256(Path(rsa_v2.__file__).read_bytes()).hexdigest() == 'c3baf0090dc980f47460fc53702a2a9029bf79ae30fc0b85784efe7c194c5202'
