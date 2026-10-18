"""A common HQC claim must not erase configuration-specific certificates."""
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from crucible import registry
from crucible.protocol_specs import builtin_spec
from test_calibrated_api import current_system


def test_every_current_release_has_a_downloadable_spec_without_generation(current_system):
    client = TestClient(current_system[0])
    for scheme_id, version in registry.CURRENT.items():
        adapter = registry.adapter_for(scheme_id)
        with patch.object(adapter, "generate", side_effect=AssertionError("Export must not generate")):
            response = client.get("/api/protocol/spec", params={"scheme_id": scheme_id})
            assert response.status_code == 200, (scheme_id, response.text)
            assert response.json() == builtin_spec(scheme_id, version)
            assert response.json()["version"] == version


def test_hqc_spec_exports_common_claim_without_rewriting_any_configuration(current_system):
    adapter = registry.code_hqc_v3
    before = deepcopy(adapter.manifests())
    assert len({p["correctness"]["model_log2_bound_display"] for p in before}) > 1
    spec = builtin_spec("hqc", adapter.SCHEME_VERSION)
    claim = spec["correctness"]
    assert claim["kind"] == "statistical" and claim["failure_bound_exponent"] == 128
    assert claim["failure_bound"] == before[0]["correctness"]["failure_bound"]
    assert claim["argument"] == before[0]["correctness"]["argument"]
    assert before[0]["correctness"]["scope"] in claim["scope"]
    assert before[0]["correctness"]["assumptions"] in claim["scope"]
    assert "conjectural" in claim["argument"]
    assert "model_bound_exponent" in claim["evidence"]
    assert "correctness_certificate" in claim["evidence"]
    assert "justification" not in claim  # A translation creates no reviewed evidence.
    assert spec["parameters"]["supported_tiers"]["values"] == list(range(1, 67))
    assert adapter.manifests() == before
    catalogue = TestClient(current_system[0]).get("/api/schemes").json()["schemes"]
    published = next(s for s in catalogue if s["id"] == "hqc")["profiles"]
    assert {p["id"]: p["correctness"] for p in published} == {p["id"]: p["correctness"] for p in before}


@pytest.mark.parametrize("field", ["kind", "failure_bound", "failure_bound_exponent", "scope",
                                  "assumptions", "argument", "model_id", "evidence_status"])
def test_hqc_export_rejects_any_changed_shared_claim(monkeypatch, field):
    adapter = registry.code_hqc_v3
    profiles = deepcopy(adapter.manifests())
    profiles[-1]["correctness"][field] = "Different shared premise or claim"
    monkeypatch.setattr(adapter, "manifests", lambda: profiles)
    with pytest.raises(ValueError, match="common correctness statement"):
        builtin_spec("hqc", adapter.SCHEME_VERSION)


def test_hqc_export_rejects_missing_profile_certificate(monkeypatch):
    adapter = registry.code_hqc_v3
    profiles = deepcopy(adapter.manifests())
    del profiles[-1]["correctness"]["model_log2_bound_display"]
    monkeypatch.setattr(adapter, "manifests", lambda: profiles)
    with pytest.raises(ValueError, match="lacks its correctness certificate"):
        builtin_spec("hqc", adapter.SCHEME_VERSION)
