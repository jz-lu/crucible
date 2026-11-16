"""Intrinsic readable sections, exact citations and isolated display bindings."""
from copy import deepcopy
from dataclasses import replace
import base64
import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

from crucible import notebook, notebook_graph, note_sections, registry, research_reviews
from crucible.app import create_app
from crucible.config import Settings, load_settings
from crucible.db import User
from crucible.notebook_numbers import NotebookNumber
from test_notebook import Actor, ORIGIN, create, revise, publication_body, system


def content():
    return {"title": "Readable conditional research", "claim": "A stated conditional result.",
        "conditions": "The premise remains unproved in the declared model.",
        "body": "Full technical text, including μ and every original detail.", "tags": ["local-fixture"],
        "sections": [{"id": "security", "title": "Security", "body": "A conditional security explanation."},
                     {"id": "correctness", "title": "Correctness", "body": "The honest experiment and its conditions."},
                     {"id": "premise", "title": "Premise A", "body": "An explicitly unproved premise."},
                     {"id": "attack", "title": "Attack work", "body": "The exact model and its limits."}],
        "extensions": {"scheme_context": {"scheme_id": "rsa-kem", "version": registry.CURRENT["rsa-kem"]},
            "assumptions": [{"id": "rsa-local-premise", "label": "A-0001", "version": 1,
                "section_id": "premise", "stages": [1], "status": "unproved"}]}}


