"""Standalone client HTTP, filesystem and solver boundaries; no live records."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest

from scripts import crucible_cli as cli


TOKEN = "cru_" + "a" * 43
CANDIDATE = "ba" * 32


@pytest.fixture
def server(tmp_path):
    state = {"requests": [], "issued": False, "answers": [], "methods": 0, "uploads": 0,
             "fail_answer_once": False, "closed": False, "redirect": False, "wrong_hash": False}
    source = b'# Inert reference source.\nraise RuntimeError("Do not execute fetched reference source")\n'
    digest = hashlib.sha256(source).hexdigest()
    manifest = {"id": "rsa-v3-tier-1", "instance_size": 1, "shared_secret_bytes": 32,
                "implementation": {"source_sha256": digest}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def reply(self, value, status=200, raw=False):
            body = value if raw else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain" if raw else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Bounded-response tests intentionally close oversized bodies.
                pass

        def do_GET(self): self.respond()
        def do_POST(self): self.respond()

        def respond(self):
            path = urlsplit(self.path).path
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))) if self.command == "POST" else None
            state["requests"].append((self.command, self.path, body, self.headers.get("Authorization")))
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", state["origin"] + "/redirect-target")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/api/agent/connect/start":
                return self.reply({"device_code": "crd_" + "d" * 43, "user_code": "ABCD-EFGH",
                    "verification_uri": state["origin"] + "/#connect?code=ABCD-EFGH", "expires_in": 600, "interval": 2})
            if path == "/api/agent/connect/poll":
                return self.reply({"status": "authorized", "access_token": TOKEN,
                    "expires_at": time.time() + 3600, "token_type": "Bearer", "user": {"handle": "solver"}})
            if path == "/api/schemes":
                return self.reply({"schemes": [{"id": "rsa-kem", "name": "RSA", "version": "3", "profiles": [manifest]}],
                                   "padding": "x" * state.get("catalogue_padding", 0)})
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self.reply({"detail": "Unauthorized"}, 401)
            challenge = {"id": "challenge-1", "scheme_id": "rsa-kem", "scheme_version": "3",
                "status": "expired" if state["closed"] else "open", "ends_at": time.time() + 300,
                "public": {"encoding": {"shared_secret_bytes": 32}}}
            if path == "/api/challenges":
                if body is not None: state["issued"] = True
                return self.reply({"challenge": challenge if state["issued"] else None})
            if path == "/api/challenges/challenge-1/download":
                return self.reply({"format": "crucible-challenge-v1", "challenge": challenge, "scheme_manifest": manifest})
            if path == "/api/protocol/spec":
                return self.reply({"version": "3", "implementation": {"sha256": digest}})
            if path == "/api/protocol/source":
                return self.reply(b"changed" if state["wrong_hash"] else source, raw=True)
            if path == "/api/methods":
                state["methods"] += 1
                return self.reply({"method": {"id": "method-" + str(state["methods"]), **body}})
            if path.endswith("/source"):
                state["uploads"] += 1
                state["source"] = body
                return self.reply({"source": {"sha256": hashlib.sha256(cli.encode(body)).hexdigest(),
                    "files": len(body["files"]), "total_bytes": sum(len(item["content"].encode()) for item in body["files"])}})
            if path == "/api/challenges/challenge-1/submit":
                state["answers"].append(body)
                if state["fail_answer_once"]:
                    state["fail_answer_once"] = False
                    # Simulate a receipt that was recorded but whose response failed.
                    return self.reply({"detail": CANDIDATE + TOKEN}, 503)
                return self.reply({"submission": {"id": "receipt-1", "status": "correct", "method_id": body["method_id"]}})
            self.reply({"detail": "Not found"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["origin"] = "http://127.0.0.1:" + str(httpd.server_port)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    config = tmp_path / "credentials" / "config.json"
    cli.save_json(config, {"format": "crucible-client-config-v1", "default_server": state["origin"],
        "servers": {state["origin"]: {"access_token": TOKEN, "expires_at": time.time() + 3600}}})
    yield state, config, tmp_path / "attack-workspace"
    httpd.shutdown()
    httpd.server_close()
    worker.join()


def invoke(server, *args):
    state, config, _ = server
    return cli.main(["--server", state["origin"], "--config", str(config), "--json", *map(str, args)])


def ready(server):
    _, _, root = server
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 0
    (root / "method.md").write_text("# Public-input attack\nFactor the public modulus and apply the specified KDF. No prior preparation.\n")
    (root / "attack" / "solve.py").write_text("print('" + CANDIDATE + "')\n")
    return root


def test_login_stores_private_origin_bound_credentials_without_printing_them(server, monkeypatch, capsys):
    state, config, _ = server
    config.unlink()
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    assert invoke(server, "login") == 0
    output = capsys.readouterr()
    assert "ABCD-EFGH" in output.err and opened == [state["origin"] + "/#connect?code=ABCD-EFGH"]
    assert TOKEN not in output.out + output.err and "crd_" not in output.out + output.err
    assert config.stat().st_mode & 0o777 == 0o600
    assert cli.configuration(config)["servers"][state["origin"]]["access_token"] == TOKEN
    assert all(request[3] is None for request in state["requests"])


def test_login_retries_lost_authorized_poll_with_same_private_code(server, monkeypatch, capsys):
    state, config, _ = server
    config.unlink()
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    request = cli.Client.request
    lost = []

    def unreliable(self, path, *args, **kwargs):
        response = request(self, path, *args, **kwargs)
        if path.endswith("/poll") and not lost:
            lost.append(True)
            raise cli.ClientError("Could not reach the configured server.", "network_error")
        return response

    monkeypatch.setattr(cli.Client, "request", unreliable)
    assert invoke(server, "login", "--no-browser") == 0
    polls = [item for item in state["requests"] if item[1].endswith("/poll")]
    assert len(polls) == 2 and polls[0][2] == polls[1][2]
    assert len([item for item in state["requests"] if item[1].endswith("/start")]) == 1
    assert cli.configuration(config)["servers"][state["origin"]]["access_token"] == TOKEN
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err and "crd_" not in output.out + output.err


def test_init_fetches_contract_and_verified_reference_without_executing_or_leaking_credentials(server, capsys):
    state, config, root = server
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["challenge_id"] == "challenge-1" and report["tier"] == 1
    assert {"challenge.json", "protocol.json", "reference", "attack", "method.md", "result.json", ".crucible.json", "README.md", ".gitignore"} <= {item.name for item in root.iterdir()}
    assert state["issued"] and state["methods"] == 0 and not state["answers"]
    assert all(TOKEN not in item.read_text() for item in root.rglob("*") if item.is_file())
    assert config.parent != root


def test_exact_get_and_refusal_to_overwrite_existing_workspace(server):
    state, _, root = server
    assert invoke(server, "get", "--challenge-id", "challenge-1", "--path", root) == 0
    assert not state["issued"]
    before = len(state["requests"])
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 1
    assert not any(request[0] == "POST" for request in state["requests"][before:])


def test_wrong_source_hash_creates_no_workspace(server):
    state, _, root = server
    state["wrong_hash"] = True
    assert invoke(server, "get", "--challenge-id", "challenge-1", "--path", root) == 1
    assert not root.exists()


def test_full_catalogue_has_separate_bounded_response_allowance(server, monkeypatch):
    state, config, _ = server
    client = cli.Client(state["origin"], config)
    state["catalogue_padding"] = cli.MAX_RESPONSE + 1
    assert client.request("/api/schemes", auth=False)["schemes"][0]["id"] == "rsa-kem"
    monkeypatch.setattr(cli, "MAX_CATALOGUE_RESPONSE", 1024)
    with pytest.raises(cli.ClientError, match="size limit"):
        client.request("/api/schemes", auth=False)
    monkeypatch.setattr(cli, "MAX_RESPONSE", 10)
    with pytest.raises(cli.ClientError, match="size limit"):
        client.request("/api/challenges/challenge-1/download")


def test_run_measures_only_local_command_then_submit_uploads_only_selected_sources(server, capsys):
    state, _, _ = server
    root = ready(server)
    (root / "attack" / "not-selected.py").write_text("PRIVATE_UNSELECTED = True\n")
    (root / ".env").write_text("SECRET=DO_NOT_UPLOAD\n")
    capsys.readouterr()
    assert invoke(server, "run", "--path", root, "--workers", "2", "--precomputation", "None", "--", sys.executable, "attack/solve.py") == 0
    output = capsys.readouterr().out
    assert CANDIDATE not in output and TOKEN not in output
    result = cli.load_json(root / "result.json")
    assert result["candidate"] == CANDIDATE
    assert result["compute"]["wall_seconds"] >= 0 and result["compute"]["workers"] == 2
    assert result["compute"]["peak_memory"] == "Not measured"
    assert "Remote compute" in result["compute"]["notes"] and not state["answers"]
    assert invoke(server, "submit", "--path", root) == 0
    assert [item["path"] for item in state["source"]["files"]] == ["attack/solve.py", "method.md"]
    assert "DO_NOT_UPLOAD" not in json.dumps(state["source"])
    assert state["methods"] == state["uploads"] == len(state["answers"]) == 1
    assert (root / ".crucible" / "submission.json").stat().st_mode & 0o777 == 0o600
    assert cli.load_json(root / "receipt.json")["submission"]["status"] == "correct"


def test_lost_receipt_retry_preserves_exact_answer_method_source_and_identifier_after_expiry(server, capsys):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    state["fail_answer_once"] = True
    capsys.readouterr()
    assert invoke(server, "submit", "--path", root) == 1
    error = capsys.readouterr().out
    assert CANDIDATE not in error and TOKEN not in error
    frozen = cli.load_json(root / ".crucible" / "submission.json")
    assert frozen["payload"] == state["answers"][0]
    state["closed"] = True
    (root / "attack" / "solve.py").write_text("print('changed after first submission')\n")
    (root / "method.md").write_text("Changed after the original frozen submission.\n")
    assert invoke(server, "submit", "--path", root) == 0
    assert state["answers"][0] == state["answers"][1]
    assert state["methods"] == state["uploads"] == 1
    requests = len(state["requests"])
    assert invoke(server, "submit", "--path", root) == 0
    assert len(state["requests"]) == requests


def test_new_submission_to_closed_challenge_is_not_sent(server):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    state["closed"] = True
    assert invoke(server, "submit", "--path", root) == 1
    assert not state["answers"] and state["methods"] == state["uploads"] == 0


def test_changed_result_cannot_replace_uncertain_submission(server):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    state["fail_answer_once"] = True
    assert invoke(server, "submit", "--path", root) == 1
    result = cli.load_json(root / "result.json")
    result["candidate"] = "cc" * 32
    cli.save_json(root / "result.json", result)
    assert invoke(server, "submit", "--path", root) == 1
    assert len(state["answers"]) == 1
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 1


def test_no_redirect_is_followed_and_no_off_origin_or_expired_token_is_sent(server):
    state, config, _ = server
    client = cli.Client(state["origin"], config)
    state["redirect"] = True
    with pytest.raises(cli.ClientError, match="Redirects"):
        client.request("/api/challenges/challenge-1/download")
    assert len(state["requests"]) == 1
    for path in ("//other.example/a", "https://other.example/a", "/\\other.example"):
        with pytest.raises(cli.ClientError): client.request(path)
    assert len(state["requests"]) == 1
    with pytest.raises(cli.ClientError, match="Connect"):
        cli.Client("http://localhost:" + str(urlsplit(state["origin"]).port), config).request("/api/challenges")
    stored = cli.configuration(config)
    stored["servers"][state["origin"]]["expires_at"] = 1
    cli.save_json(config, stored)
    with pytest.raises(cli.ClientError, match="expired"): client.request("/api/challenges")
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("value", ["http://example.com", "https://a.example/path", "https://user:pass@a.example", "https://a.example?x=1", "file:///tmp", "https://a.example\\evil", "https://a.example/#fragment"])
def test_bad_server_origins_are_rejected(value):
    with pytest.raises(cli.ClientError): cli.origin(value)


@pytest.mark.parametrize("selected", ["../result.json", "result.json", "attack/../result.json", "attack/.env", "/tmp/file", "attack\\file.py"])
def test_source_allowlist_rejects_credentials_state_and_traversal(server, selected):
    root = ready(server)
    with pytest.raises(cli.ClientError): cli.source_bundle(root, [selected])


def test_source_allowlist_rejects_symlinks_and_oversized_files(server):
    root = ready(server)
    (root / "attack" / "link.py").symlink_to(root / "result.json")
    with pytest.raises(cli.ClientError): cli.source_bundle(root, ["attack/link.py"])
    (root / "attack" / "huge.py").write_bytes(b"a" * (256 * 1024 + 1))
    with pytest.raises(cli.ClientError): cli.source_bundle(root, ["attack/huge.py"])


@pytest.mark.parametrize("selected", ["attack/a:b.py", "attack/CON.py", "attack/NUL", "attack/COM1.txt",
    "attack/LPT9.txt", "attack/trailing.", "attack/trailing ", "attack/" + "x" * 101,
    "attack/" + "/".join(["x"] * 12), "attack/SOLVE.py"])
def test_source_path_validation_matches_portable_server_contract(tmp_path, selected):
    # Bad paths must be rejected before opening any files or saving retry state.
    with pytest.raises(cli.ClientError): cli.source_bundle(tmp_path, [selected])


def test_source_content_and_parent_collisions_rejected_before_submission(server):
    state, _, _ = server
    root = ready(server)
    with pytest.raises(cli.ClientError): cli.source_bundle(root, ["attack/solve.py/child"])
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    (root / "attack" / "nul.py").write_bytes(b"a\x00b")
    assert invoke(server, "submit", "--path", root, "--source", "attack/nul.py") == 1
    assert not (root / ".crucible" / "submission.json").exists()
    assert state["methods"] == state["uploads"] == 0 and not state["answers"]


def test_changed_method_description_is_rejected_before_freezing_or_posting(server):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    (root / "method.md").write_text("# Different method\nThis no longer describes the generated result.\n")
    before = len(state["requests"])
    assert invoke(server, "submit", "--path", root) == 1
    assert not (root / ".crucible" / "submission.json").exists()
    assert state["requests"][before:] == []


def test_workspace_cannot_switch_servers(server):
    _, config, _ = server
    root = ready(server)
    assert cli.main(["--server", "https://elsewhere.example", "--config", str(config), "--json", "submit", "--path", str(root)]) == 1


def test_solver_failure_and_invalid_output_do_not_write_key_or_submit(server, capsys):
    state, _, _ = server
    root = ready(server)
    (root / "attack" / "solve.py").write_text("import sys\nprint('" + TOKEN + "', file=sys.stderr)\nraise SystemExit(2)\n")
    capsys.readouterr()
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 1
    assert TOKEN not in capsys.readouterr().out
    assert cli.load_json(root / "result.json")["candidate"] == "" and not state["answers"]
    (root / "attack" / "solve.py").write_text("print('not a key')\n")
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 1
    assert cli.load_json(root / "result.json")["candidate"] == ""
