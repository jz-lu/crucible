"""Release-4 certificates, unchanged old transcripts, and boundary correctness."""

from dataclasses import replace
import hashlib
import math
from pathlib import Path
from unittest.mock import call, patch

import pytest

from crucible import dh_v3, dh_v4
from scripts.generate_dh_v4_primes import generate_pair
from scripts.solve_teaching import recover


def test_release_four_covers_every_tier_and_keeps_release_three_frozen():
    manifests = dh_v4.manifests()
    assert [item["instance_size"] for item in manifests] == list(range(20, 301))
    assert {item["version"] for item in manifests} == {"4"}
    assert manifests[-1]["modulus_bits"] == 301
    assert manifests[-1]["encoding"]["group_element_bytes"] == 38
    assert manifests[-1]["correctness"]["failure_bound"] == "0"
    assert "20 through 300" in manifests[-1]["correctness"]["scope"]
    assert manifests[-1]["implementation"]["source"] == "crucible/dh_v4.py"
    assert hashlib.sha256(Path(dh_v4.__file__).read_bytes()).hexdigest() == dh_v4.IMPLEMENTATION_SHA256
    assert hashlib.sha256(Path(dh_v3.__file__).read_bytes()).hexdigest() == "c76aa43773e8c89f395d7286c43e5c6ceac90b8313532337dd48137fa58a2b8e"


def test_every_new_parameter_has_independent_exact_primality_certificates():
    for bits in range(129, 301):
        profile = dh_v4._profile(f"dh-{bits}")
        p, q = profile.p, profile.q
        factor = 2 ** ((bits + 1) // 2)
        assert q.bit_length() == bits and p.bit_length() == bits + 1
        assert p == 2 * q + 1 and q % 2 == 1
        assert (q - 1) % factor == 0
        assert ((q - 1) // factor) % 2 == 1
        # Pocklington: fully factored F | (q-1), F > sqrt(q), and a
        # witness satisfying Fermat plus gcd for every prime dividing F.
        assert factor * factor > q
        assert pow(3, q - 1, q) == 1
        assert pow(3, (q - 1) // 2, q) == q - 1
        assert math.gcd(pow(3, (q - 1) // 2, q) - 1, q) == 1
        # Now q is proved prime, so apply Pocklington to p with F=q.
        assert (p - 1) % q == 0 and q * q > p
        assert pow(2, p - 1, p) == 1
        assert math.gcd(pow(2, (p - 1) // q, p) - 1, p) == 1
        assert 1 < profile.g < p - 1 and pow(profile.g, q, p) == 1
        with pytest.raises(ValueError):
            dh_v4._verify_safe_prime_certificate(bits, p + 2, q)
        with pytest.raises(ValueError):
            dh_v4._verify_safe_prime_certificate(bits, 2 * (q + 2) + 1, q + 2)


def test_every_v3_profile_preserves_parameters_distribution_encoding_and_targets():
    for bits in range(20, 129):
        profile_id = f"dh-{bits}"
        before, after = dh_v3.get_manifest(profile_id), dh_v4.get_manifest(profile_id)
        for field in ("parameters", "encoding", "kdf", "algorithms", "distribution",
                      "shared_secret_bytes", "parameter_bits", "modulus_bits"):
            assert before[field] == after[field], (profile_id, field)
        q = int(before["parameters"]["q"], 16)
        for x, r in ((1, 1), (7, 11), (q - 1, q - 1)):
            with patch.object(dh_v3.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                old = dh_v3.generate(profile_id)
            with patch.object(dh_v4.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                new = dh_v4.generate(profile_id)
            assert old.target == new.target
            assert new.public["version"] == "4"
            assert old.public["version"] == "3"
            omit = {"version", "implementation_sha256"}
            assert {key: value for key, value in old.public.items() if key not in omit} == {
                key: value for key, value in new.public.items() if key not in omit}


@pytest.mark.parametrize("bits", [129, 136, 192, 255, 256, 299, 300])
def test_extended_boundaries_use_exact_draws_and_canonical_transcript_kdf(bits):
    profile_id = f"dh-{bits}"
    profile = dh_v4._profile(profile_id)
    for x, r in ((1, profile.q - 1), (profile.q - 1, 1),
                 (profile.q - 1, profile.q - 1), (17, 23)):
        with patch.object(dh_v4.secrets, "randbelow", side_effect=[x - 1, r - 1]) as draws:
            generated = dh_v4.generate(profile_id)
        assert draws.call_args_list == [call(profile.q - 1), call(profile.q - 1)]
        assert generated.diagnostics["shared_secret_matches"] is True
        assert generated.diagnostics["honest_trials"] == 1
        pk = bytes.fromhex(generated.public["public_key"])
        ciphertext = bytes.fromhex(generated.public["ciphertext"])
        assert len(pk) == len(ciphertext) == profile.width
        assert int.from_bytes(pk, "big") == pow(profile.g, x, profile.p)
        assert int.from_bytes(ciphertext, "big") == pow(profile.g, r, profile.p)
        shared = pow(profile.g, x * r, profile.p)
        fields = [profile_id.encode("ascii")]
        fields.extend(value.to_bytes(profile.width, "big") for value in (profile.p, profile.q, profile.g))
        fields.extend((pk, ciphertext, shared.to_bytes(profile.width, "big")))
        framed = dh_v3.DOMAIN + b"".join(len(field).to_bytes(4, "big") + field for field in fields)
        assert generated.target == hashlib.sha256(framed).digest()
        assert dh_v4.decapsulate(profile_id, x, ciphertext) == generated.target
        assert len(generated.target) == 32


def test_extended_inputs_reject_noncanonical_elements_and_private_exponents():
    for bits in (129, 192, 256, 300):
        profile_id = f"dh-{bits}"
        profile = dh_v4._profile(profile_id)
        public_key, private_key = dh_v4.keygen(profile_id)
        ciphertext, _ = dh_v4.encapsulate(profile_id, public_key)
        malformed = [b"", b"\x00" + public_key]
        malformed.extend(value.to_bytes(profile.width, "big") for value in (0, 1, profile.p - 1, profile.p, profile.p - 4))
        for value in malformed:
            with pytest.raises(ValueError):
                dh_v4.encapsulate(profile_id, value)
            with pytest.raises(ValueError):
                dh_v4.decapsulate(profile_id, private_key, value)
        for private in (0, profile.q, -1, True, 1.5):
            with pytest.raises(ValueError):
                dh_v4.decapsulate(profile_id, private, ciphertext)
    for unknown in ("dh-19", "dh-301", "dh-999", "dh-0300"):
        with pytest.raises(ValueError):
            dh_v4.generate(unknown)
    with pytest.raises(ValueError):
        dh_v4.validate_profile(replace(dh_v4._profile("dh-300"), parameter_bits=301))


def test_offline_search_reproduces_old_and_new_certificate_constants():
    for bits in (37, 128, 129, 300):
        generated = generate_pair(bits)
        profile = dh_v4._profile(f"dh-{bits}")
        assert (generated["p"], generated["q"]) == (profile.p, profile.q)
    for invalid in (36, 301, True, 129.0):
        with pytest.raises(ValueError):
            generate_pair(invalid)


def test_example_solver_supports_v4_small_profiles_without_expanding_its_limit():
    generated = dh_v4.generate("dh-20")
    assert recover(generated.public) == generated.target.hex()
    for profile_id in ("dh-37", "dh-129", "dh-300"):
        with pytest.raises(ValueError, match="resource limit"):
            recover(dh_v4.generate(profile_id).public)