@pytest.fixture
def local_system(tmp_path):
    identifiers = [str(uuid4()) for _ in range(3)]
    path = tmp_path / "scheme-note-bindings.json"
    settings = Settings(f"sqlite:///{tmp_path / 'fresh-notes.db'}", b"s" * 32,
        origins=(ORIGIN,), reviewer_ids=(identifiers[1],), rate_limits=False,
        local_note_corpus_only=True, local_note_bindings_path=str(path))
    app = create_app(settings, clock=lambda: 3_600_600.0, initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in zip(identifiers, ("section-author", "section-reviewer", "section-reader")):
            session.add(User(id=identifier, handle=handle, password_hash="unused-fixture-only", created_at=3_600_600.0))
    yield app, *(Actor(app, identifier) for identifier in identifiers), path
    app.state.db.engine.dispose()


def publish(owner, reviewer, note):
    response = owner.post(f"/api/notebook/notes/{note['id']}/publication-requests", publication_body(note))
    assert response.status_code == 200, response.text
    response = reviewer.post(f"/api/notebook/notes/{note['id']}/publication-reviews", {
        **publication_body(note), "decision": "approved", "reason": "Make this fixture readable; no scientific approval."})
    assert response.status_code == 200, response.text


def pinned(note, section="security"):
    return f"/api/notebook/notes/{note['id']}?revision={note['revision']}&sha256={note['content_sha256']}&section={section}"


def manifest_for(note, scheme_id="rsa-kem"):
    version = registry.CURRENT[scheme_id]
    descriptor = registry.descriptor(registry.adapter_for(scheme_id, version))
    initial = descriptor.get("initial_security_model") or {}
    models = {initial["id"]: {"section_id": "attack", "metric": "classical", "kind": "attack-reference",
                            "assumption_ids": ["rsa-local-premise"]}} if initial.get("id") else {}
    return {"format": "crucible-local-note-bindings-v1", "releases": [{"scheme_id": scheme_id,
        "version": version, "note": {key: note[key] for key in ("id", "revision", "content_sha256")},
        "sections": {"security": "security", "correctness": "correctness"}, "graph_models": models}]}


def binding_url(scheme_id="rsa-kem"):
    return f"/api/protocol/note-justifications?scheme_id={scheme_id}&version={registry.CURRENT[scheme_id]}"


def test_sections_preserve_full_text_hash_and_old_revision(system):
    _, owner, _, reader, _ = system
    original = content()
    note = create(owner, original)
    assert note["content_sha256"] == notebook._hash(original)
    response = owner.client.get(pinned(note))
    assert response.status_code == 200
    record = response.json()["note"]
    assert record["revisions"][0]["content"] == original
    assert record["selected_section"] == {"id": "security", "section_sha256": notebook._hash(original["sections"][0])}
    assert reader.client.get(pinned(note)).status_code == 404
    edited = deepcopy(original)
    edited["sections"][0]["title"] = "A clearer title for the same stable section"
    later = revise(owner, note, edited)
    assert later["content_sha256"] != note["content_sha256"]
    assert owner.client.get(pinned(note)).json()["note"]["revisions"][0]["content"] == original
    assert owner.client.get(pinned(later)).json()["note"]["selected_section"]["id"] == "security"
    exported = owner.client.get("/api/notebook/export").json()
    assert exported["notes"][0]["revisions"][0]["sections"] == original["sections"]


def test_legacy_content_hash_has_no_new_empty_field(system):
    _, owner, _, _, _ = system
    legacy = content(); legacy.pop("sections")
    created = create(owner, legacy)
    assert created["content_sha256"] == notebook._hash(legacy)
    assert "sections" not in created["revisions"][0]["content"]


@pytest.mark.parametrize("change", [
    lambda value: value["sections"].append(deepcopy(value["sections"][0])),
    lambda value: value["sections"][0].update(id="Security Heading"),
    lambda value: value["sections"][0].update(id="constructor"),
    lambda value: value["sections"][0].update(body="  "),
    lambda value: value["sections"][0].update(title="t" * 241),
    lambda value: value["sections"][0].update(body="b" * 40001),
])
def test_invalid_readable_partition_rejected(system, change):
    _, owner, _, _, _ = system
    value = content(); change(value)
    response = owner.post("/api/notebook/notes", {"content": value, "idempotency_key": str(uuid4())})
    assert response.status_code == 422


def test_exact_section_requires_complete_unambiguous_pin(system):
    _, owner, _, _, _ = system
    note = create(owner, content())
    path = f"/api/notebook/notes/{note['id']}"
    for query in ("?section=security", "?revision=1&section=security", "?sha256=" + note["content_sha256"],
                  pinned(note).split("?", 1)[1] + "&section=correctness"):
        url = path + (query if query.startswith("?") else "?" + query)
        assert owner.client.get(url).status_code == 422
    assert owner.client.get(pinned(note, "secur")).status_code == 404
    assert owner.client.get(pinned(note).replace(note["content_sha256"], "0" * 64)).status_code == 409


def test_section_reference_graph_keeps_exact_pin_without_granting_access():
    base = "/#notebook?note=exact-note&revision=2&sha256=" + "a" * 64
    for suffix in ("&note_section=security", "&location=L12"):
        edges = list(notebook_graph.references({"references": [{"url": base + suffix, "relation": "supports", "scope": "Only this claim"}]}))
        assert len(edges) == 1
        assert edges[0]["target_revision"] == 2 and edges[0]["target_sha256"] == "a" * 64
    for suffix in ("&note_section=security&location=L12", "&note_section=security&note_section=correctness", "&note_section=Bad"):
        assert not list(notebook_graph.references({"references": [{"url": base + suffix}]}))


def test_fresh_local_corpus_has_no_bundled_notes_or_allocated_numbers(local_system):
    app, owner, _, _, _ = local_system
    index = owner.client.get("/api/notebook/notes?projection=index&include_curated=true").json()
    assert index["notes"] == [] and index["capabilities"]["curated_notes"] is False
    assert owner.client.get("/api/notebook/timeline").json()["total"] == 0
    assert owner.client.get("/api/notebook/tags").json()["tags"] == []
    assert owner.client.get("/api/notebook/curated-note-numbers").json()["numbers"] == {}
    assert owner.client.get("/api/session").json()["capabilities"]["curated_notes"] is False
    assert owner.client.get("/map/notes.json").json()["notes"] == []
    assert '"notes": []' in owner.client.get("/map/notes-data.js").text
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(NotebookNumber)) == 0
        first = notebook.curated_collection()[0]
        revision = first["revisions"][0]
        with pytest.raises(HTTPException) as error:
            research_reviews.source_snapshot(session, session.get(User, owner.identifier), research_reviews.SourceRef(
                note_id=first["id"], revision=revision["revision"], content_sha256=revision["content_sha256"]))
        assert error.value.status_code == 404


