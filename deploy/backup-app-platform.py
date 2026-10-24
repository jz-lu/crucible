"""Nonroot, separately scheduled App Platform backup job; never import the app.

Requires an operator-prepared configuration declaration matching the deployed
release and the exact web service secret/configuration version. Validation here
cannot prove that the declaration matches the running website; a remote restore
exercise remains mandatory. No repository initialization, pruning or restore.

Runtime-only secrets: CRUCIBLE_BACKUP_CONFIGURATION_JSON (format, release_id,
runtime_env mapping, runtime_env_sha256), CRUCIBLE_BACKUP_DATABASE_URL (dedicated
read-only backup account), CRUCIBLE_BACKUP_CA_PEM, CRUCIBLE_BACKUP_RESTIC_PASSWORD,
AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY. Optional AWS_SESSION_TOKEN and
AWS_DEFAULT_REGION. RESTIC_REPOSITORY is explicit HTTPS S3; RELEASE.json comes
from CRUCIBLE_BACKUP_RELEASE_MANIFEST in the immutable image. Set
CRUCIBLE_BACKUP_JOB=app-platform explicitly. No implicit production defaults.

The preserved runtime.env uses NAME=<JSON string> lines. Restore by splitting
on the first '=' and JSON-decoding each value into an environment mapping;
never source/eval the file. The surrounding recovery-set-v1 receipt is unchanged.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url, URL
from sqlalchemy.pool import NullPool

FORMAT = "crucible-app-platform-backup-config-v1"
LOCK_KEY = int.from_bytes(hashlib.sha256(b"crucible-beta-backup-v1").digest()[:8], "big", signed=True)
LOCK_HIGH, LOCK_LOW = (LOCK_KEY >> 32) & 0xffffffff, LOCK_KEY & 0xffffffff
MAX_CONFIG_BYTES = 1024 * 1024


class JobError(RuntimeError):
    pass


def core_module():
    spec = importlib.util.spec_from_file_location("crucible_paired_backup", Path(__file__).with_name("backup-beta.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(value, limit, *, multiline=False, allow_empty=False):
    if (type(value) is not str or (not value and not allow_empty) or len(value.encode("utf-8")) > limit
            or "\x00" in value or (not multiline and any(ord(c) < 32 for c in value))):
        raise JobError("Invalid explicit backup configuration.")
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JobError("Duplicate backup configuration keys.")
        result[key] = value
    return result


def _json(value):
    def reject(_):
        raise JobError("Invalid backup configuration value.")
    try:
        return json.loads(_text(value, MAX_CONFIG_BYTES, multiline=True),
                          object_pairs_hook=_object, parse_constant=reject)
    except (ValueError, RecursionError):
        raise JobError("Invalid backup configuration JSON.") from None


def runtime_bytes(values):
    if type(values) is not dict or not 5 <= len(values) <= 100:
        raise JobError("An explicit bounded runtime configuration is required.")
    for name, value in values.items():
        if type(name) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", name):
            raise JobError("Invalid runtime variable name.")
        _text(value, 65536, multiline=True, allow_empty=True)
    # JSON-quoted strings; this is a recovery format, never a shell script.
    raw = "".join(name + "=" + json.dumps(value, ensure_ascii=False) + "\n"
                  for name, value in sorted(values.items())).encode()
    if len(raw) > MAX_CONFIG_BYTES:
        raise JobError("Runtime configuration exceeds its bound.")
    return raw


def database(value, *, runtime=False):
    try:
        url = make_url(_text(value, 16384))
        if (url.drivername not in {"postgresql", "postgresql+psycopg"}
                or not all((url.host, url.username, url.password, url.database))
                or url.host.startswith("/") or "," in url.host
                or set(url.query) - ({"sslmode", "sslrootcert"} if runtime else {"sslmode"})
                or url.query.get("sslmode") != "verify-full"):
            raise JobError("An explicit PostgreSQL backup connection with verified TLS is required.")
        for item in (url.host, url.username, url.database, url.password):
            _text(item, 8192)
        if any(item != item.strip() for item in (url.host, url.username, url.database)):
            raise JobError("Database identifiers cannot have surrounding whitespace.")
        port = url.port if url.port is not None else 5432
        if not 1 <= port <= 65535:
            raise JobError("Invalid database port.")
        if runtime and "sslrootcert" in url.query:
            cert = _text(url.query["sslrootcert"], 4096)
            if not Path(cert).is_absolute():
                raise JobError("The runtime CA path must be explicit.")
        return url, port
    except (ValueError, TypeError):
        raise JobError("Invalid backup database configuration.") from None


def _identity(url, port):
    return url.host.lower(), port, url.database


def _write(directory, name, content):
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content if isinstance(content, bytes) else content.encode())
    return path


def _pgpass(value):
    return value.replace("\\", "\\\\").replace(":", "\\:")


def prepare(environ, directory):
    """Materialize only declared configuration; no network or app imports."""
    declaration = _json(environ.get("CRUCIBLE_BACKUP_CONFIGURATION_JSON"))
    if type(declaration) is not dict or set(declaration) != {"format", "release_id", "runtime_env", "runtime_env_sha256"} or declaration["format"] != FORMAT:
        raise JobError("An explicit backup configuration declaration is required.")
    raw = runtime_bytes(declaration["runtime_env"])
    if hashlib.sha256(raw).hexdigest() != declaration["runtime_env_sha256"]:
        raise JobError("Runtime configuration differs from its declared digest.")
    values = declaration["runtime_env"]
    if values.get("CRUCIBLE_ENV") != "production" or values.get("CRUCIBLE_ACCESS_MODE") != "beta":
        raise JobError("The preserved configuration must identify a production beta.")
    parsed = urlsplit(values.get("CRUCIBLE_ORIGIN", ""))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise JobError("The preserved configuration requires an HTTPS origin.")
    try:
        secret = base64.b64decode(values.get("CRUCIBLE_SECRET", ""), validate=True)
    except ValueError:
        raise JobError("The original server secret is required.") from None
    if len(secret) < 32:
        raise JobError("The original server secret is required.")
    runtime_url, runtime_port = database(values.get("DATABASE_URL"), runtime=True)
    backup_url, port = database(environ.get("CRUCIBLE_BACKUP_DATABASE_URL"))
    if _identity(runtime_url, runtime_port) != _identity(backup_url, port):
        raise JobError("The preserved runtime and backup database targets disagree.")
    release = Path(_text(environ.get("CRUCIBLE_BACKUP_RELEASE_MANIFEST"), 4096))
    if not release.is_absolute() or release.is_symlink():
        raise JobError("The immutable release manifest must be an absolute regular file.")
    fd = os.open(release, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                or info.st_mode & 0o022 or not 0 < info.st_size <= MAX_CONFIG_BYTES):
            raise JobError("Invalid immutable release manifest ownership, mode or size.")
        release_raw = stream.read(MAX_CONFIG_BYTES + 1)
    metadata = _json(release_raw.decode("utf-8"))
    if type(metadata) is not dict or metadata.get("format") != "crucible-host-release-v1" or metadata.get("release_id") != declaration["release_id"]:
        raise JobError("The runtime declaration and deployed release disagree.")
    ca = _write(directory, "postgres-ca.pem", _text(environ.get("CRUCIBLE_BACKUP_CA_PEM"), 128 * 1024, multiline=True))
    service = {"host": backup_url.host, "port": str(port), "dbname": backup_url.database,
               "user": backup_url.username, "sslmode": "verify-full", "sslrootcert": str(ca)}
    # Newlines were rejected above; no interpolation or nested service options.
    service_file = _write(directory, "pg-service.conf", "[crucible-backup]\n" + "".join(k+"="+v+"\n" for k,v in service.items()))
    pgpass = _write(directory, "pgpass", ":".join(_pgpass(value) for value in
        (backup_url.host, str(port), backup_url.database, backup_url.username, backup_url.password)) + "\n")
    key = _write(directory, "restic-password", _text(environ.get("CRUCIBLE_BACKUP_RESTIC_PASSWORD"), 65536))
    aws = {"aws_access_key_id": _text(environ.get("AWS_ACCESS_KEY_ID"), 4096),
           "aws_secret_access_key": _text(environ.get("AWS_SECRET_ACCESS_KEY"), 4096)}
    if environ.get("AWS_SESSION_TOKEN"):
        aws["aws_session_token"] = _text(environ["AWS_SESSION_TOKEN"], 16384)
    credentials = _write(directory, "aws-credentials", "[crucible-backup]\n" + "".join(k+"="+v+"\n" for k,v in aws.items()))
    runtime = _write(directory, "runtime.env", raw)
    release_copy = _write(directory, "RELEASE.json", release_raw)
    env = {"RESTIC_REPOSITORY": _text(environ.get("RESTIC_REPOSITORY"), 8192),
           "RESTIC_PASSWORD_FILE": str(key), "AWS_SHARED_CREDENTIALS_FILE": str(credentials),
           "AWS_PROFILE": "crucible-backup", "PGSERVICEFILE": str(service_file),
           "PGPASSFILE": str(pgpass), "PGSERVICE": "crucible-backup",
           "CRUCIBLE_BACKUP_RUNTIME_ENV": str(runtime), "CRUCIBLE_BACKUP_RELEASE_MANIFEST": str(release_copy),
           "CRUCIBLE_BACKUP_LOCK": str(directory / "backup.lock"), "RESTIC_CACHE_DIR": str(directory / "cache")}
    if environ.get("AWS_DEFAULT_REGION"):
        env["AWS_DEFAULT_REGION"] = _text(environ["AWS_DEFAULT_REGION"], 200)
    connect = {**service, "password": backup_url.password, "port": port, "connect_timeout": 5,
               "options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000"}
    return env, connect


class Ownership:
    def __init__(self, connection, driver, pid):
        self.connection, self.driver, self.pid = connection, driver, pid

    def check(self):
        connection = self.connection
        if connection.closed or connection.invalidated or connection.connection.driver_connection is not self.driver:
            raise JobError("The backup lock connection was lost.")
        row = connection.execute(text("SELECT pg_backend_pid(), EXISTS (SELECT 1 FROM pg_locks "
            "WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted "
            "AND classid::bigint=:high AND objid::bigint=:low AND objsubid=1)"),
            {"high": LOCK_HIGH, "low": LOCK_LOW}).one()
        if row[0] != self.pid or row[1] is not True:
            raise JobError("The backup advisory lock is no longer held.")


@contextmanager
def locked(connect):
    engine = create_engine(URL.create("postgresql+psycopg"), connect_args=connect,
                           poolclass=NullPool, isolation_level="AUTOCOMMIT", hide_parameters=True)
    try:
        with engine.connect() as connection:
            row = connection.execute(text("SELECT pg_backend_pid(), pg_try_advisory_lock(:key)"), {"key": LOCK_KEY}).one()
            if row[1] is not True:
                raise JobError("Another backup is already running.")
            ownership = Ownership(connection, connection.connection.driver_connection, row[0])
            ownership.check()
            yield ownership
    finally:
        # Closing a NullPool connection releases the session lock; it cannot
        # leak into a reused pool connection or be reacquired after reconnect.
        engine.dispose()


def run_job(environ, *, require_remote=True):
    core = core_module()
    with tempfile.TemporaryDirectory(prefix="crucible-backup-") as name:
        directory = Path(name)
        directory.chmod(0o700)
        env, connect = prepare(environ, directory)
        core.configuration(env, require_remote=require_remote)
        with locked(connect) as ownership:
            return core.backup(env, require_remote=require_remote, before_completion=ownership.check)


def main():
    os.umask(0o077)
    try:
        if os.geteuid() == 0 or os.environ.get("CRUCIBLE_BACKUP_JOB") != "app-platform":
            raise JobError("Use the explicitly configured nonroot scheduled backup job.")
        result = run_job(os.environ)
    except Exception:
        # Never print provider credentials, URLs, configuration or driver errors.
        print(json.dumps({"status": "failed", "message": "Backup incomplete. Verify the private job configuration, release, lock ownership and encrypted repository. No pruning or recovery was attempted."}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
