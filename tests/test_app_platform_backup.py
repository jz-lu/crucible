"""Synthetic job tests; never load app configuration, real credentials or data."""
from contextlib import contextmanager
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "deploy" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def job():
    return load("backup-app-platform")


@pytest.fixture
def inputs(tmp_path, job):
    runtime = {"CRUCIBLE_ENV": "production", "CRUCIBLE_ACCESS_MODE": "beta",
               "CRUCIBLE_ORIGIN": "https://beta.example.invalid",
               "CRUCIBLE_SECRET": base64.b64encode(b"synthetic-original-secret-32bytes!").decode(),
               "DATABASE_URL": "postgresql+psycopg://web:synthetic@db.example.invalid:25060/crucible?sslmode=verify-full&sslrootcert=/run/ca.pem",
               "CRUCIBLE_GITHUB_CLIENT_SECRET": "synthetic $(do-not-evaluate) `nor-this` \\\" λ",
               "CRUCIBLE_DATABASE_CA_PEM": "-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----\n",
               "CRUCIBLE_MAIL_FROM": ""}
    declaration = {"format": job.FORMAT, "release_id": "fixture-v1", "runtime_env": runtime,
                   "runtime_env_sha256": hashlib.sha256(job.runtime_bytes(runtime)).hexdigest()}
    release = tmp_path / "image-release.json"
    release.write_text(json.dumps({"format": "crucible-host-release-v1", "release_id": "fixture-v1"}))
    release.chmod(0o644)
    return {"CRUCIBLE_BACKUP_CONFIGURATION_JSON": json.dumps(declaration),
            "CRUCIBLE_BACKUP_DATABASE_URL": "postgresql+psycopg://backup:p%3Aa%5Css@db.example.invalid:25060/crucible?sslmode=verify-full",
            "CRUCIBLE_BACKUP_RELEASE_MANIFEST": str(release),
            "CRUCIBLE_BACKUP_CA_PEM": "-----BEGIN CERTIFICATE-----\nsynthetic\n-----END CERTIFICATE-----\n",
            "CRUCIBLE_BACKUP_RESTIC_PASSWORD": "synthetic-restic-password",
            "AWS_ACCESS_KEY_ID": "synthetic-access-key", "AWS_SECRET_ACCESS_KEY": "synthetic-secret-key",
            "AWS_SESSION_TOKEN": "synthetic-token", "AWS_DEFAULT_REGION": "fixture-region",
            "RESTIC_REPOSITORY": "s3:https://fixture.example.invalid/bucket",
            "UNRELATED_APPLICATION_SECRET": "never-forward-me"}


def declared(inputs, job, change):
    value = json.loads(inputs["CRUCIBLE_BACKUP_CONFIGURATION_JSON"])
    change(value)
    value["runtime_env_sha256"] = hashlib.sha256(job.runtime_bytes(value["runtime_env"])).hexdigest()
    return {**inputs, "CRUCIBLE_BACKUP_CONFIGURATION_JSON": json.dumps(value)}


def directory(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def test_exact_config_preservation_private_files_and_separate_roles(job, inputs, tmp_path):
    root = directory(tmp_path)
    env, connect = job.prepare(inputs, root)
    _, release, _, child = job.core_module().configuration(env)
    assert release["release_id"] == "fixture-v1"
    recovered = dict((name, json.loads(value)) for name, value in
                     (line.split("=", 1) for line in (root / "runtime.env").read_text().splitlines()))
    assert recovered == json.loads(inputs["CRUCIBLE_BACKUP_CONFIGURATION_JSON"])["runtime_env"]
    assert (root / "pgpass").read_text() == "db.example.invalid:25060:crucible:backup:p\\:a\\\\ss\n"
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in root.iterdir())
    assert connect["user"] == "backup" and connect["password"] == "p:a\\ss"
    assert connect["sslmode"] == "verify-full" and connect["connect_timeout"] == 5
    assert "default_transaction_read_only=on" in connect["options"]
    assert "UNRELATED_APPLICATION_SECRET" not in env and "UNRELATED_APPLICATION_SECRET" not in child
    assert "CRUCIBLE_SECRET" not in child and "AWS_SECRET_ACCESS_KEY" not in child
    assert Path(child["AWS_SHARED_CREDENTIALS_FILE"]).read_text().startswith("[crucible-backup]\n")