def test_local_corpus_is_opt_in_and_forbidden_in_production(system):
    app, owner, _, _, _ = system
    assert owner.client.get("/api/session").json()["capabilities"]["curated_notes"] is True
    assert owner.client.get(binding_url()).status_code == 404
    with pytest.raises(RuntimeError, match="development-only"):
        replace(app.state.settings, production=True, local_note_corpus_only=True)


def test_bindings_require_publication_and_preserve_old_revision(local_system):
    _, owner, reviewer, reader, path = local_system
    note = create(owner, content())
    manifest = manifest_for(note); path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404
    assert owner.client.get(binding_url()).status_code == 200
    publish(owner, reviewer, note)
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    binding = response.json()
    assert binding["prototype"] is True and binding["scientific_adoption"] is False
    assert binding["sections"]["security"]["content_sha256"] == note["content_sha256"]
    assert binding["sections"]["security"]["note_section"] == "security"
    assert binding["assumptions"][0]["version"] == 1
    assert binding["assumptions"][0]["source"]["note_section"] == "premise"
    assert all("bits" not in item for item in binding["graph_models"].values())
    edited = content(); edited["sections"][0]["body"] += " A later private revision."
    later = revise(owner, note, edited)
    assert reader.client.get(binding_url()).json()["note"]["revision"] == 1
    assert reader.client.get(pinned(later)).status_code == 404
    manifest["releases"][0]["note"]["revision"] = 2
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("change", [
    lambda doc: doc["releases"].append(deepcopy(doc["releases"][0])),
    lambda doc: doc["releases"][0]["note"].update(content_sha256="0" * 64),
    lambda doc: doc["releases"][0]["sections"].update(security="missing"),
    lambda doc: doc["releases"][0]["graph_models"].update(unknown={"section_id": "attack", "metric": "classical", "kind": "fiction"}),
    lambda doc: next(iter(doc["releases"][0]["graph_models"].values())).update(assumption_ids=["missing"]),
])
def test_invalid_binding_manifest_fails_closed(local_system, change):
    _, owner, reviewer, reader, path = local_system
    note = create(owner, content()); publish(owner, reviewer, note)
    manifest = manifest_for(note); change(manifest); path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404


def test_assumption_stage_scope_is_validated_against_exact_release(local_system):
    _, owner, reviewer, reader, path = local_system
    value = content(); value["extensions"]["assumptions"][0]["stages"] = [9999]
    note = create(owner, value); publish(owner, reviewer, note)
    path.write_text(json.dumps(manifest_for(note)))
    assert reader.client.get(binding_url()).status_code == 404


def test_missing_scheme_argument_cannot_be_bound_as_an_attack_premise(local_system):
    _, owner, reviewer, reader, path = local_system
    value = content()
    value["extensions"]["assumptions"][0]["status"] = "incomplete"
    note = create(owner, value); publish(owner, reviewer, note)
    manifest = manifest_for(note)
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url()).status_code == 404
    for model in manifest["releases"][0]["graph_models"].values():
        model["assumption_ids"] = []
    path.write_text(json.dumps(manifest))
    response = reader.client.get(binding_url())
    assert response.status_code == 200
    assert all(model["source"]["note_section"] == "attack" and model["assumptions"] == []
               for model in response.json()["graph_models"].values())


def test_binding_does_not_trust_changed_stored_content(local_system):
    app, owner, reviewer, reader, path = local_system
    note = create(owner, content()); publish(owner, reviewer, note)
    path.write_text(json.dumps(manifest_for(note)))
    with app.state.db.transaction(write=True) as session:
        saved = session.scalar(select(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == note["id"]))
        changed = deepcopy(saved.content); changed["sections"][0]["body"] += " Corruption."
        saved.content = changed
    assert reader.client.get(binding_url()).status_code == 404


