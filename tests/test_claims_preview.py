"""The explicit developer preview cannot adopt hosted data or enable real logins."""
from dataclasses import replace
import json

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import dh_v4, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import LoginSession, User
from scripts.prepare_note_integration import AUTHOR, REVIEWER
from scripts.serve_claims_preview import attach_preview_entry, preview_environment, workspace


@pytest.mark.parametrize("environment", [
    {"DATABASE_URL": "postgresql://example/production"},
    {"DATABASE_URL": "sqlite:///other-research.db"},
    {"CRUCIBLE_SECRET": "do-not-use"},
    {"CRUCIBLE_ENV": "production"},
    {"CRUCIBLE_DATA_DIR": ".local/existing-notes"},
])
def test_host_configuration_is_refused_before_local_setup(tmp_path, environment):
    with pytest.raises(RuntimeError):
        preview_environment(environment, tmp_path)
    assert not list(tmp_path.iterdir())


def test_preview_never_inherits_model_mail_or_provider_settings(tmp_path):
    supplied = {"PATH": "/bin", "OPENAI_API_KEY": "not-a-real-key",
                "CRUCIBLE_GITHUB_ENABLED": "true", "CRUCIBLE_GITHUB_CLIENT_SECRET": "dummy",
                "CRUCIBLE_PUBLICATION_SCREENING_MODE": "automatic", "CRUCIBLE_SMTP_PASSWORD": "dummy"}
    clean = preview_environment(supplied, tmp_path)
    assert clean["PATH"] == "/bin"
    assert clean["CRUCIBLE_PUBLICATION_SCREENING_MODE"] == "disabled"
    assert clean["CRUCIBLE_GITHUB_ENABLED"] == "false"
    assert clean["CRUCIBLE_MAIL_BACKEND"] == "disabled"
    assert clean["CRUCIBLE_LOCAL_NOTE_CORPUS_ONLY"] == "true"
    assert not {"OPENAI_API_KEY", "CRUCIBLE_SMTP_PASSWORD", "CRUCIBLE_GITHUB_CLIENT_SECRET"} & clean.keys()
    assert supplied["OPENAI_API_KEY"] == "not-a-real-key"


def test_workspace_is_repeatable_but_does_not_adopt_unmarked_data(tmp_path):
    directory = workspace(tmp_path)
    assert directory == tmp_path / ".local" / "claims-preview"
    assert workspace(tmp_path) == directory
    (directory / "preview-workspace.json").unlink()
    database = directory / "crucible.db"
    database.write_bytes(b"existing research")
    with pytest.raises(RuntimeError, match="unmarked data"):
        workspace(tmp_path)
    assert database.read_bytes() == b"existing research"


@pytest.mark.parametrize("location", [".local", ".local/claims-preview", ".local/claims-preview/crucible.db"])
def test_symlink_cannot_redirect_the_workspace(tmp_path, location):
    target = tmp_path / "outside"
    target.mkdir()
    path = tmp_path / location
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target)
    with pytest.raises(RuntimeError, match="symlink"):
        workspace(tmp_path)
    assert not list(target.iterdir())


@pytest.fixture
def preview(tmp_path, monkeypatch):
    # Portable application tests, not substitutes for preview native validation.
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})

    class PreviewSettings(Settings):
        @property
        def cookie_name(self):
            return "crucible_claims_preview_8000"

    settings = PreviewSettings(f"sqlite:///{tmp_path / 'crucible.db'}", b"s" * 32,
        origins=("http://127.0.0.1:8000",), local_note_corpus_only=True, rate_limits=False,
        local_note_bindings_path=str(tmp_path / "scheme-note-bindings.json"), reviewer_ids=(REVIEWER,))
    app = create_app(settings, initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in [(AUTHOR, "preview-author"), (REVIEWER, "preview-reviewer")]:
            session.add(User(id=identifier, handle=handle, password_hash="local-fixture-no-password-login", created_at=0))
    attach_preview_entry(app, tmp_path)
    yield app, settings, tmp_path
    app.state.db.engine.dispose()


def client(app, *, address="127.0.0.1", host="127.0.0.1:8000"):
    return TestClient(app, base_url="http://" + host, client=(address, 31000), follow_redirects=False)


def test_only_explicit_loopback_entry_creates_synthetic_sessions(preview):
    app, settings, directory = preview
    browser = client(app)
    assert browser.get("/").status_code == 200
    assert settings.cookie_name not in browser.cookies
    for request in (
        lambda: client(app, address="203.0.113.9").get("/preview"),
        lambda: client(app, host="evil.example").get("/preview"),
        lambda: browser.get("/preview", headers={"Sec-Fetch-Site": "cross-site"}),
        lambda: browser.get("/preview", headers={"Origin": "https://evil.example"}),
    ):
        assert request().status_code == 403
    assert browser.get("/preview?persona=administrator").status_code == 400
    assert browser.get("/preview?trial=1").status_code == 503
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == 0

    response = browser.get("/preview")
    assert response.status_code == 303
    assert response.headers["location"] == "/#challenge"
    assert response.headers["cache-control"] == "no-store"
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    assert browser.get("/api/session").json()["user"]["id"] == AUTHOR
    assert browser.get("/preview?persona=reviewer").status_code == 303
    assert browser.get("/api/session").json()["user"]["id"] == REVIEWER

    guide = "00000000-0000-0000-0000-000000000012"
    (directory / "statement-trial-receipt.json").write_text(json.dumps({"guide_note_id": guide}))
    response = browser.get("/preview?persona=reviewer&trial=1")
    assert response.headers["location"] == "/#notebook?note=" + guide


def test_normal_application_does_not_gain_a_preview_entry(preview):
    preview_app, settings, _ = preview
    ordinary = create_app(replace(settings, local_note_corpus_only=False, local_note_bindings_path=None))
    try:
        assert client(ordinary).get("/preview").status_code == 404
        assert ordinary.state.access_policy.audience("GET", "/preview") is None
        assert preview_app.state.access_policy.audience("GET", "/preview") is None
    finally:
        ordinary.state.db.engine.dispose()
