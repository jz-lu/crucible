#!/usr/bin/env python3
"""Crucible client: private local workspaces and explicit result submission.

Python 3.11+, standard library only. Run --help for commands. Downloaded
protocol source is a reference artifact; this client never executes it.
"""
import argparse
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4
import webbrowser

DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_CONFIG = Path.home() / ".config" / "crucible" / "config.json"
MAX_RESPONSE = 2 * 1024 * 1024
MAX_CATALOGUE_RESPONSE = 8 * 1024 * 1024
MAX_CHALLENGE_RESPONSE = 8 * 1024 * 1024
# Up to 50 bounded specifications plus reviewer notes/evidence per history page.
MAX_PROPOSAL_RESPONSE = 8 * 1024 * 1024
MAX_SPECIFICATION = 48 * 1024
MAX_PROPOSAL_GUIDE = 64 * 1024
PROPOSAL_GUIDE = "/api/protocol/proposal-guide"
MAX_AGENT_PIPELINE = 64 * 1024
MAX_NOTE_LIST_RESPONSE = 8 * 1024 * 1024
MAX_NOTEBOOK_RESPONSE = 8 * 1024 * 1024
MAX_NOTE_WORKSPACE = 2 * 1024 * 1024
MAX_NOTE_CONTENT = 384 * 1024
NOTEBOOK = "/api/notebook"
DEFAULT_SCOPES = ("challenges:read", "challenges:write", "methods:read", "methods:write",
                  "submissions:read", "submissions:write", "proposals:read", "proposals:write",
                  "notes:read", "notes:rate")
NOTEBOOK_SCOPES = ("notebook:read", "notebook:write", "notebook:publish")
AGENT_PIPELINE = "/api/agent/pipeline"
AGENT_GUIDANCE = "/api/agent/guidance/"
PROPOSALS = "/api/protocol/proposals"
METHOD_TODO = "CRUCIBLE_METHOD_TODO"

# Only schema field names can appear in validation diagnostics. Unknown JSON
# keys and arbitrary server messages can contain submitted secrets or terminals
# controls, so they are never copied into the client error output.
PROPOSAL_FIELDS = frozenset("specification format name version summary algorithms setup keygen encapsulate decapsulate parameters tier_meaning supported_tiers kind min max step values mapping distribution reuse encoding public_key ciphertext shared_secret shared_secret_bytes correctness failure_bound policy_id failure_bound_exponent justification statement conditions configuration_coverage derivation private_key argument scope evidence security notion model claim assumptions tier_design parameter_progression cost_tradeoff fixed_correctness endpoint_status endpoint_target endpoint_evidence intermediate_steps parameter_selection objective constraints security_model search_domain comparison_evidence tradeoffs_and_uncertainty implementation url sha256 artifact_sha256 runtime entrypoint references title _instructions idempotency_key expected_revision content_sha256 range list".split())


def proposal_field_errors(raw):
    try:
        document = json.loads(raw)
        errors = document.get("errors", [])
        if not isinstance(errors, list):
            return []
    except (ValueError, AttributeError):
        return []
    result = []
    for error in errors[:50]:
        if not isinstance(error, dict) or not isinstance(error.get("path"), str):
            continue
        parts = error["path"].split(".")[:12]
        safe = [part if part in PROPOSAL_FIELDS or re.fullmatch(r"[0-9]{1,4}", part)
                else "<unknown-field>" for part in parts]
        result.append({"path": ".".join(safe) or "specification",
                       "message": "Check this field's required type and bounds in schema.json; remove unknown fields."})
    return result


class ClientError(ValueError):
    def __init__(self, message, code="client_error", data=None):
        super().__init__(message)
        self.code, self.data = code, data


def retry_after_seconds(value):
    """Accept bounded delta-seconds only; never expose arbitrary header text."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 86400 else None


def origin(value):
    try:
        parts = urlsplit(value)
        port = parts.port
    except (ValueError, TypeError):
        raise ClientError("Use an absolute HTTPS server origin.") from None
    if (parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise ClientError("Server must be an origin without credentials, a path, query or fragment.")
    host = parts.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parts.scheme == "http" and not loopback:
        raise ClientError("HTTP is allowed only for loopback development servers; use HTTPS.")
    if any(character.isspace() for character in value) or "\\" in value:
        raise ClientError("Invalid server origin.")
    host = "[" + host + "]" if ":" in host else host
    suffix = "" if port in (None, 443 if parts.scheme == "https" else 80) else ":" + str(port)
    return parts.scheme + "://" + host + suffix


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def load_json(path, limit=MAX_RESPONSE):
    path = Path(path)
    if path.is_symlink():
        raise ClientError("Refusing to read a symbolic link: " + path.name)
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ClientError("File exceeds its size limit: " + path.name)
        value = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeError, ValueError):
        raise ClientError("Cannot read a valid JSON file: " + path.name) from None
    if not isinstance(value, dict):
        raise ClientError("Expected a JSON object in " + path.name)
    return value


def private_write(path, data):
    """Atomic 0600 write; do not follow a destination or parent symlink."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ClientError("Refusing to write through a symbolic link.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".crucible-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def save_json(path, value):
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def configuration(path):
    path = Path(path).expanduser()
    if not path.exists():
        return {"format": "crucible-client-config-v1", "servers": {}}
    if path.stat().st_mode & 0o077:
        raise ClientError("Credential file must be private. Set its permissions to 600 before continuing.")
    value = load_json(path, 65536)
    if value.get("format") != "crucible-client-config-v1" or not isinstance(value.get("servers"), dict):
        raise ClientError("Unsupported credential file format.")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


class Client:
    def __init__(self, server, config_path=DEFAULT_CONFIG):
        self.server, self.config_path = origin(server), Path(config_path).expanduser()
        self.expected_handle = None
        # Environment proxy settings must not redirect loopback credentials.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, body=None, *, auth=True, raw=False):
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise ClientError("Only same-origin API paths are allowed.")
        parts = urlsplit(path)
        if parts.scheme or parts.netloc or parts.fragment:
            raise ClientError("Only same-origin API paths are allowed.")
        headers = {"Accept": "application/json", "User-Agent": "crucible-cli/1"}
        if auth:
            credential = configuration(self.config_path)["servers"].get(self.server)
            if not isinstance(credential, dict) or not isinstance(credential.get("access_token"), str):
                raise ClientError("Connect this server first with the login command.", "login_required")
            if self.expected_handle is not None and credential.get("handle") != self.expected_handle:
                raise ClientError("The connected account changed. Reconnect the original proposal author before retrying.", "proposal_account_changed")
            expiry = credential.get("expires_at")
            if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
                raise ClientError("This connection expired. Run login again.", "login_required")
            token = credential["access_token"]
            if not re.fullmatch(r"cru_[A-Za-z0-9_-]{43}", token):
                raise ClientError("Invalid stored credential. Run login again.", "login_required")
            headers["Authorization"] = "Bearer " + token
        data = encode(body) if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        if parts.path == PROPOSALS + "/validate" and data is not None and not auth:
            # Public, format-only validation creates no session or proposal.
            # Keep the server's same-origin boundary; this sends no credential.
            headers["Origin"] = self.server
        request = Request(self.server + path, data=data, headers=headers)
        # Catalogue manifests and full public-key encodings need separate read
        # budgets. This does not enlarge result or source upload limits.
        response_limit = MAX_RESPONSE
        if parts.path == "/api/schemes":
            response_limit = MAX_CATALOGUE_RESPONSE
        elif parts.path == "/api/agent/notes":
            response_limit = MAX_NOTE_LIST_RESPONSE
        elif parts.path.startswith(NOTEBOOK + "/"):
            response_limit = MAX_NOTEBOOK_RESPONSE
        elif parts.path == PROPOSAL_GUIDE:
            response_limit = MAX_PROPOSAL_GUIDE
        elif parts.path == AGENT_PIPELINE or parts.path.startswith(AGENT_GUIDANCE):
            response_limit = MAX_AGENT_PIPELINE
        elif parts.path == "/api/challenges" or re.fullmatch(r"/api/challenges/[A-Za-z0-9_-]+(?:/(?:download|submit))?", parts.path):
            # Submission receipts include the complete public challenge too.
            response_limit = MAX_CHALLENGE_RESPONSE
        elif parts.path == "/api/me/protocol-proposals" or re.fullmatch(r"/api/protocol/proposals/[0-9a-f-]{36}(?:/revisions)?", parts.path):
            response_limit = MAX_PROPOSAL_RESPONSE
        try:
            with self.opener.open(request, timeout=30) as response:
                if response.url != self.server + path:
                    raise ClientError("Redirects are not followed. Check the configured server.", "redirect_rejected")
                value = response.read(response_limit + 1)
        except HTTPError as error:
            if 300 <= error.code < 400:
                raise ClientError("Redirects are not followed. Check the configured server.", "redirect_rejected") from None
            messages = {401: "Connection expired or was revoked; run login again.",
                        403: "This connection is not allowed to perform that action.",
                        404: "The requested resource was not found or is not yours.",
                        409: "The request conflicts with recorded state or the round deadline. Preserve the retry file.",
                        422: "The server rejected the input format. Check the workspace and result contract.",
                        429: "Too many requests. Wait before retrying."}
            delay = retry_after_seconds(error.headers.get("Retry-After"))
            details = {"retry_after_seconds": delay} if delay is not None else {}
            proposal_request = parts.path == "/api/me/protocol-proposals" or parts.path == PROPOSALS or parts.path.startswith(PROPOSALS + "/")
            if proposal_request:
                messages.update({403: "Proposal access needs a newly connected agent with proposal permissions. Run login again and use the proposal author's account.",
                                 409: "The proposal revision or frozen retry conflicts with recorded state. Preserve the retry file; use proposal show, then proposal revise into a new directory for a new revision.",
                                 413: "The specification must fit within 48 KiB and the complete request within 64 KiB.",
                                 422: "Check the specification fields against schema.json and the template instructions."})
                if error.code == 422:
                    raw_error = error.read(65537)
                    if len(raw_error) <= 65536:
                        details["field_errors"] = proposal_field_errors(raw_error)
            if parts.path == "/api/agent/notes" or parts.path.startswith("/api/agent/notes/"):
                messages.update({403: "Note access needs notes:read or notes:rate permissions. Reconnect if needed; self-votes and ineligible accounts are not allowed.",
                                 404: "That exact published note revision is unavailable.",
                                 409: "Your account's rating changed. Use notes show to read the current version and reassess before changing it.",
                                 422: "Check the note ID, exact revision/hash, rating value and expected version."})
            if parts.path.startswith(NOTEBOOK + "/"):
                messages.update({403: "Notebook access needs explicit notebook permissions. Run login --notebook --no-browser and ask the participant to approve the connection once.",
                                 409: "The saved note, revision or retry conflicts with recorded state. Preserve the retry file; use notebook show to inspect the current revision before preparing a new request.",
                                 410: "This note is in Trash or its revision was withdrawn. Use notebook list --deleted for your deleted drafts.",
                                 413: "The note or notebook exceeds the server's bounded workspace size.",
                                 422: "Check the note-format contract, required content fields and exact revision/hash."})
                if parts.path.endswith("/delete"):
                    messages[409] = "Deletion requires the exact latest revision of an unsubmitted private draft. Submitted evidence is retained; an existing deletion requires its original retry file."
            # Never echo a request body, candidate, token, or arbitrary server error text.
            raise ClientError(messages.get(error.code, "The server could not complete the request."),
                              "http_" + str(error.code), details) from None
        except (URLError, TimeoutError, OSError):
            raise ClientError("Could not reach the configured server. Preserve the retry file and retry.", "network_error") from None
        if len(value) > response_limit:
            raise ClientError("Server response exceeds the client size limit.")
        if raw:
            return value
        try:
            parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            raise ClientError("The server returned invalid JSON.") from None
        if not isinstance(parsed, dict):
            raise ClientError("The server returned an unexpected document.")
        return parsed


