"""An isolated notebook does not advertise unavailable bundled-note sources."""
from fastapi.testclient import TestClient

from crucible import display_notes, registry
from test_api import system
from test_note_sections import local_system


def test_local_catalogue_and_benchmarks_suppress_only_bundled_note_links(local_system, system):
    _, local_owner, _, _, _ = local_system
    normal_app, _, _ = system
    normal = TestClient(normal_app)
    scheme_id, version = "teaching-dh-kem", "7"
    params = {"scheme_id": scheme_id, "version": version}
    binding = display_notes.for_release(scheme_id, version)
    exact = "/api/schemes?projection=release&scheme_id=teaching-dh-kem&version=7"
    before = normal.get(exact).json()["scheme"]
    assert before["evidence_note"] == binding

    for suffix in ("", "?projection=navigation", "?progress_only=true",
                   "?projection=map", "?projection=release&scheme_id=teaching-dh-kem&version=7"):
        response = local_owner.client.get("/api/schemes" + suffix)
        assert response.status_code == 200, response.text
        document = response.json()
        assert "curated:" not in response.text
        if "scheme" in document:
            assert document["scheme"]["evidence_note"] is None
            assert document["scheme"]["profiles"] == before["profiles"]
        for current in document.get("schemes", []):
            for release in (current, *current.get("previous_releases", [])):
                assert release.get("evidence_note") is None

    ordinary = normal.get("/api/protocol/benchmarks", params=params).json()
    isolated = local_owner.client.get("/api/protocol/benchmarks", params=params).json()
    assert ordinary["supporting_note"] == {key: value for key, value in binding.items() if key != "api_url"}
    assert isolated["supporting_note"] is None
    assert isolated["report"] == ordinary["report"]
    assert isolated["method"] == ordinary["method"]
    assert local_owner.client.get("/api/protocol/assumptions", params=params).json()["supporting_note"] is None
    assert local_owner.client.get(binding["api_url"]).status_code == 404

    # Another app and the shared registry keep their original exact bindings.
    assert normal.get(exact).json()["scheme"]["evidence_note"] == binding
    assert registry.descriptor(registry.adapter_for(scheme_id, version))["evidence_note"] == binding
