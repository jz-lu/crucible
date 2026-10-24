"""The proposal guide is a fixed, read-only document behind the normal beta gate."""
import subprocess
import urllib.request
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from crucible import beta_access, display_notes, registry
from crucible.config import ROOT
from crucible.machine_auth import MachineToken
from test_beta_boundary import private_app
from test_machine_proposals import connect
from test_protocol_proposals import proposal_system


PATH = "/api/protocol/proposal-guide"
EVIDENCE_PATH = "/api/protocol/evidence-note"


@pytest.fixture
def curated_notes(monkeypatch):
    """Source fixtures exercise the real revision/hash resolver before offline publication."""
    def content(title, claim):
        return {"title": title, "claim": claim, "conditions": "Fixture scope only.",
                "body": "A read-only fixture document.", "tags": [], "kind": "argument",
                "references": [], "artifacts": [], "extensions": {}}

    older = content("Earlier evidence", "Earlier exact claim.")
    current = content("Current evidence", "Current exact claim.")
    guide = content("Proposal guide evidence", "The guide is a scoped editorial document.")
    scheme_binding = {"id": "curated:scheme-fixture", "revision": 2,
                      "content_sha256": display_notes.digest(current),
                      "url": "#notebook?note=curated%3Ascheme-fixture&revision=2"}
    guide_binding = {"id": "curated:guide-fixture", "revision": 1,
                     "content_sha256": display_notes.digest(guide),
                     "url": "#notebook?note=curated%3Aguide-fixture&revision=1"}
    bindings = {"format": "crucible-display-note-bindings-v1",
                "releases": [{"scheme_id": "teaching-dh-kem", "version": "7", "note": scheme_binding}],
                "proposal_guide": {"note": guide_binding}}
    collection = {"notes": [
        {"id": "scheme-fixture", "revisions": [{"revision": 1, **older}, {"revision": 2, **current}]},
        {"id": "guide-fixture", "revisions": [{"revision": 1, **guide}]},
        {"id": "unbound-fixture", "revisions": [{"revision": 1, **content("Unbound", "Not selected by this endpoint.")}]},
    ]}
    monkeypatch.setattr(display_notes, "_bindings", lambda: deepcopy(bindings))
    monkeypatch.setattr(display_notes, "_collection", lambda: deepcopy(collection))
    return {"scheme": scheme_binding, "guide": guide_binding, "older": older,
            "current": current, "guide_content": guide}


def assert_guide(response):
    assert response.status_code == 200, response.text
    assert response.content == (ROOT / "docs" / "proposal-guide.txt").read_bytes()
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    # The first offline binding build may not have run in a fresh workspace.
    # Once published, the endpoint must expose precisely that revision's link.
    note = display_notes.proposal_guide()
    if note is None:
        assert "link" not in response.headers
    else:
        assert response.headers["link"] == '</' + note["url"] + '>; rel="describedby"'


def proposal_reader(private_app, scopes=("proposals:read",)):
    browser = private_app.test_login("participant")
    agent, credential = connect(private_app, browser)
    with private_app.state.db.transaction(write=True) as session:
        session.get(MachineToken, credential["token"]["id"]).scopes = list(scopes)
    return browser, agent


def test_open_local_guide_returns_exact_raw_document_without_execution_or_database_writes(proposal_system, monkeypatch):
    app, _, _ = proposal_system

    def forbidden(*args, **kwargs):
        pytest.fail("Reading a proposal guide must not execute cryptography, start workers or retrieve URLs")

    for adapter in set(registry.ADAPTERS.values()):
        if hasattr(adapter, "generate"):
            monkeypatch.setattr(adapter, "generate", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(maxsplit=1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}:
            writes.append(statement)

    event.listen(app.state.db.engine, "before_cursor_execute", observe)
    try:
        client = TestClient(app)
        assert_guide(client.get(PATH))
        # Query strings do not select a different local file or remote source.
        assert_guide(client.get(PATH, params={"path": "../../.local/server.key", "url": "https://example.invalid/code.py"}))
        assert not client.cookies
        assert not writes
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_beta_anonymous_requests_cannot_read_the_guide(private_app, method):
    response = TestClient(private_app).request(method, PATH)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert b"CRUCIBLE" not in response.content
    assert "link" not in response.headers


def test_beta_member_and_proposal_read_token_can_read_the_same_document(private_app):
    browser, agent = proposal_reader(private_app)
    assert_guide(browser.client.get(PATH))
    assert_guide(agent.get(PATH))
    assert not agent.cookies


@pytest.mark.parametrize("scopes", [[], ["challenges:read"], ["proposals:write"]])
def test_unrelated_or_write_only_token_scopes_do_not_grant_guide_reads(private_app, scopes):
    browser, agent = proposal_reader(private_app, scopes)
    # A permitted cookie must not bypass the explicit bearer credential's scope.
    agent.cookies.update(browser.client.cookies)
    response = agent.get(PATH)
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert b"CRUCIBLE" not in response.content
    assert "link" not in response.headers


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_proposal_read_scope_is_not_a_method_wildcard(private_app, method):
    _, agent = proposal_reader(private_app)
    response = agent.request(method, PATH, json={})
    assert response.status_code == 403
    assert "link" not in response.headers


def test_revoked_membership_blocks_a_previously_authorized_proposal_reader(private_app):
    _, agent = proposal_reader(private_app)
    assert_guide(agent.get(PATH))
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, "revoked", private_app.test_now[0])
    response = agent.get(PATH)
    assert response.status_code == 401
    assert b"CRUCIBLE" not in response.content
    assert "link" not in response.headers


