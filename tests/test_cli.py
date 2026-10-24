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
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import crucible_cli as cli


TOKEN = "cru_" + "a" * 43
CANDIDATE = "ba" * 32


@pytest.fixture
def server(tmp_path):
    state = {"requests": [], "issued": False, "answers": [], "methods": 0, "uploads": 0,
             "fail_answer_once": False, "closed": False, "redirect": False, "wrong_hash": False,
             "pipeline": {"format": "crucible-agent-pipeline-v1", "purpose": "Fixture model contract"}}
    source = b'# Inert reference source.\nraise RuntimeError("Do not execute fetched reference source")\n'
    digest = hashlib.sha256(source).hexdigest()
    manifest = {"id": "rsa-v3-tier-1", "instance_size": 1, "shared_secret_bytes": 32,
                "implementation": {"source_sha256": digest}}
    state["manifest"] = manifest
    state["source_sha256"] = digest

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def reply(self, value, status=200, raw=False, headers=None):
            body = value if raw else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain" if raw else "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
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
            if state.get("error_status"):
                headers = {"Retry-After": state["retry_after"]} if state.get("retry_after") is not None else {}
                return self.reply({"detail": TOKEN + CANDIDATE}, state["error_status"], headers=headers)
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", state["origin"] + "/redirect-target")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/api/agent/connect/start":
                state["requested_scopes"] = body.get("scopes")
                return self.reply({"device_code": "crd_" + "d" * 43, "user_code": "ABCD-EFGH",
                    "verification_uri": state["origin"] + "/#connect?code=ABCD-EFGH", "expires_in": 600, "interval": 2,
                    **({"scopes": state.get("offered_scopes", body["scopes"])} if "scopes" in body else {})})
            if path == "/api/agent/connect/poll":
                return self.reply({"status": "authorized", "access_token": TOKEN,
                    "expires_at": time.time() + 3600, "token_type": "Bearer", "user": {"handle": "solver"},
                    **({"scopes": state.get("granted_scopes", state["requested_scopes"])} if state.get("requested_scopes") else {})})
            if (path in {cli.AGENT_PIPELINE, "/api/schemes"} or path.startswith(cli.AGENT_GUIDANCE)) and state.get("beta_required") and self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self.reply({"detail": "Unauthorized"}, 401)
            if path == cli.AGENT_GUIDANCE + "note-format.txt":
                return self.reply(state.get("guidance", b"Note format fixture.\n"), raw=True)
            if path == cli.AGENT_PIPELINE:
                return self.reply(state["pipeline"], state.get("pipeline_status", 200))
            if path == "/api/schemes":
                return self.reply({"schemes": [{"id": "rsa-kem", "name": "RSA", "version": "3", "profiles": [manifest],
                    "selection_kind": state.get("selection_kind", "tier"),
                    "default_progression": state.get("default_progression")}],
                                   "padding": "x" * state.get("catalogue_padding", 0)})
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self.reply({"detail": "Unauthorized"}, 401)
            challenge = {"id": "challenge-1", "scheme_id": "rsa-kem", "scheme_version": "3",
                "status": "expired" if state["closed"] else "open", "ends_at": time.time() + 300,
                "public": {"encoding": {"shared_secret_bytes": 32}, "public_key": "a" * state.get("challenge_padding", 0)}}
            if path == "/api/challenges":
                if body is not None: state["issued"] = True
                return self.reply({"challenge": challenge if state["issued"] else None})
            if path == "/api/challenges/challenge-1/download":
                return self.reply({"format": "crucible-challenge-v1", "challenge": challenge, "scheme_manifest": manifest})
            if path == "/api/protocol/spec":
                return self.reply({"version": "3", "implementation": {"sha256": digest}})
            if path == "/api/protocol/source":
                return self.reply(b"changed" if state["wrong_hash"] else source, raw=True)
            if path == "/api/protocol/artifacts":
                return self.reply(state["artifacts"])
            if path == "/api/protocol/artifact-source":
                name = parse_qs(urlsplit(self.path).query)["path"][0]
                return self.reply(state["artifact_files"][name], raw=True)
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
                return self.reply({"submission": {"id": "receipt-1", "status": "correct", "method_id": body["method_id"]},
                                   "challenge": challenge})
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


