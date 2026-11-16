"""Release-image gate: actual curated bindings against the actual native runtime.

Run this suite in the reviewed Linux image with the candidate source installed.
It creates only a disposable test database, never imports fixture publications,
and never replaces registry manifests or bypasses implementation verification.
"""
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

from crucible import beta_access, notebook, registry
from crucible.app import create_app
from crucible.config import load_settings
from crucible.db import User
from test_note_presentations import Actor, ORIGIN

# Release checks are mounted at /tests, separately from the installed package.
ROOT = Path(notebook.__file__).resolve().parents[1]


def test_all_eight_packaged_hosted_bindings_have_exact_context_and_native_accounts(tmp_path, monkeypatch):
    path = ROOT / "crucible/data/scheme_note_bindings.json"
    manifest = json.loads(path.read_text())
    for name in tuple(os.environ):
        if name in {"DATABASE_URL", "OPENAI_API_KEY"} or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CRUCIBLE_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://native-check-only.invalid/test")
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"t" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ORIGIN", ORIGIN)
    # Exercise production defaults, then use only a disposable local database.
    settings = replace(load_settings(), rate_limits=False,
                       database_url=f"sqlite:///{tmp_path / 'read-only-release-check.db'}")
    assert settings.note_presentation_bindings_path == str(path)
    app = create_app(settings, clock=lambda: 3600600.0, initialize=True)
    try:
        identifier = str(uuid4())
        with app.state.db.transaction(write=True) as session:
            session.add(User(id=identifier, handle="release-check-reader", password_hash="unused-test-only", created_at=3600600.0))
            session.flush()
            beta_access.set_membership(session, identifier, "active", 3600600.0)
        reader = Actor(app, identifier)
        capabilities = reader.client.get("/api/session").json()["capabilities"]
        assert capabilities["curated_notes"] and capabilities["note_justifications"] and capabilities["ram_attack_comparison"]
        assert not capabilities["statement_workflow_trial"] and not capabilities["local_note_justifications"]
        assert not any(route.path.startswith("/api/notebook/statement-workflow") for route in app.routes)
        collection = {note["id"]: note for note in notebook.curated_collection()}
        assert len(manifest["releases"]) == 8
        total = 0
        for binding in manifest["releases"]:
            response = reader.client.get("/api/protocol/note-justifications", params={
                "scheme_id": binding["scheme_id"], "version": binding["version"]})
            assert response.status_code == 200, (binding["scheme_id"], response.text)
            packet = response.json()
            assert packet["note"] == binding["note"] and packet["scientific_adoption"] is False
            selected = next(r for r in collection[packet["note"]["id"]]["revisions"]
                            if r["revision"] == packet["note"]["revision"])
            assert selected["content_sha256"] == packet["note"]["content_sha256"]
            content = selected["content"]
            assert packet["attack_comparison"]["analysis_sha256"] == content["extensions"]["ram_analysis_sha256"]
            profiles = {m["id"] for m in registry.adapter_for(binding["scheme_id"], binding["version"]).manifests()}
            assert {p["profile_id"] for p in packet["attack_comparison"]["estimates"]} == profiles
            total += len(profiles)
            for argument in [*packet["scheme_arguments"].values(),
                             *(model["argument"] for model in packet["graph_models"].values())]:
                assert argument["origin"] == "canonical" and argument["scientific_adoption"] is False
                assert "presentation" not in argument
                statements = [argument["resolved_conclusion_statement"], *(use["statement"] for use in argument["dependencies"])]
                for statement in statements:
                    assert statement["source"] == {**binding["note"], "statement": statement["id"]}
                    assert sum(section["body"].count("[[statement:" + statement["id"] + "]]")
                               for section in content["sections"]) == 1
        assert total == 379
    finally:
        app.state.db.engine.dispose()
