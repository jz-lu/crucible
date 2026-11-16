"""Scientific identity and human-visible premise checks for the RAM catalogue."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from crucible import registry
from crucible.attack_models import catalogue, common


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COUNTS = {
    "teaching-dh-kem@7": 50,
    "rsa-kem@4": 50,
    "dhkem-x25519@curve-compressed-v2": 50,
    "ml-kem@compressed-research-v4": 6,
    "frodokem@anchor-research-v3": 80,
    "sntrup@3": 27,
    "hqc@modeled-research-v3": 66,
    "classic-mceliece@length-research-v1": 50,
}


@pytest.fixture(scope="module")
def artifact():
    return json.loads(catalogue.PATH.read_text())


@pytest.fixture(scope="module")
def notes():
    return json.loads((ROOT / "crucible/data/scheme_note_presentations.json").read_text())["releases"]


@pytest.fixture(scope="module")
def manifests():
    result = {}
    for release in EXPECTED_COUNTS:
        scheme_id, version = release.split("@", 1)
        adapter = registry.adapter_for(scheme_id, version)
        result[release] = [adapter.get_manifest(row["id"])
                           for row in registry.descriptor(adapter)["profiles"]]
    return result


def test_all_eight_releases_cover_their_exact_379_profile_manifests(artifact, manifests):
    assert set(artifact["releases"]) == set(EXPECTED_COUNTS)
    total = 0
    for release, count in EXPECTED_COUNTS.items():
        scheme_id, version = release.split("@", 1)
        row = catalogue.for_release(scheme_id, version, expected_manifests=manifests[release])
        assert len(row["estimates"]) == count
        expected = {p["id"]: catalogue.digest(p) for p in manifests[release]}
        assert {p["profile_id"]: p["manifest_sha256"] for p in row["estimates"]} == expected
        assert row["analysis_sha256"] == catalogue.digest({k:v for k,v in row.items() if k != "analysis_sha256"})
        total += len(row["estimates"])
    assert total == 379


def test_every_cost_preserves_integer_account_and_normalized_success(artifact):
    for row in artifact["releases"].values():
        assert row["computation_model_sha256"] == catalogue.digest(common.MODEL)
        models = {m["id"] for m in row["models"]}
        for point in row["estimates"]:
            assert point["model_id"] in models
            assert point["computation_model_id"] == common.MODEL_ID
            work, memory = int(point["work_bound"]), int(point["memory_bits"])
            assert work > 0 and memory > 0
            assert sum(int(x) for x in point["breakdown"].values()) == work
            assert point["bits"] == common.log2_int(work)
            assert point["memory_log2_bits"] == common.log2_int(memory)
            assert point["success_lower_bound"] == "1/2"
            assert point["analysis_success_statement"]
            assert point["success_status"]


def test_sources_are_exact_and_only_the_designated_accounting_files(artifact):
    expected = {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in catalogue.SOURCE_PATHS}
    assert artifact["source_sha256"] == expected
    assert artifact["computation_model"] == common.MODEL
    for row in artifact["releases"].values():
        assert row["source_sha256"] == expected


def test_every_attack_premise_is_a_visible_canonical_statement_with_exact_scope(artifact, notes):
    for release, row in artifact["releases"].items():
        note = notes[release]
        assert note["ram_analysis_sha256"] == row["analysis_sha256"]
        sections = {s["id"]:s for s in note["readable_sections"]}
        authored_sections = {s["id"]:s for s in note["sections"]}
        authored_premises = {a["id"]:a for a in note["assumptions"]}
        premises = {a["id"]:a for a in note["statements"]}
        arguments = {a["id"]:a for a in note["arguments"]}
        for point in row["estimates"]:
            graph = note["graph_models"][point["model_id"]]
            assert graph["metric"] == "ram"
            specification = sections[graph["section_id"]]
            assert specification["body"] and specification["title"]
            argument = arguments[graph["argument_id"]]
            active = {use["statement"]["statement_id"] for use in argument["dependencies"]
                      if point["profile_id"] in use["configuration_ids"]}
            wanted = {"ram-"+a["id"] for a in point["assumptions"]}
            assert active == wanted
            for raw in point["assumptions"]:
                premise = premises["ram-"+raw["id"]]
                assert premise["label"].startswith("A-")
                assert premise["definition"] and premise["title"]
                authored = authored_premises[premise["id"]]
                assert premise["definition"] == (authored.get("statement") or authored_sections[authored["section_id"]]["body"])
                # Full technical material must exist, but is not the only
                # location where a premise is disclosed to the participant.
                assert raw["technical"] in note["full_text_appendix"]
        assert "not the cheapest known attacks" in sections["ram-model"]["body"]
        assert "python scripts/build_ram_attack_notes.py --check" in note["full_text_appendix"]


def test_compact_presentation_avoids_proof_bodies_and_is_independently_mutable(artifact):
    row = catalogue.for_release("rsa-kem", "4")
    view = catalogue.presentation(row)
    assert "not best-known attack costs" in view["explanation"]
    assert len(json.dumps(view)) < 100_000
    assert all("technical" not in p and "breakdown" not in p and "human" not in p for p in view["estimates"])
    row["estimates"][0]["bits"] = -1
    assert catalogue.for_release("rsa-kem", "4")["estimates"][0]["bits"] > 0


def test_module_readable_account_is_parameterized_and_discloses_rounding_scope(notes):
    note = notes["ml-kem@compressed-research-v4"]
    sections = {s["id"]:s for s in note["readable_sections"]}
    model = next(value for value in note["graph_models"].values() if value["metric"] == "ram")
    human = sections[model["section_id"]]["body"]
    assert "2^n" in human and "degree" in human
    assert "256-bit native message" not in human
    premise = next(a for a in note["statements"] if a["id"] == "ram-module-v4-message-correctness")
    visible = premise["definition"]
    assert "1/8" in visible and "actual" in visible
    assert "rounding" in visible
    assert "32/64" in visible and "128/256" in visible
    assert "unproved" in visible


def _write_tampered(tmp_path, artifact, mutate, *, reseal=True):
    value = deepcopy(artifact)
    row = value["releases"]["rsa-kem@4"]
    mutate(value, row)
    if reseal:
        row["analysis_sha256"] = catalogue.digest({k:v for k,v in row.items() if k != "analysis_sha256"})
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize("mutate", [
    lambda data,row: data["computation_model"].update(memory="All memory operations are free"),
    lambda data,row: row.update(computation_model_sha256="0"*64),
    lambda data,row: row["models"][0].update(unit="CPU seconds"),
    lambda data,row: row["models"][0].update(computation_model_id="different-model"),
    lambda data,row: row["estimates"][0].update(model_id="unknown-attack"),
    lambda data,row: row["estimates"].append(deepcopy(row["estimates"][0])),
    lambda data,row: row["estimates"][0].update(bits=row["estimates"][0]["bits"]+1),
    lambda data,row: row["estimates"][0].update(memory_log2_bits=-1),
    lambda data,row: row["estimates"][0].update(success_lower_bound="1/100"),
    lambda data,row: row["estimates"][0]["breakdown"].update(unaccounted_extra=1),
    lambda data,row: data["source_sha256"].update({"../../unknown-file":"0"*64}),
    lambda data,row: data["source_sha256"].update({"crucible/attack_models/common.py":"0"*64}),
])
def test_resealed_but_incompatible_account_is_rejected(tmp_path, artifact, mutate):
    path = _write_tampered(tmp_path, artifact, mutate)
    with pytest.raises(ValueError):
        catalogue.for_release("rsa-kem", "4", path=path)


@pytest.mark.parametrize("mutate", [
    lambda data,row: row["estimates"][0].update(profile_id="unregistered-profile"),
    lambda data,row: row["estimates"][0].update(manifest_sha256="0"*64),
    lambda data,row: row["estimates"].pop(),
])
def test_registered_profile_identity_is_enforced_even_after_resealing(tmp_path, artifact, manifests, mutate):
    path = _write_tampered(tmp_path, artifact, mutate)
    with pytest.raises(ValueError):
        catalogue.for_release("rsa-kem", "4", path=path, expected_manifests=manifests["rsa-kem@4"])


def test_unsealed_mutation_and_cross_release_manifest_are_rejected(tmp_path, artifact, manifests):
    path = _write_tampered(tmp_path, artifact, lambda data,row: row.update(version="other"), reseal=False)
    with pytest.raises(ValueError):
        catalogue.for_release("rsa-kem", "4", path=path)
    with pytest.raises(ValueError):
        catalogue.for_release("rsa-kem", "4", expected_manifests=manifests["teaching-dh-kem@7"])


def test_absent_evidence_is_not_fabricated(tmp_path):
    assert catalogue.for_release("rsa-kem", "4", path=tmp_path/"missing.json") is None
    assert catalogue.for_release("rsa-kem", "unaccounted") is None
