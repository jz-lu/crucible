"""Canonical byte measurements are display metadata, not new adapter contracts."""
from copy import deepcopy
from unittest.mock import patch

import pytest

from crucible import dh_v3, dh_v4, kem, registry, rsa
from crucible.legacy import teaching_dh_v1


@pytest.mark.parametrize("adapter", [teaching_dh_v1, kem, dh_v3, dh_v4, rsa])
def test_descriptor_measures_every_profile_without_generating_instances(adapter):
    original = adapter.manifests()
    with patch.object(adapter, "generate", side_effect=AssertionError("Catalogue must not generate instances")):
        with patch.object(adapter, "keygen", side_effect=AssertionError("Catalogue must not generate keys")):
            scheme = registry.descriptor(adapter)
    assert scheme["profiles"] == original
    assert set(scheme["profile_metrics"]) == {profile["id"] for profile in original}
    basis = scheme["profile_metrics_basis"]
    assert basis["unit"] == "bytes"
    assert "binary" in basis["encoding"]
    assert "JSON" in basis["excludes"] and "shared domain parameters" in basis["excludes"]
    for profile in original:
        tier = profile.get("instance_size", profile["parameter_bits"])
        if adapter is rsa:
            width = (tier + 7) // 8
            expected = {"public_key_bytes": 2 * width, "ciphertext_bytes": width}
        else:
            width = (tier + 1 + 7) // 8
            expected = {"public_key_bytes": width, "ciphertext_bytes": width}
        assert scheme["profile_metrics"][profile["id"]] == expected


@pytest.mark.parametrize("adapter,tiers", [
    (teaching_dh_v1, [20, 24, 36]), (kem, [20, 23, 24, 31, 32, 36]),
    (dh_v3, [20, 23, 24, 63, 64, 127, 128]), (rsa, [20, 24, 25, 64, 65, 128]),
    (dh_v4, [20, 128, 129, 135, 136, 255, 256, 295, 296, 300]),
])
def test_byte_boundaries_match_canonical_serialization_not_hex_or_json(adapter, tiers):
    scheme = registry.descriptor(adapter)
    for tier in tiers:
        profile_id = f'rsa-{tier}' if adapter is rsa else f'dh-{tier}'
        generated = adapter.generate(profile_id)
        metrics = scheme["profile_metrics"][profile_id]
        assert metrics["public_key_bytes"] == len(bytes.fromhex(generated.public["public_key"]))
        assert metrics["ciphertext_bytes"] == len(bytes.fromhex(generated.public["ciphertext"]))
        assert len(generated.public["public_key"]) == 2 * metrics["public_key_bytes"]
        assert len(generated.public["ciphertext"]) == 2 * metrics["ciphertext_bytes"]
    if adapter is rsa:
        assert scheme["profile_metrics"]["rsa-128"] == {"public_key_bytes": 32, "ciphertext_bytes": 16}
        assert "exponent" in scheme["profile_metrics_basis"]["notes"]
        assert "padded to the full modulus byte width" in scheme["profile_metrics_basis"]["notes"]
    if adapter is dh_v4:
        assert scheme["profile_metrics"]["dh-295"] == {"public_key_bytes": 37, "ciphertext_bytes": 37}
        assert scheme["profile_metrics"]["dh-296"] == {"public_key_bytes": 38, "ciphertext_bytes": 38}
        assert scheme["profile_metrics"]["dh-300"] == {"public_key_bytes": 38, "ciphertext_bytes": 38}
        assert scheme["instance_size_range"] == {"min": 20, "max": 300, "step": 1}


def test_general_explicit_lengths_do_not_need_a_scheme_specific_frontend_rule():
    manifest = {"scheme_id": "future-kem", "version": "8", "instance_size": 128,
                "encoding": {"public_key_bytes": 1184, "ciphertext_bytes": 1088}}
    assert registry.profile_metrics(manifest) == {"public_key_bytes": 1184, "ciphertext_bytes": 1088}
    manifest["encoding"]["group_element_bytes"] = 17
    assert registry.profile_metrics(manifest) == {"public_key_bytes": 1184, "ciphertext_bytes": 1088}
    unknown = {"scheme_id": "future-kem", "version": "8", "instance_size": 128,
               "encoding": {"group_element_bytes": 17, "integer_bytes": 16}}
    assert registry.profile_metrics(unknown) == {"public_key_bytes": None, "ciphertext_bytes": None}


@pytest.mark.parametrize("value", [None, "variable", "32", 32.0, True, -1, {"min": 16, "max": 32}])
def test_variable_or_invalid_explicit_lengths_stay_unknown_even_with_a_legacy_width(value):
    manifest = deepcopy(kem.get_manifest("dh-20"))
    manifest["encoding"]["public_key_bytes"] = value
    assert registry.profile_metrics(manifest) == {"public_key_bytes": None, "ciphertext_bytes": 3}


def test_missing_metadata_and_unknown_releases_are_not_guessed():
    for encoding in (None, {}, "fixed-width"):
        assert registry.profile_metrics({"encoding": encoding}) == {"public_key_bytes": None, "ciphertext_bytes": None}
    assert registry.profile_metrics({"scheme_id": kem.SCHEME_ID, "version": "99",
        "encoding": {"group_element_bytes": 3}}) == {"public_key_bytes": None, "ciphertext_bytes": None}
    assert registry.profile_metrics({"encoding": {"public_key_bytes": 0, "ciphertext_bytes": 1}}) == {
        "public_key_bytes": 0, "ciphertext_bytes": 1}