def login(client, args):
    requested = list(DEFAULT_SCOPES + NOTEBOOK_SCOPES) if args.notebook else None
    if args.scope:
        if args.notebook:
            raise ClientError("Use either --notebook or explicit --scope values.")
        requested = list(dict.fromkeys(args.scope))
    body = {"label": args.label, **({"scopes": requested} if requested is not None else {})}
    start = client.request("/api/agent/connect/start", body, auth=False)
    if requested is not None and start.get("scopes") != requested:
        raise ClientError("The server did not offer the exact requested permissions. No connection was saved.")
    uri = start.get("verification_uri", "")
    parts = urlsplit(uri)
    if origin(parts.scheme + "://" + parts.netloc) != client.server:
        raise ClientError("The server provided an off-origin authorization link.")
    device_code = start.get("device_code")
    if not isinstance(device_code, str) or not device_code.startswith("crd_"):
        raise ClientError("The server returned an invalid connection request.")
    print("Approve this connection in your browser: " + uri, file=sys.stderr)
    print("Check that the displayed code is " + str(start.get("user_code", "")) + ".", file=sys.stderr)
    if not args.no_browser:
        webbrowser.open(uri)
    deadline = time.monotonic() + min(600, max(0, float(start.get("expires_in", 600))))
    interval = max(2, min(30, float(start.get("interval", 2))))
    while time.monotonic() < deadline:
        time.sleep(interval)
        try:
            result = client.request("/api/agent/connect/poll", {"device_code": device_code}, auth=False)
        except ClientError as error:
            if error.code in {"network_error", "http_502", "http_503", "http_504"}:
                # Keep the same private device code: an approval response may
                # have been lost after the server minted the credential.
                interval = min(30, max(2, interval * 2))
                continue
            if error.code != "http_429":
                raise
            delay = error.data.get("retry_after_seconds") if isinstance(error.data, dict) else None
            interval = max(interval, min(30, retry_after_seconds(delay) or 2))
            continue
        status = result.get("status")
        if status == "pending":
            continue
        if status != "authorized":
            raise ClientError("Connection request was denied or expired. Run login to start again.", "authorization_failed")
        if requested is not None and result.get("scopes") != requested:
            raise ClientError("The granted permissions differ from the request. No connection was saved.")
        token = result.get("access_token")
        if not isinstance(token, str) or not re.fullmatch(r"cru_[A-Za-z0-9_-]{43}", token):
            raise ClientError("The server returned an invalid access token.")
        expiry = result.get("expires_at")
        if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
            raise ClientError("The server returned an expired access token.")
        config = configuration(client.config_path)
        handle = result.get("user", {}).get("handle")
        if not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9_]{3,32}", handle):
            raise ClientError("The server returned an invalid account identity.")
        scopes = result.get("scopes")
        if scopes is not None and (not isinstance(scopes, list) or any(
                value not in DEFAULT_SCOPES + NOTEBOOK_SCOPES for value in scopes)):
            raise ClientError("The server returned unsupported permissions. No connection was saved.")
        config["servers"][client.server] = {"access_token": token, "expires_at": expiry, "handle": handle,
                                           **({"scopes": scopes} if scopes is not None else {})}
        config["default_server"] = client.server
        save_json(client.config_path, config)
        return {"status": "connected", "server": client.server,
                "handle": result.get("user", {}).get("handle"), "expires_at": expiry,
                **({"scopes": scopes} if scopes is not None else {})}
    raise ClientError("Connection request expired. Run login to start again.", "authorization_expired")


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ClientError("Invalid challenge or method identifier.")
    return value


def download_challenge(client, identifier):
    package = client.request("/api/challenges/" + safe_id(identifier) + "/download")
    if package.get("format") != "crucible-challenge-v1" or package.get("challenge", {}).get("id") != identifier:
        raise ClientError("The downloaded challenge does not match the requested identifier.")
    return package


