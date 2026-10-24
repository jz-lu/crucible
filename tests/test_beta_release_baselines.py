"""Original baseline bootstrap uses only synthetic databases and public evidence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("baseline_operator", ROOT / "scripts/beta_release_baselines.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def encoded(value):
    raw = json.dumps(value).encode()
    return raw, hashlib.sha256(raw).hexdigest()


def document():
    releases = []
    for name, artifact in (("fixture-a", "b" * 64), ("fixture-b", None)):
        implementation = {"source_sha256": "a" * 64, "artifact_sha256": artifact}
        releases.append({"scheme_id": name, "version": "v1", **implementation,
            "manifests": [{"id": name + "-profile", "scheme_id": name, "version": "v1",
                           "implementation": implementation.copy()}]})
    return {"format": tool.FORMAT, "database_read": False, "database_admission": False,
            "platform": {"system": "Darwin", "machine": "arm64", "python": "3.14.6"}, "releases": releases}


def evidence():
    return tool.parse_evidence(*encoded(document()))


def schema(engine):
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE adapter_versions (scheme_id VARCHAR(100),version VARCHAR(32),"
            "source_sha256 VARCHAR(64) NOT NULL,artifact_sha256 VARCHAR(64),created_at FLOAT NOT NULL,"
            "PRIMARY KEY(scheme_id,version))"))
        connection.execute(sa.text("CREATE TABLE challenges (id TEXT PRIMARY KEY, scheme_id TEXT, scheme_version TEXT, manifest TEXT)"))
        connection.execute(sa.text("CREATE TABLE implementation_builds (id TEXT PRIMARY KEY, evidence TEXT)"))
        connection.execute(sa.text("INSERT INTO implementation_builds VALUES ('unrelated','retained existing review')"))


@pytest.fixture
def database(tmp_path):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "synthetic.sqlite"), poolclass=NullPool)
    schema(engine)
    yield engine
    engine.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        return {name: [tuple(row) for row in connection.execute(sa.text("SELECT * FROM " + name + " ORDER BY 1"))]
                for name in ("adapter_versions", "challenges", "implementation_builds")}


def history(engine, manifest, scheme="fixture-b"):
    with engine.begin() as connection:
        connection.execute(sa.text("INSERT INTO challenges VALUES ('history',:scheme,'v1',:manifest)"),
            {"scheme": scheme, "manifest": json.dumps(manifest)})


def test_read_only_plan_then_additive_idempotent_apply_preserves_history(database):
    records = evidence()
    with database.begin() as connection:
        connection.execute(sa.text("INSERT INTO adapter_versions VALUES ('fixture-a','v1',:source,:artifact,17)"),
                           {"source": "a" * 64, "artifact": "b" * 64})
    history(database, document()["releases"][1]["manifests"][0])
    original = snapshot(database)
    planned = tool.operate(database, records, sorted(records), now=100)
    assert planned["status"] == "checked_no_writes"
    assert [row["action"] for row in planned["releases"]] == ["preserve", "initialize"]
    assert snapshot(database) == original
    applied = tool.operate(database, records, sorted(records), apply=True, now=101)
    assert [row["created_at"] for row in applied["releases"]] == [17, 101]
    after = snapshot(database)
    assert after["challenges"] == original["challenges"]
    assert after["implementation_builds"] == original["implementation_builds"]
    tool.operate(database, records, sorted(records), apply=True, now=500)
    assert snapshot(database) == after


def test_conflicting_existing_anchor_rolls_back_whole_operation(database):
    with database.begin() as connection:
        connection.execute(sa.text("INSERT INTO adapter_versions VALUES ('fixture-b','v1',:source,NULL,17)"),
                           {"source": "c" * 64})
    original = snapshot(database)
    records = evidence()
    with pytest.raises(tool.BaselineError, match="existing baseline conflicts"):
        tool.operate(database, records, sorted(records), apply=True, now=101)
    assert snapshot(database) == original


@pytest.mark.parametrize("manifest", [None, {}, {"implementation": {}},
    {"implementation": {"source_sha256": "c" * 64}},
    {"implementation": {"source_sha256": "a" * 64, "admission": {"kind": "additional"}}}])
def test_unanchored_conflicting_or_missing_history_never_initializes(database, manifest):
    history(database, manifest)
    before = snapshot(database)
    records = evidence()
    with pytest.raises(tool.BaselineError, match="Historical challenges"):
        tool.operate(database, records, sorted(records), apply=True, now=101)
    assert snapshot(database) == before


@pytest.mark.parametrize("mutation", [
    lambda x: x.update(format="unknown"),
    lambda x: x.update(database_read=True),
    lambda x: x["releases"].append(deepcopy(x["releases"][0])),
    lambda x: x["releases"][0]["manifests"][0].update(version="v2"),
    lambda x: x["releases"][0]["manifests"][0]["implementation"].update(source_sha256="c" * 64),
    lambda x: x["releases"][0]["manifests"][0]["implementation"].update(admission={}),
    lambda x: x["releases"][0].update(manifests=[]),
])
def test_untrusted_or_inconsistent_evidence_fails_closed(mutation):
    value = document()
    mutation(value)
    with pytest.raises(tool.BaselineError):
        tool.parse_evidence(*encoded(value))


def test_exact_bytes_duplicate_keys_and_selection():
    raw, digest = encoded(document())
    with pytest.raises(tool.BaselineError, match="reviewed digest"):
        tool.parse_evidence(raw + b"\n", digest)
    duplicate = b'{"format":1,"format":2}'
    with pytest.raises(tool.BaselineError, match="Duplicate JSON"):
        tool.parse_evidence(duplicate, hashlib.sha256(duplicate).hexdigest())
    records = evidence()
    assert tool.select_releases(records, ["fixture-b@v1"]) == [("fixture-b", "v1")]
    for selected in (["unknown@v1"], ["fixture-b@v1", "fixture-b@v1"], ["fixture-b"]):
        with pytest.raises(tool.BaselineError):
            tool.select_releases(records, selected)


def cli_setup(tmp_path, monkeypatch, database):
    raw, digest = encoded(document())
    source = tmp_path / "public-evidence.json"
    source.write_bytes(raw)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setenv(tool.TARGET_ENV, "postgresql+psycopg://synthetic:PRIVATE@localhost/fixture")
    monkeypatch.setattr(tool, "target_engine", lambda *a, **k: database)
    args = [str(source), "--evidence-sha256", digest, "--apply", "--reviewed-by", "fixture-operator",
            "--review-basis", "Reviewed original public runtime evidence", "--receipt", str(receipt)]
    return args, receipt


def test_cli_never_uses_default_database_and_never_clobbers_receipt(tmp_path, monkeypatch, database, capsys):
    args, receipt = cli_setup(tmp_path, monkeypatch, database)
    monkeypatch.delenv(tool.TARGET_ENV)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///SHOULD_NOT_BE_OPENED")
    assert tool.main(args) == 1
    assert tool.TARGET_ENV in capsys.readouterr().err
    assert not receipt.exists()
    monkeypatch.setenv(tool.TARGET_ENV, "explicit-fixture")
    receipt.write_bytes(b"existing receipt")
    assert tool.main(args) == 1
    assert receipt.read_bytes() == b"existing receipt"
    assert not snapshot(database)["adapter_versions"]


def test_receipt_failure_before_commit_rolls_back(tmp_path, monkeypatch, database):
    args, receipt = cli_setup(tmp_path, monkeypatch, database)
    original_write = tool.write_receipt
    def fail_precommit(path, value, **kwargs):
        if value["status"] == "commit_pending":
            raise OSError("synthetic disk failure")
        original_write(path, value, **kwargs)
    monkeypatch.setattr(tool, "write_receipt", fail_precommit)
    assert tool.main(args) == 1
    assert not snapshot(database)["adapter_versions"]
    assert json.loads(receipt.read_text())["status"] == "not_applied"


def test_receipt_failure_after_commit_retains_pending_and_requests_verification(tmp_path, monkeypatch, database, capsys):
    args, receipt = cli_setup(tmp_path, monkeypatch, database)
    original_write = tool.write_receipt
    def fail_after_commit(path, value, **kwargs):
        if value["status"] in {"committed", "verification_required"}:
            raise OSError("synthetic disk failure")
        original_write(path, value, **kwargs)
    monkeypatch.setattr(tool, "write_receipt", fail_after_commit)
    assert tool.main(args) == 2
    assert len(snapshot(database)["adapter_versions"]) == 2
    assert json.loads(receipt.read_text())["status"] == "commit_pending"
    assert "verification is incomplete" in capsys.readouterr().err


def test_ambiguous_commit_is_not_reported_as_rollback(tmp_path, monkeypatch, database):
    args, receipt = cli_setup(tmp_path, monkeypatch, database)
    original_write = tool.write_receipt
    committing = False
    def track_commit(path, value, **kwargs):
        nonlocal committing
        original_write(path, value, **kwargs)
        committing = value["status"] == "commit_pending"
    def lost_commit_ack(_):
        if committing:
            raise SQLAlchemyError("synthetic lost commit acknowledgement")
    monkeypatch.setattr(tool, "write_receipt", track_commit)
    sa.event.listen(database, "commit", lost_commit_ack)
    try:
        assert tool.main(args) == 2
        assert json.loads(receipt.read_text())["status"] == "verification_required"
    finally:
        sa.event.remove(database, "commit", lost_commit_ack)


def test_successful_apply_retains_private_complete_operation_receipt(tmp_path, monkeypatch, database):
    args, receipt = cli_setup(tmp_path, monkeypatch, database)
    assert tool.main(args) == 0
    saved = json.loads(receipt.read_text())
    assert saved["status"] == "committed"
    assert saved["evidence_sha256"] == encoded(document())[1]
    assert saved["selected_releases"] == [["fixture-a", "v1"], ["fixture-b", "v1"]]
    assert saved["reviewed_by"] == "fixture-operator"
    assert all(row["action"] == "initialize" and row["created_at"] == saved["timestamp"] for row in saved["releases"])
    assert receipt.stat().st_mode & 0o077 == 0
    assert snapshot(database)["implementation_builds"] == [("unrelated", "retained existing review")]


def test_database_errors_are_redacted(tmp_path, monkeypatch, database, capsys):
    args, _ = cli_setup(tmp_path, monkeypatch, database)
    def fail(*args, **kwargs):
        raise SQLAlchemyError("postgresql://user:PRIVATE@host/db participant-secret")
    monkeypatch.setattr(tool, "operate", fail)
    assert tool.main(args) == 1
    output = capsys.readouterr()
    assert "PRIVATE" not in output.err + output.out
    assert "participant-secret" not in output.err + output.out


@pytest.mark.parametrize("override", ["host", "hostaddr", "port", "service", "servicefile", "dbname", "database",
                                      "options", "user", "username", "password"])
def test_target_rejects_query_overrides_before_engine_creation(monkeypatch, override):
    monkeypatch.setattr(tool.sa, "create_engine", lambda *a, **k: pytest.fail("Ambiguous target must not reach the driver"))
    url = sa.engine.URL.create("postgresql+psycopg", username="fixture", password="PRIVATE", host="127.0.0.1",
        database="intended", query={"sslmode": "verify-full", override: "unexpected"})
    with pytest.raises(tool.BaselineError) as caught:
        tool.target_engine(url, allow_local_test=True)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("url", [
    "postgresql+psycopg:///beta?sslmode=verify-full",
    "postgresql+psycopg://db.invalid/?sslmode=verify-full",
    "postgresql+psycopg://db.invalid/beta",
    "postgresql+psycopg://db.invalid/beta?sslmode=require",
    "postgresql+psycopg://db.invalid/beta?sslmode=verify-full&sslmode=disable",
    "postgresql+psycopg://db.invalid/beta?sslmode=verify-full&sslrootcert=relative.crt",
    "postgresql+psycopg://db.invalid:0/beta?sslmode=verify-full",
    "postgresql+psycopg://db.invalid:65536/beta?sslmode=verify-full",
    "postgresql+psycopg://db.invalid:PRIVATE/beta?sslmode=verify-full",
    "sqlite:///must-not-create.sqlite",
    *[sa.engine.URL.create("postgresql+psycopg", host=host, database="beta", query={"sslmode": "verify-full"})
      for host in (" ", "db.invalid\n", "/var/run/postgresql", "db.invalid\\socket", "db.invalid,another.invalid", "db.invalid\x00")],
    *[sa.engine.URL.create("postgresql+psycopg", host="db.invalid", database=name, query={"sslmode": "verify-full"})
      for name in (" ", " beta", "beta\x00")],
])
def test_invalid_or_unverified_target_refused_even_with_local_fixture_flag(monkeypatch, url):
    monkeypatch.setattr(tool.sa, "create_engine", lambda *a, **k: pytest.fail("Invalid target must not reach the driver"))
    with pytest.raises(tool.BaselineError) as caught:
        tool.target_engine(url, allow_local_test=True)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_unverified_loopback_requires_explicit_fixture_permission(monkeypatch, host):
    calls = []
    monkeypatch.setattr(tool.sa, "create_engine", lambda *args, **kwargs: calls.append((args, kwargs)) or "fixture")
    url = sa.engine.URL.create("postgresql+psycopg", host=host, database="synthetic")
    with pytest.raises(tool.BaselineError):
        tool.target_engine(url)
    assert not calls
    assert tool.target_engine(url, allow_local_test=True) == "fixture"
    assert calls[0][0][0] == url


def test_verified_remote_target_retains_exact_tls_settings(monkeypatch):
    calls = []
    monkeypatch.setattr(tool.sa, "create_engine", lambda *args, **kwargs: calls.append((args, kwargs)) or "fixture")
    url = sa.engine.URL.create("postgresql+psycopg", username="operator", host="beta-db.invalid", port=25060,
        database="beta", query={"sslmode": "verify-full", "sslrootcert": "/etc/crucible/provider-ca.crt"})
    assert tool.target_engine(url) == "fixture"
    assert calls[0][0][0] == url
    assert calls[0][1] == {"poolclass": NullPool, "hide_parameters": True, "connect_args": {"connect_timeout": 10}}


@pytest.fixture
def postgres():
    dsn = os.environ.get("CRUCIBLE_BASELINE_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set the explicit disposable PostgreSQL fixture DSN")
    url = sa.engine.make_url(dsn)
    admin = sa.create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    name = "crucible_baseline_test_" + uuid.uuid4().hex
    with admin.connect() as connection:
        connection.exec_driver_sql('CREATE DATABASE "' + name + '"')
    engine = tool.target_engine(url.set(database=name), allow_local_test=True)
    try:
        schema(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql('DROP DATABASE "' + name + '"')
        admin.dispose()


def test_postgres_plan_enforces_read_only_and_preserves_rows(postgres, monkeypatch):
    records = evidence()
    original_plan = tool.plan
    def assert_read_only(connection, *args):
        assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        return original_plan(connection, *args)
    monkeypatch.setattr(tool, "plan", assert_read_only)
    before = snapshot(postgres)
    assert tool.operate(postgres, records, sorted(records), now=100)["status"] == "checked_no_writes"
    assert snapshot(postgres) == before


def test_postgres_apply_serializes_against_other_operator_and_preserves_history(postgres):
    records = evidence()
    history(postgres, document()["releases"][1]["manifests"][0])
    before = snapshot(postgres)
    ready, release = threading.Event(), threading.Event()
    def hold_before_commit(_):
        ready.set()
        assert release.wait(10)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tool.operate, postgres, records, sorted(records), apply=True, now=101,
                             before_commit=hold_before_commit)
        try:
            assert ready.wait(10)
            with pytest.raises(SQLAlchemyError):
                tool.operate(postgres, records, sorted(records), apply=True, now=102)
        finally:
            release.set()
        assert future.result(timeout=10)["status"] == "committed"
    after = snapshot(postgres)
    assert len(after["adapter_versions"]) == 2
    assert after["challenges"] == before["challenges"]
    assert after["implementation_builds"] == before["implementation_builds"]
    tool.operate(postgres, records, sorted(records), apply=True, now=999)
    assert snapshot(postgres) == after
