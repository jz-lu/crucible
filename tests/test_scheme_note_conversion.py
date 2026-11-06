"""Canonical note conversion preserves authored science and exact release uses."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re

import pytest

from crucible import notebook, registry
from crucible.attack_models import catalogue
from test_note_sections import local_system, publish, binding_url
from test_notebook import create

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sources():
    return json.loads((ROOT / "crucible/data/scheme_note_presentations.json").read_text())["releases"]


def note_content(release, row):
    scheme_id, version = release.split("@", 1)
    return {"title": row["title"], "claim": row["summary"],
        "conditions": "Exact research release " + release, "body": row["full_text_appendix"],
        "tags": ["conversion-test"],
        "sections": [{key: section[key] for key in ("id", "title", "body")}
                     for section in row["readable_sections"]],
        "statements": row["statements"], "arguments": row["arguments"],
        "extensions": {"scheme_context": {"scheme_id": scheme_id, "version": version},
            "ram_analysis_sha256": row["ram_analysis_sha256"],
            "source_refs": row["source_refs"],
            "canonical_conversion": row["canonical_conversion"]}}


def test_definitions_and_existing_scope_prose_are_copied_without_new_entailment(sources):
    assert len(sources) == 8
    assert sum(len(row["statements"]) for row in sources.values()) == 73
    assert sum(len(row["arguments"]) for row in sources.values()) == 39
    for release, row in sources.items():
        notebook.NoteContent.model_validate(note_content(release, row))
        sections = {item["id"]: item for item in row["sections"]}
        premises = {item["id"]: item for item in row["assumptions"]}
        for value in row["statements"]:
            if value["id"].startswith("claim-"):
                continue
            premise = premises[value["id"]]
            assert value["definition"] == (premise.get("statement") or sections[premise["section_id"]]["body"])
            assert value["label"] == premise["label"]
        for argument in row["arguments"]:
            for use in argument["dependencies"]:
                premise = premises[use["statement"]["statement_id"]]
                assert use.get("use") == premise.get("scope")
                assert not {"required", "mapping", "justification"} & use.keys()
                assert use["configuration_ids"]
                assert set(use["configuration_ids"]) <= set(premise["profile_ids"])
                assert set(use["configuration_ids"]) <= set(row["profile_ids"])


def test_readable_conversion_moves_only_exact_definition_text_and_keeps_reasoning(sources):
    for row in sources.values():
        before = {item["id"]: item for item in row["sections"]}
        after = {item["id"]: item for item in row["readable_sections"]}
        moved = {item["section_id"]: item for item in row["assumptions"]}
        for identifier, section in before.items():
            premise = moved.get(identifier)
            if premise and premise.get("statement") and section["body"].startswith(premise["statement"]):
                remaining = section["body"][len(premise["statement"]):].lstrip()
                if remaining:
                    assert after[identifier] == {**section, "body": remaining}
                else:
                    assert identifier not in after
            elif premise and not premise.get("statement"):
                assert identifier not in after
            else:
                assert after[identifier] == section
        assert {item["section_id"] for item in row["arguments"]} <= after.keys()


def test_role_conclusions_keep_authored_status_and_ram_keeps_exact_per_profile_premises(sources):
    for release, row in sources.items():
        arguments = {item["id"]: item for item in row["arguments"]}
        statements = {item["id"]: item for item in row["statements"]}
        for role, identifier in row["argument_bindings"].items():
            argument, claim = arguments[identifier], row["scheme_arguments"][role]
            assert argument["status"] == claim["status"]
            assert argument["scope"] == claim["scope"]
            assert "conclusion" not in argument
            conclusion = statements[argument["conclusion_statement"]["statement_id"]]
            assert conclusion["label"] == role.title() + " claim"
            assert release in conclusion["definition"]
            assert "**Scope.**" not in conclusion["definition"]
            assert "Full text" not in conclusion["definition"]
            assert not re.search(r"\bA-[1-9][0-9]*\b", conclusion["definition"])
            assert [use["statement"]["statement_id"] for use in argument["dependencies"]] == row["scheme_assumptions"][role]
        scheme_id, version = release.split("@", 1)
        evidence = catalogue.for_release(scheme_id, version)
        for point in evidence["estimates"]:
            argument = arguments[row["graph_models"][point["model_id"]]["argument_id"]]
            assert {use["statement"]["statement_id"] for use in argument["dependencies"]
                    if point["profile_id"] in use["configuration_ids"]} == {"ram-" + premise["id"] for premise in point["assumptions"]}
        for identifier, graph in row["graph_models"].items():
            argument = arguments[graph["argument_id"]]
            assert argument["section_id"] == graph["section_id"]
            if graph["metric"] != "ram":
                assert argument["status"] == "incomplete"
                assert "not converted" in argument["scope"]
                assert "Unquantified reference" in statements[argument["conclusion_statement"]["statement_id"]]["definition"]


def test_all_conclusions_have_one_canonical_identity_and_exact_attack_accounts(sources):
    for release, row in sources.items():
        statements = {item["id"]: item for item in row["statements"]}
        scheme_id, version = release.split("@", 1)
        evidence = catalogue.for_release(scheme_id, version)
        for argument in row["arguments"]:
            assert "conclusion" not in argument
            assert argument["conclusion_statement"] == {"statement_id": "claim-" + argument["id"]}
            conclusion = statements[argument["conclusion_statement"]["statement_id"]]
            assert not conclusion["label"].startswith("A-")
            graph = row["graph_models"].get(argument["id"])
            if graph and graph["metric"] == "ram":
                definition = conclusion["definition"]
                assert evidence["analysis_sha256"] in definition
                assert r"\Pr[\text{recover the complete shared key}]\ge1/2" in definition
                assert r"W_j" in definition and r"M_j" in definition
                for point in evidence["estimates"]:
                    assert ("`" + point["profile_id"] + "`" in definition) == (point["model_id"] == argument["id"])
        conclusion_ids = {argument["conclusion_statement"]["statement_id"] for argument in row["arguments"]}
        assert len(conclusion_ids) == len(row["arguments"])
        assert set(statements) == conclusion_ids | {premise["id"] for premise in row["assumptions"]}


@pytest.mark.parametrize("release, source_formula, visible_formula", [
    ("sntrup@3", "OW_core(T_B) + (2Q+1)/2^256", r"\epsilon_{\rm core}(T_B)+(2Q+1)2^{-256}+\delta_{\rm hash}(T,Q)"),
    ("hqc@modeled-research-v3", "q_G*OW_PKE(T_B)+2^-256", r"q_G\epsilon_{\rm PKE}(T_B)+2^{-256}+\delta_{\rm hash}(T,q_G)"),
    ("classic-mceliece@length-research-v1", "OW_SD(T_B) + 2^-256", r"\epsilon_{\rm SD}(T_B)+2^{-256}+\delta_{\rm hash}(T,Q)"),
])
def test_code_security_conclusions_preserve_existing_reduction_and_transfer(sources, release, source_formula, visible_formula):
    row = sources[release]
    assert source_formula in row["full_text_argument_appendix"]
    assert "Add this term to the right side and cap at one" in row["full_text_argument_appendix"]
    statement = next(item for item in row["statements"] if item["id"] == "claim-scheme-security")
    assert visible_formula in statement["definition"]
    assert "not numerically certified small values" in statement["definition"]


def test_role_claim_identity_is_independent_of_argument_exposition_and_premise_labels(sources):
    builder = script("build_ram_attack_notes")
    for release, row in sources.items():
        changed = deepcopy(row)
        for section in changed["sections"]:
            if section["id"] in {"security", "correctness"}:
                section["body"] += "\n\nA clearer exposition of the same result."
        for premise in changed["assumptions"]:
            premise["label"] = "A-999"
        evidence = catalogue.for_release(*release.split("@", 1))
        converted = builder.canonical_note_blocks(release, changed, evidence)
        before = {item["id"]: item for item in row["statements"]}
        after = {item["id"]: item for item in converted["statements"]}
        for identifier in ("claim-scheme-security", "claim-scheme-correctness"):
            assert after[identifier] == before[identifier]


def test_correctness_conclusions_keep_native_message_and_full_stage_bounds(sources):
    claims = {key: next(item["definition"] for item in row["statements"]
                       if item["id"] == "claim-scheme-correctness") for key, row in sources.items()}
    module = claims["ml-kem@compressed-research-v4"]
    for expression in ("n=8,16", "2^{-367}&n=32", "2^{-193}&n=64", "2^{-236}&n=128", "2^{-128}&n=256"):
        assert expression in module
    assert "2^{-133}" in claims["frodokem@anchor-research-v3"]
    assert "16,24," in claims["frodokem@anchor-research-v3"]
    assert "j=1,2,3,4" in claims["hqc@modeled-research-v3"]
    assert "2^{-128}&j=5,6" in claims["hqc@modeled-research-v3"]
    for release in ("ml-kem@compressed-research-v4", "frodokem@anchor-research-v3", "hqc@modeled-research-v3"):
        assert "failure to recover the entire native message" in claims[release]
        assert "not each key individually" in claims[release]
    for release in ("teaching-dh-kem@7", "rsa-kem@4", "dhkem-x25519@curve-compressed-v2", "sntrup@3", "classic-mceliece@length-research-v1"):
        assert r"\(d=0\)" in claims[release]
    assert "including zero and nonunits" in claims["rsa-kem@4"]
    assert "two configurations outside" in claims["sntrup@3"]
    for value in claims.values():
        assert "never on agreement" in value
        assert "do not certify native executable correctness" in value


def test_security_conclusions_target_every_native_key_bit_and_keep_unknown_slack(sources):
    for release, row in sources.items():
        adapter = registry.adapter_for(*release.split("@", 1))
        key_lengths = {adapter.get_manifest(profile)["shared_secret_bytes"] for profile in row["profile_ids"]}
        assert len(key_lengths) == 1
        bits = 8 * next(iter(key_lengths))
        claim = next(item["definition"] for item in row["statements"] if item["id"] == "claim-scheme-security")
        assert f"complete {bits}-bit shared key in one output" in claim
        assert "supremum probability" in claim
        assert "not numerically certified small values" in claim
        assert "single classical target" in claim
        assert "No decapsulation, key-checking or leakage oracle" in claim


def test_conversion_does_not_guess_missing_configuration_coverage(sources):
    row = deepcopy(sources["rsa-kem@4"])
    row["assumptions"][0]["profile_ids"] = []
    with pytest.raises(ValueError, match="exact nonempty configuration"):
        script("build_ram_attack_notes").canonical_note_blocks("rsa-kem@4", row, catalogue.for_release("rsa-kem", "4"))


def test_hqc_bound_defines_its_named_expression_in_the_actual_statement(sources):
    row = sources["hqc@modeled-research-v3"]
    statement = next(item for item in row["statements"] if item["id"] == "hqc-decoder-tail-domination")
    definition = statement["definition"]
    for expression in (r"F(m,x,y,r_1,r_2,e)=", r"a=", r"p=", r"b_i=",
                       r"\pi=", r"\tau=", r"\beta=", r"B_{\rm model}=",
                       r"\mathcal D_{\rm seed}", r"\mathcal D_{\rm ideal}"):
        assert expression in definition
    assert "64-bit dyadic" in definition and "256-bit dyadics" in definition
    assert "does not silently replace" in definition
    companion = next(item for item in row["readable_sections"] if item["id"] == "tail-model")
    assert companion["body"].startswith("### Use in this construction")
    assert r"B_{\rm model}=" not in companion["body"]
    assert "explicit unproved transfer" in companion["body"]
    for other in sources.values():
        for premise in other["statements"]:
            if "full formula below" in premise["definition"]:
                assert premise["id"] == statement["id"]
                assert r"B_{\rm model}=" in premise["definition"]


def test_existing_unrelated_metadata_references_and_artifacts_survive_revision():
    preserve = script("prepare_note_integration").preserve_unrelated_content
    original = {"extensions": {"assumptions": ["historical"], "collaborator_context": {"keep": True}},
        "references": [{"title": "Earlier exact source", "url": "https://example.org/original"}],
        "artifacts": [{"path": "proof.txt", "sha256": "a" * 64}]}
    generated = {"extensions": {"canonical_conversion": {"format": "new"}},
        "references": [*original["references"], {"title": "Added source", "url": "https://example.org/new"}]}
    before = deepcopy(original)
    result = preserve(original, generated)
    assert result["extensions"] == {"collaborator_context": {"keep": True}, "canonical_conversion": {"format": "new"}}
    assert result["references"] == generated["references"]
    assert result["artifacts"] == original["artifacts"]
    assert original == before
    assert preserve(result, generated) == result


def test_revisions_keep_old_premise_numbers_without_assigning_them_to_conclusions(sources):
    numbered = script("prepare_note_integration").numbered_premises
    for release, row in sources.items():
        current = numbered(note_content(release, row))
        assert [(item["id"], item["label"]) for item in current] == [
            (item["id"], item["label"]) for item in row["assumptions"]]
        legacy = {"extensions": {"assumptions": row["assumptions"]}}
        assert [(item["id"], item["label"]) for item in numbered(legacy)] == [
            (item["id"], item["label"]) for item in current]


def test_all_eight_generated_notes_bind_canonical_roles_graphs_and_exact_premises(local_system, sources):
    _, owner, reviewer, reader, path = local_system
    for release, row in sources.items():
        scheme_id, version = release.split("@", 1)
        note = create(owner, note_content(release, row))
        publish(owner, reviewer, note)
        path.write_text(json.dumps({"format": "crucible-local-note-bindings-v1", "releases": [{
            "scheme_id": scheme_id, "version": version,
            "note": {key: note[key] for key in ("id", "revision", "content_sha256")},
            "sections": {"security": "security", "correctness": "correctness"},
            "arguments": row["argument_bindings"], "graph_models": row["graph_models"]}]}))
        response = reader.client.get(binding_url(scheme_id))
        assert response.status_code == 200, (release, response.text)
        packet = response.json()
        assert packet["scientific_adoption"] is False
        assert packet["attack_comparison"]["analysis_sha256"] == row["ram_analysis_sha256"]
        target_response = reader.client.get("/api/notebook/statement-workflow/targets")
        assert target_response.status_code == 200, target_response.text
        target_packet = target_response.json()
        assert target_packet["payload_limit_reached"] is False
        targets = {item["target"]["id"]: item for item in target_packet["targets"]}
        assert len(targets) == 2 + sum(graph["metric"] == "ram" for graph in row["graph_models"].values())
        for role in ("security", "correctness"):
            argument = packet["scheme_arguments"][role]
            assert argument["origin"] == "canonical"
            assert argument["source"]["argument"] == row["argument_bindings"][role]
            assert argument["source"]["content_sha256"] == note["content_sha256"]
            assert argument["presentation"]["origin"] == "initial_admission"
            assert argument["presentation"]["independent_review"] is False
            assert argument["presentation"]["current_status"] == "selected"
            state = targets[argument["presentation"]["target_id"]]
            assert set(state["target"]["configuration_ids"]) == set(row["profile_ids"])
        for identifier, model in packet["graph_models"].items():
            assert model["source"]["argument"] == model["argument"]["id"]
            if model["metric"] == "ram":
                assert model["presentation"]["origin"] == "initial_admission"
                assert model["presentation"]["independent_review"] is False
                state = targets[model["presentation"]["target_id"]]
                assert set(state["target"]["configuration_ids"]) == {
                    point["profile_id"] for point in packet["attack_comparison"]["estimates"] if point["model_id"] == identifier}
            else:
                assert model["argument"]["status"] == "incomplete"
                assert "presentation" not in model
            for premise in model["assumptions"]:
                assert premise["source"]["statement"] == premise["id"]
                assert premise["source"]["content_sha256"] == note["content_sha256"]
        for state in targets.values():
            assert state["current"]["origin"] == "initial_admission"
            assert state["current"]["independent_review"] is False
            assert len(state["roster"]) == 1
            assert state["roster"][0]["status"] == "admitted"
            assert state["target"]["conclusion_statement"]["content_sha256"] == note["content_sha256"]