def workspace(client, package, destination, *, pipeline=None, pipeline_checked=False):
    root = Path(destination).expanduser().absolute()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ClientError("Choose a new or empty workspace directory; existing files are never overwritten.")
    if not pipeline_checked:
        pipeline = agent_pipeline(client, allow_legacy=True)
    challenge = package["challenge"]
    query = "?" + urlencode({"scheme_id": challenge["scheme_id"], "version": challenge["scheme_version"]})
    specification = client.request("/api/protocol/spec" + query)
    source = client.request("/api/protocol/source" + query, raw=True)
    expected = package.get("scheme_manifest", {}).get("implementation", {}).get("source_sha256")
    if (hashlib.sha256(source).hexdigest() != expected
            or specification.get("implementation", {}).get("sha256") != expected
            or specification.get("version") != challenge["scheme_version"]):
        raise ClientError("Reference source does not match the challenge's pinned implementation hash.")
    artifacts = None
    source_files = {}
    artifact_hash = package.get("scheme_manifest", {}).get("implementation", {}).get("artifact_sha256")
    if artifact_hash is not None:
        if not isinstance(artifact_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_hash):
            raise ClientError("The challenge has an invalid implementation artifact pin.")
        artifacts = client.request("/api/protocol/artifacts" + query)
        payload = {key: value for key, value in artifacts.items() if key != "artifact_sha256"}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=True, allow_nan=False).encode("utf-8")).hexdigest()
        if (digest != artifact_hash or artifacts.get("artifact_sha256") != artifact_hash
                or artifacts.get("scheme_id") != challenge["scheme_id"]
                or artifacts.get("scheme_version", artifacts.get("version")) != challenge["scheme_version"]
                or ("adapter_source_sha256" in artifacts and artifacts["adapter_source_sha256"] != expected)):
            raise ClientError("Implementation artifacts do not match the challenge's pinned dependency closure.")
        declared = artifacts.get("files", {})
        if isinstance(declared, list):
            try:
                pairs = [(row["path"], row["sha256"]) for row in declared]
                declared = dict(pairs)
                if len(declared) != len(pairs):
                    raise ClientError("Duplicate public source artifact paths.")
            except (KeyError, TypeError):
                raise ClientError("The server returned an invalid source closure.") from None
        if not isinstance(declared, dict) or len(declared) > 32:
            raise ClientError("The source closure exceeds its file allowance.")
        if any(not isinstance(name, str) for name in declared):
            raise ClientError("Invalid public source artifact path.")
        names = {name.casefold() for name in declared}
        if len(names) != len(declared) or any("/".join(name.split("/")[:i]) in names
                for name in names for i in range(1, len(name.split("/")))):
            raise ClientError("Public source artifact paths conflict on a portable filesystem.")
        total = 0
        for name, digest in declared.items():
            if not isinstance(name, str):
                raise ClientError("Invalid public source artifact path.")
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name
                    or not path.parts or path.parts[0] not in {"crucible", "scripts", "data"}
                    or path.suffix not in {".py", ".c", ".h", ".json", ".txt", ".sage"}
                    or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise ClientError("Invalid public source artifact path or pin.")
            content = client.request("/api/protocol/artifact-source" + query + "&" + urlencode({"path": name}), raw=True)
            total += len(content)
            if len(content) > 2*1024*1024 or total > 8*1024*1024 or hashlib.sha256(content).hexdigest() != digest:
                raise ClientError("Public source closure is oversized or differs from the challenge's pinned artifacts.")
            source_files[name] = content
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if pipeline is not None:
        save_json(root / "pipeline.json", pipeline)
    save_json(root / "challenge.json", package)
    save_json(root / "protocol.json", specification)
    private_write(root / "reference" / "protocol.py", source)
    if artifacts is not None:
        save_json(root / "reference" / "implementation-artifacts.json", artifacts)
    for name, content in source_files.items():
        private_write(root / "reference" / "source" / name, content)
    save_json(root / ".crucible.json", {"format": "crucible-workspace-v1", "server": client.server,
                                      "challenge_id": challenge["id"]})
    private_write(root / "attack" / "solve.py", '''"""Implement your attack on challenge.json. Print only the recovered key as hex.
This stub deliberately performs no attack and never submits a result.
"""
import json
from pathlib import Path

challenge = json.loads(Path("challenge.json").read_text())["challenge"]
raise SystemExit("Implement the attack using the public challenge inputs first.")
''')
    private_write(root / "method.md", "# Local attack\n\n" + METHOD_TODO +
                  ": describe your actual algorithm, key derivation, preparation, and reused data before running.\n")
    save_json(root / "result.json", {"format": "crucible-recovery-v1", "challenge_id": challenge["id"],
        "candidate": "", "ai_model": "", "method": {"name": "Local attack", "pseudocode": ""},
        "compute": {"hardware": "", "workers": 1, "wall_seconds": None,
                    "precomputation": "Not reported", "peak_memory": "Not measured", "notes": ""},
        "_instructions": {"workflow": "Implement attack/solve.py and describe the method in method.md. Run with the client to generate this result, then submit explicitly.",
                          "candidate": "Print the complete shared secret in hex from your solver, or one JSON object with only candidate.",
                          "ai_model": "Optional text, at most 300 characters: name and version of the AI model(s) that helped implement this result. Outer whitespace is trimmed. Leave blank or omit if unreported; null, NUL and invalid Unicode are rejected. Report only what you know; this is self-reported and does not affect points.",
                          "compute": "Only the local command's wall time is measured. Declare external work, workers and precomputation accurately.",
                          "deadline": "Complete submissions must reach the server strictly before challenge.ends_at; a retry never changes the answer."}})
    private_write(root / "README.md", "# Crucible attack workspace\n\n"
        + pipeline_readme(client, pipeline) +
        "Edit attack/solve.py and method.md, then run your own command through the client.\n"
        "The solver reads challenge.json and prints one hexadecimal shared secret (or JSON with candidate).\n"
        "Protocol source under reference/ is for inspection; it is never automatically executed.\n"
        "The exact profile ID identifies this configuration. Frozen instance_size/tier fields are legacy selectors and can differ from displayed stages.\n"
        "If present, reference/implementation-artifacts.json records the pinned dependency closure.\n"
        "Declared first-party source and parameter files are hash-checked under reference/source/.\n"
        "Its digest is verified against the challenge; this does not install or independently audit those dependencies.\n"
        "Run: python /path/to/crucible_cli.py run -- python attack/solve.py\n"
        "Optionally add --ai-model \"Model name and version\" before -- to credit the AI model(s) that helped implement this result.\n"
        "Submit explicitly: python /path/to/crucible_cli.py submit result.json\n"
        "Use --source attack/helper.py to include additional selected UTF-8 source files.\n"
        "The defaults are attack/solve.py and method.md. Credentials, results and local state are excluded.\n"
        "Results, source and compute declarations are uploaded to the configured Crucible server.\n"
        "A verified key does not verify your reported compute or method. Do not put credentials in source.\n"
        "Once submission starts, keep .crucible/submission.json: retries reuse its frozen payload.\n")
    private_write(root / ".gitignore", ".crucible/\n.crucible.json\nchallenge.json\nresult.json\nreceipt.json\n__pycache__/\n.env\n")
    manifest = package.get("scheme_manifest", {})
    named = manifest.get("profile_kind") == "standard-parameter-set"
    return {"status": "ready", "path": str(root), "challenge_id": challenge["id"],
            "pipeline_status": "saved" if pipeline is not None else "unavailable-legacy-server",
            "scheme_id": challenge["scheme_id"], "profile_id": manifest.get("profile_id", manifest.get("id")),
            "profile_label": manifest.get("profile_label", manifest.get("name")),
            "selection_kind": "parameter-set" if named else "tier",
            "tier": None if named else manifest.get("instance_size"),
            "ends_at": challenge["ends_at"], "challenge_status": challenge["status"]}


def init_workspace(client, args):
    schemes = public_reference(client, "/api/schemes").get("schemes", [])
    scheme = next((item for item in schemes if item.get("id") == args.scheme), None)
    if scheme is None:
        raise ClientError("Unknown scheme; use schemes to list available identifiers.")
    stage = getattr(args, "stage", None)
    if (args.tier is not None or stage is not None) and (scheme.get("selection_kind") == "parameter-set"
            or any(item.get("profile_kind") == "standard-parameter-set" for item in scheme.get("profiles", []))):
        raise ClientError("This scheme uses named parameter sets. Select an exact --profile-id from schemes; storage ordinals are not stages.")
    stage_entry = None
    if stage is not None:
        progression = scheme.get("default_progression")
        if not isinstance(progression, dict):
            raise ClientError("This server has no published stage progression. Use an exact --profile-id from schemes, or the legacy --tier selector.")
        stage_entry = next((item for item in progression.get("stages", []) if item.get("number") == stage), None)
        if stage_entry is None:
            raise ClientError("That stage is not available in this scheme's default progression.")
    profile = next((item for item in scheme.get("profiles", []) if
                    (item.get("profile_id", item.get("id")) == stage_entry["profile_id"] if stage_entry is not None
                     else item.get("profile_id", item.get("id")) == args.profile_id if args.profile_id is not None
                     else item.get("instance_size") == args.tier)), None)
    if profile is None:
        raise ClientError("That parameter profile is not available for this scheme.")
    # Validate destination before issuing a challenge as a side effect.
    destination = Path(args.path).expanduser()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ClientError("Choose a new or empty workspace directory.")
    selected_id = profile.get("profile_id", profile.get("id"))
    if not isinstance(selected_id, str) or not selected_id:
        raise ClientError("The selected parameter profile has no valid identifier.")
    # Obtain the model contract before issuing an hourly challenge. A failed
    # contract read must not silently issue an instance or create local files.
    pipeline = agent_pipeline(client, allow_legacy=True)
    query = urlencode({"scheme_id": scheme["id"], "scheme_version": scheme["version"], "profile_id": selected_id})
    current = client.request("/api/challenges?" + query).get("challenge")
    if current is None:
        current = client.request("/api/challenges", {"scheme_id": scheme["id"], "scheme_version": scheme["version"],
                                                     "profile_id": selected_id})["challenge"]
    package = download_challenge(client, current["id"])
    downloaded = package.get("scheme_manifest", {})
    if (package["challenge"].get("scheme_id") != scheme["id"]
            or package["challenge"].get("scheme_version") != scheme["version"]
            or downloaded.get("profile_id", downloaded.get("id")) != selected_id):
        raise ClientError("The server returned a challenge for a different parameter profile.")
    result = workspace(client, package, args.path, pipeline=pipeline, pipeline_checked=True)
    if "stage_number" in profile:
        result.update(stage=profile["stage_number"], stage_label=profile.get("stage_label"),
                      progression_id=profile.get("progression_id"))
    if stage_entry is not None:
        result.update(stage=stage, stage_label=f"Stage {stage}",
                      progression_id=progression["id"])
    return result


def workspace_metadata(path):
    root = Path(path).expanduser().absolute()
    metadata = load_json(root / ".crucible.json", 4096)
    if metadata.get("format") != "crucible-workspace-v1":
        raise ClientError("Run init or get to create a challenge workspace first.")
    safe_id(metadata.get("challenge_id"))
    return root, metadata


def method_from_file(root):
    path = root / "method.md"
    if path.is_symlink():
        raise ClientError("method.md must be a regular local file.")
    text = path.read_text(encoding="utf-8")
    if METHOD_TODO in text or not text.strip() or len(text) > 15000:
        raise ClientError("Describe the actual attack in method.md before running; maximum 15000 characters.")
    first = text.splitlines()[0].lstrip("# ").strip()
    return {"name": first[:100] or "Local attack", "pseudocode": text.strip()}


