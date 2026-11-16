"""Hosted read-only bindings preserve exact sources without enabling the trial."""
import base64
from copy import deepcopy
from dataclasses import replace
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from crucible import beta_access, display_notes, notebook, registry
from crucible.app import create_app
from crucible.config import HOSTED_NOTE_PRESENTATIONS, Settings, load_settings
from crucible.db import LoginSession, User
from crucible.machine_auth import MachineToken
from crucible.security import mac, token_hash
from test_notebook import create, revise
from test_note_sections import binding_url, publish
from test_notebook_moderation import withdraw

ORIGIN = "https://presentation.example"
FORMAT = "crucible-note-presentation-bindings-v1"


class Actor:
    def __init__(self, app, identifier):
        self.identifier = identifier
        self.client = TestClient(app, base_url=ORIGIN)
        raw = "test-presentation-session-" + str(uuid4())
        with app.state.db.transaction(write=True) as session:
            session.add(LoginSession(token_hash=token_hash(raw), user_id=identifier,
                                     expires_at=app.state.clock() + 3600))
        self.client.cookies.set(app.state.settings.cookie_name, raw)
        self.csrf = mac(app.state.settings.secret, "csrf-v1", raw.encode("ascii"))

    def post(self, path, body):
        return self.client.post(path, json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": self.csrf})


def content():
    profile = registry.descriptor(registry.adapter_for("rsa-kem", registry.CURRENT["rsa-kem"]))["profiles"][0]["id"]
    return {"title": "Hosted presentation test fixture", "claim": "An explicitly illustrative claim.",
        "conditions": "Only the test premise, not a scientific result.", "body": "The full test document.", "tags": [],
        "sections": [
            {"id": "security", "title": "Security explanation", "body": "Before the premise.\n\n[[statement:premise]]\n\nAfter the premise.\n\n[[statement:security-claim]]"},
            {"id": "correctness", "title": "Correctness explanation", "body": "The scoped test claim.\n\n[[statement:correctness-claim]]"}],
        "statements": [
            {"id": "premise", "label": "A-1", "title": "Fixture premise", "definition": "Assume test event H."},
            {"id": "security-claim", "label": "S-1", "title": "Fixture security", "definition": "The illustrative conditional conclusion."},
            {"id": "correctness-claim", "label": "C-1", "title": "Fixture correctness", "definition": "The illustrative unconditional conclusion."}],
        "arguments": [
            {"id": "security", "title": "Fixture security argument", "section_id": "security",
             "conclusion_statement": {"statement_id": "security-claim"}, "status": "conditional", "scope": "The test configuration.",
             "dependencies": [{"statement": {"statement_id": "premise"}, "configuration_ids": [profile]}]},
            {"id": "correctness", "title": "Fixture correctness argument", "section_id": "correctness",
             "conclusion_statement": {"statement_id": "correctness-claim"}, "status": "unconditional", "scope": "The test configuration.", "dependencies": []}],
        "extensions": {"scheme_context": {"scheme_id": "rsa-kem", "version": registry.CURRENT["rsa-kem"]}}}


def binding(note):
    return {"format": FORMAT, "releases": [{"scheme_id": "rsa-kem", "version": registry.CURRENT["rsa-kem"],
        "note": {key: note[key] for key in ("id", "revision", "content_sha256")},
        "arguments": {"security": "security", "correctness": "correctness"},
        "sections": {"security": "security", "correctness": "correctness"}, "graph_models": {}}]}


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    identifiers = [str(uuid4()) for _ in range(3)]
    manifest = tmp_path / "hosted-bindings.json"
    settings = Settings(f"sqlite:///{tmp_path / 'test.db'}", b"s" * 32, production=True,
        origins=(ORIGIN,), access_mode="beta", reviewer_ids=(identifiers[1],), rate_limits=False,
        note_presentation_bindings_path=str(manifest))
    app = create_app(settings, clock=lambda: 3_600_600.0, initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in zip(identifiers, ("test-author", "test-reviewer", "test-reader")):
            session.add(User(id=identifier, handle=handle, password_hash="test-only", created_at=app.state.clock()))
        session.flush()
        for identifier in identifiers:
            beta_access.set_membership(session, identifier, "active", app.state.clock())
    value = content()
    older = {key: item for key, item in value.items() if key not in {"sections", "statements", "arguments"}}
    workspace = {"format": notebook.FORMAT, "views": [], "notes": [{"id": "hosted-fixture", "revisions": [
        {"revision": 1, "createdAt": 1000, **older}, {"revision": 2, "createdAt": 2000, **value}]}]}
    corpus = tmp_path / "curated.json"
    corpus.write_text(json.dumps(workspace))
    monkeypatch.setattr(notebook, "CURATED_NOTES_PATH", corpus)
    monkeypatch.setattr(display_notes, "_collection", lambda: deepcopy(workspace))
    pin = {"id": "curated:hosted-fixture", "revision": 2, "content_sha256": notebook._hash(value)}
    manifest.write_text(json.dumps(binding(pin)))
    yield app, *(Actor(app, identifier) for identifier in identifiers), manifest, corpus, pin
    app.state.db.engine.dispose()


def machine(app, actor, scopes):
    raw = "cru_" + "a" * 43
    with app.state.db.transaction(write=True) as session:
        session.add(MachineToken(id=str(uuid4()), user_id=actor.identifier, token_hash=token_hash(raw),
            label="Test presentation reader", scopes=scopes, created_at=app.state.clock(), expires_at=app.state.clock() + 3600))
    return TestClient(app, base_url=ORIGIN, headers={"Authorization": "Bearer " + raw})


def test_hosted_curated_arguments_retain_canonical_context_and_no_trial_controls(hosted):
    app, _, reviewer, reader, _, _, pin = hosted
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    packet = response.json()
    assert packet["note"] == pin and packet["scientific_adoption"] is False
    assert packet["scheme_arguments"]["security"]["status"] == "conditional"
    assert packet["scheme_arguments"]["correctness"]["status"] == "unconditional"
    premise = packet["scheme_arguments"]["security"]["dependencies"][0]["statement"]
    assert premise["source"] == {**pin, "statement": "premise"}
    assert all("presentation" not in argument for argument in packet["scheme_arguments"].values())
    document = display_notes.document(pin)
    assert document["content_sha256"] == pin["content_sha256"]
    assert "Before the premise.\n\n[[statement:premise]]\n\nAfter the premise." in document["content"]["sections"][0]["body"]
    historical = display_notes.document(pin, revision=1)
    assert historical["content_sha256"] == notebook._hash(historical["content"])
    assert "statements" not in historical["content"]
    capabilities = reviewer.client.get("/api/session").json()["capabilities"]
    assert capabilities["curated_notes"] and capabilities["note_justifications"] and capabilities["ram_attack_comparison"]
    assert not capabilities["local_note_justifications"] and not capabilities["statement_workflow_trial"]
    assert not any(route.path.startswith("/api/notebook/statement-workflow") for route in app.routes)
    for path in ("/preview?persona=reviewer", "/api/notebook/statement-workflow/targets"):
        response = reviewer.client.get(path)
        assert response.status_code == 404 and "set-cookie" not in response.headers
    for path in ("/api/notebook/statement-workflow/updates", "/api/notebook/statement-workflow/updates/" + str(uuid4()) + "/review"):
        assert reviewer.post(path, {}).status_code == 404


def test_hosted_account_bindings_require_publication_even_for_the_owner(hosted):
    _, owner, reviewer, reader, manifest, _, _ = hosted
    first = create(owner, content())
    manifest.write_text(json.dumps(binding(first)))
    assert owner.client.get(binding_url()).status_code == reader.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, first)
    assert reader.client.get(binding_url()).status_code == 200
    edited = content(); edited["body"] += " A later private edit."
    revise(owner, first, edited)
    assert reader.client.get(binding_url()).json()["note"]["content_sha256"] == first["content_sha256"]
    assert withdraw(reviewer, first).status_code == 200
    assert owner.client.get(binding_url()).status_code == reader.client.get(binding_url()).status_code == 404


def test_hosted_external_statement_sources_obey_the_same_publication_boundary(hosted):
    _, owner, reviewer, reader, manifest, _, _ = hosted
    premise = create(owner, content())
    value = content()
    value["arguments"][0]["dependencies"][0]["statement"].update(
        note_id=premise["id"], revision=premise["revision"], content_sha256=premise["content_sha256"])
    note = create(owner, value); publish(owner, reviewer, note)
    manifest.write_text(json.dumps(binding(note)))
    assert owner.client.get(binding_url()).status_code == 404
    publish(owner, reviewer, premise)
    response = reader.client.get(binding_url())
    assert response.status_code == 200, response.text
    assert response.json()["scheme_arguments"]["security"]["dependencies"][0]["statement"]["source"]["id"] == premise["id"]
    assert withdraw(reviewer, premise).status_code == 200
    assert reader.client.get(binding_url()).status_code == 404


def test_hosted_binding_never_adopts_development_publication_receipts(hosted):
    app, owner, reviewer, reader, manifest, _, _ = hosted
    note = create(owner, content()); publish(owner, reviewer, note)
    manifest.write_text(json.dumps(binding(note)))
    with app.state.db.transaction(write=True) as session:
        publication = session.scalar(select(notebook.NotebookPublication).join(notebook.NotebookRevision,
            notebook.NotebookRevision.id == notebook.NotebookPublication.revision_id).where(
                notebook.NotebookRevision.note_id == note["id"]))
        publication.test_only_self_review = True
    assert owner.client.get(binding_url()).status_code == reader.client.get(binding_url()).status_code == 404


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(format="crucible-local-note-bindings-v1"),
    lambda value: value["releases"][0]["note"].update(content_sha256="0" * 64),
    lambda value: value["releases"][0]["note"].update(revision=1),
    lambda value: value["releases"][0]["arguments"].update(security="missing"),
    lambda value: value["releases"].append(deepcopy(value["releases"][0])),
])
def test_hosted_bindings_fail_closed_on_stale_or_ambiguous_sources(hosted, mutate):
    _, _, _, reader, manifest, _, pin = hosted
    value = binding(pin); mutate(value); manifest.write_text(json.dumps(value))
    response = reader.client.get(binding_url())
    assert response.status_code == 404 and "scheme_arguments" not in response.json()


