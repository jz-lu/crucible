"""Compact chart reads preserve evidence identities; fixtures are not measurements."""
from copy import deepcopy
import pytest

from crucible import resource_publications as rp
from test_resource_publications import system, archive, fixture_files, announce, IDENTITY, NOW
from test_resource_state import event, failure_archive, source


URL = "/api/protocol/resources?scheme_id=dhkem-x25519&version=1"
CHARTS = URL + "&projection=charts"
OPERATIONS = ("keygen", "encapsulate", "decapsulate")


def detailed_fixture(identity=IDENTITY):
    files, publication = fixture_files(identity)
    resources = files["resources.json"]
    resources["not_needed_for_graphs"] = {"raw_receipt_reference": "synthetic-only"}
    row = resources["configurations"][0]
    row["communication"] = {"fresh_exchange_bytes": 64, "cached_exchange_bytes": 32,
                            "setup_bytes": 0, "explanation": "Fixture framing"}
    native = row["native_evidence"][0]
    native.update({
        "platform_identified": True,
        "profile": {"environment": {"os": "Linux", "cpu": "Synthetic CPU"},
                    "compiler": {"id": "synthetic-compiler"},
                    "sampling": {"workers": 5, "trials": 20},
                    "method": {"id": "synthetic-method"}},
        "environment": {"os": "Linux", "cpu": "Synthetic CPU"},
        "compiler": {"id": "synthetic-compiler", "flags": ["fixture"]},
        "verification": {"message": "Synthetic test-only evidence"},
        "first_call": {op: {"complete": True,
                             "duration_ns": {"n": 5, "median": 1500, "q1": 1200, "q3": 1800},
                             "recorded": 5} for op in OPERATIONS},
    })
    for op in OPERATIONS:
        native["computation"][op]["duration_ns"].update({"q1": 900, "q3": 1100,
                                                        "min": 800, "max": 1200, "mean": 1001.25})
    native["memory_components"]["keygen:stack"] = {"complete": True, "values": {"n": 5, "max": 99}}
    return files, publication


def read(client, url=CHARTS):
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.json()


def test_chart_values_and_method_context_equal_full_read_without_mutation(system):
    app, client = system
    files, publication = detailed_fixture()
    original = deepcopy(files["resources.json"])
    raw = archive(files, publication)
    receipt = rp.ingest(app.state.db, raw, IDENTITY, NOW)
    full = read(client, URL)
    compact = read(client)
    assert compact["projection"] == "charts" and "projection" not in full
    for key in ("schema_revision", "state", "scheme_id", "version", "expected", "lifecycle",
                "historical", "selected_historical"):
        assert compact[key] == full[key]
    assert set(compact["publication"]) == {"id", "completeness", "outcome", "received_at", "binding", "source"}
    assert compact["publication"] == {key: full["publication"][key] for key in compact["publication"]}
    assert compact["publication"]["id"] == receipt["publication_id"]
    before, after = full["resources"]["configurations"][0], compact["resources"]["configurations"][0]
    assert set(after) == {"configuration", "sizes_bytes", "communication", "native_evidence"}
    assert after["configuration"] == before["configuration"]
    assert after["sizes_bytes"] == {key: before["sizes_bytes"][key] for key in ("public_key", "private_key", "ciphertext")}
    assert after["communication"] == {key: before["communication"][key]
                                       for key in ("fresh_exchange_bytes", "cached_exchange_bytes")}
    old, new = before["native_evidence"][0], after["native_evidence"][0]
    assert set(new) == {"implementation", "comparison_profile", "platform_identified", "computation", "first_call", "memory_components"}
    for key in ("implementation", "comparison_profile", "platform_identified"):
        assert new[key] == old[key]
    for kind in ("computation", "first_call"):
        for op in OPERATIONS:
            assert new[kind][op] == {key: old[kind][op][key] for key in ("complete", "duration_ns")}
    assert set(new["memory_components"]) == {op + ":rss" for op in OPERATIONS}
    for op in OPERATIONS:
        assert new["memory_components"][op + ":rss"] == old["memory_components"][op + ":rss"]
    assert compact["resources"]["measurement_profiles"] == {
        old["comparison_profile"]: {key: old[key] for key in ("profile", "environment", "compiler")}}
    assert read(client, URL) == full, "Chart reads must not rewrite the full evidence view"
    with app.state.db.transaction() as session:
        stored = session.get(rp.ResourcePublication, receipt["publication_id"])
        assert stored.resources == original
    assert client.get(full["publication"]["evidence_url"]).content == raw


def test_profile_context_is_deduplicated_without_losing_exact_integers():
    resources = detailed_fixture()[0]["resources.json"]
    second = deepcopy(resources["configurations"][0])
    second["configuration"] = "f" * 64
    tagged = {"type": "integer", "decimal": "9007199254740993"}
    second["sizes_bytes"]["public_key"] = tagged
    second["native_evidence"][0]["memory_components"]["keygen:rss"]["values"]["max"] = tagged
    resources["configurations"].append(second)
    compact = rp._chart_resources(resources)
    assert len(compact["configurations"]) == 2 and len(compact["measurement_profiles"]) == 1
    assert compact["configurations"][1]["sizes_bytes"]["public_key"] == tagged
    assert compact["configurations"][1]["native_evidence"][0]["memory_components"]["keygen:rss"]["values"]["max"] == tagged
    compact["measurement_profiles"]["c" * 64]["profile"]["sampling"]["workers"] = 99
    assert resources["configurations"][0]["native_evidence"][0]["profile"]["sampling"]["workers"] == 5


