import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible.method_sources import SourceArtifact, MethodSource
from test_api import Actor
from test_calibrated_api import current_system


def bundle(content="print('participant code, stored only')", path="attack/solve.py"):
    return {"format": "crucible-source-v1", "files": [{"path": path, "content": content}]}


def participants(app):
    alice, bob = Actor(app), Actor(app)
    alice.register("source_alice")
    bob.register("source_bob")
    return alice, bob


def test_source_is_owned_inert_then_published_with_exact_receipt(current_system, tmp_path):
    app, _, _ = current_system
    alice, bob = participants(app)
    method = alice.method()
    path = f'/api/methods/{method["id"]}/source'
    marker = tmp_path / "must-not-exist"
    payload = bundle(f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n")
    response = alice.post(path, payload)
    assert response.status_code == 200, response.text
    descriptor = response.json()["source"]
    assert descriptor["files"] == 1 and len(descriptor["sha256"]) == 64
    assert alice.client.get(path).json() == payload
    assert bob.client.get(path).status_code == TestClient(app).get(path).status_code == 404
    assert bob.post(path, payload).status_code == 404
    assert alice.post(path, payload).json()["source"] == descriptor
    assert alice.post(path, bundle("changed")).status_code == 409
    challenge = alice.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 1}).json()["challenge"]
    answer = alice.answer(challenge, method)
    receipt = alice.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert receipt.status_code == 200 and receipt.json()["submission"]["status"] == "correct"
    public = TestClient(app)
    assert public.get(path).json() == payload
    detail = public.get('/api/submissions/' + receipt.json()["submission"]["id"]).json()["submission"]
    assert detail["method"]["source"] == descriptor
    assert payload["files"][0]["content"] not in json.dumps(detail)
    assert answer["candidate"] not in json.dumps(detail)
    download = public.get(descriptor["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist() == ["attack/solve.py"]
        assert archive.read("attack/solve.py").decode() == payload["files"][0]["content"]
    assert alice.post(path, payload).status_code == 200  # Lost-upload retry after final answer.
    assert not marker.exists()


@pytest.mark.parametrize("attach_first", [True, False])
def test_wrong_answer_never_publishes_source_and_final_answer_seals_evidence(current_system, attach_first):
    app, _, _ = current_system
    alice, bob = participants(app)
    method = alice.method()
    path = f'/api/methods/{method["id"]}/source'
    if attach_first:
        assert alice.post(path, bundle()).status_code == 200
    challenge = alice.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 1}).json()["challenge"]
    answer = alice.answer(challenge, method)
    answer["candidate"] = ("01" if answer["candidate"].startswith("00") else "00") + answer["candidate"][2:]
    assert alice.post(f'/api/challenges/{challenge["id"]}/submit', answer).json()["submission"]["status"] == "incorrect"
    assert bob.client.get(path).status_code == 404
    assert TestClient(app).get(path + "/download").status_code == 404
    assert alice.post(path, bundle()).status_code == (200 if attach_first else 409)


def test_canonical_snapshot_deduplicates_without_giving_another_owner_access(current_system):
    app, _, _ = current_system
    alice, bob = participants(app)
    payload = bundle()
    payload["files"].append({"path": "method.md", "content": "Description."})
    first = alice.method()
    second = bob.method()
    a = alice.post(f'/api/methods/{first["id"]}/source', payload).json()["source"]
    payload["files"].reverse()
    b = bob.post(f'/api/methods/{second["id"]}/source', payload).json()["source"]
    assert a["sha256"] == b["sha256"]
    assert bob.client.get(a["url"]).status_code == 404
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SourceArtifact)) == 1
        assert session.scalar(select(func.count()).select_from(MethodSource)) == 2


@pytest.mark.parametrize("path", ["../secret", "/tmp/attack.py", "attack/../key", "attack//file", ".env",
    "attack/.git/config", "attack\\solve.py", "C:/attack.py", "attack/CON.txt", "attack/x.",
    "attack/x ", "attack/./file", "attack/\x00.py"])
def test_unsafe_archive_paths_rejected(current_system, path):
    app, _, _ = current_system
    alice = Actor(app)
    alice.register("path_owner")
    response = alice.post(f'/api/methods/{alice.method()["id"]}/source', bundle(path=path))
    assert response.status_code == 422
    assert "participant code" not in response.text