@pytest.mark.parametrize("status,header,seconds", [(503, "3", 3), (429, "0", 0), (429, "86400", 86400)])
def test_retry_after_is_bounded_machine_readable_and_does_not_retry(server, capsys, status, header, seconds):
    state, _, _ = server
    state.update(error_status=status, retry_after=header)
    assert invoke(server, "schemes") == 1
    output = capsys.readouterr()
    error = json.loads(output.out)["error"]
    assert error["code"] == "http_" + str(status)
    assert error["retry_after_seconds"] == seconds
    assert type(error["retry_after_seconds"]) is int
    assert TOKEN not in output.out + output.err and CANDIDATE not in output.out + output.err
    assert len(state["requests"]) == 1


def test_retry_after_human_output_is_concise_without_echoing_server_body(server, capsys):
    state, config, _ = server
    state.update(error_status=503, retry_after="3")
    assert cli.main(["--server", state["origin"], "--config", str(config), "schemes"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "The server could not complete the request.\nRetry after 3 seconds.\n"
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("header", [None, "", "-1", "1.5", "86401", "9" * 1000,
    "Wed, 21 Oct 2026 07:28:00 GMT", TOKEN + CANDIDATE])
def test_invalid_retry_after_is_not_exposed_in_json_or_human_output(server, capsys, header):
    state, config, _ = server
    state.update(error_status=429, retry_after=header)
    assert invoke(server, "schemes") == 1
    output = capsys.readouterr()
    error = json.loads(output.out)["error"]
    assert error == {"code": "http_429", "message": "Too many requests. Wait before retrying."}
    assert not output.err
    assert cli.main(["--server", state["origin"], "--config", str(config), "schemes"]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "Too many requests. Wait before retrying.\n"
    assert len(state["requests"]) == 2  # One request per explicit command, no automatic retry.


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


def test_notebook_login_requests_explicit_scopes_without_agent_browser_use(server, monkeypatch, capsys):
    state, config, _ = server
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr(cli.webbrowser, "open", lambda _: pytest.fail("The agent must not open a browser."))
    assert invoke(server, "login", "--notebook", "--no-browser") == 0
    output = capsys.readouterr()
    assert state["requested_scopes"] == list(cli.DEFAULT_SCOPES + cli.NOTEBOOK_SCOPES)
    assert cli.configuration(config)["servers"][state["origin"]]["scopes"] == state["requested_scopes"]
    assert TOKEN not in output.out + output.err and "crd_" not in output.out + output.err


@pytest.mark.parametrize("field", ["offered_scopes", "granted_scopes"])
def test_explicit_login_refuses_a_changed_permission_set(server, monkeypatch, capsys, field):
    state, config, _ = server
    before = config.read_bytes()
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    state[field] = list(cli.DEFAULT_SCOPES)
    assert invoke(server, "login", "--notebook", "--no-browser") == 1
    assert "permissions" in json.loads(capsys.readouterr().out)["error"]["message"]
    assert config.read_bytes() == before


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
    assert report["pipeline_status"] == "saved"
    assert cli.load_json(root / "pipeline.json") == state["pipeline"]
    assert (root / "pipeline.json").stat().st_mode & 0o077 == 0
    assert "read and follow pipeline.json" in (root / "README.md").read_text()
    assert all(TOKEN not in item.read_text() for item in root.rglob("*") if item.is_file())
    assert config.parent != root


def test_beta_catalogue_and_model_guide_use_connection_without_exposing_it(server, capsys):
    state, _, root = server
    state["beta_required"] = True
    assert invoke(server, "guide") == 0
    assert json.loads(capsys.readouterr().out) == state["pipeline"]
    assert invoke(server, "schemes") == 0
    capsys.readouterr()
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 0
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err
    for path in (cli.AGENT_PIPELINE, "/api/schemes"):
        attempts = [row[3] for row in state["requests"] if row[1] == path]
        assert attempts == [None, "Bearer " + TOKEN] * 2
    pipeline_read = next(i for i, row in enumerate(state["requests"]) if row[1] == cli.AGENT_PIPELINE)
    issue = next(i for i, row in enumerate(state["requests"]) if row[0] == "POST" and row[1] == "/api/challenges")
    assert pipeline_read < issue


@pytest.mark.parametrize("status,document", [(200, {"format": "unsupported"}),
    (403, {"detail": TOKEN}), (503, {"detail": TOKEN}),
    (200, {"format": "crucible-agent-pipeline-v1", "large": "x" * cli.MAX_AGENT_PIPELINE})])
def test_bad_pipeline_never_issues_challenge_or_creates_workspace(server, capsys, status, document):
    state, _, root = server
    state.update(pipeline_status=status, pipeline=document)
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 1
    assert not root.exists() and not state["issued"]
    assert not any(row[0] == "POST" for row in state["requests"])
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err


def test_missing_legacy_pipeline_is_explicit_but_standalone_guide_does_not_fake_one(server, capsys):
    state, _, root = server
    state["pipeline_status"] = 404
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 0
    assert json.loads(capsys.readouterr().out)["pipeline_status"] == "unavailable-legacy-server"
    assert not (root / "pipeline.json").exists()
    assert "No central model contract was downloaded" in (root / "README.md").read_text()
    assert invoke(server, "guide") == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "http_404"


def test_guidance_resources_support_beta_json_and_plain_text_without_execution(server, capsys):
    state, config, _ = server
    state["beta_required"] = True
    state["guidance"] = b"print('This is inert research text, not executed')\n"
    assert invoke(server, "guide", "--resource", "note-format.txt") == 0
    assert json.loads(capsys.readouterr().out) == {"resource": "note-format.txt", "content": state["guidance"].decode()}
    assert cli.main(["--server", state["origin"], "--config", str(config), "guide", "--resource", "note-format.txt"]) == 0
    assert capsys.readouterr().out == state["guidance"].decode()
    assert [row[3] for row in state["requests"]] == [None, "Bearer " + TOKEN] * 2
    assert all(row[0] == "GET" for row in state["requests"])


@pytest.mark.parametrize("resource", ["../note-format.txt", "https://other.example/guide.txt", "/note-format.txt", "a%2fb.txt", "a?x.txt", "a\\b.txt"])
def test_guidance_resource_paths_are_rejected_before_network(server, resource):
    state, _, _ = server
    assert invoke(server, "guide", "--resource", resource) == 1
    assert state["requests"] == []


@pytest.mark.parametrize("content", [b"", b"\xff", b"escape\x1b[31m", b"x" * (cli.MAX_AGENT_PIPELINE + 1)], ids=["empty", "utf8", "terminal-control", "oversized"])
def test_guidance_resource_rejects_unsafe_or_oversized_text(server, content, capsys):
    state, _, _ = server
    state["guidance"] = content
    assert invoke(server, "guide", "--resource", "note-format.txt") == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "client_error"


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
    monkeypatch.setattr(cli, "MAX_CHALLENGE_RESPONSE", 10)
    with pytest.raises(cli.ClientError, match="size limit"):
        client.request("/api/challenges/challenge-1/download")


def test_named_profiles_require_exact_ids_and_never_present_ordinals_as_tiers(server, capsys):
    state, _, root = server
    state["selection_kind"] = "parameter-set"
    state["manifest"].update(profile_kind="standard-parameter-set", profile_label="Named fixture 768")
    assert invoke(server, "init", "rsa-kem", "--tier", "1", "--path", root) == 1
    assert not state["issued"] and not root.exists()
    assert "storage ordinals are not stages" in capsys.readouterr().out
    assert invoke(server, "init", "rsa-kem", "--profile-id", "not-published", "--path", root) == 1
    assert not state["issued"]
    capsys.readouterr()
    assert invoke(server, "init", "rsa-kem", "--profile-id", "rsa-v3-tier-1", "--path", root) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tier"] is None and report["profile_label"] == "Named fixture 768"
    assert report["profile_id"] == "rsa-v3-tier-1" and report["selection_kind"] == "parameter-set"
    issue = next(request for request in state["requests"] if request[0] == "POST" and request[1] == "/api/challenges")
    assert issue[2] == {"scheme_id": "rsa-kem", "scheme_version": "3", "profile_id": "rsa-v3-tier-1"}


def test_profile_id_and_tier_are_mutually_exclusive():
    with pytest.raises(SystemExit) as error:
        cli.parser().parse_args(["init", "fixture", "--tier", "1", "--profile-id", "fixture-profile"])
    assert error.value.code == 2


def test_stage_resolves_published_configuration_without_reinterpreting_legacy_tier(server, capsys):
    state, _, root = server
    state["default_progression"] = {"id": "test/default/v1", "stage_count": 2,
        "stages": [{"number": 2, "profile_id": "rsa-v3-tier-1"}]}
    assert invoke(server, "init", "rsa-kem", "--stage", "2", "--path", root) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["stage"] == 2 and report["tier"] == 1
    assert report["progression_id"] == "test/default/v1"
    assert report["profile_id"] == "rsa-v3-tier-1"
    assert json.loads((root / "challenge.json").read_text())["scheme_manifest"]["instance_size"] == 1


def test_missing_or_unknown_stage_never_issues_a_different_configuration(server, capsys):
    state, _, root = server
    assert invoke(server, "init", "rsa-kem", "--stage", "1", "--path", root) == 1
    assert "no published stage progression" in capsys.readouterr().out
    state["default_progression"] = {"id": "test/default/v1", "stages": [{"number": 2, "profile_id": "rsa-v3-tier-1"}]}
    assert invoke(server, "init", "rsa-kem", "--stage", "1", "--path", root) == 1
    assert "not available" in capsys.readouterr().out
    assert not state["issued"] and not root.exists()


@pytest.mark.parametrize("other", [["--tier", "1"], ["--profile-id", "exact-id"]])
def test_stage_and_other_selectors_are_mutually_exclusive(other):
    with pytest.raises(SystemExit) as error:
        cli.parser().parse_args(["init", "fixture", "--stage", "1", *other])
    assert error.value.code == 2


def test_large_public_key_download_run_and_receipt_keep_separate_size_limits(server, monkeypatch, capsys):
    state, config, _ = server
    state["challenge_padding"] = 2_800_000
    root = ready(server)
    assert (root / "challenge.json").stat().st_size > cli.MAX_RESPONSE
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    assert invoke(server, "submit", "--path", root) == 0
    assert state["answers"][-1]["candidate"] == CANDIDATE
    assert cli.load_json(root / "receipt.json")["submission"]["id"] == "receipt-1"
    requests = len(state["requests"])
    assert invoke(server, "submit", "--path", root) == 0
    assert len(state["requests"]) == requests, "A durable large-response receipt needs no network retry"
    assert state["methods"] == state["uploads"] == len(state["answers"]) == 1
    assert len(cli.encode(state["answers"][0])) < 65536, "The answer upload remains small"
    monkeypatch.setattr(cli, "MAX_CHALLENGE_RESPONSE", 1024)
    with pytest.raises(cli.ClientError, match="size limit"):
        cli.Client(state["origin"], config).request("/api/challenges/challenge-1/download")
    with pytest.raises(cli.ClientError, match="size limit"):
        cli.Client(state["origin"], config).request("/api/challenges/challenge-1/submit", state["answers"][0])
    monkeypatch.setattr(cli, "MAX_RESPONSE", 10)
    with pytest.raises(cli.ClientError, match="size limit"):
        cli.Client(state["origin"], config).request("/api/protocol/spec")


def artifact_fixture(state):
    value = {"format": "crucible-implementation-artifact-v1", "scheme_id": "rsa-kem", "scheme_version": "3",
             "adapter_source_sha256": state["source_sha256"], "dependencies": [{"name": "Non-ASCII fixture café", "sha256": "1" * 64}]}
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value["artifact_sha256"] = digest
    state["manifest"]["implementation"]["artifact_sha256"] = digest
    state["artifacts"] = value
    return value


def test_workspace_verifies_and_saves_pinned_dependency_closure_without_execution(server):
    state, _, root = server
    expected = artifact_fixture(state)
    assert invoke(server, "get", "--challenge-id", "challenge-1", "--path", root) == 0
    assert cli.load_json(root / "reference" / "implementation-artifacts.json") == expected
    assert not state["issued"] and not state["answers"]


@pytest.mark.parametrize("invalid", [None, "mismatch", "traversal"])
def test_workspace_downloads_only_pinned_first_party_source(server, invalid):
    state, _, root = server
    artifact = artifact_fixture(state)
    content = b'raise RuntimeError("inert reference, never execute")\n'
    name = "../outside.py" if invalid == "traversal" else "crucible/reference.py"
    artifact["files"] = {name: hashlib.sha256(content).hexdigest()}
    artifact["artifact_sha256"] = hashlib.sha256(json.dumps(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    state["manifest"]["implementation"]["artifact_sha256"] = artifact["artifact_sha256"]
    state["artifact_files"] = {name: b"changed" if invalid == "mismatch" else content}
    result = invoke(server, "get", "--challenge-id", "challenge-1", "--path", root)
    assert result == (1 if invalid else 0)
    if invalid:
        assert not root.exists()
    else:
        assert (root / "reference/source/crucible/reference.py").read_bytes() == content
    assert not state["issued"] and not state["answers"]


@pytest.mark.parametrize("tamper", ["content", "self_hash", "self_consistent_wrong_pin", "scheme", "version", "source"])
def test_wrong_dependency_closure_creates_no_workspace(server, tamper):
    state, _, root = server
    document = artifact_fixture(state)
    if tamper in {"content", "self_consistent_wrong_pin"}:
        document["dependencies"][0]["sha256"] = "2" * 64
    elif tamper == "self_hash": document["artifact_sha256"] = "3" * 64
    elif tamper == "scheme": document["scheme_id"] = "another-scheme"
    elif tamper == "version": document["scheme_version"] = "other-release"
    elif tamper == "source": document["adapter_source_sha256"] = "4" * 64
    if tamper in {"self_consistent_wrong_pin", "scheme", "version", "source"}:
        document["artifact_sha256"] = hashlib.sha256(json.dumps(
            {key: value for key, value in document.items() if key != "artifact_sha256"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if tamper != "self_consistent_wrong_pin":
            state["manifest"]["implementation"]["artifact_sha256"] = document["artifact_sha256"]
    assert invoke(server, "get", "--challenge-id", "challenge-1", "--path", root) == 1
    assert not root.exists() and not state["issued"] and not state["answers"]


def test_run_measures_only_local_command_then_submit_uploads_only_selected_sources(server, capsys):
    state, _, _ = server
    root = ready(server)
    (root / "attack" / "not-selected.py").write_text("PRIVATE_UNSELECTED = True\n")
    (root / ".env").write_text("SECRET=DO_NOT_UPLOAD\n")
    capsys.readouterr()
    template = cli.load_json(root / "result.json")
    assert template["ai_model"] == "" and "300 characters" in template["_instructions"]["ai_model"]
    assert invoke(server, "run", "--path", root, "--workers", "2", "--precomputation", "None", "--ai-model", "  Model A v1; Model B v2  ", "--", sys.executable, "attack/solve.py") == 0
    output = capsys.readouterr().out
    assert CANDIDATE not in output and TOKEN not in output
    result = cli.load_json(root / "result.json")
    assert result["candidate"] == CANDIDATE
    assert result["ai_model"] == "Model A v1; Model B v2"
    assert result["compute"]["wall_seconds"] >= 0 and result["compute"]["workers"] == 2
    assert result["compute"]["peak_memory"] == "Not measured"
    assert "Remote compute" in result["compute"]["notes"] and not state["answers"]
    assert invoke(server, "submit", "--path", root) == 0
    assert [item["path"] for item in state["source"]["files"]] == ["attack/solve.py", "method.md"]
    assert "DO_NOT_UPLOAD" not in json.dumps(state["source"])
    assert state["methods"] == state["uploads"] == len(state["answers"]) == 1
    assert state["answers"][0]["ai_model"] == result["ai_model"]
    assert (root / ".crucible" / "submission.json").stat().st_mode & 0o777 == 0o600
    assert cli.load_json(root / "receipt.json")["submission"]["status"] == "correct"


@pytest.mark.parametrize("ai_model", [None, "Model A v1"])
def test_lost_receipt_retry_preserves_exact_answer_method_source_and_identifier_after_expiry(server, capsys, ai_model):
    state, _, _ = server
    root = ready(server)
    options = ["--ai-model", ai_model] if ai_model else []
    assert invoke(server, "run", "--path", root, *options, "--", sys.executable, "attack/solve.py") == 0
    state["fail_answer_once"] = True
    capsys.readouterr()
    assert invoke(server, "submit", "--path", root) == 1
    error = capsys.readouterr().out
    assert CANDIDATE not in error and TOKEN not in error
    frozen = cli.load_json(root / ".crucible" / "submission.json")
    assert frozen["payload"] == state["answers"][0]
    if ai_model:
        assert frozen["result"]["ai_model"] == frozen["payload"]["ai_model"] == ai_model
    else:
        assert "ai_model" not in frozen["result"] and "ai_model" not in frozen["payload"]
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


@pytest.mark.parametrize("change", [{"candidate": "cc" * 32}, {"ai_model": "Added after submission"}])
def test_changed_result_cannot_replace_uncertain_submission(server, change):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    state["fail_answer_once"] = True
    assert invoke(server, "submit", "--path", root) == 1
    result = cli.load_json(root / "result.json")
    result.update(change)
    cli.save_json(root / "result.json", result)
    assert invoke(server, "submit", "--path", root) == 1
    assert len(state["answers"]) == 1
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 1


@pytest.mark.parametrize("annotation,expected", [("", None), (" \t\n", None),
    ("  Model A v1; Model B v2  ", "Model A v1; Model B v2"), ("🧪" * 300, "🧪" * 300)])
def test_result_model_annotation_normalizes_without_changing_legacy_results(annotation, expected):
    result = {"format": "crucible-recovery-v1", "challenge_id": "challenge-1", "candidate": CANDIDATE,
              "method": {"name": "Method", "pseudocode": "Apply the method."},
              "compute": {"hardware": "CPU", "workers": 1, "wall_seconds": 1,
                          "precomputation": "None", "peak_memory": "Not measured"}}
    package = {"challenge": {"id": "challenge-1"}, "scheme_manifest": {"shared_secret_bytes": 32}}
    legacy = cli.validate_result(result, package)
    normalized = cli.validate_result({**result, "ai_model": annotation}, package)
    assert normalized == {**legacy, **({"ai_model": expected} if expected else {})}
    if expected is None:
        assert cli.encode(normalized) == cli.encode(legacy)


@pytest.mark.parametrize("annotation", [None, False, 1, [], {}, "x" * 301, " " + "🧪" * 300, "a\x00b", "\ud800"])
def test_invalid_model_annotations_are_rejected(annotation):
    with pytest.raises(cli.ClientError, match="ai_model"):
        cli.normalize_ai_model(annotation)


def test_manual_result_model_annotation_is_submitted(server):
    state, _, _ = server
    root = ready(server)
    assert invoke(server, "run", "--path", root, "--", sys.executable, "attack/solve.py") == 0
    result = cli.load_json(root / "result.json")
    assert "ai_model" not in result
    result["ai_model"] = "  Manually reported model v3  "
    cli.save_json(root / "result.json", result)
    assert invoke(server, "submit", "--path", root) == 0
    assert state["answers"][0]["ai_model"] == "Manually reported model v3"


def test_invalid_run_model_annotation_does_not_execute_solver(server):
    state, _, _ = server
    root = ready(server)
    marker = root / "ran.txt"
    assert invoke(server, "run", "--path", root, "--ai-model", "x" * 301, "--", sys.executable,
                  "-c", "from pathlib import Path; Path('ran.txt').write_text('ran')") == 1
    assert not marker.exists() and not state["answers"]


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
