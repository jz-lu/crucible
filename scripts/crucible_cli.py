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
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4
import webbrowser

DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_CONFIG = Path.home() / ".config" / "crucible" / "config.json"
MAX_RESPONSE = 2 * 1024 * 1024
MAX_CATALOGUE_RESPONSE = 8 * 1024 * 1024
METHOD_TODO = "CRUCIBLE_METHOD_TODO"


class ClientError(ValueError):
    def __init__(self, message, code="client_error", data=None):
        super().__init__(message)
        self.code, self.data = code, data


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
        request = Request(self.server + path, data=data, headers=headers)
        # The current catalogue embeds every tier's complete frozen manifest.
        response_limit = MAX_CATALOGUE_RESPONSE if parts.path == "/api/schemes" else MAX_RESPONSE
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
            # Never echo a request body, candidate, token, or arbitrary server error text.
            raise ClientError(messages.get(error.code, "The server could not complete the request."),
                              "http_" + str(error.code), {"retry_after": error.headers.get("Retry-After")}) from None
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
    start = client.request("/api/agent/connect/start", {"label": args.label}, auth=False)
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
            try:
                interval = max(interval, min(30, float(error.data.get("retry_after") or 2)))
            except ValueError:
                interval = max(interval, 5)
            continue
        status = result.get("status")
        if status == "pending":
            continue
        if status != "authorized":
            raise ClientError("Connection request was denied or expired. Run login to start again.", "authorization_failed")
        token = result.get("access_token")
        if not isinstance(token, str) or not re.fullmatch(r"cru_[A-Za-z0-9_-]{43}", token):
            raise ClientError("The server returned an invalid access token.")
        expiry = result.get("expires_at")
        if not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= time.time():
            raise ClientError("The server returned an expired access token.")
        config = configuration(client.config_path)
        config["servers"][client.server] = {"access_token": token, "expires_at": expiry}
        config["default_server"] = client.server
        save_json(client.config_path, config)
        return {"status": "connected", "server": client.server,
                "handle": result.get("user", {}).get("handle"), "expires_at": expiry}
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


def workspace(client, package, destination):
    root = Path(destination).expanduser().absolute()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ClientError("Choose a new or empty workspace directory; existing files are never overwritten.")
    challenge = package["challenge"]
    query = "?" + urlencode({"scheme_id": challenge["scheme_id"], "version": challenge["scheme_version"]})
    specification = client.request("/api/protocol/spec" + query)
    source = client.request("/api/protocol/source" + query, raw=True)
    expected = package.get("scheme_manifest", {}).get("implementation", {}).get("source_sha256")
    if (hashlib.sha256(source).hexdigest() != expected
            or specification.get("implementation", {}).get("sha256") != expected
            or specification.get("version") != challenge["scheme_version"]):
        raise ClientError("Reference source does not match the challenge's pinned implementation hash.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    save_json(root / "challenge.json", package)
    save_json(root / "protocol.json", specification)
    private_write(root / "reference" / "protocol.py", source)
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
        "candidate": "", "method": {"name": "Local attack", "pseudocode": ""},
        "compute": {"hardware": "", "workers": 1, "wall_seconds": None,
                    "precomputation": "Not reported", "peak_memory": "Not measured", "notes": ""},
        "_instructions": {"workflow": "Implement attack/solve.py and describe the method in method.md. Run with the client to generate this result, then submit explicitly.",
                          "candidate": "Print the complete shared secret in hex from your solver, or one JSON object with only candidate.",
                          "compute": "Only the local command's wall time is measured. Declare external work, workers and precomputation accurately.",
                          "deadline": "Complete submissions must reach the server strictly before challenge.ends_at; a retry never changes the answer."}})
    private_write(root / "README.md", "# Crucible attack workspace\n\n"
        "Edit attack/solve.py and method.md, then run your own command through the client.\n"
        "The solver reads challenge.json and prints one hexadecimal shared secret (or JSON with candidate).\n"
        "Protocol source under reference/ is for inspection; it is never automatically executed.\n"
        "Run: python /path/to/crucible_cli.py run -- python attack/solve.py\n"
        "Submit explicitly: python /path/to/crucible_cli.py submit result.json\n"
        "Use --source attack/helper.py to include additional selected UTF-8 source files.\n"
        "The defaults are attack/solve.py and method.md. Credentials, results and local state are excluded.\n"
        "Results, source and compute declarations are uploaded to the configured Crucible server.\n"
        "A verified key does not verify your reported compute or method. Do not put credentials in source.\n"
        "Once submission starts, keep .crucible/submission.json: retries reuse its frozen payload.\n")
    private_write(root / ".gitignore", ".crucible/\n.crucible.json\nchallenge.json\nresult.json\nreceipt.json\n__pycache__/\n.env\n")
    return {"status": "ready", "path": str(root), "challenge_id": challenge["id"],
            "scheme_id": challenge["scheme_id"], "tier": package.get("scheme_manifest", {}).get("instance_size"),
            "ends_at": challenge["ends_at"], "challenge_status": challenge["status"]}


