"""Exercise the standalone CLI's HTTP client against isolated real API routes."""
from io import BytesIO
import json
import time
from urllib.error import HTTPError, URLError

import pytest

from scripts import crucible_cli as cli
from crucible import notebook, notebook_trash
from test_notebook import system, content, count, workspace, create, revise
from test_notebook_agents import agent


@pytest.fixture
def connected_cli(system, tmp_path, monkeypatch):
    app, owner, *_ = system
    api = agent(app, owner)
    config = tmp_path / "credentials.json"
    server = "http://localhost:8000"
    cli.save_json(config, {"format": "crucible-client-config-v1", "default_server": server,
        "servers": {server: {"access_token": api.headers["Authorization"].removeprefix("Bearer "),
                             "expires_at": time.time() + 3600, "handle": "author"}}})
    state = {"requests": [], "lose_once": False, "bad_hash": False}

    class Reply(BytesIO):
        def __init__(self, data, url):
            super().__init__(data)
            self.url = url

    class Transport:
        def open(self, request, timeout):
            path = request.full_url.removeprefix(server)
            state["requests"].append((request.get_method(), path, request.data))
            result = api.request(request.get_method(), path, content=request.data, headers=dict(request.header_items()))
            if result.status_code >= 400:
                raise HTTPError(request.full_url, result.status_code, "fixture", result.headers, BytesIO(result.content))
            if state["lose_once"] and request.get_method() == "POST":
                state["lose_once"] = False
                raise URLError("lost after commit")
            data = result.content
            if state["bad_hash"] and "note" in result.json():
                body = result.json()
                body["note"]["revisions"][-1]["content"]["body"] = "tampered"
                data = cli.encode(body)
            return Reply(data, request.full_url)

    original = cli.Client.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.opener = Transport()
    monkeypatch.setattr(cli.Client, "__init__", initialize)
    def invoke(*args):
        return cli.main(["--config", str(config), "--json", "notebook", *map(str, args)])
    return app, owner, invoke, state, config, tmp_path


def output(capsys):
    return json.loads(capsys.readouterr().out)


