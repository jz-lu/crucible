"""Supplemental literature evidence must stay scoped to exact releases/models."""
from copy import deepcopy
import math
from urllib.parse import urlsplit

import pytest

from crucible import registry, security_references


CURRENT = [
    ("teaching-dh-kem", "7"), ("rsa-kem", "4"),
    ("dhkem-x25519", "curve-compressed-v2"), ("ml-kem", "compressed-research-v4"),
    ("frodokem", "anchor-research-v3"), ("sntrup", "3"),
    ("hqc", "modeled-research-v3"), ("classic-mceliece", "length-research-v1"),
]


@pytest.mark.parametrize("scheme_id,version", CURRENT)
def test_current_reference_contract_has_explicit_gaps_and_exact_parameter_bindings(scheme_id, version):
    result = security_references.for_release(scheme_id, version)
    assert result["format"] == "crucible-security-references-v1"
    assert (result["scheme_id"], result["version"], result["revision"]) == (scheme_id, version, 1)
    assert set(result["metrics"]) == {"classical", "quantum"}
    profiles = {item["id"] for item in registry.ADAPTERS[(scheme_id, version)].manifests()}
    for metric in result["metrics"].values():
        assert metric["explanation"].strip()
        models = {model["id"]: model for model in metric["models"]}
        assert len(models) == len(metric["models"])
        for model in models.values():
            for key in ("id", "label", "unit", "scope"):
                assert isinstance(model[key], str) and model[key].strip()
            for key in ("assumptions", "limitations"):
                assert model[key] and all(isinstance(item, str) and item.strip() for item in model[key])
            assert model["sources"]
            for source in model["sources"]:
                url = urlsplit(source["url"])
                assert source["title"].strip() and url.scheme == "https" and url.hostname
                assert url.username is None and url.password is None
        assert len({point["profile_id"] for point in metric["estimates"]}) == len(metric["estimates"])
        for point in metric["estimates"]:
            assert set(point) == {"profile_id", "model_id", "bits"}
            assert point["profile_id"] in profiles and point["model_id"] in models
            assert type(point["bits"]) in (int, float) and math.isfinite(point["bits"]) and point["bits"] >= 0


@pytest.mark.parametrize("identity", [
    ("rsa-kem", "3"), ("teaching-dh-kem", "6"), ("ml-kem", "packed-research-v3"),
    ("hqc", "growth-research-v2"), ("unreviewed-future-scheme", "1"),
])
def test_archival_or_unknown_identity_does_not_inherit_current_evidence(identity):
    assert security_references.for_release(*identity) is None


def test_only_three_sourced_endpoint_points_are_added_without_filling_small_configuration_gaps():
    points = []
    for identity in CURRENT:
        result = security_references.for_release(*identity)
        for metric, values in result["metrics"].items():
            points.extend((identity, metric, point["profile_id"], point["bits"]) for point in values["estimates"])
    assert points == [
        (("ml-kem", "compressed-research-v4"), "classical", "module-lwe-v4-tier-6", 118),
        (("ml-kem", "compressed-research-v4"), "quantum", "module-lwe-v4-tier-6", 107),
        (("frodokem", "anchor-research-v3"), "quantum", "frodo-lwe-v3-tier-80", 123.0),
    ]


def test_quantum_models_use_distinct_units_and_assumptions_not_a_universal_bit_conversion():
    module = security_references.for_release("ml-kem", "compressed-research-v4")["metrics"]
    frodo = security_references.for_release("frodokem", "anchor-research-v3")["metrics"]
    classical, quantum = (module[key]["models"][0] for key in ("classical", "quantum"))
    frodo_quantum = frodo["quantum"]["models"][0]
    assert len({classical["id"], quantum["id"], frodo_quantum["id"]}) == 3
    assert classical["unit"] == quantum["unit"] == "log2 Core-SVP work"
    assert frodo_quantum["unit"] == "log2 modeled lattice work"
    assert "Grover-sieving" in " ".join(quantum["assumptions"])
    assert "random-walk" in " ".join(frodo_quantum["assumptions"])
    assert "Gaussian-LWE" in " ".join(frodo_quantum["assumptions"])
    assert "not a current exhaustive best-attack estimate" in " ".join(quantum["limitations"])
    assert "not a quantum IND-CCA bound" in " ".join(frodo_quantum["limitations"])
    assert "no value is extrapolated" in " ".join(classical["limitations"])


@pytest.mark.parametrize("identity", CURRENT[:3])
def test_shor_is_qualitative_and_never_zero_or_half_the_classical_curve(identity):
    quantum = security_references.for_release(*identity)["metrics"]["quantum"]
    assert quantum["estimates"] == []
    assert "polynomial-time" in quantum["explanation"]
    assert "does not mean zero cost" in quantum["explanation"]
    assert quantum["models"][0]["sources"][0]["url"].endswith("quant-ph/9508027v2")
    assert "halving" in " ".join(quantum["models"][0]["limitations"])


def test_research_reassessment_notes_do_not_turn_historical_categories_or_prange_into_points():
    hqc = security_references.for_release("hqc", "modeled-research-v3")["metrics"]["classical"]
    mceliece = security_references.for_release("classic-mceliece", "length-research-v1")["metrics"]["classical"]
    assert not hqc["estimates"] and not mceliece["estimates"]
    assert "2025" in hqc["explanation"] and "2026" in hqc["explanation"]
    assert "not plotted as security bits" in mceliece["explanation"]
    assert any("1984" in source["url"] for source in mceliece["models"][0]["sources"])


def test_catalogue_addition_leaves_frozen_manifests_intact_and_returns_defensive_copies():
    adapter = registry.ADAPTERS[("ml-kem", "compressed-research-v4")]
    before = deepcopy(adapter.manifests())
    descriptor = registry.descriptor(adapter)
    assert descriptor["security_references"] == security_references.for_release("ml-kem", adapter.SCHEME_VERSION)
    assert adapter.manifests() == before
    assert all("security_references" not in manifest for manifest in before)
    descriptor["security_references"]["metrics"]["classical"]["models"][0]["sources"].clear()
    descriptor["security_references"]["metrics"]["quantum"]["estimates"][0]["bits"] = 0
    fresh = security_references.for_release("ml-kem", adapter.SCHEME_VERSION)
    assert fresh["metrics"]["classical"]["models"][0]["sources"]
    assert fresh["metrics"]["quantum"]["estimates"][0]["bits"] == 107
