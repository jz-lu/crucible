"""Opt-in disposable local Linux PostgreSQL/restic test, synthetic data only.

Creates its own nonroot PG cluster, TLS certificate and local encrypted repository;
never connects to an existing server or object store. No application imports.
"""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("CRUCIBLE_APP_BACKUP_FIXTURE") != "1",
                               reason="Explicit disposable nonroot Linux backup fixture only")


def command(args, *, env=None, content=None):
    return subprocess.run(args, env=env, input=content, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90).stdout


@pytest.fixture
def fixture(tmp_path):
    assert os.geteuid() != 0, "Run this isolated backup job fixture as a nonroot Linux user"
    assert shutil.which("restic") and shutil.which("openssl")
    pg_bin = next(Path("/usr/lib/postgresql").glob("*/bin/initdb")).parent
    spec = importlib.util.spec_from_file_location("app_backup_fixture", ROOT / "deploy/backup-app-platform.py")
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    cluster, cert, key = tmp_path / "cluster", tmp_path / "server.crt", tmp_path / "server.key"
    admin_password = tmp_path / "admin-password"
    admin_password.write_text("synthetic-admin-password")
    admin_password.chmod(0o600)
    command([str(pg_bin / "initdb"), "-D", str(cluster), "--username=fixture", "--pwfile", str(admin_password),
             "--auth-local=trust", "--auth-host=scram-sha-256", "--no-locale", "--encoding=UTF8"])
    command(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost", "-keyout", str(key), "-out", str(cert)])
    key.chmod(0o600)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (cluster / "postgresql.conf").open("a") as stream:
        stream.write(f"\nlisten_addresses='127.0.0.1'\nport={port}\nunix_socket_directories='{tmp_path}'\n"
                     f"ssl=on\nssl_cert_file='{cert}'\nssl_key_file='{key}'\nshared_buffers='16MB'\nmax_connections=10\n")
    command([str(pg_bin / "pg_ctl"), "-D", str(cluster), "-l", str(tmp_path / "postgres.log"), "-w", "start"])
    try:
        admin = {"PATH": "/usr/bin:/bin", "PGHOST": "localhost", "PGPORT": str(port), "PGUSER": "fixture",
                 "PGPASSWORD": "synthetic-admin-password", "PGSSLMODE": "verify-full", "PGSSLROOTCERT": str(cert)}
        command(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "-d", "postgres"], env=admin, content=(
            "CREATE ROLE backup_reader LOGIN PASSWORD 'synthetic-backup-password';\n"
            "CREATE DATABASE fixture_source;\nCREATE DATABASE fixture_restore;\n").encode())
        command(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", "-d", "fixture_source"], env=admin, content=(
            "CREATE TABLE fixture_notes(id int PRIMARY KEY, body text, evidence jsonb);\n"
            "INSERT INTO fixture_notes VALUES(1, 'synthetic private note λ', '{\"large\":123456789012345678901234567890}');\n"
            "GRANT CONNECT ON DATABASE fixture_source TO backup_reader;\n"
            "GRANT USAGE ON SCHEMA public TO backup_reader;\nGRANT SELECT ON ALL TABLES IN SCHEMA public TO backup_reader;\n").encode())
        runtime = {"CRUCIBLE_ENV": "production", "CRUCIBLE_ACCESS_MODE": "beta", "CRUCIBLE_ORIGIN": "https://fixture.example.invalid",
                   "CRUCIBLE_SECRET": base64.b64encode(b"original-synthetic-server-secret!").decode(),
                   "DATABASE_URL": f"postgresql+psycopg://fixture:synthetic-admin-password@localhost:{port}/fixture_source?sslmode=verify-full",
                   "CRUCIBLE_DATABASE_CA_PEM": cert.read_text(), "CRUCIBLE_GITHUB_CLIENT_SECRET": 'synthetic \\" $(not-evaluated)'}
        declaration = {"format": job.FORMAT, "release_id": "synthetic-fixture-v1", "runtime_env": runtime,
                       "runtime_env_sha256": hashlib.sha256(job.runtime_bytes(runtime)).hexdigest()}
        release = tmp_path / "image-release.json"
        release.write_text(json.dumps({"format": "crucible-host-release-v1", "release_id": declaration["release_id"]}))
        release.chmod(0o644)
        restic_key = tmp_path / "fixture-restic-key"
        restic_key.write_text("synthetic-local-repository-password"); restic_key.chmod(0o600)
        repository = str(tmp_path / "encrypted-fixture-repo")
        restore_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RESTIC_REPOSITORY": repository,
                       "RESTIC_PASSWORD_FILE": str(restic_key), "RESTIC_CACHE_DIR": str(tmp_path / "restore-cache")}
        # Explicitly isolated fixture repository only; production adapter never initializes one.
        command(["restic", "init"], env=restore_env)
        inputs = {"CRUCIBLE_BACKUP_CONFIGURATION_JSON": json.dumps(declaration),
                  "CRUCIBLE_BACKUP_DATABASE_URL": f"postgresql+psycopg://backup_reader:synthetic-backup-password@localhost:{port}/fixture_source?sslmode=verify-full",
                  "CRUCIBLE_BACKUP_RELEASE_MANIFEST": str(release), "CRUCIBLE_BACKUP_CA_PEM": cert.read_text(),
                  "CRUCIBLE_BACKUP_RESTIC_PASSWORD": restic_key.read_text(), "AWS_ACCESS_KEY_ID": "unused-fixture",
                  "AWS_SECRET_ACCESS_KEY": "unused-fixture", "RESTIC_REPOSITORY": repository}
        connect_admin = {"host": "localhost", "port": port, "dbname": "fixture_source", "user": "fixture",
                         "password": admin["PGPASSWORD"], "sslmode": "verify-full", "sslrootcert": str(cert), "connect_timeout": 5}
        engine = create_engine(URL.create("postgresql+psycopg"), connect_args=connect_admin,
                               poolclass=NullPool, isolation_level="AUTOCOMMIT")
        try:
            yield job, inputs, restore_env, admin, runtime, engine, tmp_path
        finally:
            engine.dispose()
    finally:
        command([str(pg_bin / "pg_ctl"), "-D", str(cluster), "-m", "immediate", "-w", "stop"])