def test_distinct_measurement_profiles_remain_distinct_and_missing_fields_stay_missing():
    resources = fixture_files()[0]["resources.json"]
    second = deepcopy(resources["configurations"][0])
    second["configuration"] = "f" * 64
    second["native_evidence"][0]["comparison_profile"] = "d" * 64
    second["native_evidence"][0]["profile"] = {"environment": {"cpu": "Other synthetic CPU"}}
    resources["configurations"].append(second)
    compact = rp._chart_resources(resources)
    assert compact["measurement_profiles"]["c" * 64] == {}
    assert compact["measurement_profiles"]["d" * 64] == {"profile": {"environment": {"cpu": "Other synthetic CPU"}}}
    for row in compact["configurations"]:
        assert "platform_identified" not in row["native_evidence"][0]
        assert "first_call" not in row["native_evidence"][0]


@pytest.mark.parametrize("field", ["profile", "environment", "compiler"])
def test_contradictory_metadata_fails_closed_in_chart_read_only(system, field):
    app, client = system
    receipt = rp.ingest(app.state.db, archive(*detailed_fixture()), IDENTITY, NOW)
    # Deliberately corrupt the stored comparison context, modelling an old
    # inconsistent producer. No actual evidence artifact is changed.
    with app.state.db.transaction() as session:
        stored = session.get(rp.ResourcePublication, receipt["publication_id"])
        data = deepcopy(stored.resources)
        second = deepcopy(data["configurations"][0]); second["configuration"] = "f" * 64
        second["native_evidence"][0][field] = {"contradiction": True}
        data["configurations"].append(second); stored.resources = data
    response = client.get(CHARTS)
    assert response.status_code == 409
    assert response.json() == {"detail": "Resource measurement context is inconsistent."}
    assert len(read(client, URL)["resources"]["configurations"]) == 2


def test_metadata_comparison_distinguishes_json_types():
    resources = detailed_fixture()[0]["resources.json"]
    first = resources["configurations"][0]
    first["native_evidence"][0]["profile"]["sampling"]["workers"] = 1
    second = deepcopy(first)
    second["native_evidence"][0]["profile"]["sampling"]["workers"] = True
    resources["configurations"].append(second)
    with pytest.raises(rp.HTTPException) as error:
        rp._chart_resources(resources)
    assert error.value.status_code == 409


def test_missing_current_historical_and_retired_identities_match_full_reads(system):
    app, client = system
    missing = read(client)
    assert missing["resources"] is None and missing["publication"] is None
    assert missing["state"] == "not_calculated"
    original = rp.ingest(app.state.db, archive(*detailed_fixture()), IDENTITY, NOW)
    newer = source(101)
    latest = rp.ingest(app.state.db, archive(*detailed_fixture(newer)), newer, NOW + 1)
    current = read(client)
    assert current["publication"]["id"] == latest["publication_id"] and not current["selected_historical"]
    assert current["historical"] == read(client, URL)["historical"]
    for selected in (original["publication_id"], latest["publication_id"]):
        compact = read(client, CHARTS + "&publication_id=" + selected)
        full = read(client, URL + "&publication_id=" + selected)
        assert compact["publication"]["id"] == selected
        assert compact["selected_historical"] == full["selected_historical"]
        assert compact["historical"] == full["historical"]
    announce(app.state.db, source(102), producer="e" * 64)
    assert read(client)["state"] == "outdated" and read(client)["resources"] is None
    historical = read(client, CHARTS + "&publication_id=" + original["publication_id"])
    assert historical["selected_historical"] and historical["resources"]["configurations"]
    from crucible import resource_state
    resource_state.ingest_control(app.state.db, {"format": "crucible-resource-desired-v1", "source": source(103), "bindings": []},
                                  source(103), NOW, rp.validate_binding)
    retired = read(client)
    assert retired["state"] == "retired" and retired["expected"] is None


def test_lifecycle_and_incomplete_selected_report_stay_explicit(system):
    app, client = system
    event(app.state.db)
    assert read(client)["lifecycle"] == read(client, URL)["lifecycle"]
    failed = rp.ingest(app.state.db, failure_archive(), IDENTITY, NOW)
    compact = read(client)
    assert compact["lifecycle"] == read(client, URL)["lifecycle"]
    assert compact["state"] == "failed" and compact["resources"] is None
    selected = read(client, CHARTS + "&publication_id=" + failed["publication_id"])
    assert selected["publication"]["completeness"] == "incomplete"
    assert selected["selected_historical"]
    assert selected["resources"] == {"configurations": [], "measurement_profiles": {}}


@pytest.mark.parametrize("value", ["", "full", "unknown", "Charts"])
def test_unknown_projection_is_rejected(system, value):
    _, client = system
    assert client.get(URL + "&projection=" + value).status_code == 400


def test_compact_view_preserves_private_access_and_publication_ownership(system):
    app, client = system
    assert client.get(CHARTS + "&publication_id=" + "f" * 64).status_code == 404
    object.__setattr__(app.state.settings, "access_mode", "beta")
    for path in (URL, CHARTS, CHARTS + "&publication_id=" + "f" * 64):
        assert client.get(path).status_code == 401


def test_compact_response_compresses_normally_and_full_default_is_unchanged(system):
    app, client = system
    rp.ingest(app.state.db, archive(*detailed_fixture()), IDENTITY, NOW)
    compact = client.get(CHARTS, headers={"Accept-Encoding": "identity"})
    zipped = client.get(CHARTS, headers={"Accept-Encoding": "gzip"})
    full = client.get(URL, headers={"Accept-Encoding": "identity"})
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert zipped.json() == compact.json()
    assert len(compact.content) < len(full.content)
    assert "parameters" in full.json()["resources"]["configurations"][0]
    assert "stack_evidence" in full.json()["resources"]["configurations"][0]
    assert "memory_evidence" in full.json()["resources"]["configurations"][0]
    assert "expected" in full.json()["publication"]
    assert "projection" not in full.json()
