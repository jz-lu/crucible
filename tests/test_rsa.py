import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible import rsa
from scripts.solve_rsa import recover


def test_registered_modulus_ladder_and_public_recovery():
    assert [item["instance_size"] for item in rsa.manifests()] == list(range(20, 129))
    for manifest in rsa.manifests():
        size = manifest["instance_size"]
        for _ in range(6):
            public_key, private = rsa.keygen(manifest["id"])
            p, q = private.p, private.q
            # Independent full trial division confirms generated primes.
            if size <= 36:
                assert all(p % divisor for divisor in range(2, math.isqrt(p) + 1))
                assert all(q % divisor for divisor in range(2, math.isqrt(q) + 1))
            assert p != q and (p * q).bit_length() == size
            assert (p.bit_length(), q.bit_length()) == ((size + 1) // 2, size // 2)
            assert rsa.EXPONENT * private.d % ((p - 1) * (q - 1)) == 1
            ciphertext, target = rsa.encapsulate(manifest["id"], public_key)
            assert rsa.decapsulate(manifest["id"], private, ciphertext) == target
            public = rsa.serialize(manifest["id"], public_key, ciphertext)
            if size <= 36:
                assert recover(public) == target.hex()
            assert len(target) == 32


def test_known_answer_vector_and_stated_kdf():
    public_key = bytes.fromhex("0f98a5010001")
    private = rsa.PrivateKey(1009, 1013, pow(65537, -1, 1008 * 1012))
    with patch.object(rsa.secrets, "randbelow", return_value=424242) as rng:
        ciphertext, target = rsa.encapsulate("rsa-20", public_key)
    rng.assert_called_once_with(1022117)
    assert ciphertext.hex() == "08b7e6"
    assert target.hex() == "7d40d6a6db74d8f67644bef6f198dcc084c158b38ab82d396867efbc2a49bab5"
    assert rsa.decapsulate("rsa-20", private, ciphertext) == target
    public = rsa.serialize("rsa-20", public_key, ciphertext)
    fields = [b"rsa-20", bytes.fromhex("0f98a5"), bytes.fromhex("010001"), ciphertext,
              (424242).to_bytes(3, "big")]
    framed = bytes.fromhex(public["kdf"]["domain_hex"]) + b"".join(len(value).to_bytes(4, "big") + value for value in fields)
    assert hashlib.sha256(framed).digest() == target
    assert recover({"challenge": {"public": public}}) == target.hex()


def test_correctness_includes_zero_one_nonunits_and_upper_boundary():
    public_key, private = rsa.keygen("rsa-20")
    modulus = private.p * private.q
    for secret_integer in (0, 1, private.p, private.q, modulus - 1):
        with patch.object(rsa.secrets, "randbelow", return_value=secret_integer):
            ciphertext, target = rsa.encapsulate("rsa-20", public_key)
        assert rsa.decapsulate("rsa-20", private, ciphertext) == target
        assert recover(rsa.serialize("rsa-20", public_key, ciphertext)) == target.hex()


def test_keygen_rejects_equal_primes_and_wrong_modulus_size():
    with patch.object(rsa, "_sample_prime", side_effect=[1009, 1009, 521, 523, 1009, 1013]) as sample:
        public_key, private = rsa.keygen("rsa-20")
    assert sample.call_count == 6
    assert (private.p, private.q) == (1009, 1013)
    assert public_key.hex() == "0f98a5010001"


def test_input_validation_happens_before_sampling():
    public_key, private = rsa.keygen("rsa-20")
    malformed = [b"", public_key[:-1], public_key + b"\x00", bytearray(public_key), "pk",
                 (2 ** 18 + 1).to_bytes(3, "big") + (65537).to_bytes(3, "big"),
                 public_key[:3] + (3).to_bytes(3, "big")]
    with patch.object(rsa.secrets, "randbelow") as rng:
        for value in malformed:
            with pytest.raises(ValueError):
                rsa.encapsulate("rsa-20", value)
        for operation in (rsa.get_manifest, rsa.keygen, rsa.generate):
            with pytest.raises(ValueError):
                operation("rsa-999")
        rng.assert_not_called()
    modulus = private.p * private.q
    for value in (b"", b"\x00", b"\x00" * 4, modulus.to_bytes(3, "big"), "000001"):
        with pytest.raises(ValueError):
            rsa.decapsulate("rsa-20", private, value)
    for bad in (0, True, rsa.PrivateKey(private.p, private.q, 0),
                rsa.PrivateKey(private.p, private.q, True), rsa.PrivateKey(private.p, private.p, private.d),
                rsa.PrivateKey(1023, 1013, 5)):
        with pytest.raises(ValueError):
            rsa.decapsulate("rsa-20", bad, b"\x00" * 3)


def test_public_serializer_has_no_private_material_or_secret_target():
    generated = rsa.generate("rsa-20")
    assert set(generated.public) == {"scheme_id", "version", "profile_id", "instance_size", "parameter_bits",
        "parameters", "public_key", "ciphertext", "encoding", "kdf", "implementation_sha256"}
    assert set(generated.public["parameters"]) == {"N", "e"}
    assert generated.target.hex() not in json.dumps(generated.public)
    assert generated.target.hex() not in repr(generated)
    assert generated.diagnostics["shared_secret_matches"]


def test_mismatch_preserves_encapsulation_target_and_does_not_resample():
    public_key, private = rsa.keygen("rsa-20")
    ciphertext, target = rsa.encapsulate("rsa-20", public_key)
    with patch.object(rsa, "keygen", return_value=(public_key, private)) as keygen:
        with patch.object(rsa, "encapsulate", return_value=(ciphertext, target)) as encap:
            with patch.object(rsa, "decapsulate", return_value=b"x" * 32) as decap:
                generated = rsa.generate("rsa-20")
    assert generated.target == target
    assert not generated.diagnostics["shared_secret_matches"]
    assert generated.diagnostics["honest_trials"] == 1
    assert (keygen.call_count, encap.call_count, decap.call_count) == (1, 1, 1)


def test_metadata_is_fresh_and_pins_exact_reference_source():
    manifest = rsa.get_manifest("rsa-20")
    assert manifest["correctness"]["failure_bound"] == "0"
    assert not manifest["admission"]["official_ranked"]
    assert "not a standardized" in manifest["security_claim"]
    assert manifest["implementation"]["source_sha256"] == hashlib.sha256(Path(rsa.__file__).read_bytes()).hexdigest()
    manifest["parameters"]["e"] = "00"
    manifest["kdf"]["fields"].clear()
    assert rsa.get_manifest("rsa-20")["parameters"]["e"] == "010001"
    assert len(rsa.get_manifest("rsa-20")["kdf"]["fields"]) == 5
