"""A visible evidence binding must resolve to its exact note and source data."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from crucible import benchmark_reports, display_notes, registry
from scripts.build_display_notes import source_snapshot
from test_api import system


@pytest.mark.parametrize("scheme_id,version", registry.CURRENT.items())
def test_every_current_release_is_backed_by_an_exact_note_and_current_snapshot(scheme_id, version):
    adapter = registry.adapter_for(scheme_id, version)
    binding = display_notes.for_release(scheme_id, version)
    document = display_notes.document(binding)
    evidence = display_notes.evidence(binding)
    assert document["content_sha256"] == binding["content_sha256"]
    assert display_notes.digest(document["content"]) == binding["content_sha256"]
    assert document["review_status"] == "editorial-inclusion-not-certification"
    assert evidence["evidence"] == source_snapshot(adapter)
    assert display_notes.digest(evidence["evidence"]) == evidence["evidence_sha256"]
    assert registry.descriptor(adapter)["evidence_note"] == binding
    report = benchmark_reports.report_for(adapter)
    assert display_notes.for_report(scheme_id, version, report)["content_sha256"] == binding["content_sha256"]
    changed = deepcopy(report)
    changed["id"] = "a different report"
    assert display_notes.for_report(scheme_id, version, changed) is None
    assert display_notes.for_report(scheme_id, version, None) is None


def test_source_artifacts_are_reachable_only_through_the_bound_note():
    dh = display_notes.for_release("teaching-dh-kem", "7")
    rsa = display_notes.for_release("rsa-kem", "4")
    other = display_notes.evidence(rsa)["evidence_sha256"]
    assert display_notes.evidence(dh, evidence_sha256=other) is None
    assert display_notes.evidence(dh, evidence_sha256="../../anything") is None
    assert display_notes.evidence(dh, evidence_sha256="0" * 64) is None
    assert display_notes.document(dh, sha256="0" * 64) is None
    assert display_notes.evidence(dh, revision=999) is None
    assert display_notes.for_release("teaching-dh-kem", "6") is None


def test_current_proposal_advice_has_a_pinned_note_and_plain_guide_snapshot():
    binding = display_notes.proposal_guide()
    note = display_notes.document(binding)
    evidence = display_notes.evidence(binding)
    assert note and evidence
    assert "2^-128" in note["content"]["body"]
    assert "proposal" in json.dumps(evidence["evidence"]).lower()


def test_read_only_evidence_api_resolves_pins_and_rejects_wrong_artifacts(system):
    app, _, _ = system
    client = TestClient(app)
    binding = display_notes.for_release("ml-kem", "compressed-research-v4")
    response = client.get(binding["api_url"])
    assert response.status_code == 200
    note = response.json()["note"]
    assert note["content_sha256"] == binding["content_sha256"]
    data_url = note["content"]["extensions"]["display_evidence"]["data_url"]
    response = client.get(data_url)
    assert response.status_code == 200
    assert response.json()["supporting_note"]["content_sha256"] == binding["content_sha256"]
    params = {"scheme_id": "ml-kem", "version": "compressed-research-v4", "data": "true", "evidence_sha256": "0" * 64}
    assert client.get("/api/protocol/evidence-note", params=params).status_code == 404
    guide = client.get("/api/protocol/proposal-guide?note=true").json()["note"]
    data_url = guide["content"]["extensions"]["display_evidence"]["data_url"]
    assert client.get(data_url).status_code == 200