@pytest.mark.parametrize("scopes,expected", [(["notes:read"], 200), (["notebook:read"], 403),
    (["challenges:read"], 403), (["notes:rate"], 403), ([], 403)])
def test_binding_agent_reads_have_one_explicit_scope_and_no_method_wildcard(hosted, scopes, expected):
    app, _, _, reader, _, _, _ = hosted
    agent = machine(app, reader, scopes)
    agent.cookies.update(reader.client.cookies)  # A browser cookie cannot bypass the explicit token scope.
    response = agent.get(binding_url())
    assert response.status_code == expected and response.headers["cache-control"] == "no-store"
    if expected == 200:
        pin = response.json()["note"]
        source = agent.get("/api/agent/notes/" + pin["id"], params={"revision": pin["revision"],
                           "content_sha256": pin["content_sha256"]})
        assert source.status_code == 200 and source.json()["note"]["content"] == content()
    for method in ("HEAD", "POST", "PUT", "DELETE"):
        assert agent.request(method, binding_url()).status_code == 403


def test_beta_membership_and_revocation_protect_both_readers(hosted):
    app, _, _, reader, _, _, _ = hosted
    anonymous = TestClient(app, base_url=ORIGIN)
    assert anonymous.get(binding_url()).status_code == 401
    assert not any(anonymous.get("/api/session").json()["capabilities"].values())
    anonymous.close()
    agent = machine(app, reader, ["notes:read"])
    assert agent.get(binding_url()).status_code == 200
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, reader.identifier, "revoked", app.state.clock())
    assert reader.client.get(binding_url()).status_code == agent.get(binding_url()).status_code == 401
    assert not any(reader.client.get("/api/session").json()["capabilities"].values())


