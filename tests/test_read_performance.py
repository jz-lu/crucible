"""Avoid repeated read work without stale scores or relaxed artifact checks."""
from copy import deepcopy
import gzip
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from crucible import milestones, registry, scoring
from crucible.public_json import accepts_gzip, catalogue_response
from test_calibrated_api import current_system
from test_people import add_user, person, registered_receipt


def test_compact_people_preserve_ranking_and_real_components_without_unused_queries(current_system):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    registered_receipt(app, alice, registry.code_hqc_v3, 3)
    registered_receipt(app, bob, registry.code_hqc_v3, 1, 1)
    client = TestClient(app)
    full = client.get("/api/people").json()
    queries = []
    def capture(_, __, statement, ___, ____, _____):
        if statement.lstrip().lower().startswith(("select", "with")):
            queries.append(statement)
    event.listen(app.state.db.engine, "before_cursor_execute", capture)
    try:
        compact = client.get("/api/people?include_records=false").json()
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", capture)
    assert len(queries) == 2  # Account count + scored page; no summary queries.
    assert compact["pagination"] == full["pagination"] and compact["scoring"] == full["scoring"]
    omitted = {"records", "correct_submission_count", "schemes_recovered"}
    assert compact["people"] == [{k: v for k, v in row.items() if k not in omitted} for row in full["people"]]
    assert all(not omitted.intersection(row) for row in compact["people"])
    # These are live aggregates, not a cached scoreboard.
    registered_receipt(app, bob, registry.code_hqc_v3, 5, 2)
    updated = client.get("/api/people?include_records=false").json()["people"]
    assert updated[0]["handle"] == "bob" and updated[0]["points"] == 185


def test_modern_profile_skips_hidden_milestones_and_reuses_family_score_query(current_system, monkeypatch):
    app, _, _ = current_system
    alice, bob = (add_user(app, handle) for handle in ("alice", "bob"))
    first = registered_receipt(app, alice, registry.code_hqc_v3, 1, 0)
    registered_receipt(app, bob, registry.code_hqc_v3, 3, 1)
    registered_receipt(app, alice, registry.code_hqc_v3, 3, 2)
    baseline = person(app, "alice")
    calls = []
    original = scoring.score_relations
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(scoring, "score_relations", counted)
    monkeypatch.setattr(milestones, "summary", lambda *_: pytest.fail("Hidden milestones must not be computed"))
    response = TestClient(app).get("/api/people/alice?include_milestones=false")
    assert response.status_code == 200, response.text
    compact = response.json()["person"]
    assert compact == {k: v for k, v in baseline.items() if k != "first_ten"}
    assert len(calls) == 2  # Ranked participant + family breakdown, never a third for first_records.
    assert compact["first_records"][0]["submission_id"] == first["submission_id"]
    # Dedicated legacy history remains available; the projection does not erase it.
    assert TestClient(app).get("/api/people/alice/milestones").status_code == 200


@pytest.mark.parametrize("path", ["/api/people?include_records=bad", "/api/people/alice?include_milestones=bad"])
def test_compact_flags_validate_as_booleans(current_system, path):
    assert TestClient(current_system[0]).get(path).status_code == 422


def test_presentation_cache_copies_values_and_invalidates_replaced_provider(monkeypatch):
    adapter = registry.adapter_for("sntrup")
    original = adapter.manifests
    calls = []
    def first_provider():
        calls.append("first")
        return original()
    monkeypatch.setattr(adapter, "manifests", first_provider)
    first = registry.presentation_manifests(adapter)
    expected = deepcopy(first)
    first[0]["parameters"]["p"] = -1
    second = registry.presentation_manifests(adapter)
    assert second == expected and calls == ["first"]
    first_rows = milestones._profile_rows(registry.metadata_identity(), False)
    assert milestones._profile_rows(registry.metadata_identity(), False) is first_rows
    assert calls == ["first"]
    def second_provider():
        calls.append("second")
        result = original()
        result[0]["profile_label"] = "Changed display provider"
        return result
    monkeypatch.setattr(adapter, "manifests", second_provider)
    second_rows = milestones._profile_rows(registry.metadata_identity(), False)
    assert calls == ["first", "second"]
    assert second_rows != first_rows
    assert registry.presentation_manifests(adapter)[0]["profile_label"] == "Changed display provider"


