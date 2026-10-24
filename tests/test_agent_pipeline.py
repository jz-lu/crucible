"""Agent orientation is inert documentation, not a new credential capability."""
from dataclasses import replace
import json
import secrets
import subprocess
import urllib.request
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from crucible import beta_access, machine_auth, registry
from crucible.app import create_app
from crucible.config import ROOT
from crucible.security import token_hash
from test_beta_boundary import private_app


PIPELINE = "/api/agent/pipeline"
PREFIX = "/api/agent/guidance/"
RESOURCE_NAMES = {
    "agent-workflow.txt", "note-format.txt", "note-requests.txt",
    "request-types.json", "proposal-guide.txt", "correctness-policy.txt",
    "ladder-principles.txt", "scoring-policy.txt", "scoring-base.txt",
    "benchmark-policy.txt",
}
READ_PATHS = [PIPELINE, *(PREFIX + name for name in sorted(RESOURCE_NAMES))]


def credential(app, scopes=machine_auth.SCOPES, *, state="active"):
    """Create a narrowly scoped credential without exercising device onboarding."""
    raw = "cru_" + secrets.token_urlsafe(32)
    now = app.test_now[0]
    identifier = str(uuid4())
    with app.state.db.transaction(write=True) as session:
        session.add(machine_auth.MachineToken(
            id=identifier, user_id=app.test_member, token_hash=token_hash(raw),
            label="Pipeline boundary fixture", scopes=scopes, created_at=now - 1,
            expires_at=now if state == "expired" else now + 60,
            revoked_at=now if state == "revoked" else None))
    return {"Authorization": "Bearer " + raw}


def assert_document(response, path):
    from crucible import agent_guidance

    assert response.status_code == 200, (path, response.text)
    if path == PIPELINE:
        expected = ROOT / "web" / "agent" / "pipeline.json"
        media_type = "application/json"
    else:
        relative_path, media_type = agent_guidance.RESOURCES[path.removeprefix(PREFIX)]
        expected = ROOT / relative_path
    assert response.content == expected.read_bytes()
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_beta_anonymous_pipeline_and_all_guidance_reads_are_private(private_app, method):
    with TestClient(private_app) as client:
        for path in READ_PATHS:
            response = client.request(method, path)
            assert response.status_code == 401, (path, response.text)
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "link" not in response.headers
            assert b"crucible-agent-pipeline-v1" not in response.content


def test_beta_browser_member_can_read_exact_documents(private_app):
    member = private_app.test_login("participant")
    for path in READ_PATHS:
        assert_document(member.client.get(path), path)


@pytest.mark.parametrize("scope", machine_auth.SCOPES)
def test_each_existing_single_scope_can_read_orientation_only(private_app, scope):
    headers = credential(private_app, [scope])
    with TestClient(private_app) as client:
        for path in READ_PATHS:
            assert_document(client.get(path, headers=headers), path)
        # Only the explicit own-notebook read permission can read these routes.
        for path in ("/api/notebook/notes", "/api/notebook/export", "/map/notes.json"):
            expected = 200 if scope == "notebook:read" and path != "/map/notes.json" else 403
            assert client.get(path, headers=headers).status_code == expected
        assert not client.cookies


@pytest.mark.parametrize("scopes", [[], ["unknown:read"], ["*"], {"challenges:read": True}])
def test_unknown_empty_and_malformed_scopes_do_not_pass_with_a_member_cookie(private_app, scopes):
    member = private_app.test_login("participant")
    headers = credential(private_app, scopes)
    for path in READ_PATHS:
        response = member.client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)


@pytest.mark.parametrize("state", ["invalid", "unrecognized", "expired", "revoked"])
def test_bad_credentials_cannot_fall_back_to_a_valid_member_cookie(private_app, state):
    member = private_app.test_login("participant")
    if state == "invalid":
        headers = {"Authorization": "Bearer not-a-credential"}
    elif state == "unrecognized":
        headers = {"Authorization": "Bearer cru_" + secrets.token_urlsafe(32)}
    else:
        headers = credential(private_app, state=state)
    for path in READ_PATHS:
        response = member.client.get(path, headers=headers)
        assert response.status_code == 401, (path, response.text)
        assert response.headers["cache-control"] == "no-store"