@pytest.mark.parametrize("payload", [
    {"format": "crucible-source-v1", "files": []},
    {**bundle(), "execute": True},
    {"format": "crucible-source-v1", "files": [{"path": "x", "content": 5}]},
    bundle("nul\x00content"), bundle("\ud800"), bundle("x" * (256 * 1024 + 1)),
    bundle("é" * (128 * 1024 + 1)),
    {"format": "crucible-source-v1", "files": [{"path": "X.py", "content": "a"}, {"path": "x.py", "content": "b"}]},
    {"format": "crucible-source-v1", "files": [{"path": "attack", "content": "a"}, {"path": "attack/x.py", "content": "b"}]},
    {"format": "crucible-source-v1", "files": [{"path": f"f{i}.py", "content": "x"} for i in range(101)]},
    {"format": "crucible-source-v1", "files": [{"path": f"f{i}.py", "content": "x" * (256 * 1024)} for i in range(5)]},
])
def test_invalid_or_oversized_sources_rejected_without_echo(current_system, payload):
    app, _, _ = current_system
    alice = Actor(app)
    alice.register("size_owner")
    # Ensure JSON encodes lone surrogates instead of failing in the HTTP client.
    response = alice.client.post(f'/api/methods/{alice.method()["id"]}/source', content=json.dumps(payload),
        headers={"Origin": "http://testserver", "X-CSRF-Token": alice.csrf, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert len(response.content) < 500


def test_upload_has_own_byte_limit_and_keeps_browser_csrf(current_system):
    app, _, _ = current_system
    alice = Actor(app)
    alice.register("limit_owner")
    path = f'/api/methods/{alice.method()["id"]}/source'
    payload = bundle("x" * 70000)
    assert alice.post(path, payload).status_code == 200
    assert alice.client.post(path, json=payload, headers={"Origin": "http://testserver"}).status_code == 403
    assert alice.client.post(path, json=payload).status_code == 403
    huge = json.dumps(bundle("x" * (2 * 1024 * 1024)))
    assert alice.client.post(path, content=huge, headers={"Origin": "http://testserver", "Content-Type": "application/json"}).status_code == 413
    assert alice.post("/api/methods", {"name": "x", "pseudocode": "x" * 70000}).status_code == 413


def test_scoped_agent_can_attach_and_read_source_but_revocation_blocks_it(current_system):
    from test_machine_auth import connected
    app, _, _ = current_system
    browser, agent, _, credential = connected(app)
    method = agent.post("/api/methods", json={"name": "Agent source", "pseudocode": "Inspect public inputs."}).json()["method"]
    path = f'/api/methods/{method["id"]}/source'
    assert agent.post(path, json=bundle()).status_code == 200
    assert agent.get(path).json() == bundle()
    assert TestClient(app).get(path).status_code == 404
    assert browser.post(f'/api/agent/tokens/{credential["token"]["id"]}/revoke', {}).status_code == 200
    assert agent.get(path).status_code == 401
    assert agent.post(path, json=bundle()).status_code == 401
    assert browser.client.get(path).json() == bundle()


def test_agent_and_source_migrations_are_additive_and_preserve_accounts(tmp_path, monkeypatch):
    import base64
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from crucible.db import Database, LoginSession, User
    root = Path(__file__).resolve().parents[1]
    url = f"sqlite:///{tmp_path / 'migrations.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0004")
    db = Database(url)
    with db.transaction(write=True) as session:
        session.add(User(id="unchanged-user", handle="unchanged", password_hash="unchanged-hash", created_at=1))
        session.flush()
        session.add(LoginSession(token_hash="a" * 64, user_id="unchanged-user", expires_at=9999999999))
    command.upgrade(config, "head")
    assert {"machine_tokens", "device_connections", "source_artifacts", "method_sources"} <= set(inspect(db.engine).get_table_names())
    with db.transaction() as session:
        assert session.get(User, "unchanged-user").password_hash == "unchanged-hash"
        assert session.get(LoginSession, "a" * 64).user_id == "unchanged-user"
    command.downgrade(config, "0004")
    assert "source_artifacts" not in inspect(db.engine).get_table_names()
    with db.transaction() as session:
        assert session.get(User, "unchanged-user").handle == "unchanged"
        assert session.get(LoginSession, "a" * 64).expires_at == 9999999999
    db.engine.dispose()