def test_create_lost_response_retries_exact_write_and_rejects_changed_content(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    source = root / "note.json"
    cli.save_json(source, content())
    state["lose_once"] = True
    assert invoke("create", "--file", source) == 1
    assert output(capsys)["error"]["code"] == "network_error"
    assert count(app, notebook.NotebookNote) == 1
    retry = root / "note.json.create-retry.json"
    assert retry.stat().st_mode & 0o777 == 0o600
    assert "receipt" not in cli.load_json(retry)
    assert invoke("create", "--file", source) == 0
    receipt = output(capsys)["receipt"]
    assert receipt["revision"] == 1 and receipt["publication_status"] == "private"
    assert state["requests"][0] == state["requests"][1]
    assert count(app, notebook.NotebookNote) == 1
    cli.save_json(source, content(title="Changed after sending"))
    before = len(state["requests"])
    assert invoke("create", "--file", source) == 1
    assert output(capsys)["error"]["code"] == "notebook_retry_changed"
    assert len(state["requests"]) == before


def test_cli_import_export_and_followup_publication_are_end_to_end(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    source = root / "workspace.json"
    cli.save_json(source, workspace())
    assert invoke("import", "--file", source) == 0
    receipt = output(capsys)["receipt"]
    identifier = receipt["id_map"]["local-note"]
    assert invoke("list", "--search", "Imported") == 0
    assert [note["id"] for note in output(capsys)["notes"]] == [identifier]
    assert invoke("show", identifier) == 0
    note = output(capsys)["note"]
    assert invoke("export", "--note-id", identifier, "--out", root / "export.json") == 0
    output(capsys)
    exported = cli.load_json(root / "export.json")
    assert exported["notes"][0]["id"] == identifier
    assert exported["notes"][0]["revisions"][0]["body"] == content()["body"]
    retry = root / "publication.json"
    state["lose_once"] = True
    args = ("request-publication", identifier, "--revision", 1, "--sha256", note["content_sha256"], "--retry", retry)
    assert invoke(*args) == 1
    output(capsys)
    assert invoke(*args) == 0
    assert output(capsys)["receipt"]["publication_status"] == "submitted"
    assert count(app, notebook.NotebookPublication) == 1
    assert count(app, notebook.NotebookNote) == 1
    assert state["requests"][-1] == state["requests"][-2]
    assert invoke("import", "--file", source) == 0
    assert output(capsys)["receipt"]["replayed"]
    assert count(app, notebook.NotebookImport) == 1


def test_cli_revision_checks_base_and_does_not_publish_edits(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    first = create(owner)
    source = root / "revised.json"
    cli.save_json(source, content(title="CLI revision"))
    args = ("revise", first["id"], "--file", source, "--revision", 1, "--sha256", first["content_sha256"])
    assert invoke(*args) == 0
    receipt = output(capsys)["receipt"]
    assert receipt["revision"] == 2 and receipt["publication_status"] == "private"
    assert invoke(*args) == 0
    assert output(capsys)["receipt"]["revision"] == 2
    assert count(app, notebook.NotebookRevision) == 2
    assert invoke(*args, "--retry", root / "new-request.json") == 1
    assert output(capsys)["error"]["code"] == "http_409"
    assert count(app, notebook.NotebookRevision) == 2
    assert invoke("request-publication", first["id"], "--revision", 1, "--sha256", first["content_sha256"]) == 1
    assert output(capsys)["error"]["code"] == "http_409"
    assert count(app, notebook.NotebookPublication) == 0


def test_cli_retry_is_bound_to_owner_and_server(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    source = root / "note.json"
    cli.save_json(source, content())
    assert invoke("create", "--file", source) == 0
    output(capsys)
    saved = cli.configuration(config)
    original = saved["default_server"]
    saved["servers"][original]["handle"] = "reviewer"
    cli.save_json(config, saved)
    before = len(state["requests"])
    assert invoke("create", "--file", source) == 1
    assert output(capsys)["error"]["code"] == "notebook_retry_changed"
    saved["servers"][original]["handle"] = "author"
    saved["servers"]["http://127.0.0.1:8000"] = saved["servers"][original]
    cli.save_json(config, saved)
    assert invoke("create", "--file", source, "--server", "http://127.0.0.1:8000") == 1
    assert output(capsys)["error"]["code"] == "notebook_retry_changed"
    assert len(state["requests"]) == before


def test_cli_checks_returned_content_hash_and_rejects_unpinned_publication(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    note = create(owner)
    state["bad_hash"] = True
    assert invoke("show", note["id"]) == 1
    assert "hash" in output(capsys)["error"]["message"]
    before = len(state["requests"])
    assert invoke("request-publication", note["id"], "--revision", 1, "--sha256", "bad") == 1
    output(capsys)
    assert len(state["requests"]) == before
    assert count(app, notebook.NotebookPublication) == 0


def test_cli_private_note_delete_restore_and_lost_response_retries(connected_cli, capsys):
    app, owner, invoke, state, config, root = connected_cli
    source = root / "test-note.json"
    cli.save_json(source, content(title="Disposable CLI test"))
    assert invoke("create", "--file", source) == 0
    note = output(capsys)["receipt"]
    pin = (note["id"], "--revision", note["revision"], "--sha256", note["content_sha256"])
    deletion_args = ("delete", *pin, "--retry", root / "delete-1.json")
    state["lose_once"] = True
    assert invoke(*deletion_args) == 1
    assert output(capsys)["error"]["code"] == "network_error"
    assert invoke(*deletion_args) == 0
    deletion = output(capsys)["receipt"]
    assert deletion["deleted"] is True
    assert state["requests"][-1] == state["requests"][-2]
    assert invoke("show", *pin) == 1
    assert output(capsys)["error"]["code"] == "http_410"
    assert invoke("list", "--search", "Disposable") == 0
    assert output(capsys)["notes"] == []
    assert invoke("list", "--deleted") == 0
    assert output(capsys)["notes"][0]["deletion"]["id"] == deletion["deletion_id"]
    assert invoke("show", *pin, "--deleted") == 0
    assert output(capsys)["note"]["revisions"][0]["content"] == content(title="Disposable CLI test")
    assert invoke("export", "--out", root / "export.json") == 0
    assert output(capsys)["notes"] == 0
    restore_args = ("restore", note["id"], "--deletion-id", deletion["deletion_id"], "--retry", root / "restore-1.json")
    state["lose_once"] = True
    assert invoke(*restore_args) == 1
    output(capsys)
    assert invoke(*restore_args) == 0
    assert output(capsys)["receipt"]["deleted"] is False
    assert state["requests"][-1] == state["requests"][-2]
    assert invoke("show", *pin) == 0
    assert output(capsys)["note"]["revision"] == 1
    assert invoke(*deletion_args) == 0
    assert output(capsys)["receipt"]["deleted"] is False  # Old retry cannot undo restore.
    assert invoke("delete", *pin, "--retry", root / "delete-2.json") == 0
    assert output(capsys)["receipt"]["deleted"] is True
    assert invoke(*restore_args) == 0
    assert output(capsys)["receipt"]["deleted"] is True  # Nor can restore undo a later deletion.
    assert count(app, notebook_trash.NotebookDeletion) == 2
    assert count(app, notebook.NotebookPublication) == 0
