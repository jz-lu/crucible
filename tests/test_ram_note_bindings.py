"""RAM curves require readable exact revisions and matching analysis identity."""
from copy import deepcopy
import json

import pytest

from crucible import registry
from crucible.attack_models import catalogue
from test_note_sections import local_system, content, publish, pinned, binding_url
from test_notebook import create, revise


def ram_content(scheme_id="rsa-kem", version=None):
    version = version or registry.CURRENT[scheme_id]
    evidence = catalogue.for_release(scheme_id, version)
    assert evidence is not None
    value = content()
    value["extensions"] = {
        "scheme_context": {"scheme_id": scheme_id, "version": version},
        "assumptions": [],
        "ram_analysis_sha256": evidence["analysis_sha256"],
    }
    for model in evidence["models"]:
        value["sections"].append({"id": model["id"], "title": model["label"],
                                  "body": "Exact attack account for this fixture."})
    return value, evidence


def ram_binding(note, evidence):
    return {"format": "crucible-local-note-bindings-v1", "releases": [{
        "scheme_id": evidence["scheme_id"], "version": evidence["version"],
        "note": {key: note[key] for key in ("id", "revision", "content_sha256")},
        "sections": {"security": "security", "correctness": "correctness"},
        "graph_models": {model["id"]: {
            "section_id": model["id"], "metric": "ram", "kind": "attack-upper-bound",
            "assumption_ids": [],
        } for model in evidence["models"]},
    }]}


def test_published_exact_note_authorizes_matching_complete_ram_curve(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(ram_binding(note, evidence)))
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["prototype"] is True and result["scientific_adoption"] is False
    assert result["attack_comparison"]["analysis_sha256"] == evidence["analysis_sha256"]
    assert result["attack_comparison"]["computation_model_id"] == "crucible-bit-ram-v1"
    assert {point["profile_id"] for point in result["attack_comparison"]["estimates"]} == {
        point["profile_id"] for point in evidence["estimates"]}
    assert all(point["success_lower_bound"] == "1/2"
               for point in result["attack_comparison"]["estimates"])
    for model_id, model in result["graph_models"].items():
        assert model["metric"] == "ram"
        assert model["source"]["id"] == note["id"]
        assert model["source"]["revision"] == note["revision"]
        assert model["source"]["content_sha256"] == note["content_sha256"]
        assert model["source"]["note_section"] == model_id
    assert "technical" not in result["attack_comparison"]["estimates"][0]


def test_private_note_never_leaks_its_ram_curve_to_another_reader(local_system):
    _, owner, _, reader, path = local_system
    value, evidence = ram_content()
    note = create(owner, value)
    path.write_text(json.dumps(ram_binding(note, evidence)))
    assert reader.client.get(binding_url()).status_code == 404
    assert reader.client.get(pinned(note, "attack")).status_code == 404
    # Authors can inspect their own unpublished local research binding.
    assert owner.client.get(binding_url()).status_code == 200


@pytest.mark.parametrize("analysis_hash", [None, "0" * 64, "not-a-hash"])
def test_missing_or_incorrect_analysis_digest_fails_closed(local_system, analysis_hash):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    if analysis_hash is None:
        value["extensions"].pop("ram_analysis_sha256")
    else:
        value["extensions"]["ram_analysis_sha256"] = analysis_hash
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(ram_binding(note, evidence)))
    response = reader.client.get(binding_url())
    assert response.status_code == 404
    assert "attack_comparison" not in response.json()