def test_nonroot_paired_backup_restores_exact_data_and_configuration(fixture):
    job, inputs, restore_env, admin, runtime, _, root = fixture
    output = job.run_job(inputs, require_remote=False)
    assert output["status"] == "complete" and output["restore_tested"] is False
    receipt = json.loads(command(["restic", "dump", output["completion_snapshot"], "completion.json"], env=restore_env))
    assert receipt["format"] == "crucible-recovery-set-v1"
    runtime_path = next(path for path in receipt["configuration_sha256"] if path.endswith("/runtime.env"))
    raw = command(["restic", "dump", receipt["configuration_snapshot"], runtime_path], env=restore_env)
    assert hashlib.sha256(raw).hexdigest() == receipt["configuration_sha256"][runtime_path]
    assert dict((name, json.loads(value)) for name, value in (line.split("=", 1) for line in raw.decode().splitlines())) == runtime
    assert not Path(runtime_path).parent.exists(), "Runtime-only secrets must be removed after completion"
    dump = command(["restic", "dump", receipt["database_snapshot"], "database.dump"], env=restore_env)
    command(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", "fixture_restore"], env=admin, content=dump)
    sql = "COPY (SELECT * FROM fixture_notes ORDER BY id) TO STDOUT;\n".encode()
    before = command(["psql", "-X", "-q", "-d", "fixture_source"], env=admin, content=sql)
    after = command(["psql", "-X", "-q", "-d", "fixture_restore"], env=admin, content=sql)
    assert before == after and "λ".encode() in after and b"123456789012345678901234567890" in after
    for path in (root / "encrypted-fixture-repo").rglob("*"):
        if path.is_file():
            assert runtime["CRUCIBLE_SECRET"].encode() not in path.read_bytes()


def test_independent_connections_contend_and_autocommit_is_readonly(fixture):
    job, inputs, _, _, _, admin_engine, root = fixture
    private = root / "private"; private.mkdir(mode=0o700)
    _, connect = job.prepare(inputs, private)
    with job.locked(connect) as owner:
        assert owner.connection.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
        with admin_engine.connect() as admin:
            state = admin.execute(text("SELECT state, xact_start FROM pg_stat_activity WHERE pid=:pid"), {"pid": owner.pid}).one()
            assert state == ("idle", None), "Session lock must not require a long idle transaction"
        with pytest.raises(job.JobError, match="already running"):
            with job.locked(connect):
                pytest.fail("Second physical connection acquired an already-held lock")
        owner.check()
    with job.locked(connect) as next_owner:
        next_owner.check()


def test_server_connection_loss_after_dump_prevents_completion(fixture, monkeypatch):
    job, inputs, restore_env, _, _, admin_engine, _ = fixture
    core = job.core_module()
    original_snapshot, original_locked = core.restic_snapshot, job.locked
    owners = []
    @contextmanager
    def locked(connect):
        with original_locked(connect) as owner:
            owners.append(owner)
            yield owner
    def snapshot(arguments, env, **kwargs):
        result = original_snapshot(arguments, env, **kwargs)
        if "database" in arguments:
            with admin_engine.connect() as admin:
                assert admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": owners[0].pid}).scalar_one()
        return result
    monkeypatch.setattr(job, "core_module", lambda: core)
    monkeypatch.setattr(job, "locked", locked)
    monkeypatch.setattr(core, "restic_snapshot", snapshot)
    with pytest.raises(Exception):
        job.run_job(inputs, require_remote=False)
    snapshots = json.loads(command(["restic", "snapshots", "--json"], env=restore_env))
    assert len(snapshots) == 2 and all("complete" not in item.get("tags", []) for item in snapshots)
    assert owners[0].connection.closed, "A disconnected lock must never be replaced silently"