def test_valid_token_still_requires_current_beta_membership(private_app):
    headers = credential(private_app, ["proposals:write"])
    with private_app.state.db.transaction(write=True) as session:
        session.delete(session.get(beta_access.BetaMembership, private_app.test_member))
    with TestClient(private_app) as client:
        for path in READ_PATHS:
            response = client.get(path, headers=headers)
            assert response.status_code == 403, (path, response.text)
            assert response.headers["cache-control"] == "no-store"


def test_membership_revocation_invalidates_prior_pipeline_access(private_app):
    member = private_app.test_login("participant")
    headers = credential(private_app, ["methods:read"])
    assert_document(member.client.get(PIPELINE, headers=headers), PIPELINE)
    with private_app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, private_app.test_member, "revoked", private_app.test_now[0])
    for path in READ_PATHS:
        assert member.client.get(path, headers=headers).status_code == 401
        assert member.client.get(path).status_code == 401


def test_document_permission_is_not_a_method_or_route_wildcard(private_app):
    headers = credential(private_app, ["challenges:read"])
    with TestClient(private_app) as client:
        for path in READ_PATHS:
            for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                response = client.request(method, path, headers=headers, json={})
                assert response.status_code == 403, (method, path, response.text)
        for path in ("/api/challenges", "/api/notebook/notes", "/api/protocol/proposals/validate"):
            assert client.post(path, headers=headers, json={}).status_code == 403
        # Existing proposal guidance retains its specific proposal-read scope.
        assert client.get("/api/protocol/proposal-guide", headers=headers).status_code == 403
        write_only = credential(private_app, ["challenges:write"])
        assert client.get("/api/schemes", headers=write_only).status_code == 403


def test_unknown_files_and_traversal_are_never_guidance(private_app):
    member = private_app.test_login("participant")
    headers = credential(private_app)
    paths = [
        PREFIX + "unknown.txt", PREFIX + "app.py", PREFIX + "pipeline.json",
        PREFIX + ".env", PREFIX + "..%2F..%2Fcrucible%2Fapp.py",
        PREFIX + "%2e%2e%2f%2e%2e%2f.local%2fserver.key",
        PREFIX + "%252e%252e%252f.local%252fserver.key",
        PREFIX + "..%5c..%5c.local%5cserver.key",
        PREFIX + "proposal-guide.txt%2F..%2F..%2Fapp.py",
        "/web/agent/pipeline.json", "/agent/pipeline.json", "/docs/proposal-guide.txt",
    ]
    for path in paths:
        assert member.client.get(path).status_code == 404, path
        response = member.client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)
    # Caller-supplied filenames or URLs cannot select a different document.
    for path in (PIPELINE, PREFIX + "proposal-guide.txt"):
        response = member.client.get(path, params={"path": "../../.local/server.key",
                                                  "url": "https://example.invalid/code.py"})
        assert_document(response, path)