def test_cached_display_never_bypasses_generation_artifact_verification(monkeypatch):
    adapter = registry.adapter_for("sntrup")
    frozen = registry.presentation_manifests(adapter)
    def unavailable():
        raise adapter.NativeImplementationError("Fixture artifact mismatch")
    monkeypatch.setattr(adapter, "verify_artifact", unavailable)
    assert registry.presentation_manifests(adapter) == frozen
    with pytest.raises(adapter.NativeImplementationError, match="Fixture artifact mismatch"):
        adapter.generate(frozen[0]["id"])
    # The exact manifest/artifact API also remains live, not the display cache.
    with pytest.raises(adapter.NativeImplementationError):
        adapter.get_manifest(frozen[0]["id"])


@pytest.mark.parametrize("header,expected", [
    (None, False), ("", False), ("identity", False), ("gzip", True),
    ("br, GZip; q=0.5", True), ("*;q=1", True), ("gzip;q=0", False),
    ("gzip;q=0, *;q=1", False), ("gzip;q=0, gzip;q=1", False),
    ("gzip;q=0.1, *;q=0", True), ("gzip;q=nan", False),
    ("gzip;q=invalid", False), ("gzip;q=-1", False), ("gzip;q=2", False),
])
def test_public_gzip_negotiation_respects_explicit_prohibitions(header, expected):
    assert accepts_gzip(header) is expected


def test_public_compression_preserves_json_and_content_lengths():
    document = {"schemes": [{"name": "Crucible — fixture", "text": "a" * 4096}]}
    plain = catalogue_response(document, None)
    encoded = catalogue_response(document, "gzip")
    assert "content-encoding" not in plain.headers
    assert encoded.headers["content-encoding"] == "gzip"
    assert gzip.decompress(encoded.body) == plain.body
    assert json.loads(plain.body) == document
    assert int(encoded.headers["content-length"]) == len(encoded.body)
    assert int(plain.headers["content-length"]) == len(plain.body)
    assert encoded.headers["vary"] == plain.headers["vary"] == "Accept-Encoding"
    assert encoded.headers["content-type"] == plain.headers["content-type"] == "application/json"


def test_catalogue_api_compression_keeps_shape_no_store_and_security_headers(current_system):
    client = TestClient(current_system[0])
    plain = client.get("/api/schemes", headers={"Accept-Encoding": "identity"})
    compressed = client.get("/api/schemes", headers={"Accept-Encoding": "gzip"})
    forbidden = client.get("/api/schemes", headers={"Accept-Encoding": "gzip;q=0,*;q=1"})
    assert plain.status_code == compressed.status_code == forbidden.status_code == 200
    assert plain.json() == compressed.json() == forbidden.json()
    assert "content-encoding" not in plain.headers and "content-encoding" not in forbidden.headers
    assert compressed.headers["content-encoding"] == "gzip"
    assert int(compressed.headers["content-length"]) < int(plain.headers["content-length"]) / 5
    for response in (plain, compressed, forbidden):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["vary"] == "Accept-Encoding"
    # Compression is scoped to the public catalogue, not credential/account data.
    session = client.get("/api/session", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in session.headers


def test_catalogue_cached_metadata_does_not_cache_dynamic_recoveries(current_system):
    app, _, _ = current_system
    client = TestClient(app)
    before = {row["id"]: row for row in client.get("/api/schemes").json()["schemes"]}
    user = add_user(app, "alice")
    actual = registered_receipt(app, user, registry.code_hqc_v2, 3)
    after = {row["id"]: row for row in client.get("/api/schemes").json()["schemes"]}
    assert before["hqc"]["participant_count"] == after["hqc"]["participant_count"] == 0
    archived = next(row for row in after["hqc"]["previous_releases"]
                    if row["version"] == registry.code_hqc_v2.SCHEME_VERSION)
    assert archived["participant_count"] == 1
    assert archived["first_recovery"]["submission_id"] == actual["submission_id"]
    current = registered_receipt(app, user, registry.code_hqc_v3, 3, 1)
    latest = next(row for row in client.get("/api/schemes").json()["schemes"] if row["id"] == "hqc")
    assert latest["participant_count"] == 1
    assert latest["first_recovery"]["submission_id"] == current["submission_id"]