def test_all_current_scheme_releases_have_independent_exact_note_bindings(local_system):
    _, owner, reviewer, reader, path = local_system
    manifest = {"format": "crucible-local-note-bindings-v1", "releases": []}
    notes = {}
    assert len(registry.CURRENT) == 8
    for scheme_id, version in registry.CURRENT.items():
        value = content()
        value["extensions"] = {"scheme_context": {"scheme_id": scheme_id, "version": version}, "assumptions": []}
        note = create(owner, value); publish(owner, reviewer, note)
        binding = manifest_for(note, scheme_id)["releases"][0]
        for graph in binding["graph_models"].values():
            graph["assumption_ids"] = []
        manifest["releases"].append(binding); notes[scheme_id] = note
    path.write_text(json.dumps(manifest))
    for scheme_id, note in notes.items():
        response = reader.client.get(binding_url(scheme_id))
        assert response.status_code == 200, (scheme_id, response.text)
        assert response.json()["sections"]["correctness"]["id"] == note["id"]
    # A real registered release is not sufficient when its source belongs to
    # another release: the source's own exact context must agree.
    manifest["releases"][0]["note"] = manifest["releases"][1]["note"]
    path.write_text(json.dumps(manifest))
    assert reader.client.get(binding_url(manifest["releases"][0]["scheme_id"])).status_code == 404


def test_exact_profile_scope_supports_nonstage_configurations(local_system):
    _, owner, reviewer, reader, path = local_system
    scheme_id = next(identifier for identifier in registry.CURRENT if "ntru" in identifier)
    version = registry.CURRENT[scheme_id]
    descriptor = registry.descriptor(registry.adapter_for(scheme_id, version))
    intermediate = next(profile for profile in descriptor["profiles"] if profile.get("stage_number") is None)
    value = content()
    value["extensions"]["scheme_context"] = {"scheme_id": scheme_id, "version": version}
    assumption = value["extensions"]["assumptions"][0]
    assumption.update(stages=[], profile_ids=[intermediate["id"]])
    note = create(owner, value); publish(owner, reviewer, note)
    path.write_text(json.dumps(manifest_for(note, scheme_id)))
    response = reader.client.get(binding_url(scheme_id))
    assert response.status_code == 200, response.text
    assert response.json()["assumptions"][0]["profile_ids"] == [intermediate["id"]]
    value["extensions"]["assumptions"][0]["profile_ids"] = ["not-in-this-release"]
    later = revise(owner, note, value); publish(owner, reviewer, later)
    path.write_text(json.dumps(manifest_for(later, scheme_id)))
    assert reader.client.get(binding_url(scheme_id)).status_code == 404