def run_solver(args):
    root, metadata = workspace_metadata(args.path)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise ClientError("Provide a local solver command after --.")
    ai_model = normalize_ai_model(args.ai_model)
    if (root / ".crucible" / "submission.json").exists():
        raise ClientError("A submission is already frozen for this workspace. Retry it without changing the answer.")
    package = load_json(root / "challenge.json", MAX_CHALLENGE_RESPONSE)
    if package.get("challenge", {}).get("id") != metadata["challenge_id"]:
        raise ClientError("Workspace and challenge identifiers differ.")
    method = method_from_file(root)
    started = time.monotonic()
    # Explicit local command only: no shell and no fetched reference execution.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=errors) as process:
            try:
                output = process.stdout.read(16385)
                if len(output) > 16384:
                    process.kill()
                    raise ClientError("Solver stdout is too large; print only a single recovered key.")
                code = process.wait()
            except BaseException:
                process.kill()
                process.wait()
                raise
            elapsed = time.monotonic() - started
            errors.seek(0)
            private_write(root / ".crucible" / "run.stderr", errors.read(65536))
    if code:
        raise ClientError("Solver failed. Its diagnostic output is in .crucible/run.stderr; no result was submitted.", "solver_failed")
    try:
        candidate = output.decode("utf-8").strip()
        if candidate.startswith("{"):
            document = json.loads(candidate)
            if not isinstance(document, dict) or set(document) != {"candidate"}:
                raise ValueError()
            candidate = document["candidate"]
    except (ValueError, UnicodeError):
        raise ClientError("Solver must print one hexadecimal key or one JSON object containing candidate.") from None
    result = {"format": "crucible-recovery-v1", "challenge_id": metadata["challenge_id"],
              "candidate": candidate, "method": method,
              "compute": {"hardware": args.hardware or (platform.system() + " " + platform.machine() +
                  "; " + str(os.cpu_count() or "unknown") + " logical CPUs available; accelerator usage not measured"),
                  "workers": args.workers, "wall_seconds": elapsed,
                  "precomputation": args.precomputation or "Not reported; prior and external work are not measured by this runner.",
                  "peak_memory": "Not measured", "notes": "Wall time measures this local command, including startup. Workers are declared, not observed. Remote compute and previous attempts are not included."}}
    if ai_model:
        result["ai_model"] = ai_model
    result = validate_result(result, package)
    save_json(root / "result.json", result)
    return {"status": "result_written", "path": str(root / "result.json"), "wall_seconds": elapsed,
            "challenge_id": metadata["challenge_id"], "submitted": False}


