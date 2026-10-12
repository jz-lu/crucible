"""Known-answer and invariant tests for the intentionally weak teaching KEM.

Run with pytest or `python -m unittest discover -s tests -p test_kem.py`.
Deterministic private exponents below are OFFLINE vectors, never live material.
"""

import hashlib
import json
from math import isqrt
from pathlib import Path
import unittest
from unittest.mock import patch

from crucible import kem


VECTOR = {
    "profile_id": "dh-20",
    "x": 7,
    "r": 11,
    "public_key": "004000",
    "ciphertext": "0ffe83",
    "shared_element": "040afc",
    "shared_secret": "2540d4f3d46a42f2baf10d4c4d47898f58052a103fbc15e5b3e57060e890b9ea",
}


def trial_division_prime(value):
    if value < 2:
        return False
    if value % 2 == 0:
        return value == 2
    return all(value % divisor for divisor in range(3, isqrt(value) + 1, 2))


class KemTests(unittest.TestCase):
    def test_every_integer_size_has_an_independently_recoverable_instance(self):
        from scripts.solve_teaching import recover
        profiles = kem.manifests()
        self.assertEqual([item["parameter_bits"] for item in profiles], list(range(20, 37)))
        self.assertEqual([item["instance_size"] for item in profiles], list(range(20, 37)))
        self.assertEqual([item["id"] for item in profiles], [f"dh-{bits}" for bits in range(20, 37)])
        for manifest in profiles:
            with self.subTest(profile=manifest["id"]):
                challenge = kem.generate(manifest["id"])
                self.assertEqual(challenge.public["instance_size"], manifest["instance_size"])
                self.assertEqual(manifest["size_parameter"]["comparison_scope"], "scheme-version")
                self.assertEqual(recover(challenge.public), challenge.target.hex())

    def test_archived_release_is_unchanged_and_existing_parameters_are_stable(self):
        from crucible.legacy import teaching_dh_v1 as legacy
        from scripts.solve_teaching import recover
        self.assertEqual(legacy.IMPLEMENTATION_SHA256,
                         "d3cfc485212a60cefc9903b5410797c3942391e6280cfbd51c95559d59eb537c")
        self.assertNotEqual(kem.SCHEME_VERSION, legacy.SCHEME_VERSION)
        self.assertEqual(kem.DOMAIN, legacy.DOMAIN)
        for previous in legacy.manifests():
            with self.subTest(profile=previous["id"]):
                current = kem.get_manifest(previous["id"])
                for field in ("parameters", "encoding", "kdf"):
                    self.assertEqual(current[field], previous[field])
                with patch.object(legacy.secrets, "randbelow", side_effect=[6, 10]):
                    old = legacy.generate(previous["id"])
                with patch.object(kem.secrets, "randbelow", side_effect=[6, 10]):
                    new = kem.generate(previous["id"])
                self.assertEqual(new.target, old.target)
                self.assertEqual(recover(old.public), old.target.hex())

    def test_literal_known_answer_vector(self):
        with patch.object(kem.secrets, "randbelow", side_effect=[VECTOR["x"] - 1, VECTOR["r"] - 1]) as rng:
            pk, sk = kem.keygen(VECTOR["profile_id"])
            ciphertext, secret = kem.encapsulate(VECTOR["profile_id"], pk)
        self.assertEqual(rng.call_count, 2)
        self.assertEqual(pk.hex(), VECTOR["public_key"])
        self.assertEqual(ciphertext.hex(), VECTOR["ciphertext"])
        self.assertEqual(secret.hex(), VECTOR["shared_secret"])
        self.assertEqual(kem.decapsulate("dh-20", sk, ciphertext), secret)

    def test_public_kdf_spec_is_sufficient_to_reproduce_vector(self):
        public = kem.serialize("dh-20", bytes.fromhex(VECTOR["public_key"]), bytes.fromhex(VECTOR["ciphertext"]))
        fields = [public["profile_id"].encode("ascii")]
        fields += [bytes.fromhex(public["parameters"][name]) for name in ("p", "q", "g")]
        fields += [bytes.fromhex(VECTOR[name]) for name in ("public_key", "ciphertext", "shared_element")]
        transcript = bytes.fromhex(public["kdf"]["domain_hex"])
        transcript += b"".join(len(field).to_bytes(4, "big") + field for field in fields)
        self.assertEqual(hashlib.sha256(transcript).hexdigest(), VECTOR["shared_secret"])

    def test_constants_are_exact_safe_primes_with_expected_order(self):
        for manifest in kem.manifests():
            with self.subTest(profile=manifest["id"]):
                p, q, g = (int(manifest["parameters"][name], 16) for name in ("p", "q", "g"))
                # Independent exact checks, rather than trusting runtime MR.
                self.assertTrue(trial_division_prime(p))
                self.assertTrue(trial_division_prime(q))
                self.assertEqual(p, 2 * q + 1)
                self.assertEqual(q.bit_length(), manifest["parameter_bits"])
                self.assertEqual(p.bit_length(), manifest["modulus_bits"])
                self.assertEqual(pow(g, q, p), 1)
                self.assertNotEqual(g, 1)

    def test_roundtrips_across_entire_registered_ladder(self):
        for manifest in kem.manifests():
            for _ in range(20):
                with self.subTest(profile=manifest["id"]):
                    pk, sk = kem.keygen(manifest["id"])
                    ciphertext, expected = kem.encapsulate(manifest["id"], pk)
                    self.assertEqual(kem.decapsulate(manifest["id"], sk, ciphertext), expected)
                    self.assertEqual(len(expected), 32)

    def test_distribution_includes_both_honest_exponent_boundaries(self):
        for manifest in kem.manifests():
            q = int(manifest["parameters"]["q"], 16)
            for x, r in ((1, 1), (1, q - 1), (q - 1, 1), (q - 1, q - 1)):
                with self.subTest(profile=manifest["id"], x=x, r=r):
                    with patch.object(kem.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                        pk, sk = kem.keygen(manifest["id"])
                        ciphertext, target = kem.encapsulate(manifest["id"], pk)
                    self.assertEqual(kem.decapsulate(manifest["id"], sk, ciphertext), target)

    def test_rejects_invalid_group_values_and_noncanonical_encodings(self):
        m = kem.get_manifest("dh-20")
        p = int(m["parameters"]["p"], 16)
        q = int(m["parameters"]["q"], 16)
        width = m["encoding"]["group_element_bytes"]
        nonmember = next(i for i in range(2, p - 1) if pow(i, q, p) != 1)
        bad = [n.to_bytes(width, "big") for n in (0, 1, p - 1, p, nonmember)]
        bad += [b"", b"\x04", bytes.fromhex(VECTOR["public_key"]) + b"\x00", "004000", bytearray.fromhex("004000")]
        for encoded in bad:
            with self.subTest(value=repr(encoded)):
                with self.assertRaises(ValueError):
                    kem.encapsulate("dh-20", encoded)
                with self.assertRaises(ValueError):
                    kem.decapsulate("dh-20", 7, encoded)

    def test_rejects_private_keys_outside_declared_distribution(self):
        ciphertext = bytes.fromhex(VECTOR["ciphertext"])
        q = int(kem.get_manifest("dh-20")["parameters"]["q"], 16)
        for private in (0, -1, q, True, 1.5, "7"):
            with self.subTest(private=private):
                with self.assertRaises(ValueError):
                    kem.decapsulate("dh-20", private, ciphertext)

    def test_unknown_profile_rejected_before_randomness(self):
        with patch.object(kem.secrets, "randbelow") as rng:
            for operation in (kem.keygen, kem.generate, kem.get_manifest):
                with self.assertRaises(ValueError):
                    operation("dh-999")
            rng.assert_not_called()

    def test_profile_validation_rejects_invalid_math(self):
        for profile in (
            kem.Profile("bad", 20, 1048705, 524352),
            kem.Profile("bad", 19, 1048703, 524351),
            kem.Profile("bad", 20, 1048703, 524351, 1),
            kem.Profile("bad", 20, 1048703, 524351, 1048702),
            kem.Profile("bad", 20, 1048703, 524351, True),
        ):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    kem.validate_profile(profile)

    def test_primality_validator_is_bounded_and_rejects_pseudoprimes(self):
        for composite in (0, 1, -7, 9, 341, 561, 2047, 1373653, 3215031751, 341550071728321, 3825123056546413051):
            self.assertFalse(kem._is_prime(composite), composite)
        for prime in (2, 3, 41, 524351, 18446744073709551557):
            self.assertTrue(kem._is_prime(prime), prime)
        with self.assertRaises(ValueError):
            kem._is_prime(1 << 64)

    def test_public_payload_is_explicit_and_secret_free(self):
        generated = kem.generate("dh-20")
        self.assertEqual(set(generated.public), {
            "scheme_id", "version", "profile_id", "instance_size", "parameter_bits", "parameters",
            "public_key", "ciphertext", "encoding", "kdf", "implementation_sha256",
        })
        self.assertEqual(len(generated.target), 32)
        self.assertNotIn(generated.target.hex(), json.dumps(generated.public))
        self.assertNotIn(generated.target.hex(), repr(generated))
        self.assertTrue(generated.diagnostics["shared_secret_matches"])

    def test_mismatch_is_recorded_without_filtering_or_retrying(self):
        target = b"e" * 32
        with patch.object(kem, "keygen", return_value=(bytes.fromhex(VECTOR["public_key"]), 7)) as keygen:
            with patch.object(kem, "encapsulate", return_value=(bytes.fromhex(VECTOR["ciphertext"]), target)) as encapsulate:
                with patch.object(kem, "decapsulate", return_value=b"d" * 32) as decapsulate:
                    generated = kem.generate("dh-20")
        self.assertEqual(generated.target, target)
        self.assertFalse(generated.diagnostics["shared_secret_matches"])
        self.assertEqual(generated.diagnostics["honest_trials"], 1)
        self.assertEqual((keygen.call_count, encapsulate.call_count, decapsulate.call_count), (1, 1, 1))

    def test_manifest_distinguishes_correctness_from_security_and_test_attestation(self):
        for m in kem.manifests():
            self.assertTrue(m["educational"])
            self.assertFalse(m["admission"]["official_ranked"])
            self.assertEqual(m["correctness"]["kind"], "perfect")
            self.assertEqual(m["correctness"]["failure_bound"], "0")
            self.assertIn("independent review pending", m["correctness"]["evidence_status"])
            self.assertEqual(m["implementation"]["source_sha256"], hashlib.sha256(Path(kem.__file__).read_bytes()).hexdigest())

    def test_manifest_callers_cannot_mutate_registered_parameters(self):
        first = kem.get_manifest("dh-20")
        first["parameters"]["g"] = "00"
        first["kdf"]["fields"].clear()
        again = kem.get_manifest("dh-20")
        self.assertEqual(again["parameters"]["g"], "000004")
        self.assertEqual(len(again["kdf"]["fields"]), 7)


if __name__ == "__main__":
    unittest.main()