def test_hosted_configuration_uses_reviewed_package_and_preserves_local_guards(hosted, tmp_path, monkeypatch):
    app, _, _, _, manifest, _, _ = hosted
    with pytest.raises(RuntimeError, match="development-only"):
        replace(app.state.settings, local_note_corpus_only=True)
    with pytest.raises(RuntimeError, match="absolute manifest"):
        replace(app.state.settings, note_presentation_bindings_path="relative.json")
    with pytest.raises(RuntimeError, match="ordinary notebook corpus"):
        replace(app.state.settings, production=False, local_note_corpus_only=True)
    for key in tuple(os.environ):
        if key == "DATABASE_URL" or key.startswith("CRUCIBLE_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("CRUCIBLE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only.invalid/test")
    monkeypatch.setenv("CRUCIBLE_ORIGIN", ORIGIN)
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"test-only" * 4).decode())
    monkeypatch.setenv("CRUCIBLE_NOTE_PRESENTATION_BINDINGS", str(manifest))
    settings = load_settings()
    assert settings.note_presentation_bindings_path == str(manifest)
    assert settings.note_justifications_available and not settings.local_note_corpus_only
    assert settings.local_note_bindings_path is None
    monkeypatch.delenv("CRUCIBLE_NOTE_PRESENTATION_BINDINGS")
    assert load_settings().note_presentation_bindings_path == str(HOSTED_NOTE_PRESENTATIONS)
    monkeypatch.setenv("CRUCIBLE_NOTE_PRESENTATION_BINDINGS", "")
    assert load_settings().note_presentation_bindings_path is None
    monkeypatch.delenv("CRUCIBLE_NOTE_PRESENTATION_BINDINGS")
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    assert load_settings().note_presentation_bindings_path is None