def normalize_ai_model(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ClientError("ai_model must be text of at most 300 characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ClientError("ai_model must contain valid Unicode text.") from None
    if "\x00" in value:
        raise ClientError("ai_model cannot contain NUL characters.")
    return value.strip()


def validate_result(result, package):
    if result.get("format") != "crucible-recovery-v1" or set(result) - {"format", "challenge_id", "candidate", "method", "compute", "ai_model", "_instructions"}:
        raise ClientError("Use the crucible-recovery-v1 result format.")
    if result.get("challenge_id") != package.get("challenge", {}).get("id"):
        raise ClientError("The result belongs to a different challenge; open its exact workspace.")
    key_bytes = package.get("scheme_manifest", {}).get("shared_secret_bytes")
    candidate = result.get("candidate")
    if type(key_bytes) is not int or not 1 <= key_bytes <= 4096 or not isinstance(candidate, str) or not re.fullmatch(r"[0-9a-fA-F]{" + str(key_bytes * 2) + "}", candidate):
        raise ClientError("The candidate must be the complete shared secret in the challenge's exact hexadecimal encoding.")
    def fields(value, limits, required):
        if not isinstance(value, dict) or set(value) - set(limits) or not set(required) <= set(value):
            raise ClientError("Method or compute fields do not match the result contract.")
        cleaned = {}
        for field, limit in limits.items():
            text = value.get(field, "")
            if not isinstance(text, str) or len(text) > limit or (field in required and not text.strip()):
                raise ClientError("Invalid result field: " + field)
            cleaned[field] = text.strip()
        return cleaned
    method = fields(result.get("method"), {"name": 100, "pseudocode": 15000}, ["name", "pseudocode"])
    if METHOD_TODO in method["pseudocode"] or len(encode(method)) > 65536:
        raise ClientError("Complete the method description before submission.")
    compute = result.get("compute")
    if not isinstance(compute, dict):
        raise ClientError("A compute declaration is required.")
    workers, wall = compute.get("workers"), compute.get("wall_seconds")
    if type(workers) is not int or not 1 <= workers <= 1000000 or type(wall) not in (int, float) or not math.isfinite(wall) or not 0 <= wall <= 315576000:
        raise ClientError("Declare a valid worker count and finite nonnegative attack wall time.")
    texts = {field: value for field, value in compute.items() if field not in {"workers", "wall_seconds"}}
    cleaned = fields(texts, {"hardware": 500, "precomputation": 2000, "peak_memory": 300, "notes": 2000},
                     ["hardware", "precomputation", "peak_memory"])
    ai_model = normalize_ai_model(result.get("ai_model", ""))
    return {"format": "crucible-recovery-v1", "challenge_id": result["challenge_id"],
            "candidate": candidate.lower(), "method": method, "compute": {**cleaned, "workers": workers, "wall_seconds": wall},
            **({"ai_model": ai_model} if ai_model else {})}


def source_bundle(root, selected):
    paths = list(dict.fromkeys(["attack/solve.py", "method.md", *(selected or [])]))
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                *[f"LPT{i}" for i in range(1, 10)]}
    # Match the service's portable artifact contract before freezing a retry.
    # Validate the complete list first, including case-insensitive collisions.
    for value in paths:
        if (not isinstance(value, str) or len(value) > 240
                or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]*", value)):
            raise ClientError("Selected source must use portable relative paths.")
        parts = value.split("/")
        if len(parts) > 12 or any(not part or part.startswith(".") or part.endswith((".", " "))
                or len(part) > 100 or part.split(".")[0].upper() in reserved for part in parts):
            raise ClientError("Selected source paths must be unambiguous and nonhidden.")
        if not (value == "method.md" or len(parts) > 1 and parts[0] == "attack"):
            raise ClientError("Source files must be explicitly selected beneath attack/, or method.md.")
    names = {value.casefold() for value in paths}
    if len(names) != len(paths) or any("/".join(value.split("/")[:i]) in names
            for value in names for i in range(1, len(value.split("/")))):
        raise ClientError("Selected source paths conflict when treated as portable filenames.")
    files, total = [], 0
    for value in paths:
        path = PurePosixPath(value)
        local = root.joinpath(*path.parts)
        if any(part.is_symlink() for part in (local, *local.parents)) or not local.is_file():
            raise ClientError("Selected source must be a regular file without symbolic links: " + value)
        with local.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ClientError("A selected source file exceeds 256 KiB: " + value)
        try:
            content = raw.decode("utf-8")
        except UnicodeError:
            raise ClientError("Only UTF-8 source files can be included: " + value) from None
        if "\x00" in content:
            raise ClientError("Selected source text cannot contain NUL bytes: " + value)
        total += len(raw)
        files.append({"path": value, "content": content})
    bundle = {"format": "crucible-source-v1", "files": files}
    if len(files) > 100 or total > 1024 * 1024 or len(encode(bundle)) > 2 * 1024 * 1024:
        raise ClientError("Selected source exceeds the upload limit.")
    return bundle


def submit(client, args):
    root, metadata = workspace_metadata(args.path)
    result_path = Path(args.result)
    result = load_json(result_path if result_path.is_absolute() else root / result_path, 128 * 1024)
    package = load_json(root / "challenge.json", MAX_CHALLENGE_RESPONSE)
    result = validate_result(result, package)
    if result["challenge_id"] != metadata["challenge_id"]:
        raise ClientError("Workspace and result challenge identifiers differ.")
    fingerprint = hashlib.sha256(encode(result)).hexdigest()
    pending_path = root / ".crucible" / "submission.json"
    if pending_path.exists():
        pending = load_json(pending_path, 3 * 1024 * 1024)
        if (pending.get("format") != "crucible-pending-submission-v1" or pending.get("result") != result
                or pending.get("result_sha256") != fingerprint or pending.get("server") != client.server):
            raise ClientError("A different submission is already frozen. Retry the original result and server.")
        if pending.get("receipt"):
            return {"status": "recorded", "submission": pending["receipt"], "replayed": True}
    else:
        if method_from_file(root) != result["method"]:
            raise ClientError("method.md differs from the result's method. Update the result explicitly or run the solver again before submitting.")
        fresh = download_challenge(client, result["challenge_id"])
        validate_result(result, fresh)
        if fresh["challenge"].get("status") != "open":
            raise ClientError("This challenge is no longer open; no new submission was sent.", "challenge_closed")
        pending = {"format": "crucible-pending-submission-v1", "server": client.server,
                   "result_sha256": fingerprint, "result": result, "source": source_bundle(root, args.source),
                   "method_id": None, "source_uploaded": False, "idempotency_key": str(uuid4())}
        save_json(pending_path, pending)
    if not pending.get("method_id"):
        # Never infer source identity from matching prose. If this response is
        # lost, a retry can leave an unused method, but cannot change an answer.
        match = client.request("/api/methods", result["method"])["method"]
        pending["method_id"] = safe_id(match["id"])
        save_json(pending_path, pending)
    if not pending.get("source_uploaded"):
        source = client.request("/api/methods/" + safe_id(pending["method_id"]) + "/source", pending["source"])
        pending["source_uploaded"] = True
        pending["source_receipt"] = source.get("source")
        save_json(pending_path, pending)
    if not pending.get("payload"):
        pending["payload"] = {"candidate": result["candidate"], "compute": result["compute"],
                              "method_id": pending["method_id"], "idempotency_key": pending["idempotency_key"],
                              **({"ai_model": result["ai_model"]} if result.get("ai_model") else {})}
        # The exact answer and retry ID are durable before the first answer write.
        save_json(pending_path, pending)
    response = client.request("/api/challenges/" + safe_id(result["challenge_id"]) + "/submit", pending["payload"])
    receipt = response.get("submission")
    if not isinstance(receipt, dict) or receipt.get("status") not in {"correct", "incorrect"}:
        raise ClientError("No recognizable receipt returned. Preserve the retry file and retry the same result.")
    pending["receipt"] = receipt
    save_json(pending_path, pending)
    save_json(root / "receipt.json", {"submission": receipt, "source": pending.get("source_receipt")})
    return {"status": "recorded", "submission": receipt, "source": pending.get("source_receipt"), "replayed": False}


def proposal_id(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ClientError("Use the full proposal UUID returned by proposal list or submit.") from None
    return value


def proposal_owner(client):
    credential = configuration(client.config_path)["servers"].get(client.server)
    handle = credential.get("handle") if isinstance(credential, dict) else None
    if not isinstance(handle, str) or not re.fullmatch(r"[a-z0-9_]{3,32}", handle):
        raise ClientError("Run login again to connect proposal permissions and record the proposal author's identity.", "login_required")
    return handle


def proposal_metadata(path):
    root = Path(path).expanduser().absolute()
    metadata = load_json(root / ".crucible-proposal.json", 4096)
    if metadata.get("format") != "crucible-proposal-workspace-v1" or metadata.get("mode") not in {"new", "revision"}:
        raise ClientError("Use proposal init or proposal revise to create a proposal workspace first.")
    if origin(metadata.get("server")) != metadata.get("server"):
        raise ClientError("The proposal workspace has an invalid server binding.")
    if metadata["mode"] == "revision":
        proposal_id(metadata.get("proposal_id"))
        if (type(metadata.get("expected_revision")) is not int or not 1 <= metadata["expected_revision"] < 2147483647
                or not isinstance(metadata.get("content_sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", metadata["content_sha256"]) is None
                or not isinstance(metadata.get("owner_handle"), str)
                or re.fullmatch(r"[a-z0-9_]{3,32}", metadata["owner_handle"]) is None):
            raise ClientError("The proposal's revision base is incomplete or invalid.")
    return root, metadata


def empty_proposal_destination(path):
    root = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ClientError("Refusing to write a proposal through a symbolic link.")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ClientError("Choose a new or empty proposal directory; existing files are never overwritten.")
    return root


def proposal_document(value):
    if not isinstance(value, dict) or value.get("format") != "crucible-kem-spec-v1":
        raise ClientError("Expected a crucible-kem-spec-v1 specification object.")
    if len(encode(value)) > MAX_SPECIFICATION:
        raise ClientError("The specification must fit within 48 KiB of UTF-8 JSON.", "proposal_too_large")
    return value


def checked_proposal(record):
    if not isinstance(record, dict):
        raise ClientError("The server returned an invalid proposal record.")
    proposal_id(record.get("id"))
    if (type(record.get("revision")) is not int or not 1 <= record["revision"] <= 2147483647
            or record.get("status") not in {"submitted", "approved", "changes_requested"}
            or not isinstance(record.get("content_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", record["content_sha256"]) is None):
        raise ClientError("The server returned an invalid proposal revision.")
    document = proposal_document(record.get("specification"))
    if hashlib.sha256(encode(document)).hexdigest() != record["content_sha256"]:
        raise ClientError("The proposal content does not match its recorded hash.")
    return record


def proposal_summary(record):
    return {key: record[key] for key in ("id", "status", "revision", "content_sha256", "created_at") if key in record}


def save_specification(path, value):
    pretty = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    private_write(path, pretty if len(pretty) <= MAX_SPECIFICATION else encode(value))


def proposal_result(client, receipt, *, replayed):
    result = {"status": "recorded", "proposal": proposal_summary(receipt), "replayed": replayed,
              "proposal_url": client.server + "/#propose?proposal=" + proposal_id(receipt.get("id"))}
    source = receipt.get("source_note")
    if isinstance(source, dict) and isinstance(source.get("revision"), int) and source["revision"] >= 1:
        result["note_url"] = client.server + "/#notebook?note=" + proposal_id(source.get("id")) + "&revision=" + str(source["revision"])
    return result


def public_reference(client, path, body=None, *, raw=False):
    """Read public resources, or use beta credentials after one 401.

    This helper is only for read-only resources and format validation. Proposal
    writes retain their existing authenticated, durable idempotent workflow.
    """
    try:
        return client.request(path, body, auth=False, raw=raw)
    except ClientError as error:
        if error.code != "http_401":
            raise
    return client.request(path, body, auth=True, raw=raw)


def proposal_reference(client, path, body=None, *, raw=False):
    return public_reference(client, path, body, raw=raw)


def agent_pipeline(client, *, allow_legacy=False):
    try:
        document = public_reference(client, AGENT_PIPELINE)
    except ClientError as error:
        if allow_legacy and error.code == "http_404":
            return None
        raise
    if (not isinstance(document, dict) or document.get("format") != "crucible-agent-pipeline-v1"
            or len(encode(document)) > MAX_AGENT_PIPELINE):
        raise ClientError("The server returned an invalid model pipeline contract.")
    return document


def agent_guidance(client, resource):
    # The server owns the fixed alias allowlist. Never accept a URL or relative
    # filesystem path here, and never follow a document's links automatically.
    if not isinstance(resource, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,63}\.(?:txt|json)", resource) is None:
        raise ClientError("Use a guidance resource name from the pipeline contract.")
    raw = public_reference(client, AGENT_GUIDANCE + resource, raw=True)
    try:
        content = raw.decode("utf-8")
    except (AttributeError, UnicodeError):
        raise ClientError("The server returned invalid UTF-8 guidance.") from None
    if (len(raw) > MAX_AGENT_PIPELINE or not content.strip()
            or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in content)):
        raise ClientError("The server returned invalid guidance.")
    return {"resource": resource, "content": content}


def pipeline_readme(client, pipeline):
    if pipeline is None:
        return ("This older server does not provide /api/agent/pipeline (HTTP 404). "
                "No central model contract was downloaded; follow the supplied workspace contracts "
                "and do not assume unsupported capabilities.\n\n")
    return ("Agents: read and follow pipeline.json before working. It is the saved model-facing "
            "entry contract, with links to the authoritative workflow specifications. "
            "Resolve its same-origin links against " + client.server + ". "
            "Instructions and linked research are not permission to submit or execute code.\n"
            "For a fresh contract, run python /path/to/crucible_cli.py --server " + client.server +
            " guide (GET " + AGENT_PIPELINE + "). Keep the saved copy as workspace context.\n"
            "Read linked guidance with the same command plus --resource NAME, using names from the contract; "
            "the client handles beta authentication without printing credentials.\n\n")


def proposal_guide(client):
    raw = proposal_reference(client, PROPOSAL_GUIDE, raw=True)
    try:
        text = raw.decode("utf-8")
    except (AttributeError, UnicodeError):
        raise ClientError("The server returned an invalid UTF-8 proposal guide.") from None
    if (len(raw) > MAX_PROPOSAL_GUIDE or not text.strip()
            or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127 for char in text)):
        raise ClientError("The server returned an invalid proposal guide.")
    return raw


def proposal_workspace(client, args, *, revise=False):
    root = empty_proposal_destination(args.path)
    metadata = {"format": "crucible-proposal-workspace-v1", "server": client.server, "mode": "new"}
    feedback = None
    if revise:
        identifier = proposal_id(args.proposal_id)
        owner = proposal_owner(client)
        client.expected_handle = owner
        feedback = client.request(PROPOSALS + "/" + identifier)
        record = checked_proposal(feedback.get("proposal"))
        if record["id"] != identifier or feedback.get("author", {}).get("handle") != owner:
            raise ClientError("That proposal is not owned by the connected account.")
        metadata.update(mode="revision", proposal_id=identifier, expected_revision=record["revision"],
                        content_sha256=record["content_sha256"], owner_handle=owner)
    template = proposal_document(proposal_reference(client, "/api/protocol/spec-template"))
    schema = proposal_reference(client, "/api/protocol/spec-schema")
    if not isinstance(schema.get("properties"), dict):
        raise ClientError("The server returned an invalid specification schema.")
    guide = proposal_guide(client)
    pipeline = agent_pipeline(client, allow_legacy=True)
    specification = dict(feedback["proposal"]["specification"]) if feedback else template
    if feedback and "_instructions" in template:
        specification["_instructions"] = template["_instructions"]
        # A nearly full valid revision must remain editable and valid. Guidance
        # still remains available in template.json if it does not fit inline.
        if len(encode(specification)) > MAX_SPECIFICATION:
            specification.pop("_instructions", None)
    empty_proposal_destination(root)  # Recheck after all network reads.
    save_specification(root / "specification.json", specification)
    save_json(root / "template.json", template)
    save_json(root / "schema.json", schema)
    private_write(root / "proposal-guide.txt", guide)
    if pipeline is not None:
        save_json(root / "pipeline.json", pipeline)
    save_json(root / ".crucible-proposal.json", metadata)
    if feedback:
        save_json(root / "feedback.json", feedback)
    private_write(root / ".gitignore", ".crucible/\n.crucible-proposal.json\nproposal-receipt.json\nfeedback.json\n.env\n")
    private_write(root / "README.md", "# Crucible scheme proposal\n\n"
        + pipeline_readme(client, pipeline) +
        "Start with proposal-guide.txt: the server's current proposal guidelines and research advice, downloaded with this workspace.\n"
        "Edit specification.json; template.json contains detailed field guidance and schema.json is the format contract.\n"
        "Describe a KEM: complete algorithms, full randomness/distributions, parameter mappings, encodings, KDF and rejection behavior.\n"
        "Explain its merit, the closest existing schemes and what new attack evidence it could contribute. Cite primary sources.\n"
        "Spend a separate, deliberate pass on citations: read the primary sources, check exact versions and claim scope, and identify the supporting section, theorem or page in the relevant argument. Cite existing Crucible notes by their exact revision link and content hash, with permission for any private material. Separate mathematical ingredients from a published complete KEM and from inherited security claims. Mark inaccessible or unverified sources and originality uncertainty explicitly; never invent citations or add unrelated ones for appearance.\n"
        "State an explicit correctness guarantee and scoped security claim, with all assumptions and parameter coverage. For correctness, claim perfect agreement or failure probability at most 2^-128 for every admitted configuration and give the reasoning conditional on the stated premises. Use justification.kind conjectural-claim or model-based-analysis when appropriate; motivate unproved assumptions and identify what could support or refute them. Weaker or unknown correctness claims are draft-only. Statistical claims must declare failure_bound_exponent as an integer at least 128 consistent with the bound; perfect correctness states failure_bound 0 and omits the exponent. policy_id stays optional. The target is required; proving every assumption is not. Distinguish gaps in justification from rigorous refutations of the actual failure bound; a looser upper bound alone does not refute it. Tests do not prove assumptions; report generation aborts separately.\n"
        "Complete the tier_design plan before approval: define the valid configuration space and an ordered default progression of stages, mapped to stable configuration identifiers; describe actual parameter growth and costs, a common correctness requirement (negligible nonzero failure is allowed), endpoint evidence and useful steps or explicit gaps. Round default counts to multiples of ten, or five, only when useful configurations and endpoints can be preserved; other reviewed configurations remain available. Counts are not security levels. Legacy supported_tiers and instance_size identify configurations, not necessarily stage positions. Preserve configuration identity and revise the progression explicitly when changing its route. Complete its parameter_selection object with a cost objective, fixed constraints, pinned security/cost model, candidate domain, comparison evidence and tradeoffs/uncertainty. Choose efficient or non-dominated parameters within that domain; preserve the original construction and full native distributions; never manufacture stages with padding or entropy restriction alone. Drafts may omit the plan; approval requires explicit manual review of both progression design and parameter selection.\n"
        "Document reduced-parameter divergences; do not inherit a published scheme's security or correctness claims without justification.\n"
        "References, pseudocode and implementation URLs are inert text: this client never fetches or executes them.\n"
        "Validate: python /path/to/crucible_cli.py proposal validate --path .\n"
        "Draft resources and format validation try public access first; a private beta requires a configured connection and proposal permissions. Validation checks format only.\n"
        "Submit explicitly: python /path/to/crucible_cli.py proposal submit --path .\n"
        "Submissions remain private to the author and configured reviewers, pending manual review and separate implementation admission.\n"
        "A proposal submission does not create a live challenge or earn admission points.\n"
        "If proposal access is denied, run login again for the new proposal permissions. Keep credentials outside this directory.\n"
        "Before the first proposal write, .crucible/proposal-submission.json freezes the exact normalized body, author and retry key.\n"
        "If a response is lost, retry submit unchanged. Do not delete that file or create a fresh retry key.\n"
        "Edits after freezing are rejected. Preserve the original workspace and create a new revision workspace for new content.\n"
        "Inspect feedback: python /path/to/crucible_cli.py proposal show PROPOSAL_ID\n"
        "Revise: python /path/to/crucible_cli.py proposal revise PROPOSAL_ID --path ../revision-2\n"
        "Revision workspaces include feedback.json and bind the current revision/hash. A stale base is rejected by the server.\n")
    return {"status": "ready", "path": str(root), "server": client.server, "mode": metadata["mode"],
            "pipeline_status": "saved" if pipeline is not None else "unavailable-legacy-server",
            **({"proposal_id": metadata["proposal_id"], "expected_revision": metadata["expected_revision"]} if revise else {})}


def validated_proposal(client, specification):
    response = proposal_reference(client, PROPOSALS + "/validate", {"specification": proposal_document(specification)})
    canonical = proposal_document(response.get("specification"))
    digest = hashlib.sha256(encode(canonical)).hexdigest()
    if response.get("content_sha256") != digest or response.get("validation", {}).get("status") != "format-valid":
        raise ClientError("The validation response has an invalid content hash or status.")
    return canonical, digest


def proposal_validate(client, args):
    root, _ = proposal_metadata(args.path)
    _, digest = validated_proposal(client, load_json(root / "specification.json", MAX_SPECIFICATION))
    return {"status": "format-valid", "content_sha256": digest,
            "scope": "Format only; correctness, security, novelty, implementation and admission remain unverified."}


def proposal_submit(client, args):
    root, metadata = proposal_metadata(args.path)
    specification = proposal_document(load_json(root / "specification.json", MAX_SPECIFICATION))
    input_digest = hashlib.sha256(encode(specification)).hexdigest()
    owner = proposal_owner(client)
    if metadata.get("owner_handle", owner) != owner:
        raise ClientError("Reconnect the account that owns this proposal revision.", "proposal_account_changed")
    client.expected_handle = owner
    endpoint = PROPOSALS + ("/" + metadata["proposal_id"] + "/revisions" if metadata["mode"] == "revision" else "")
    expected_revision = metadata["expected_revision"] + 1 if metadata["mode"] == "revision" else 1
    pending_path = root / ".crucible" / "proposal-submission.json"
    if pending_path.exists():
        pending = load_json(pending_path, 128 * 1024)
        if (pending.get("format") != "crucible-pending-proposal-v1" or pending.get("workspace") != metadata
                or pending.get("input_sha256") != input_digest or pending.get("owner_handle") != owner):
            raise ClientError("A different proposal or author is already frozen. Restore the original specification and account to retry; use a new revision workspace for changes.", "proposal_retry_changed")
        payload = pending.get("payload")
        if not isinstance(payload, dict) or hashlib.sha256(encode(payload)).hexdigest() != pending.get("payload_sha256"):
            raise ClientError("The frozen proposal retry file changed. Preserve it for recovery; no request was sent.")
        proposal_id(payload.get("idempotency_key"))
        canonical = proposal_document(payload.get("specification"))
        digest = hashlib.sha256(encode(canonical)).hexdigest()
        expected_keys = {"specification", "idempotency_key"}
        if metadata["mode"] == "revision":
            expected_keys |= {"expected_revision", "content_sha256"}
            if payload.get("expected_revision") != metadata["expected_revision"] or payload.get("content_sha256") != metadata["content_sha256"]:
                raise ClientError("The frozen revision base changed. No request was sent.")
        if set(payload) != expected_keys:
            raise ClientError("The frozen proposal payload has unexpected fields.")
        if pending.get("receipt"):
            receipt = pending["receipt"]
            if (not isinstance(receipt, dict) or receipt.get("content_sha256") != digest
                    or receipt.get("revision") != expected_revision
                    or receipt.get("status") not in {"submitted", "approved", "changes_requested"}
                    or metadata["mode"] == "revision" and receipt.get("id") != metadata["proposal_id"]):
                raise ClientError("The saved proposal receipt does not match the frozen request.")
            return proposal_result(client, receipt, replayed=True)
    else:
        canonical, digest = validated_proposal(client, specification)
        payload = {"specification": canonical, "idempotency_key": str(uuid4())}
        if metadata["mode"] == "revision":
            payload.update(expected_revision=metadata["expected_revision"], content_sha256=metadata["content_sha256"])
        pending = {"format": "crucible-pending-proposal-v1", "workspace": metadata,
                   "input_sha256": input_digest, "owner_handle": owner,
                   "payload": payload, "payload_sha256": hashlib.sha256(encode(payload)).hexdigest()}
        # The retry identity and exact request are durable before any proposal
        # mutation. A lost response never causes a new key or recanonicalization.
        save_json(pending_path, pending)
    if len(encode(payload)) > 65536:
        raise ClientError("The frozen proposal request exceeds 64 KiB.")
    response = client.request(endpoint, payload)
    record = checked_proposal(response.get("proposal"))
    if (record["content_sha256"] != digest or record["revision"] != expected_revision
            or metadata["mode"] == "revision" and record["id"] != metadata["proposal_id"]):
        raise ClientError("The returned proposal receipt does not match the frozen request. Preserve the retry file.")
    receipt = proposal_summary(record)
    pending["receipt"] = receipt
    save_json(pending_path, pending)
    save_json(root / "proposal-receipt.json", {"format": "crucible-proposal-receipt-v1",
        "server": client.server, "owner_handle": owner, "proposal": receipt,
        "proposal_url": client.server + "/#propose?proposal=" + record["id"]})
    return proposal_result(client, receipt, replayed=False)


def proposal_read(client, args):
    if args.proposal_action == "list":
        response = client.request("/api/me/protocol-proposals")
        records = response.get("proposals")
        if not isinstance(records, list) or len(records) > 50:
            raise ClientError("The server returned an invalid proposal list.")
        return {"proposals": [{**proposal_summary(checked_proposal(record)),
                               "name": record["specification"].get("name")} for record in records],
                "limit": 50}
    identifier = proposal_id(args.proposal_id)
    query = {"limit": args.limit}
    if args.before_revision is not None:
        if not 1 <= args.before_revision <= 2147483647:
            raise ClientError("--before-revision must be a positive revision number.")
        query["before_revision"] = args.before_revision
    response = client.request(PROPOSALS + "/" + identifier + "/revisions?" + urlencode(query))
    if checked_proposal(response.get("proposal"))["id"] != identifier:
        raise ClientError("The server returned a different proposal.")
    return response


def notes_command(client, args):
    """Explicit pins and compare-and-set versions make identical retries safe."""
    if args.notes_action == "list":
        query = {"order": args.order, "limit": args.limit}
        for name, value in (("q", args.search), ("tag", args.tag), ("after", args.after)):
            if value is not None:
                query[name] = value
        return client.request("/api/agent/notes?" + urlencode(query))
    identifier = args.note_id
    if (not 1 <= len(identifier) <= 108 or any(ord(c) < 32 for c in identifier)
            or identifier != identifier.strip()):
        raise ClientError("Use the exact saved note ID.")
    if identifier.startswith("curated:"):
        if not identifier[8:].strip():
            raise ClientError("Use the complete curated note ID.")
    else:
        try:
            if str(UUID(identifier)) != identifier:
                raise ValueError
        except ValueError:
            raise ClientError("Use a full note UUID or curated: note ID.") from None
    path = "/api/agent/notes/" + quote(identifier, safe="")
    if args.revision is not None and not 1 <= args.revision <= 100:
        raise ClientError("--revision must be between 1 and 100.")
    if args.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.sha256):
        raise ClientError("--sha256 must be the exact note content hash.")
    if args.notes_action == "rate":
        if not 0 <= args.expected_version <= 2147483646:
            raise ClientError("Use the current your_rating.version as --expected-version.")
        return client.request(path + "/rating", {"value": args.value, "revision": args.revision,
            "content_sha256": args.sha256, "expected_version": args.expected_version})
    query = {key: value for key, value in (("revision", args.revision), ("content_sha256", args.sha256)) if value is not None}
    result = client.request(path + ("?" + urlencode(query) if query else ""))
    saved = result.get("note")
    if (not isinstance(saved, dict) or saved.get("id") != identifier or not isinstance(saved.get("content"), dict)
            or (args.revision is not None and saved.get("revision") != args.revision)
            or (args.sha256 is not None and saved.get("content_sha256") != args.sha256)
            or hashlib.sha256(encode(saved["content"])).hexdigest() != saved.get("content_sha256")):
        raise ClientError("The server returned a different note or an invalid content hash.")
    return result


def notebook_id(value):
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ClientError("Use the full saved notebook UUID.") from None
    return value


def notebook_pin(revision, digest):
    if type(revision) is not int or not 1 <= revision <= 100:
        raise ClientError("Use an exact note revision between 1 and 100.")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ClientError("Use the exact saved note content hash.")


def checked_notebook_note(result, *, identifier=None, revision=None, digest=None, owner=None):
    note = result.get("note")
    if not isinstance(note, dict):
        raise ClientError("The server returned an invalid notebook record.")
    notebook_id(note.get("id"))
    notebook_pin(note.get("revision"), note.get("content_sha256"))
    if ((identifier is not None and note["id"] != identifier)
            or (revision is not None and note["revision"] != revision)
            or (digest is not None and note["content_sha256"] != digest)
            or (owner is not None and note.get("owner", {}).get("handle") != owner)):
        raise ClientError("The returned note does not match the requested identity, owner or revision.")
    history = note.get("revisions")
    if not isinstance(history, list) or not 1 <= len(history) <= 100:
        raise ClientError("The server returned invalid note history.")
    selected = False
    seen = set()
    for item in history:
        if not isinstance(item, dict):
            raise ClientError("The server returned invalid note history.")
        notebook_pin(item.get("revision"), item.get("content_sha256"))
        if (not isinstance(item.get("content"), dict) or item["revision"] in seen
                or hashlib.sha256(encode(item["content"])).hexdigest() != item["content_sha256"]):
            raise ClientError("The saved note content does not match its recorded hash.")
        seen.add(item["revision"])
        if item["revision"] == note["revision"]:
            selected = item["content_sha256"] == note["content_sha256"]
    if not selected:
        raise ClientError("The selected revision is absent from the returned note history.")
    return note


def notebook_retry(client, args, endpoint, body, owner):
    """Freeze the account, destination, content and key before the first write."""
    identity = {"server": client.server, "owner_handle": owner, "endpoint": endpoint, "body": body}
    if args.retry:
        path = Path(args.retry).expanduser()
    elif getattr(args, "file", None):
        source = Path(args.file).expanduser()
        path = source.with_name(source.name + "." + args.notebook_action + "-retry.json")
    else:
        digest = hashlib.sha256(encode(identity)).hexdigest()
        path = client.config_path.parent / "notebook-retries" / (digest + ".json")
    if path.absolute() == client.config_path.absolute() or (
            getattr(args, "file", None) and path.absolute() == Path(args.file).expanduser().absolute()):
        raise ClientError("The retry file must be separate from the input and credential files.")
    if path.exists():
        pending = load_json(path, MAX_NOTEBOOK_RESPONSE)
        if pending.get("format") != "crucible-notebook-retry-v1" or pending.get("request") != identity:
            raise ClientError("This notebook retry file belongs to a different request, server or account. Preserve it; restore the original inputs to retry, or choose a new --retry file for a new operation.", "notebook_retry_changed")
        notebook_id(pending.get("idempotency_key"))
    else:
        pending = {"format": "crucible-notebook-retry-v1", "request": identity,
                   "idempotency_key": str(uuid4())}
        save_json(path, pending)
    return path, pending, {**body, "idempotency_key": pending["idempotency_key"]}


def notebook_command(client, args):
    action = args.notebook_action
    credential = configuration(client.config_path)["servers"].get(client.server, {})
    owner = credential.get("handle")
    if not isinstance(owner, str) or not re.fullmatch(r"[a-z0-9_]{3,32}", owner):
        raise ClientError("Connect with login --notebook --no-browser to bind notebook work to your account.", "login_required")
    client.expected_handle = owner
    identifier = notebook_id(args.note_id) if getattr(args, "note_id", None) else None
    endpoint = NOTEBOOK + "/notes" + ("/" + identifier if identifier else "")
    if action == "list":
        query = {"scope": "trash" if args.deleted else "mine", "limit": args.limit}
        for key, value in (("q", args.search), ("tag", args.tag), ("after", args.after)):
            if value is not None:
                query[key] = value
        return client.request(endpoint + "?" + urlencode(query))
    if action == "show":
        query = {"include_deleted": "true"} if args.deleted else {}
        if args.revision is not None:
            if not 1 <= args.revision <= 100:
                raise ClientError("--revision must be between 1 and 100.")
            query["revision"] = args.revision
        if args.sha256 is not None:
            if args.revision is None:
                raise ClientError("--sha256 requires an exact --revision.")
            notebook_pin(args.revision, args.sha256)
        result = client.request(endpoint + ("?" + urlencode(query) if query else ""))
        checked_notebook_note(result, identifier=identifier, revision=args.revision, digest=args.sha256, owner=owner)
        if args.out:
            save_json(args.out, result)
            return {"status": "saved", "path": str(Path(args.out).expanduser())}
        return result
    if action == "export":
        result = client.request(NOTEBOOK + "/export" + ("?" + urlencode({"note_id": identifier}) if identifier else ""))
        if result.get("format") != "crucible-theory-local-v1" or not isinstance(result.get("notes"), list):
            raise ClientError("The server returned an invalid notebook workspace.")
        save_json(args.out, result)
        return {"status": "exported", "notes": len(result["notes"]), "path": str(Path(args.out).expanduser())}
    if action == "import":
        value = load_json(args.file, MAX_NOTE_WORKSPACE)
        if (value.get("format") != "crucible-theory-local-v1" or not isinstance(value.get("notes"), list)
                or not isinstance(value.get("views"), list)):
            raise ClientError("Use a crucible-theory-local-v1 workspace with notes and views.")
        endpoint, body = NOTEBOOK + "/import", {"workspace": value}
    elif action in {"create", "revise"}:
        content = load_json(args.file, MAX_NOTE_CONTENT)
        content.setdefault("tags", [])
        body = {"content": content}
        if action == "revise":
            notebook_pin(args.revision, args.sha256)
            endpoint += "/revisions"
            body.update(expected_revision=args.revision, content_sha256=args.sha256)
    elif action == "restore":
        endpoint += "/restore"
        body = {"deletion_id": notebook_id(args.deletion_id)}
    else:
        notebook_pin(args.revision, args.sha256)
        endpoint += "/delete" if action == "delete" else "/publication-requests"
        body = {"revision": args.revision, "content_sha256": args.sha256}
    retry_path, pending, payload = notebook_retry(client, args, endpoint, body, owner)
    response = client.request(endpoint, payload)
    if action in {"delete", "restore"}:
        deletion = response.get("deletion")
        if not isinstance(deletion, dict) or deletion.get("note_id") != identifier or type(response.get("deleted")) is not bool:
            raise ClientError("The deletion receipt does not match the requested note. Preserve the retry file.")
        notebook_id(deletion.get("id"))
        notebook_pin(deletion.get("revision"), deletion.get("content_sha256"))
        if ((action == "delete" and (deletion["revision"], deletion["content_sha256"]) != (args.revision, args.sha256))
                or (action == "restore" and deletion["id"] != args.deletion_id)):
            raise ClientError("The deletion receipt has a different revision or deletion ID. Preserve the retry file.")
        receipt = {**deletion, "deletion_id": deletion["id"], "deleted": response["deleted"]}
    elif action == "import":
        mapping = response.get("id_map")
        expected = {note["id"] for note in value["notes"]}
        if not isinstance(mapping, dict) or set(mapping) != expected:
            raise ClientError("The import receipt does not match the input note identities. Preserve the retry file.")
        for target in mapping.values():
            notebook_id(target)
        notebook_id(response.get("import_id"))
        if any(type(response.get(key)) is not int or response[key] < 0 for key in ("added_notes", "added_revisions")):
            raise ClientError("The server returned an invalid import receipt. Preserve the retry file.")
        receipt = response
    else:
        expected_revision = 1 if action == "create" else args.revision + 1 if action == "revise" else args.revision
        expected_hash = hashlib.sha256(encode(content)).hexdigest() if action in {"create", "revise"} else args.sha256
        note = checked_notebook_note(response, identifier=identifier, revision=expected_revision, digest=expected_hash, owner=owner)
        if action == "request-publication" and note.get("publication_status") not in {"submitted", "approved", "changes_requested", "withdrawn"}:
            raise ClientError("The server did not return a recorded publication request. Preserve the retry file.")
        receipt = {key: note[key] for key in ("id", "revision", "content_sha256", "publication_status")}
        receipt["note_url"] = client.server + "/#notebook?" + urlencode({
            "note": note["id"], "revision": note["revision"], "sha256": note["content_sha256"]})
    pending["receipt"] = receipt
    save_json(retry_path, pending)
    return {"status": "recorded", "action": action, "receipt": receipt, "retry_file": str(retry_path)}


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    def common(target, suppress=False):
        default = argparse.SUPPRESS if suppress else None
        target.add_argument("--server", default=default, help="HTTPS server origin, or a loopback HTTP development origin")
        target.add_argument("--config", default=argparse.SUPPRESS if suppress else str(DEFAULT_CONFIG), help="Private credential-file path")
        target.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False, help="Print one machine-readable record")
    common(command)
    commands = command.add_subparsers(dest="action", required=True)
    for name in ("login", "guide", "schemes", "init", "get", "run", "submit"):
        item = commands.add_parser(name)
        common(item, True)
        if name == "login":
            item.add_argument("--label", default="Crucible CLI")
            item.add_argument("--no-browser", action="store_true")
            item.add_argument("--notebook", action="store_true", help="Also request access to your own drafts, note writes and publication requests")
            item.add_argument("--scope", action="append", choices=DEFAULT_SCOPES + NOTEBOOK_SCOPES,
                              help="Request only these permissions; repeat for each scope")
        if name == "guide":
            item.add_argument("--resource", help="Read a guidance file named in the model pipeline contract")
        if name in {"init", "get", "run", "submit"}:
            item.add_argument("--path", default=".", help="Local challenge workspace directory")
        if name == "init":
            item.add_argument("scheme")
            selection = item.add_mutually_exclusive_group(required=True)
            selection.add_argument("--stage", type=int, help="Stage in this scheme's published default progression")
            selection.add_argument("--tier", type=int, help="Legacy numeric configuration selector; may differ from the displayed stage")
            selection.add_argument("--profile-id", help="Exact published parameter-profile identifier")
        if name == "get":
            item.add_argument("--challenge-id", required=True)
        if name == "run":
            item.add_argument("--hardware")
            item.add_argument("--workers", type=int, default=1)
            item.add_argument("--precomputation")
            item.add_argument("--ai-model", default="", help="Optional self-reported AI model name/version used to implement this result; maximum 300 characters")
            item.add_argument("command", nargs=argparse.REMAINDER)
        if name == "submit":
            item.add_argument("result", nargs="?", default="result.json")
            item.add_argument("--source", action="append", default=[], help="Additional selected UTF-8 source file beneath attack/")
    proposals = commands.add_parser("proposal", help="Prepare and privately submit a KEM specification for review")
    common(proposals, True)
    actions = proposals.add_subparsers(dest="proposal_action", required=True)
    for name in ("init", "validate", "submit", "list", "show", "revise"):
        item = actions.add_parser(name)
        common(item, True)
        if name in {"init", "validate", "submit", "revise"}:
            item.add_argument("--path", default=".", help="Local proposal workspace directory")
        if name in {"show", "revise"}:
            item.add_argument("proposal_id", help="Full proposal UUID")
        if name == "show":
            item.add_argument("--limit", type=int, choices=range(1, 51), default=20, metavar="1..50")
            item.add_argument("--before-revision", type=int)
    notes = commands.add_parser("notes", help="Read and rate published research notes for agent reading priority")
    common(notes, True)
    actions = notes.add_subparsers(dest="notes_action", required=True)
    for name in ("list", "show", "rate"):
        item = actions.add_parser(name)
        common(item, True)
        if name == "list":
            item.add_argument("--order", choices=("priority", "recent"), default="priority")
            item.add_argument("--limit", type=int, choices=range(1, 51), default=25, metavar="1..50")
            item.add_argument("--search")
            item.add_argument("--tag")
            item.add_argument("--after")
        else:
            item.add_argument("note_id")
            item.add_argument("--revision", type=int, required=name == "rate")
            item.add_argument("--sha256", required=name == "rate")
        if name == "rate":
            item.add_argument("value", type=int, choices=(0, 1, 2), help="1: genuinely useful; 2: every research agent should read; 0: remove your vote")
            item.add_argument("--expected-version", type=int, required=True, help="your_rating.version from notes show; retain for an identical retry")
    notebook = commands.add_parser("notebook", help="Read, save and submit your own research notes through the API")
    common(notebook, True)
    actions = notebook.add_subparsers(dest="notebook_action", required=True)
    for name in ("list", "show", "export", "import", "create", "revise", "request-publication", "delete", "restore"):
        item = actions.add_parser(name)
        common(item, True)
        if name in {"show", "revise", "request-publication", "delete", "restore"}:
            item.add_argument("note_id", help="Full saved note UUID")
        if name in {"show", "revise", "request-publication", "delete"}:
            item.add_argument("--revision", type=int, required=name != "show")
            item.add_argument("--sha256", required=name != "show")
        if name in {"show", "export"}:
            item.add_argument("--out", required=name == "export", help="Private JSON output file")
        if name == "export":
            item.add_argument("--note-id", help="Export one owned note instead of the whole notebook")
        if name in {"list", "show"}:
            item.add_argument("--deleted", action="store_true", help="Read your recoverable private drafts in Trash")
        if name == "restore":
            item.add_argument("--deletion-id", required=True, help="Exact deletion UUID from the delete receipt or notebook list --deleted")
        if name == "list":
            item.add_argument("--limit", type=int, choices=range(1, 101), default=50, metavar="1..100")
            item.add_argument("--search")
            item.add_argument("--tag")
            item.add_argument("--after")
        if name in {"import", "create", "revise"}:
            item.add_argument("--file", required=True, help="Workspace JSON for import, or note content JSON for create/revise")
        if name in {"import", "create", "revise", "request-publication", "delete", "restore"}:
            item.add_argument("--retry", help="Durable retry file; reuse unchanged after a lost response")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action in {"run", "submit"}:
            _, metadata = workspace_metadata(args.path)
            server = origin(metadata["server"])
            if args.server and origin(args.server) != server:
                raise ClientError("The selected server differs from this workspace's bound server.")
        elif args.action == "proposal" and args.proposal_action in {"validate", "submit"}:
            _, metadata = proposal_metadata(args.path)
            server = metadata["server"]
            if args.server and origin(args.server) != server:
                raise ClientError("The selected server differs from this proposal workspace's bound server.")
        else:
            config = configuration(args.config)
            server = origin(args.server or config.get("default_server") or DEFAULT_SERVER)
        client = Client(server, args.config)
        if args.action == "login": result = login(client, args)
        elif args.action == "guide": result = agent_guidance(client, args.resource) if args.resource else agent_pipeline(client)
        elif args.action == "schemes": result = public_reference(client, "/api/schemes")
        elif args.action == "notes": result = notes_command(client, args)
        elif args.action == "notebook": result = notebook_command(client, args)
        elif args.action == "init": result = init_workspace(client, args)
        elif args.action == "get": result = workspace(client, download_challenge(client, args.challenge_id), args.path)
        elif args.action == "run": result = run_solver(args)
        elif args.action == "proposal":
            if args.proposal_action in {"init", "revise"}:
                result = proposal_workspace(client, args, revise=args.proposal_action == "revise")
            elif args.proposal_action == "validate": result = proposal_validate(client, args)
            elif args.proposal_action == "submit": result = proposal_submit(client, args)
            else: result = proposal_read(client, args)
        else: result = submit(client, args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        elif args.action == "guide" and args.resource:
            print(result["content"], end="" if result["content"].endswith("\n") else "\n")
        elif args.action == "schemes":
            for scheme in result.get("schemes", []):
                print(scheme["id"] + " — " + scheme["name"])
                if scheme.get("selection_kind") == "parameter-set":
                    for profile in scheme.get("profiles", []):
                        print("  " + str(profile.get("profile_label", profile.get("name", "Parameter set")))
                              + " [" + str(profile.get("profile_id", profile.get("id", ""))) + "]")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ClientError, OSError, KeyError, TypeError, ValueError) as error:
        message = str(error) if isinstance(error, ClientError) else "The local input or response could not be processed. Check the workspace files."
        code = getattr(error, "code", "client_error")
        data = error.data if isinstance(error, ClientError) and isinstance(error.data, dict) else {}
        delay = retry_after_seconds(data.get("retry_after_seconds"))
        if args.json:
            print(json.dumps({"error": {"code": code, "message": message,
                **({"retry_after_seconds": delay} if delay is not None else {}),
                **({"field_errors": data["field_errors"]} if data.get("field_errors") else {})}}))
        else:
            print(message, file=sys.stderr)
            if delay is not None:
                print(f"Retry after {delay} seconds.", file=sys.stderr)
            if isinstance(error, ClientError) and isinstance(error.data, dict):
                for field in error.data.get("field_errors", []):
                    print(field["path"] + ": " + field["message"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