@pytest.mark.parametrize("change", [
    lambda d: d.update(format="unknown"),
    lambda d: d.update(release_id="other-release"),
    lambda d: d.update(extra="unreviewed"),
    lambda d: d["runtime_env"].update(CRUCIBLE_ENV="development"),
    lambda d: d["runtime_env"].update(CRUCIBLE_ACCESS_MODE="open"),
    lambda d: d["runtime_env"].update(CRUCIBLE_SECRET="short"),
    lambda d: d["runtime_env"].update(CRUCIBLE_ORIGIN="https://beta.example.invalid/path"),
    lambda d: d["runtime_env"].update(DATABASE_URL="postgresql://web:synthetic@other.invalid/crucible"),
])
def test_incomplete_or_mismatched_declaration_rejected(job, inputs, tmp_path, change):
    with pytest.raises(job.JobError):
        job.prepare(declared(inputs, job, change), directory(tmp_path))


def test_digest_duplicate_keys_oversize_and_nonfinite_rejected(job, inputs, tmp_path):
    bad = json.loads(inputs["CRUCIBLE_BACKUP_CONFIGURATION_JSON"])
    bad["runtime_env_sha256"] = "0" * 64
    with pytest.raises(job.JobError, match="digest"):
        job.prepare({**inputs, "CRUCIBLE_BACKUP_CONFIGURATION_JSON": json.dumps(bad)}, directory(tmp_path))
    for value in ('{"x":1,"x":2}', '{"x":NaN}', " " * (job.MAX_CONFIG_BYTES + 1)):
        with pytest.raises(job.JobError):
            job._json(value)


@pytest.mark.parametrize("url", [
    "sqlite:///db", "postgresql://backup:pw@db/db?sslmode=require",
    "postgresql://backup:pw@db/db?options=-c%20read_only=off",
    "postgresql://backup:pw@db/db?sslrootcert=/tmp/override.pem",
    "postgresql://backup:pw@db/db?hostaddr=not-an-IP",
    "postgresql://backup:pw@db/db?sslmode=verify-full&hostaddr=127.0.0.1",
    "postgresql://backup:pw@db/db",
    "postgresql://backup:pw@db/db?sslmode=verify-full&sslmode=require",
    "postgresql://backup:pw@db1,db2/db", "postgresql://backup:pw@db/space%20",
    "postgresql://backup:pw@db/db%0Auser=other", "postgresql://backup@db/db",
])
def test_connection_option_injection_and_ambiguous_database_rejected(job, url):
    with pytest.raises(job.JobError):
        job.database(url)


def test_image_manifest_permissions_and_symlink_rejected(job, inputs, tmp_path):
    release = Path(inputs["CRUCIBLE_BACKUP_RELEASE_MANIFEST"])
    release.chmod(0o666)
    with pytest.raises(job.JobError):
        job.prepare(inputs, directory(tmp_path))
    release.chmod(0o644)
    link = tmp_path / "link"
    link.symlink_to(release)
    with pytest.raises(job.JobError):
        job.prepare({**inputs, "CRUCIBLE_BACKUP_RELEASE_MANIFEST": str(link)}, tmp_path)


class FakeConnection:
    def __init__(self, acquired=True):
        self.driver = object()
        self.closed = self.invalidated = False
        self.acquired, self.held, self.pid = acquired, acquired, 1234
        self.calls = []
        self.fail = None

    @property
    def connection(self):
        assert not self.invalidated, "An invalidated SQLAlchemy connection must not reconnect"
        return SimpleNamespace(driver_connection=self.driver)

    def execute(self, query, params):
        self.calls.append((str(query), params))
        if self.fail:
            raise self.fail
        answer = self.acquired if "pg_try_advisory_lock" in str(query) else self.held
        return SimpleNamespace(one=lambda: (self.pid, answer))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


@pytest.mark.parametrize("lost", ["invalidated", "closed", "replacement", "unlocked", "pid", "disconnect"])
def test_lock_check_fails_on_loss_without_reconnect(job, lost):
    connection = FakeConnection()
    owner = job.Ownership(connection, connection.driver, connection.pid)
    owner.check()
    if lost in {"invalidated", "closed"}:
        setattr(connection, lost, True)
    elif lost == "replacement":
        connection.driver = object()
    elif lost == "unlocked":
        connection.held = False
    elif lost == "pid":
        connection.pid += 1
    else:
        connection.fail = OSError("synthetic-disconnect")
    with pytest.raises((job.JobError, OSError)):
        owner.check()