def test_proposal_reader_can_follow_the_guide_note_without_gaining_challenge_scope(private_app, curated_notes):
    _, agent = proposal_reader(private_app)
    assert_guide(agent.get(PATH))
    response = agent.get(PATH, params={"note": "true"})
    assert response.status_code == 200, response.text
    note = response.json()["note"]
    assert note["content"] == curated_notes["guide_content"]
    assert (note["id"], note["revision"], note["content_sha256"]) == (
        curated_notes["guide"]["id"], 1, curated_notes["guide"]["content_sha256"])
    assert note["review_status"] == "editorial-inclusion-not-certification"
    assert agent.get(EVIDENCE_PATH, params={"scheme_id": "teaching-dh-kem", "version": "7"}).status_code == 403
    assert agent.get("/map/notes.json").status_code == 403


def test_challenge_reader_can_fetch_bound_current_and_historical_evidence_but_not_guide(private_app, curated_notes):
    _, agent = proposal_reader(private_app, ["challenges:read"])
    params = {"scheme_id": "teaching-dh-kem", "version": "7", "revision": 2,
              "sha256": curated_notes["scheme"]["content_sha256"]}
    response = agent.get(EVIDENCE_PATH, params=params)
    assert response.status_code == 200, response.text
    assert response.json()["note"]["content"] == curated_notes["current"]
    assert response.json()["note"]["revision"] == 2
    assert response.headers["cache-control"] == "no-store"
    historical = {**params, "revision": 1, "sha256": display_notes.digest(curated_notes["older"])}
    response = agent.get(EVIDENCE_PATH, params=historical)
    assert response.status_code == 200 and response.json()["note"]["content"] == curated_notes["older"]
    assert agent.get(PATH).status_code == agent.get(PATH, params={"note": "true"}).status_code == 403
    assert agent.get("/map/notes.json").status_code == 403


def test_evidence_note_rejects_wrong_hash_revision_and_release_without_arbitrary_note_selection(private_app, curated_notes):
    _, agent = proposal_reader(private_app, ["challenges:read"])
    base = {"scheme_id": "teaching-dh-kem", "version": "7", "revision": 2,
            "sha256": curated_notes["scheme"]["content_sha256"]}
    for changes in ({"sha256": "0" * 64}, {"revision": 99}, {"revision": 1},
                    {"version": "6"}, {"version": "999"}, {"scheme_id": "../../unbound-fixture"}):
        response = agent.get(EVIDENCE_PATH, params={**base, **changes})
        assert response.status_code == 404, (changes, response.text)
        assert "content" not in response.json()
    # An unsupported query parameter must not change the curator-selected identity.
    response = agent.get(EVIDENCE_PATH, params={**base, "note_id": "curated:unbound-fixture"})
    assert response.status_code == 200
    assert response.json()["note"]["id"] == curated_notes["scheme"]["id"]


@pytest.mark.parametrize("path,params", [
    (PATH, {"note": "true"}),
    (EVIDENCE_PATH, {"scheme_id": "teaching-dh-kem", "version": "7"}),
])
def test_note_reads_retain_beta_and_exact_method_boundaries(private_app, curated_notes, path, params):
    assert TestClient(private_app).get(path, params=params).status_code == 401
    _, agent = proposal_reader(private_app, ["proposals:read", "challenges:read"])
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = agent.request(method, path, params=params, json={})
        assert response.status_code == 403
        assert "content" not in response.json()