def test_open_mode_serves_inert_documents_without_auth_execution_or_database_writes(private_app, monkeypatch):
    app = create_app(replace(private_app.state.settings, access_mode="open"),
                     clock=lambda: private_app.test_now[0])

    def forbidden(*args, **kwargs):
        pytest.fail("Reading agent orientation must not generate challenges, execute code or fetch URLs")

    for adapter in set(registry.ADAPTERS.values()):
        if hasattr(adapter, "generate"):
            monkeypatch.setattr(adapter, "generate", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    writes = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(maxsplit=1)[0].upper() in {
                "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER"}:
            writes.append(statement)

    event.listen(app.state.db.engine, "before_cursor_execute", observe)
    try:
        with TestClient(app) as client:
            for path in READ_PATHS:
                assert_document(client.get(path), path)
            assert not client.cookies
        assert not writes
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", observe)
        app.state.db.engine.dispose()


def test_pipeline_resources_and_rule_references_resolve_to_the_explicit_allowlist():
    from crucible import agent_guidance

    pipeline = json.loads((ROOT / "web" / "agent" / "pipeline.json").read_text())
    assert pipeline["format"] == "crucible-agent-pipeline-v1"
    assert pipeline["schema_version"] == 1
    assert pipeline["entrypoint"] == PIPELINE
    assert pipeline["audience"] == "models-and-agents"
    assert set(agent_guidance.RESOURCES) == RESOURCE_NAMES
    resources = pipeline["resources"]
    by_id = {resource["id"]: resource for resource in resources}
    assert len(by_id) == len(resources)
    assert {resource["path"] for resource in resources} == {
        PREFIX + name for name in RESOURCE_NAMES}
    for resource in resources:
        name = resource["path"].removeprefix(PREFIX)
        relative_path, _ = agent_guidance.RESOURCES[name]
        assert (ROOT / relative_path).is_file()
        assert resource["role"].strip()
    for step in pipeline["reading_plan"]:
        assert step["read"] and set(step["read"]) <= by_id.keys()
    rules = pipeline["rules"]
    assert rules and len({rule["id"] for rule in rules}) == len(rules)
    for rule in rules:
        assert rule["level"] in {"MUST", "SHOULD"}
        assert rule["kind"] in {"policy", "recommendation"}
        assert rule["instruction"].strip()
        assert rule["applies_to"]
        assert rule["sources"] and set(rule["sources"]) <= by_id.keys()


def test_retraction_guidance_separates_research_advice_from_author_authority():
    pipeline = json.loads((ROOT / "web/agent/pipeline.json").read_text())
    rules = {rule["id"]: rule for rule in pipeline["rules"]}
    for key in ("research.correct-course", "research.scoped-criticism"):
        assert rules[key]["level"] == "SHOULD"
        assert rules[key]["kind"] == "recommendation"
        assert "note-requests.txt" in rules[key]["sources"]
    authority = rules["authority.author-retraction"]
    assert authority["level"] == "MUST"
    assert "explicit authorization" in authority["instruction"]
    assert "do not silently withdraw" in authority["instruction"]
    capability = next(item for item in pipeline["capabilities"]["current"]
                      if item["id"] == "author-retraction")
    assert "/api/protocol/proposals/{proposal_id}/retraction" in capability["interface"]
    assert "/api/notebook/research-requests/{request_id}/retraction" in capability["interface"]
    assert "Browser-only, author-only" in capability["scope"]
    assert "ordinary bearer credentials are not accepted" in capability["scope"]
    assert len(json.dumps(pipeline).encode()) < 65536


def test_agent_contract_tracks_opt_in_notebook_and_human_publication_review():
    pipeline = json.loads((ROOT / "web/agent/pipeline.json").read_text())
    catalogue = json.loads((ROOT / "web/notebook/request-types.json").read_text())
    assert "technical_review" not in {row["id"] for row in catalogue["actions"]}
    capabilities = {item["id"]: item for item in pipeline["capabilities"]["current"]}
    assert "technical_review is retired" in capabilities["notebook-and-change-proposals"]["scope"]
    assert "private-note-sharing" not in capabilities
    publication = capabilities["note-publication"]
    assert "publication-requests" in publication["interface"]
    assert "Author-only" in publication["scope"]
    assert "notebook:publish" in publication["scope"]
    assert "Agents cannot approve publication" in publication["scope"]
    owned = capabilities["agent-owned-notebook"]
    assert "notebook:read" in owned["scope"] and "notebook:write" in owned["scope"]
    assert "retry file" in owned["scope"]
    rule = next(row for row in pipeline["rules"] if row["id"] == "authority.note-publication")
    assert "Later edits remain private" in rule["instruction"]
    assert "historical grants confer no access" in rule["instruction"]
    assert "Do not forward another author" in rule["instruction"]
