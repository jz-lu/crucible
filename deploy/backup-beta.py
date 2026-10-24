"""Encrypted, paired beta backups. No repository init, restore, prune or admission.

The CLI requires root and an explicitly configured HTTPS S3 restic repository.
Only snapshot IDs/status are printed; subprocess diagnostics remain private.
"""
from __future__ import annotations

import configparser
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
from uuid import uuid4


class BackupError(RuntimeError):
    pass


def checked_file(value, *, private=True, limit=1024 * 1024):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise BackupError("Expected an absolute regular file.")
    info = path.stat()
    forbidden = 0o077 if private else 0o022
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_mode & forbidden or not 0 < info.st_size <= limit):
        raise BackupError("Backup input ownership, permissions or size are invalid.")
    return path


def configuration(environ, *, require_remote=True):
    required = ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE", "PGSERVICEFILE",
                "PGPASSFILE", "PGSERVICE", "CRUCIBLE_BACKUP_RUNTIME_ENV",
                "CRUCIBLE_BACKUP_RELEASE_MANIFEST", "CRUCIBLE_BACKUP_LOCK")
    if any(not environ.get(name) for name in required):
        raise BackupError("Required backup configuration is missing.")
    if require_remote and not environ["RESTIC_REPOSITORY"].startswith("s3:https://"):
        raise BackupError("Production backups require an explicit HTTPS S3 repository.")
    files = {name: checked_file(environ[name]) for name in
             ("RESTIC_PASSWORD_FILE", "PGSERVICEFILE", "PGPASSFILE", "CRUCIBLE_BACKUP_RUNTIME_ENV")}
    files["CRUCIBLE_BACKUP_RELEASE_MANIFEST"] = checked_file(
        environ["CRUCIBLE_BACKUP_RELEASE_MANIFEST"], private=False)
    manifest = json.loads(files["CRUCIBLE_BACKUP_RELEASE_MANIFEST"].read_text())
    if (manifest.get("format") != "crucible-host-release-v1" or
            not isinstance(manifest.get("release_id"), str) or
            not manifest["release_id"] or "REPLACE_" in json.dumps(manifest)):
        raise BackupError("An actual immutable release manifest is required.")
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(files["PGSERVICEFILE"])
    service = environ["PGSERVICE"]
    if service not in parser:
        raise BackupError("The configured PostgreSQL service is absent.")
    pg = parser[service]
    if (pg.get("sslmode") != "verify-full" or not pg.get("host") or
            pg["host"].startswith("/") or not pg.get("dbname") or not pg.get("user") or
            any(key in pg for key in ("password", "service", "passfile", "options"))):
        raise BackupError("The backup database service requires verified TLS and a separate password file.")
    if not pg.get("sslrootcert"):
        raise BackupError("The provider CA must be explicit.")
    checked_file(pg["sslrootcert"], private=False)
    if "AWS_SHARED_CREDENTIALS_FILE" in environ:
        checked_file(environ["AWS_SHARED_CREDENTIALS_FILE"])
    lock = Path(environ["CRUCIBLE_BACKUP_LOCK"])
    if not lock.is_absolute() or lock.is_symlink():
        raise BackupError("A private absolute lock path is required.")
    parent = lock.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
        raise BackupError("The lock directory must be owned by this account with mode 0700.")
    # Do not forward unrelated application credentials or process environment.
    names = (*required, "RESTIC_CACHE_DIR", "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE",
             "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    env = {name: environ[name] for name in names if environ.get(name)}
    env.update(PATH="/usr/bin:/bin", HOME="/nonexistent", LC_ALL="C.UTF-8",
               PGCONNECT_TIMEOUT="5", PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=900000 -c lock_timeout=5000")
    return files, manifest, lock, env


def restic_snapshot(arguments, env, *, content=None):
    process = subprocess.Popen(["/usr/bin/restic", "backup", "--json", *arguments],
        env=env, stdin=subprocess.PIPE if content is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, _ = process.communicate(content, timeout=600)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise BackupError("Encrypted backup timed out.") from None
    if process.returncode:
        raise BackupError("Encrypted backup failed; no complete recovery set was recorded.")
    summaries = [row for line in output.splitlines() if (row := json.loads(line)).get("message_type") == "summary"]
    if len(summaries) != 1 or not re.fullmatch("[0-9a-f]{8,64}", summaries[0].get("snapshot_id", "")):
        raise BackupError("Backup did not return an unambiguous snapshot receipt.")
    return summaries[0]["snapshot_id"]


def backup(environ, *, require_remote=True, before_completion=None):
    files, manifest, lock, env = configuration(environ, require_remote=require_remote)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Another backup is already running.") from None
        run_id = "crucible-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex
        shared = ["--tag", "crucible-beta", "--tag", run_id, "--host", "crucible-beta"]
        protected = [files["CRUCIBLE_BACKUP_RUNTIME_ENV"], files["CRUCIBLE_BACKUP_RELEASE_MANIFEST"]]
        checksums = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        configuration_id = restic_snapshot([*shared, "--tag", "configuration", "--", *map(str, protected)], env)
        database_id = restic_snapshot([*shared, "--tag", "database", "--stdin-filename", "database.dump",
            "--stdin-from-command", "--", "/usr/bin/pg_dump", "--format=custom", "--no-owner",
            "--no-privileges", "--no-password"], env)
        if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest for path, digest in checksums.items()):
            raise BackupError("Configuration changed during backup; this set is incomplete.")
        if before_completion is not None:
            # Optional hosting-job ownership check. The dedicated Droplet
            # command keeps its existing local flock and default behavior.
            before_completion()
        receipt = {"format": "crucible-recovery-set-v1", "run_id": run_id,
            "release_id": manifest["release_id"], "configuration_snapshot": configuration_id,
            "database_snapshot": database_id, "configuration_sha256": checksums,
            "restore_tested": False}
        receipt_id = restic_snapshot([*shared, "--tag", "complete", "--stdin", "--stdin-filename", "completion.json"],
                                    env, content=(json.dumps(receipt, sort_keys=True) + "\n").encode())
        return {"status": "complete", "run_id": run_id, "completion_snapshot": receipt_id, "restore_tested": False}


def main():
    os.umask(0o077)
    try:
        if os.geteuid() != 0:
            raise BackupError("Run through the dedicated root backup service.")
        result = backup(os.environ)
    except Exception:
        # Driver/network errors can contain connection details. Do not echo them.
        print(json.dumps({"status": "failed", "message": "Backup incomplete. Check private configuration, file permissions, tool versions, repository and database connectivity. No pruning or recovery was attempted."}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