def test_local_binding_path_comes_from_explicit_fresh_data_directory(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key == "DATABASE_URL" or key.startswith("CRUCIBLE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"fixture-only" * 3).decode())
    monkeypatch.setenv("CRUCIBLE_LOCAL_NOTE_CORPUS_ONLY", "true")
    settings = load_settings()
    assert settings.local_note_corpus_only is True
    assert settings.local_note_bindings_path == str(tmp_path / "scheme-note-bindings.json")
    monkeypatch.setenv("CRUCIBLE_ENV", "production")
    with pytest.raises(RuntimeError, match="must be false in production"):
        load_settings()


def classified_content():
    value = content()
    value["extensions"]["scheme_assumptions"] = {"security": [], "correctness": ["rsa-local-premise"]}
    return value


def security_gap():
    return {"id": "missing-security-proof", "section_id": "security",
        "statement": "This exact construction needs its own argument.",
        "scope": "The first stage of this exact release.", "status": "incomplete", "stages": [1]}


def test_authored_categories_preserve_exact_sources_and_revision_visibility(local_system):
    _, owner, reviewer, reader, path = local_system
    value = classified_content()
    value["extensions"]["assumptions"][0]["summary"] = "The real errors do not exceed the modeled failure bound."
    value["extensions"]["assumptions"][0].update(kind="distribution-bound",
        formal_statement=r"For \(E\sim D\), assume \(\Pr[|E|\ge t]\le\delta\).")
    # A premise may be used by more than one argument; grouping is not a
    # disjoint partition and is never inferred from a section's title.
    value["extensions"]["scheme_assumptions"]["security"] = ["rsa-local-premise"]
    value["extensions"]["scheme_security_gaps"] = [security_gap()]
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    assert reader.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, note)
    packet = reader.client.get(binding_url()).json()
    assert packet["assumptions"][0]["title"] == "Premise A"
    assert packet["assumptions"][0]["summary"] == value["extensions"]["assumptions"][0]["summary"]
    assert packet["assumptions"][0]["formal_statement"] == value["extensions"]["assumptions"][0]["formal_statement"]
    assert packet["assumptions"][0]["kind"] == "distribution-bound"
    for role, group in packet["scheme_assumptions"].items():
        assert group["source"] == packet["sections"][role]
        assert group["assumptions"] == packet["assumptions"]
        assert group["assumptions"][0]["source"] == {
            "id": note["id"], "revision": note["revision"], "content_sha256": note["content_sha256"],
            "note_section": "premise", "section_sha256": notebook._hash(value["sections"][2])}
        assert group["incomplete"] is (role == "security")
    later_content = deepcopy(value)
    later_content["sections"][2]["title"] = "A different private premise title"
    later_content["extensions"]["assumptions"][0]["summary"] = "A later private description must not replace the bound source."
    later_content["extensions"]["assumptions"][0]["formal_statement"] = "An altered private mathematical premise."
    later_content["extensions"]["scheme_assumptions"]["correctness"] = []
    later = revise(owner, note, later_content)
    assert reader.client.get(binding_url()).json() == packet
    assert reader.client.get(pinned(later)).status_code == 404
    path.write_text(json.dumps(manifest_for(later)))
    assert reader.client.get(binding_url()).status_code == 404


def test_unclassified_is_distinct_from_explicit_empty_categories(local_system):
    _, owner, _, _, path = local_system
    note = create(owner, content())
    path.write_text(json.dumps(manifest_for(note)))
    assert owner.client.get(binding_url()).json()["scheme_assumptions"] is None
    value = content()
    value["extensions"]["scheme_assumptions"] = {"security": [], "correctness": []}
    value["extensions"]["scheme_security_gaps"] = [security_gap()]
    note = revise(owner, note, value)
    path.write_text(json.dumps(manifest_for(note)))
    packet = owner.client.get(binding_url()).json()
    assert packet["scheme_assumptions"]["security"]["assumptions"] == []
    assert packet["scheme_assumptions"]["security"]["incomplete"] is True
    assert packet["scheme_assumptions"]["correctness"]["assumptions"] == []
    assert packet["scheme_assumptions"]["correctness"]["incomplete"] is False
    assert packet["scheme_assumptions"]["correctness"]["argument_status"] is None
    assert packet["scheme_arguments"]["security"]["status"] == "incomplete"
    assert packet["scheme_arguments"]["correctness"]["status"] is None
    # An attack premise does not become a scheme premise automatically; a
    # missing argument does not become a numbered assumption either.
    assert [a["id"] for a in packet["assumptions"]] == ["rsa-local-premise"]
    assert all(model["assumptions"] == packet["assumptions"] for model in packet["graph_models"].values())


def argument_content():
    value = classified_content()
    value["extensions"]["scheme_arguments"] = {
        "security": {"status": "incomplete", "scope": "Fresh passive key recovery."},
        "correctness": {"status": "conditional", "scope": "Fresh honest agreement under the declared premise."},
    }
    return value


def test_unconditional_requires_an_explicit_exact_scoped_argument(local_system):
    _, owner, _, _, path = local_system
    value = argument_content()
    value["extensions"]["scheme_assumptions"]["correctness"] = []
    value["extensions"]["scheme_arguments"]["correctness"] = {
        "status": "unconditional", "scope": "Honest agreement for valid parameters; faithful algorithms.",
        "summary": "Both honest parties obtain the same key when the specified algorithms run correctly."}
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    packet = owner.client.get(binding_url()).json()["scheme_assumptions"]
    assert packet["correctness"]["argument_status"] == "unconditional"
    assert packet["correctness"]["scope"] == value["extensions"]["scheme_arguments"]["correctness"]["scope"]
    assert packet["correctness"]["summary"] == value["extensions"]["scheme_arguments"]["correctness"]["summary"]
    assert packet["security"]["summary"] is None
    assert packet["correctness"]["incomplete"] is False
    assert packet["security"]["incomplete"] is True


@pytest.mark.parametrize("target", ["premise", "argument"])
@pytest.mark.parametrize("summary", ["   ", "x" * 601, 123])
def test_malformed_readable_summaries_fail_closed(local_system, target, summary):
    _, owner, _, _, path = local_system
    value = argument_content()
    item = (value["extensions"]["assumptions"][0] if target == "premise"
            else value["extensions"]["scheme_arguments"]["correctness"])
    item["summary"] = summary
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    assert owner.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("fields", [
    {"kind": "hardness"}, {"formal_statement": "An untyped claim."},
    {"kind": "proof", "formal_statement": "Not an allowed premise kind."},
    {"kind": "model", "formal_statement": "  "},
    {"kind": "hardness", "formal_statement": "x" * 4001},
    {"kind": "distribution-bound", "formal_statement": 123},
])
def test_formal_premises_require_a_bounded_statement_and_kind(local_system, fields):
    _, owner, _, _, path = local_system
    value = classified_content()
    value["extensions"]["assumptions"][0].update(fields)
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    assert owner.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("change", [
    lambda v: v["extensions"]["scheme_arguments"]["correctness"].update(status="unconditional"),
    lambda v: v["extensions"]["scheme_assumptions"].update(correctness=[]),
    lambda v: v["extensions"]["scheme_arguments"]["correctness"].update(scope=" "),
    lambda v: v["extensions"]["scheme_arguments"]["correctness"].update(status="proved"),
    lambda v: v["extensions"]["scheme_arguments"].pop("security"),
    lambda v: v["extensions"].pop("scheme_assumptions"),
    lambda v: (v["extensions"].update(scheme_security_gaps=[security_gap()]),
               v["extensions"]["scheme_arguments"]["security"].update(status="unconditional")),
])
def test_conflicting_or_unscoped_argument_claims_fail_closed(local_system, change):
    _, owner, _, _, path = local_system
    value = argument_content(); change(value)
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    assert owner.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("change", [
    lambda v: v["extensions"]["scheme_assumptions"].update(security=["unknown-id"]),
    lambda v: v["extensions"]["scheme_assumptions"].update(security=["rsa-local-premise"] * 2),
    lambda v: v["extensions"]["scheme_assumptions"].pop("security"),
    lambda v: v["extensions"]["scheme_assumptions"].update(attack=[]),
    lambda v: v["extensions"]["scheme_assumptions"].update(security="rsa-local-premise"),
    lambda v: v["extensions"].update(scheme_assumptions=None),
    lambda v: v["extensions"]["assumptions"][0].update(status="incomplete"),
    lambda v: v["extensions"]["assumptions"][0].update(stages=[]),
])
def test_invalid_scheme_classification_fails_closed(local_system, change):
    _, owner, _, _, path = local_system
    value = classified_content(); change(value)
    note = create(owner, value)
    manifest = manifest_for(note)
    manifest["releases"][0]["graph_models"] = {}
    path.write_text(json.dumps(manifest))
    assert owner.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("change", [
    lambda gap: gap.update(section_id="missing"),
    lambda gap: gap.update(stages=[9999]),
    lambda gap: gap.update(status="proved"),
    lambda gap: gap.update(statement=" "),
    lambda gap: gap.update(id="rsa-local-premise"),
    lambda gap: gap.update(profile_ids=["other-release-profile"]),
])
def test_scheme_missing_arguments_are_validated_separately(local_system, change):
    _, owner, _, _, path = local_system
    value = classified_content()
    gap = security_gap(); change(gap)
    value["extensions"]["scheme_security_gaps"] = [gap]
    note = create(owner, value)
    path.write_text(json.dumps(manifest_for(note)))
    assert owner.client.get(binding_url()).status_code == 404


def test_current_presentations_explicitly_separate_scheme_and_attack_premises():
    data = json.loads((Path(note_sections.__file__).parent / "data/scheme_note_presentations.json").read_text())
    expected = {
        "ml-kem": ["module-independent-expansion", "module-independent-compression"],
        "frodokem": ["frodo-independent-noise"],
        "hqc": ["hqc-finite-expansion-transfer", "hqc-decoder-tail-domination"],
    }
    assert set(data["releases"]) == {f"{scheme}@{version}" for scheme, version in registry.CURRENT.items()}
    for identity, row in data["releases"].items():
        groups = row["scheme_assumptions"]
        assert groups["correctness"] == expected.get(identity.split("@", 1)[0], [])
        claims = note_sections.SchemeArguments.model_validate(row["scheme_arguments"])
        assert claims.security.summary and claims.correctness.summary
        assert claims.security.status in {"conditional", "incomplete"}
        assert claims.correctness.status == ("conditional" if groups["correctness"] else "unconditional")
        assert len(row["full_text_argument_appendix"]) > 1500
        assert groups["security"]
        premises = {item["id"]: item for item in row["assumptions"]}
        gaps = {item["id"] for item in row.get("scheme_security_gaps", [])}
        for identifiers in groups.values():
            for identifier in identifiers:
                assert identifier in premises and identifier not in gaps
                assert premises[identifier]["status"] in {"unproved", "model-premise"}
                assert premises[identifier]["stages"] or premises[identifier].get("profile_ids")
                assert 30 <= len(premises[identifier]["summary"]) <= 600
                assert premises[identifier]["kind"] in {"hardness", "model", "distribution-bound"}
                assert 0 < len(premises[identifier]["statement"]) <= 4000
                section = next(s for s in row["sections"] if s["id"] == premises[identifier]["section_id"])
                assert premises[identifier]["statement"] in section["body"]
                assert set(section["source_ref_ids"]) <= {s["id"] for s in row["source_refs"]}
                assert not identifier.startswith("ram-")


def canonical_argument_content():
    value = content()
    first = registry.descriptor(registry.adapter_for("rsa-kem", registry.CURRENT["rsa-kem"]))["profiles"][0]["id"]
    value["statements"] = [{"id": "rsa-local-premise", "label": "A-1", "title": "An exact conditional premise",
        "definition": r"For the declared distribution, \(\Pr[F]\le 2^{-128}\)."}]
    value["arguments"] = [
        {"id": "scheme-security", "title": "Security argument", "conclusion": "No numerical bound is established here.",
         "scope": "The exact registered distribution.", "status": "incomplete", "section_id": "security", "dependencies": []},
        {"id": "scheme-correctness", "title": "Correctness argument", "conclusion": r"Honest disagreement is at most \(2^{-128}\).",
         "scope": "Only the first exact registered configuration.", "status": "conditional", "section_id": "correctness",
         "dependencies": [{"statement": {"statement_id": "rsa-local-premise"}, "configuration_ids": [first]}]},
    ]
    # Old metadata is deliberately divergent. The canonical contract must not
    # continue reading the old copied formula or deriving its dependency list.
    value["extensions"]["assumptions"][0]["formal_statement"] = "An obsolete copied premise."
    return value


def canonical_manifest(note):
    manifest = manifest_for(note)
    binding = manifest["releases"][0]
    binding["arguments"] = {"security": "scheme-security", "correctness": "scheme-correctness"}
    binding["graph_models"] = {}
    return manifest


def test_legacy_arguments_have_exact_reasoning_and_no_invented_conclusion(local_system):
    _, owner, _, _, path = local_system
    value = argument_content()
    value["extensions"]["scheme_arguments"]["correctness"]["summary"] = "Agreement is conditional on the stated bound."
    note = create(owner, value); path.write_text(json.dumps(manifest_for(note)))
    packet = owner.client.get(binding_url()).json()
    group = packet["scheme_arguments"]["correctness"]
    assert group["origin"] == "legacy" and group["scientific_adoption"] is False
    assert group["conclusion"] is None and group["summary"] == value["extensions"]["scheme_arguments"]["correctness"]["summary"]
    assert group["source"] == packet["sections"]["correctness"]
    assert group["dependencies"][0]["required"] is None
    assert group["dependencies"][0]["mapping"] is None


def test_canonical_arguments_use_one_definition_and_pin_revision(local_system):
    _, owner, reviewer, reader, path = local_system
    value = canonical_argument_content()
    note = create(owner, value); publish(owner, reviewer, note)
    path.write_text(json.dumps(canonical_manifest(note)))
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    packet = response.json()
    group = packet["scheme_arguments"]["correctness"]
    assert group["source"] == {"id": note["id"], "revision": 1, "content_sha256": note["content_sha256"], "argument": "scheme-correctness"}
    assert group["conclusion"] == value["arguments"][1]["conclusion"]
    assert group["scientific_adoption"] is False and group["origin"] == "canonical"
    statement = group["dependencies"][0]["statement"]
    assert statement["definition"] == value["statements"][0]["definition"]
    assert statement["source"]["statement"] == "rsa-local-premise"
    assert packet["scheme_assumptions"]["correctness"]["assumptions"][0]["formal_statement"] == statement["definition"]
    assert "Obsolete copied premise" not in response.text
    changed = deepcopy(value); changed["statements"][0]["definition"] = "A later private definition."
    later = revise(owner, note, changed)
    assert reader.client.get(binding_url()).json() == packet
    path.write_text(json.dumps(canonical_manifest(later)))
    assert reader.client.get(binding_url()).status_code == 404


def test_canonical_scheme_arguments_do_not_require_readable_companion(local_system):
    _, owner, _, _, path = local_system
    value = canonical_argument_content(); value.pop("sections")
    for argument in value["arguments"]:
        argument.pop("section_id")
    note = create(owner, value)
    manifest = canonical_manifest(note); manifest["releases"][0].pop("sections")
    path.write_text(json.dumps(manifest))
    response = owner.client.get(binding_url())
    assert response.status_code == 200, response.text
    group = response.json()["scheme_arguments"]["correctness"]
    assert group["reasoning_source"] is None and group["source"]["argument"] == "scheme-correctness"


def test_canonical_cross_note_and_normative_sources_require_visibility(local_system):
    _, owner, reviewer, reader, path = local_system
    definition = create(owner, content())
    premise_content = content(); premise_content["statements"] = canonical_argument_content()["statements"]
    premise_content["statements"][0]["definitions"] = [{"note_id": definition["id"], "revision": definition["revision"],
        "content_sha256": definition["content_sha256"], "section_id": "premise"}]
    premise = create(owner, premise_content)
    value = canonical_argument_content(); value.pop("statements")
    value["arguments"][1]["dependencies"][0]["statement"].update(note_id=premise["id"], revision=premise["revision"], content_sha256=premise["content_sha256"])
    note = create(owner, value); publish(owner, reviewer, note)
    path.write_text(json.dumps(canonical_manifest(note)))
    assert reader.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, premise)
    assert reader.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, definition)
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    assert response.json()["scheme_arguments"]["correctness"]["dependencies"][0]["statement"]["source"]["id"] == premise["id"]
    reviewer.post(f"/api/notebook/notes/{definition['id']}/publication-withdrawals", {
        **publication_body(definition), "reason": "Withdraw the normative sampler definition."}).raise_for_status()
    assert reader.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("change", [
    lambda value: value["arguments"][1]["dependencies"][0].update(configuration_ids=[]),
    lambda value: value["arguments"][1]["dependencies"][0].update(configuration_ids=["other-release-profile"]),
])
def test_canonical_bound_coverage_is_explicit_and_release_specific(local_system, change):
    _, owner, _, _, path = local_system
    value = canonical_argument_content(); change(value)
    note = create(owner, value); path.write_text(json.dumps(canonical_manifest(note)))
    assert owner.client.get(binding_url()).status_code == 404


def test_canonical_graph_source_is_argument_and_premise_definition_is_shared(local_system):
    _, owner, _, _, path = local_system
    value = canonical_argument_content()
    argument = deepcopy(value["arguments"][1]); argument.update(id="attack-account", section_id="attack", title="A named attack account")
    value["arguments"].append(argument)
    note = create(owner, value)
    manifest = canonical_manifest(note)
    graph = manifest_for(note)["releases"][0]["graph_models"]
    for item in graph.values():
        item["argument_id"] = "attack-account"
    manifest["releases"][0]["graph_models"] = graph
    path.write_text(json.dumps(manifest))
    response = owner.client.get(binding_url())
    assert response.status_code == 200, response.text
    packet = response.json()
    selected = next(iter(packet["graph_models"].values()))
    assert selected["source"]["argument"] == "attack-account"
    assert selected["reasoning_source"]["note_section"] == "attack"
    assert selected["assumptions"][0]["formal_statement"] == packet["scheme_arguments"]["correctness"]["dependencies"][0]["statement"]["definition"]