def init_workspace(client, args):
    schemes = client.request("/api/schemes", auth=False).get("schemes", [])
    scheme = next((item for item in schemes if item.get("id") == args.scheme), None)
    if scheme is None:
        raise ClientError("Unknown scheme; use schemes to list available identifiers.")
    profile = next((item for item in scheme.get("profiles", []) if item.get("instance_size") == args.tier), None)
    if profile is None:
        raise ClientError("That tier is not available for this scheme.")
    # Validate destination before issuing a challenge as a side effect.
    destination = Path(args.path).expanduser()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ClientError("Choose a new or empty workspace directory.")
    query = urlencode({"scheme_id": scheme["id"], "scheme_version": scheme["version"], "profile_id": profile["id"]})
    current = client.request("/api/challenges?" + query).get("challenge")
    if current is None:
        current = client.request("/api/challenges", {"scheme_id": scheme["id"], "scheme_version": scheme["version"],
                                                     "instance_size": args.tier})["challenge"]
    return workspace(client, download_challenge(client, current["id"]), args.path)


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
    if (root / ".crucible" / "submission.json").exists():
        raise ClientError("A submission is already frozen for this workspace. Retry it without changing the answer.")
    package = load_json(root / "challenge.json")
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
    result = validate_result(result, package)
    save_json(root / "result.json", result)
    return {"status": "result_written", "path": str(root / "result.json"), "wall_seconds": elapsed,
            "challenge_id": metadata["challenge_id"], "submitted": False}


def validate_result(result, package):
    if result.get("format") != "crucible-recovery-v1" or set(result) - {"format", "challenge_id", "candidate", "method", "compute", "_instructions"}:
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
    return {"format": "crucible-recovery-v1", "challenge_id": result["challenge_id"],
            "candidate": candidate.lower(), "method": method, "compute": {**cleaned, "workers": workers, "wall_seconds": wall}}


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
    package = load_json(root / "challenge.json")
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
                              "method_id": pending["method_id"], "idempotency_key": pending["idempotency_key"]}
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


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    def common(target, suppress=False):
        default = argparse.SUPPRESS if suppress else None
        target.add_argument("--server", default=default, help="HTTPS server origin, or a loopback HTTP development origin")
        target.add_argument("--config", default=argparse.SUPPRESS if suppress else str(DEFAULT_CONFIG), help="Private credential-file path")
        target.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False, help="Print one machine-readable record")
    common(command)
    commands = command.add_subparsers(dest="action", required=True)
    for name in ("login", "schemes", "init", "get", "run", "submit"):
        item = commands.add_parser(name)
        common(item, True)
        if name == "login":
            item.add_argument("--label", default="Crucible CLI")
            item.add_argument("--no-browser", action="store_true")
        if name in {"init", "get", "run", "submit"}:
            item.add_argument("--path", default=".", help="Local challenge workspace directory")
        if name == "init":
            item.add_argument("scheme")
            item.add_argument("--tier", required=True, type=int)
        if name == "get":
            item.add_argument("--challenge-id", required=True)
        if name == "run":
            item.add_argument("--hardware")
            item.add_argument("--workers", type=int, default=1)
            item.add_argument("--precomputation")
            item.add_argument("command", nargs=argparse.REMAINDER)
        if name == "submit":
            item.add_argument("result", nargs="?", default="result.json")
            item.add_argument("--source", action="append", default=[], help="Additional selected UTF-8 source file beneath attack/")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action in {"run", "submit"}:
            _, metadata = workspace_metadata(args.path)
            server = origin(metadata["server"])
            if args.server and origin(args.server) != server:
                raise ClientError("The selected server differs from this workspace's bound server.")
        else:
            config = configuration(args.config)
            server = origin(args.server or config.get("default_server") or DEFAULT_SERVER)
        client = Client(server, args.config)
        if args.action == "login": result = login(client, args)
        elif args.action == "schemes": result = client.request("/api/schemes", auth=False)
        elif args.action == "init": result = init_workspace(client, args)
        elif args.action == "get": result = workspace(client, download_challenge(client, args.challenge_id), args.path)
        elif args.action == "run": result = run_solver(args)
        else: result = submit(client, args)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        elif args.action == "schemes":
            for scheme in result.get("schemes", []):
                print(scheme["id"] + " — " + scheme["name"])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ClientError, OSError, KeyError, TypeError, ValueError) as error:
        message = str(error) if isinstance(error, ClientError) else "The local input or response could not be processed. Check the workspace files."
        code = getattr(error, "code", "client_error")
        if args.json:
            print(json.dumps({"error": {"code": code, "message": message}}))
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