def test_dedicated_autocommit_nullpool_connection_released(job, monkeypatch):
    connection = FakeConnection()
    disposed = []
    def factory(url, **kwargs):
        assert url.drivername == "postgresql+psycopg" and kwargs["poolclass"] is NullPool
        assert kwargs["isolation_level"] == "AUTOCOMMIT" and kwargs["hide_parameters"] is True
        assert kwargs["connect_args"] == {"synthetic": "connection"}
        return SimpleNamespace(connect=lambda: connection, dispose=lambda: disposed.append(True))
    monkeypatch.setattr(job, "create_engine", factory)
    with job.locked({"synthetic": "connection"}) as owner:
        assert not connection.closed
        owner.check()
    assert connection.closed and disposed == [True]
    assert connection.calls[0][1] == {"key": job.LOCK_KEY}
    assert connection.calls[1][1] == {"high": job.LOCK_HIGH, "low": job.LOCK_LOW}


def test_contending_job_cannot_enter_backup(job, monkeypatch):
    connection = FakeConnection(acquired=False)
    disposed = []
    monkeypatch.setattr(job, "create_engine", lambda *a, **k: SimpleNamespace(
        connect=lambda: connection, dispose=lambda: disposed.append(True)))
    with pytest.raises(job.JobError, match="already running"):
        with job.locked({}):
            pytest.fail("A contending job entered the backup section")
    assert connection.closed and disposed == [True]


@pytest.mark.parametrize("fail", [False, True])
def test_lock_loss_cannot_record_completion_receipt_and_legacy_default_works(job, inputs, tmp_path, monkeypatch, fail):
    core = job.core_module()
    env, _ = job.prepare(inputs, directory(tmp_path))
    events = []
    def snapshot(args, env, *, content=None):
        stage = "complete" if content else ("database" if "database" in args else "configuration")
        events.append(stage)
        if content:
            receipt = json.loads(content)
            assert receipt["format"] == "crucible-recovery-set-v1" and receipt["restore_tested"] is False
        return "a" * 64
    def guard():
        events.append("ownership")
        if fail:
            raise job.JobError("synthetic lock lost")
    monkeypatch.setattr(core, "restic_snapshot", snapshot)
    if fail:
        with pytest.raises(job.JobError):
            core.backup(env, before_completion=guard)
        assert events == ["configuration", "database", "ownership"]
    else:
        assert core.backup(env, before_completion=guard)["status"] == "complete"
        assert events == ["configuration", "database", "ownership", "complete"]
        events.clear()
        assert core.backup(env)["status"] == "complete"
        assert events == ["configuration", "database", "complete"]


@pytest.mark.parametrize("fail", [False, True])
def test_run_job_holds_lock_for_pair_and_cleans_runtime_secrets(job, inputs, monkeypatch, fail):
    core = job.core_module()
    monkeypatch.setattr(job, "core_module", lambda: core)
    directories, events = [], []
    @contextmanager
    def locked(connect):
        events.append("locked")
        try:
            yield SimpleNamespace(check=lambda: events.append("ownership"))
        finally:
            events.append("released")
    def backup(env, *, require_remote, before_completion):
        path = Path(env["CRUCIBLE_BACKUP_RUNTIME_ENV"])
        directories.append(path.parent)
        assert path.exists() and path.parent.stat().st_mode & 0o777 == 0o700
        assert events == ["locked"] and require_remote is True
        before_completion()
        if fail:
            raise OSError("synthetic failure")
        return {"status": "complete"}
    monkeypatch.setattr(job, "locked", locked)
    monkeypatch.setattr(core, "backup", backup)
    if fail:
        with pytest.raises(OSError):
            job.run_job(inputs)
    else:
        assert job.run_job(inputs) == {"status": "complete"}
    assert events == ["locked", "ownership", "released"]
    assert directories and not directories[0].exists()


@pytest.mark.parametrize("root,enabled", [(True, True), (False, False), (False, True)])
def test_cli_gate_and_failures_never_print_secrets(job, monkeypatch, capsys, root, enabled):
    monkeypatch.setattr(job.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr(job.os, "umask", lambda value: None)
    monkeypatch.setenv("CRUCIBLE_BACKUP_JOB", "app-platform" if enabled else "")
    calls = []
    def run(_):
        calls.append(True)
        raise RuntimeError("postgresql://secret:never-print-me@host/db")
    monkeypatch.setattr(job, "run_job", run)
    assert job.main() == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "failed"
    assert "never-print-me" not in output.out + output.err
    assert bool(calls) == (not root and enabled)
