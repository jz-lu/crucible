"""Stage navigation cannot reinterpret immutable configuration evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from crucible import progressions, registry


CURRENT_PATHS = [
    ("teaching-dh-kem", "7", 50, 50),
    ("rsa-kem", "4", 50, 50),
    ("dhkem-x25519", "curve-compressed-v2", 50, 50),
    ("ml-kem", "compressed-research-v4", 6, 6),
    ("frodokem", "anchor-research-v3", 80, 80),
    ("sntrup", "3", 25, 27),
    ("hqc", "modeled-research-v3", 66, 66),
    ("classic-mceliece", "length-research-v1", 50, 50),
]

HISTORICAL_PATHS = [("ml-kem", "packed-research-v3", 10, 11), ("hqc", "growth-research-v2", 25, 27)]

# Reviewed revision-1 membership: changing an order or selection is an explicit
# progression revision, not an incidental consequence of a presentation edit.
REVISION_ONE_MEMBERSHIP = {
    ("hqc", "modeled-research-v3"): "32c9987251f1cb961a42fae866cca5fef5332aafcf6e93bb0358eb0124fa6e47",
    ("ml-kem", "compressed-research-v4"): "5d7d7cbade1db320fae79d0ed496befc8c87c44eac29b4d03f39e402a4a95cfe",
    ("teaching-dh-kem", "7"): "8587b3bf4721aadd798d9672c7084aff20413ad3f0ef8be04b617eb722559cd1",
    ("rsa-kem", "4"): "3be4c1bcccb5186e056e51ad77c7042f8c7e7d35046f958e8925f69e274ef79f",
    ("dhkem-x25519", "curve-compressed-v2"): "4e9bef54726bc1ae3ab0925251524a21cb6c1f65656debb8c81f9325e86def0e",
    ("ml-kem", "packed-research-v3"): "bd28020da8b46edc963dec673dfea8ca91b83b432b3b635042983a4f99026df4",
    ("frodokem", "anchor-research-v3"): "07930512a2d709f2669e8320068de171536cc732725fe4ec70a24008edf14e1a",
    ("sntrup", "3"): "33db85f8af7966afb74c07bf1b412b6b2d204b14f6cd792dbdba94ddb0a33c98",
    ("hqc", "growth-research-v2"): "fff1c98cb1dd1efe69d54ea38e146827b4f2a3fa50a520263576ead07cb6b7e5",
    ("classic-mceliece", "length-research-v1"): "b035507363430c014bd1cfdf31e123cc71ff765b6f2901d6578994d2627ef2b7",
}


@pytest.mark.parametrize("scheme_id,version,count,config_count", CURRENT_PATHS + HISTORICAL_PATHS)
def test_current_paths_preserve_every_configuration_and_frozen_manifest(scheme_id, version, count, config_count):
    adapter = registry.adapter_for(scheme_id, version)
    profiles = registry.presentation_manifests(adapter)
    before = deepcopy(profiles)
    result = progressions.for_release(scheme_id, version, profiles)
    assert profiles == before
    assert registry.presentation_manifests(adapter) == before
    assert result["stage_count"] == count
    assert result["configuration_count"] == result["available_configuration_count"] == len(profiles) == config_count
    assert [s["number"] for s in result["stages"]] == list(range(1, count + 1))
    selected = [s["profile_id"] for s in result["stages"]]
    additional = result["additional_configuration_ids"]
    assert not set(selected) & set(additional)
    assert set(selected) | set(additional) == {p["id"] for p in profiles}
    assert selected == [p["id"] for p in profiles if p["id"] not in additional]
    assert selected[0] == profiles[0]["id"] and selected[-1] == profiles[-1]["id"]
    assert result["id"] == f"{scheme_id}/{version}/default-progression/v1"
    assert result["name"] == "Default progression" and result["version"] == "1"
    assert all(result[field].strip() for field in ("rule", "parameter_space", "endpoint", "status", "limitations", "selection_basis"))
    membership = {"scheme_id": scheme_id, "scheme_version": version, "progression_version": "1",
                  "configuration_ids": [p["id"] for p in profiles], "stage_configuration_ids": selected}
    assert result["membership_sha256"] == hashlib.sha256(json.dumps(membership, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert result["membership_sha256"] == REVISION_ONE_MEMBERSHIP[scheme_id, version]
    # Adapter enumeration order and mutations of returned presentation data
    # cannot change the declared progression.
    assert progressions.for_release(scheme_id, version, list(reversed(profiles))) == result
    result["stages"][0]["profile_id"] = "caller mutation"
    assert progressions.for_release(scheme_id, version, profiles)["stages"][0]["profile_id"] == profiles[0]["id"]


def test_every_current_release_has_one_declared_path_without_a_global_stage_quota():
    assert {(s, v) for s, v, _, _ in CURRENT_PATHS} == set(registry.CURRENT.items())
    counts = {progressions.for_release(s, v, registry.presentation_manifests(registry.adapter_for(s, v)))["stage_count"]
              for s, v, _, _ in CURRENT_PATHS}
    assert counts == {6, 25, 50, 66, 80}
    assert sum(c for _, _, _, c in CURRENT_PATHS) == 379


@pytest.mark.parametrize("scheme_id,version,omitted", [
    ("ml-kem", "packed-research-v3", ["module-lwe-v3-tier-7"]),
    ("ml-kem", "compressed-research-v4", []),
    ("sntrup", "3", ["sntrup-v3-tier-17", "sntrup-v3-tier-24"]),
    ("hqc", "growth-research-v2", ["hqc-growth-research-v2-tier-24", "hqc-growth-research-v2-tier-26"]),
])
def test_curated_defaults_keep_intermediate_configurations_accessible(scheme_id, version, omitted):
    adapter = registry.adapter_for(scheme_id, version)
    profiles = registry.presentation_manifests(adapter)
    result = progressions.for_release(scheme_id, adapter.SCHEME_VERSION, profiles)
    assert result["additional_configuration_ids"] == omitted
    for identifier in omitted:
        assert registry.manifest_for(identifier, scheme_id, adapter.SCHEME_VERSION)["id"] == identifier


@pytest.mark.parametrize("scheme_id,version,expected_omissions", [("ml-kem", "packed-research-v3", [7]), ("sntrup", "3", [17, 24])])
def test_curated_reference_selection_is_reproducible_without_treating_unknown_as_easy(scheme_id, version, expected_omissions):
    adapter = registry.adapter_for(scheme_id, version)
    profiles = registry.presentation_manifests(adapter)
    estimated = [i for i, p in enumerate(profiles) if p.get("initial_security_estimate") is not None]
    protected = {0, len(profiles) - 1, estimated[0]}
    protected.update(i for i in range(len(profiles)) if i not in estimated)
    retained = list(range(len(profiles)))
    omitted = []
    for _ in expected_omissions:
        candidates = []
        for position in range(1, len(retained) - 1):
            index = retained[position]
            if index in protected:
                continue
            before, after = (profiles[retained[position + offset]]["initial_security_estimate"]["bits"] for offset in (-1, 1))
            candidates.append((after - before, index))
        _, index = min(candidates)
        omitted.append(index + 1)
        retained.remove(index)
    assert omitted == expected_omissions


def test_hqc_default_uses_native_weight_spacing_without_inventing_security_estimates():
    from fractions import Fraction
    profiles = registry.presentation_manifests(registry.adapter_for("hqc", "growth-research-v2"))
    assert all(p.get("initial_security_estimate") is None for p in profiles)
    weights = list(range(1, 28))
    omitted = []
    for _ in range(2):
        _, weight = min((Fraction(weights[i + 1], weights[i - 1]), weights[i]) for i in range(1, len(weights) - 1))
        weights.remove(weight)
        omitted.append(weight)
    assert omitted == [26, 24]


def test_archived_and_named_reference_releases_do_not_gain_invented_stages():
    current = set(registry.CURRENT.items())
    for identity in registry.ADAPTERS:
        if identity in {(s, v) for s, v, _, _ in HISTORICAL_PATHS}:
            profiles = registry.presentation_manifests(registry.adapter_for(*identity))
            assert progressions.for_release(*identity, profiles)["membership_sha256"] == REVISION_ONE_MEMBERSHIP[identity]
        elif identity not in current:
            assert progressions.for_release(*identity, profiles=[]) is None
    assert progressions.for_release("unknown", "1", []) is None


@pytest.fixture
def rsa_profiles():
    return registry.presentation_manifests(registry.adapter_for("rsa-kem", "4"))


@pytest.mark.parametrize("mutation", ["insert", "remove", "replace", "duplicate", "rename"])
def test_configuration_membership_changes_require_an_explicit_revision(rsa_profiles, mutation):
    profiles = deepcopy(rsa_profiles)
    if mutation == "insert":
        new = {**profiles[-1], "id": "rsa-v4-new-intermediate", "profile_id": "rsa-v4-new-intermediate", "instance_size": 51}
        profiles.append(new)
    elif mutation == "remove":
        profiles.pop()
    elif mutation == "replace":
        profiles[-1] = {**profiles[-1], "id": "replacement", "profile_id": "replacement"}
    elif mutation == "duplicate":
        profiles.append(deepcopy(profiles[-1]))
    else:
        profiles[0]["id"] = "rsa-v4-stage-1"
    with pytest.raises(ValueError, match="membership|unique|identity"):
        progressions.for_release("rsa-kem", "4", profiles)
    assert rsa_profiles[0]["id"] == "rsa-v4-tier-1"


@pytest.mark.parametrize("ordinal", [0, 2, True, 1.0, "1", None])
def test_display_rename_cannot_rewrite_frozen_ordinals(rsa_profiles, ordinal):
    rsa_profiles[0]["instance_size"] = ordinal
    with pytest.raises(ValueError, match="Frozen configuration ordinal"):
        progressions.for_release("rsa-kem", "4", rsa_profiles)


@pytest.mark.parametrize("field,value", [("scheme_id", "other"), ("version", "3"), ("profile_id", "other")])
def test_progressions_reject_cross_release_or_ambiguous_identity(rsa_profiles, field, value):
    rsa_profiles[0][field] = value
    with pytest.raises(ValueError, match="identity"):
        progressions.for_release("rsa-kem", "4", rsa_profiles)


def test_progression_claims_do_not_hide_unresolved_scientific_design():
    def path(identifier):
        adapter = registry.adapter_for(identifier)
        return progressions.for_release(identifier, adapter.SCHEME_VERSION, registry.presentation_manifests(adapter))
    for identifier in ("teaching-dh-kem", "rsa-kem", "dhkem-x25519", "classic-mceliece"):
        assert "historical sampling" in path(identifier)["rule"]
        assert "not a required stage count" in path(identifier)["rule"]
    assert "historical endpoint" in path("hqc")["status"]
    assert "conjectural" in path("hqc")["limitations"]
    assert "current 128-bit security claim" in path("hqc")["limitations"]
    assert "joint parameter efficiency unresolved" in path("classic-mceliece")["status"]
    assert "model-qualified correctness" in path("ml-kem")["status"]
    assert "attack costs remain unestimated" in path("ml-kem")["limitations"]
    assert "actual failure probabilities need not be equal" in path("frodokem")["limitations"]
