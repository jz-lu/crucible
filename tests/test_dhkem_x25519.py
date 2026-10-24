"""RFC 9180 fixture conformance and boundary checks, no custom small curves."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from crucible import dhkem_x25519 as kem

VECTORS = json.loads((Path(__file__).parent / "fixtures/rfc9180-x25519.json").read_text())["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: f"mode{v['mode']}-kdf{v['kdf_id']}-aead{v['aead_id']}")
def test_all_pinned_rfc_unauthenticated_kem_vectors(vector):
    # PSK belongs to the later HPKE key schedule, so modes 0 and 1 use this same
    # KEM. Auth/AuthPSK instead have an additional static DH and are excluded.
    pk, sk = kem.derive_keypair(kem.PROFILE_ID, bytes.fromhex(vector["ikmR"]))
    epk, esk = kem.derive_keypair(kem.PROFILE_ID, bytes.fromhex(vector["ikmE"]))
    assert pk.hex() == vector["pkRm"] and sk.value.hex() == vector["skRm"]
    assert epk.hex() == vector["pkEm"] and esk.value.hex() == vector["skEm"]
    enc, secret = kem._encapsulate_with_private(kem.PROFILE_ID, pk, esk)
    assert enc.hex() == vector["enc"]
    assert secret.hex() == vector["shared_secret"]
    assert kem.decapsulate(kem.PROFILE_ID, sk, enc) == secret


def test_manifest_has_one_published_profile_no_fabricated_security_ladder():
    manifest, = kem.manifests()
    assert manifest["instance_size"] == manifest["profile_order"] == 1
    assert manifest["profile_kind"] == "standard-parameter-set"
    assert manifest["size_parameter"]["name"] == "Parameter set"
    assert "initial_security_estimate" not in manifest
    assert manifest["encoding"]["public_key_bytes"] == manifest["encoding"]["ciphertext_bytes"] == 32
    assert manifest["correctness"]["kind"] == "perfect"
    assert manifest["implementation"]["source_sha256"] == kem.IMPLEMENTATION_SHA256
    artifact = kem.artifact_manifest()
    assert len(artifact["artifact_sha256"]) == 64
    assert "/Users/" not in json.dumps(artifact)


def test_honest_generation_freshness_and_public_only_serialization():
    first, second = kem.generate(kem.PROFILE_ID), kem.generate(kem.PROFILE_ID)
    assert first.diagnostics["shared_secret_matches"] is True
    assert first.public["public_key"] != second.public["public_key"]
    assert first.public["ciphertext"] != second.public["ciphertext"]
    assert first.target != second.target
    assert first.target.hex() not in json.dumps(first.public)
    assert first.target.hex() not in repr(first)
    assert set(first.public["parameters"]) == {"kem_id", "curve", "kdf"}


@pytest.mark.parametrize("value", [b"", bytes(31), bytes(33), bytearray(32), "00" * 32, None])
def test_exact_bounded_input_lengths(value):
    pk, sk = kem.keygen(kem.PROFILE_ID)
    with pytest.raises(ValueError): kem.encapsulate(kem.PROFILE_ID, value)
    with pytest.raises(ValueError): kem.decapsulate(kem.PROFILE_ID, sk, value)
    with pytest.raises(ValueError): kem.serialize(kem.PROFILE_ID, value, pk)
    with pytest.raises(ValueError): kem.serialize(kem.PROFILE_ID, pk, value)


@pytest.mark.parametrize("point", [bytes(32), b"\x01" + bytes(31), ((1 << 255) - 19).to_bytes(32, "little")])
def test_low_order_inputs_are_rejected(point):
    _, sk = kem.keygen(kem.PROFILE_ID)
    with pytest.raises(ValueError): kem.encapsulate(kem.PROFILE_ID, point)
    with pytest.raises(ValueError): kem.decapsulate(kem.PROFILE_ID, sk, point)


@pytest.mark.parametrize("profile", [None, True, [], "dhkem-x25519-v1-tier-2", "dhkem-x25519-v1-rfc9180 "])
def test_unknown_profiles_fail_before_generation(profile):
    with pytest.raises(ValueError): kem.generate(profile)


def test_target_is_never_replaced_or_retried_after_disagreement(monkeypatch):
    target = b"t" * 32
    calls = []
    monkeypatch.setattr(kem, "keygen", lambda profile: (b"p" * 32, kem.PrivateKey(b"s" * 32)))
    def enc(profile, pk):
        calls.append(1)
        return b"c" * 32, target
    monkeypatch.setattr(kem, "encapsulate", enc)
    monkeypatch.setattr(kem, "decapsulate", lambda *a: b"d" * 32)
    result = kem.generate(kem.PROFILE_ID)
    assert result.target == target and result.diagnostics["shared_secret_matches"] is False
    assert calls == [1]


@pytest.mark.parametrize("tampering", ["version", "native"])
def test_dependency_version_or_binary_substitution_is_rejected(monkeypatch, tmp_path, tampering):
    distribution = kem.metadata.distribution("cryptography")
    substituted = tmp_path / "unreviewed-native.so"
    substituted.write_bytes(b"unreviewed binary; must never be imported")
    candidate = SimpleNamespace(version="0.0.0" if tampering == "version" else distribution.version,
        locate_file=lambda name: substituted if name.endswith("_rust.abi3.so") else distribution.locate_file(name))
    monkeypatch.setattr(kem.metadata, "distribution", lambda name: candidate)
    with pytest.raises(RuntimeError, match="pinned|artifact"):
        kem.generate(kem.PROFILE_ID)