def test_wrong_note_digest_does_not_fall_back_to_matching_analysis(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    note = create(owner, value)
    publish(owner, reviewer, note)
    manifest = ram_binding(note, evidence)
    manifest["releases"][0]["note"]["content_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404


def test_later_private_revision_never_silently_replaces_a_published_pin(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    original = create(owner, value)
    publish(owner, reviewer, original)
    manifest = ram_binding(original, evidence)
    path.write_text(json.dumps(manifest))
    edited = deepcopy(value)
    edited["sections"][-1]["body"] += " Private later interpretation."
    latest = revise(owner, original, edited)
    response = reader.client.get(binding_url())
    assert response.status_code == 200
    assert response.json()["note"]["revision"] == original["revision"]
    assert response.json()["note"]["content_sha256"] == original["content_sha256"]
    assert reader.client.get(pinned(latest, "attack")).status_code == 404
    # Combining a later revision number with the old digest is not a pin.
    manifest["releases"][0]["note"]["revision"] = latest["revision"]
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404
    # A coherent pin still does not grant access to a private revision.
    path.write_text(json.dumps(ram_binding(latest, evidence)))
    assert reader.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, latest)
    response = reader.client.get(binding_url())
    assert response.status_code == 200
    assert response.json()["note"]["revision"] == latest["revision"]


def test_updated_analysis_cannot_reuse_an_old_note_pin(local_system, monkeypatch):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(ram_binding(note, evidence)))
    changed = deepcopy(evidence)
    changed["analysis_sha256"] = "f" * 64
    monkeypatch.setattr(catalogue, "for_release", lambda *args, **kwargs: changed)
    assert reader.client.get(binding_url()).status_code == 404


def test_old_release_cannot_borrow_current_ram_evidence(local_system):
    _, owner, reviewer, reader, path = local_system
    old_versions = [version for scheme, version in registry.ADAPTERS
                    if scheme == "rsa-kem" and version != registry.CURRENT["rsa-kem"]]
    assert old_versions
    version = old_versions[0]
    assert catalogue.for_release("rsa-kem", version) is None
    value, evidence = ram_content()
    value["extensions"]["scheme_context"]["version"] = version
    note = create(owner, value)
    publish(owner, reviewer, note)
    manifest = ram_binding(note, evidence)
    manifest["releases"][0]["version"] = version
    path.write_text(json.dumps(manifest))
    response = reader.client.get(f"/api/protocol/note-justifications?scheme_id=rsa-kem&version={version}")
    assert response.status_code == 404


def test_conditional_ram_curve_cannot_hide_its_required_premise(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content("classic-mceliece")
    assert evidence["estimates"][0]["assumptions"]
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(ram_binding(note, evidence)))
    # Pinning the right computation is not enough if its readable premises
    # were dropped from the note and graph bindings.
    response = reader.client.get(binding_url("classic-mceliece"))
    assert response.status_code == 404


def authored_premises(value, evidence):
    descriptor = registry.descriptor(registry.adapter_for(evidence["scheme_id"], evidence["version"]))
    stage_by_profile = {p["id"]: p["stage_number"] for p in descriptor["profiles"]}
    scopes = {}
    records = {}
    for point in evidence["estimates"]:
        for premise in point["assumptions"]:
            identifier = "ram-" + premise["id"]
            scopes.setdefault(identifier, set()).add(point["profile_id"])
            records[identifier] = premise
    for index, (identifier, profiles) in enumerate(scopes.items(), 1):
        premise = records[identifier]
        value["sections"].append({"id": identifier, "title": premise["title"], "body": premise["human"]})
        value["extensions"]["assumptions"].append({
            "id": identifier, "label": f"A-{index}", "version": 1,
            "section_id": identifier, "status": "unproved",
            "profile_ids": sorted(profiles),
            "stages": sorted({stage_by_profile[p] for p in profiles}),
        })
    return value


def binding_with_premises(note, evidence):
    manifest = ram_binding(note, evidence)
    for identifier, graph in manifest["releases"][0]["graph_models"].items():
        graph["assumption_ids"] = sorted({"ram-" + a["id"]
            for point in evidence["estimates"] if point["model_id"] == identifier
            for a in point["assumptions"]})
    return manifest


def test_conditional_curve_exposes_exact_authored_premise_scopes(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content("classic-mceliece")
    authored_premises(value, evidence)
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(binding_with_premises(note, evidence)))
    response = reader.client.get(binding_url("classic-mceliece"))
    assert response.status_code == 200, response.text
    assumptions = next(iter(response.json()["graph_models"].values()))["assumptions"]
    assert assumptions[0]["id"] == "ram-prange-structured-rank-density-v1"
    assert set(assumptions[0]["profile_ids"]) == {p["profile_id"] for p in evidence["estimates"]}


@pytest.mark.parametrize("scope_field", ["profile_ids", "stages"])
def test_conditional_curve_cannot_hide_premise_on_a_required_stage(local_system, scope_field):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content("classic-mceliece")
    authored_premises(value, evidence)
    value["extensions"]["assumptions"][0][scope_field].pop(0)
    note = create(owner, value)
    publish(owner, reviewer, note)
    path.write_text(json.dumps(binding_with_premises(note, evidence)))
    assert reader.client.get(binding_url("classic-mceliece")).status_code == 404


def test_ram_attack_specification_cannot_be_rebound_to_another_section(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = ram_content()
    note = create(owner, value)
    publish(owner, reviewer, note)
    manifest = ram_binding(note, evidence)
    next(iter(manifest["releases"][0]["graph_models"].values()))["section_id"] = "security"
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404


def canonical_ram_content():
    value, evidence = ram_content("classic-mceliece")
    authored_premises(value, evidence)
    sections = {item["id"]: item for item in value["sections"]}
    value["statements"] = [{"id": premise["id"], "label": premise["label"],
        "title": sections[premise["section_id"]]["title"], "definition": sections[premise["section_id"]]["body"]}
        for premise in value["extensions"]["assumptions"]]
    value["arguments"] = [{"id": "scheme-" + role, "title": role.title() + " argument",
        "conclusion": "This fixture makes no claim for the scheme itself.", "scope": "Attack bindings are tested independently.",
        "status": "incomplete", "section_id": role, "dependencies": []} for role in ("security", "correctness")]
    for model in evidence["models"]:
        scopes = {}
        for point in evidence["estimates"]:
            if point["model_id"] == model["id"]:
                for premise in point["assumptions"]:
                    scopes.setdefault("ram-" + premise["id"], set()).add(point["profile_id"])
        value["arguments"].append({"id": model["id"], "title": model["label"],
            "conclusion": "The named algorithm has the pinned conditional work account.", "scope": model["scope"],
            "status": "conditional" if scopes else "incomplete", "section_id": model["id"],
            "dependencies": [{"statement": {"statement_id": identity}, "configuration_ids": sorted(profiles)}
                             for identity, profiles in scopes.items()]})
    value["extensions"].pop("assumptions")
    return value, evidence


def canonical_ram_binding(note, evidence):
    manifest = ram_binding(note, evidence)
    manifest["releases"][0]["arguments"] = {role: "scheme-" + role for role in ("security", "correctness")}
    for identifier, graph in manifest["releases"][0]["graph_models"].items():
        graph["argument_id"] = identifier
    return manifest


def test_canonical_ram_argument_preserves_account_and_exact_premise_sources(local_system):
    _, owner, reviewer, reader, path = local_system
    value, evidence = canonical_ram_content()
    note = create(owner, value); publish(owner, reviewer, note)
    path.write_text(json.dumps(canonical_ram_binding(note, evidence)))
    response = reader.client.get(binding_url("classic-mceliece"))
    assert response.status_code == 200, response.text
    packet = response.json()
    selected = next(iter(packet["graph_models"].values()))
    assert selected["source"]["argument"] == selected["argument"]["id"]
    assert selected["assumptions"][0]["source"]["statement"] == value["statements"][0]["id"]
    assert packet["attack_comparison"]["analysis_sha256"] == evidence["analysis_sha256"]
    assert packet["scientific_adoption"] is False


@pytest.mark.parametrize("change", ["drop-profile", "drop-dependency", "rebind-section"])
def test_canonical_ram_cannot_drop_required_scope_or_change_reasoning(local_system, change):
    _, owner, _, _, path = local_system
    value, evidence = canonical_ram_content()
    target = value["arguments"][-1]
    if change == "drop-profile":
        target["dependencies"][0]["configuration_ids"].pop()
    elif change == "drop-dependency":
        target["dependencies"] = []; target["status"] = "incomplete"
    else:
        target["section_id"] = "security"
    note = create(owner, value)
    path.write_text(json.dumps(canonical_ram_binding(note, evidence)))
    assert owner.client.get(binding_url("classic-mceliece")).status_code == 404
