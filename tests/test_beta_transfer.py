"""Synthetic transfer checks. PostgreSQL tests require a dedicated opt-in DSN.

CRUCIBLE_BETA_TRANSFER_TEST_DATABASE_URL must point to a disposable local test
cluster with CREATEDB. Each test creates/drops ONLY a randomly named database.
Neither DATABASE_URL nor the operator transfer DSN is consulted by tests.
"""
from contextlib import closing
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
import sqlite3
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from scripts import beta_transfer as transfer


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    engine = sa.create_engine("sqlite:///" + str(path))
    head, revisions = transfer.migration_chain()
    with engine.begin() as connection:
        transfer.migrate_fixture(connection, revisions, head)
        # No usable password, email, token, or real user data in these fixtures.
        connection.exec_driver_sql("INSERT INTO users VALUES ('author','synthetic-author','synthetic-hash', 1729000000.125)")
        connection.exec_driver_sql("INSERT INTO users VALUES ('reviewer','synthetic-reviewer','synthetic-hash-2', 1729000000.25)")
        connection.exec_driver_sql("INSERT INTO methods VALUES ('method','author','synthetic method','line 1\nα + β',1729000000.5)")
        connection.execute(sa.text("""INSERT INTO challenges VALUES
          ('challenge','author','synthetic','v1','profile',1,1729000000.75,1729000000,1729003600,
           :public,'{}',:digest,1)"""), {"public": '{"huge":' + str(2**2048) + ',"decimal":0.12345678901234567890123456789}', "digest": "a" * 64})
        connection.execute(sa.text("""INSERT INTO submissions VALUES
          ('submission','challenge','author','method','profile','v1',1,'submission-key',:digest,1,
           1729000010.875,10.125,:compute,'Synthetic model')"""), {"digest": "b" * 64, "compute": '{"cores":4,"notes":null}'})
        for suffix, metadata in (("sql", None), ("json", "null")):
            connection.execute(sa.text("""INSERT INTO notebook_notes VALUES
              (:id,'author',1729000000.5,1,:key,:hash,NULL,:metadata)"""),
              {"id": "note-" + suffix, "key": "note-key-" + suffix, "hash": "c" * 64, "metadata": metadata})
            connection.execute(sa.text("""INSERT INTO notebook_revisions VALUES
              (:id,:note,1,1729000001.25,:content,:hash,:key,:request,NULL)"""),
              {"id": "revision-" + suffix, "note": "note-" + suffix,
               "content": '{"title":"synthetic","body":"α\\nβ","n":9007199254740993}',
               "hash": "d" * 64, "key": "revision-key-" + suffix, "request": "e" * 64})
        connection.execute(sa.text("""INSERT INTO notebook_publications VALUES
          ('publication','revision-sql','author',1729000010.125,'publish-key',:hash,'approved','reviewer',
           1729000020.25,'Synthetic approval','review-key',:review,0)"""), {"hash": "f" * 64, "review": "1" * 64})
    engine.dispose()
    return path


@pytest.fixture
def postgres():
    dsn = os.environ.get("CRUCIBLE_BETA_TRANSFER_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit local PostgreSQL test DSN not supplied")
    url = make_url(dsn)
    assert url.host in ("127.0.0.1", "localhost", "::1"), "Tests require an explicitly local disposable cluster"
    admin = transfer.target_engine(dsn, allow_local_test=True)
    name = "crucible_transfer_test_" + uuid.uuid4().hex
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("CREATE DATABASE " + transfer.quoted(name))
    engine = transfer.target_engine(url.set(database=name).render_as_string(hide_password=False), allow_local_test=True)
    try:
        head, revisions = transfer.migration_chain()
        with engine.begin() as connection:
            transfer.migrate_fixture(connection, revisions, head)
        yield engine
    finally:
        engine.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("DROP DATABASE " + transfer.quoted(name))
        admin.dispose()


def count(engine, name):
    with engine.connect() as connection:
        return connection.exec_driver_sql("SELECT COUNT(*) FROM " + transfer.quoted(name)).scalar_one()


def test_expected_schema_and_dependency_order_without_importing_app():
    head, schema = transfer.expected_schema()
    assert head == transfer.migration_chain()[0]
    assert {"users", "notebook_notes", "submissions", "notebook_publications"} <= schema.keys()
    order = transfer.dependency_order(schema)
    assert order.index("users") < order.index("notebook_notes") < order.index("notebook_revisions")
    assert order.index("notebook_revisions") < order.index("notebook_publications")
    assert order.index("challenges") < order.index("submissions")


@pytest.mark.parametrize("relationships", [
    {"a": {"foreign_keys": [(('parent',), 'a', ('id',))]}},
    {"a": {"foreign_keys": [(('parent',), 'b', ('id',))]},
     "b": {"foreign_keys": [(('parent',), 'a', ('id',))]}},
])
def test_cycles_and_self_references_fail_closed(relationships):
    with pytest.raises(transfer.TransferError, match="cycles or self references"):
        transfer.dependency_order(relationships)


def test_source_snapshot_includes_wal_without_mutating_original(source):
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO users VALUES ('wal-user','wal-user','synthetic',2)")
        writer.commit()
        before = source.read_bytes()
        with transfer.sqlite_snapshot(source) as snapshot:
            assert snapshot.exec_driver_sql("SELECT COUNT(*) FROM users").scalar_one() == 3
            with pytest.raises(sa.exc.OperationalError):
                snapshot.exec_driver_sql("DELETE FROM users")
        assert source.read_bytes() == before


def test_source_integrity_rejects_invalid_foreign_key(source):
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE methods SET user_id='missing'")
        connection.commit()
    with pytest.raises(transfer.TransferError, match="foreign-key"):
        with transfer.sqlite_snapshot(source):
            pass


def test_schema_column_drift_rejected(source):
    head, schema = transfer.expected_schema()
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("ALTER TABLE users ADD COLUMN unexpected TEXT")
        connection.commit()
    with transfer.sqlite_snapshot(source) as snapshot:
        with pytest.raises(transfer.TransferError, match="schema"):
            transfer.verify_schema(snapshot, head, schema, "Source")


def test_schema_head_drift_rejected(source):
    head, schema = transfer.expected_schema()
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("UPDATE alembic_version SET version_num='old'")
        connection.commit()
    with transfer.sqlite_snapshot(source) as snapshot:
        with pytest.raises(transfer.TransferError, match="migration head"):
            transfer.verify_schema(snapshot, head, schema, "Source")


def test_fingerprints_preserve_sql_json_null_numbers_and_float():
    assert transfer.normalized(None, ("json",)) != transfer.normalized("null", ("json",))
    assert transfer.normalized('{"n":9007199254740993}', ("json",)) != transfer.normalized('{"n":9007199254740992}', ("json",))
    assert transfer.normalized('{"n":1.00,"x":2}', ("json",)) == transfer.normalized('{"x":2,"n":1}', ("json",))
    assert transfer.normalized('{"n":12345678901234567890123456789.1}', ("json",)) != transfer.normalized('{"n":12345678901234567890123456789.2}', ("json",))
    assert transfer.normalized(1729000000.125, ("float",)) != transfer.normalized(1729000000.25, ("float",))
    with pytest.raises(transfer.TransferError):
        transfer.normalized('{"a":1,"a":2}', ("json",))
    with pytest.raises(transfer.TransferError):
        transfer.normalized('NaN', ("json",))
    with pytest.raises(transfer.TransferError, match="column range"):
        transfer.normalized(2**32, ("integer", 32))
    with pytest.raises(transfer.TransferError, match="does not fit"):
        transfer.normalized("too long", ("string", 3))


@pytest.mark.parametrize("dsn", [
    "sqlite:///ignored.db", "postgresql+psycopg://user@remote/db",
    "postgresql+psycopg://user@remote/db?sslmode=verify-full&hostaddr=127.0.0.1",
    "postgresql+psycopg://user@remote/db?sslmode=verify-full&options=-csearch_path%3Devil",
])
def test_invalid_or_unverified_targets_refused(dsn):
    with pytest.raises(transfer.TransferError):
        transfer.target_engine(dsn)


def test_never_uses_inherited_application_database_url(monkeypatch, capsys, source):
    monkeypatch.delenv(transfer.TARGET_ENV, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://real:PRIVATE_PASSWORD@remote/real")
    assert transfer.main(["--source", str(source)]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE_PASSWORD" not in output
    assert transfer.TARGET_ENV in output


def test_cli_errors_never_render_payload_or_dsn(monkeypatch, capsys, source):
    monkeypatch.setenv(transfer.TARGET_ENV, "postgresql+psycopg://synthetic:SECRET@127.0.0.1/fixture")
    class FailureEngine:
        def dispose(self):
            pass
    monkeypatch.setattr(transfer, "target_engine", lambda *a, **kw: FailureEngine())
    def fail(*args, **kwargs):
        raise RuntimeError("SECRET private payload password_hash")
    monkeypatch.setattr(transfer, "transfer", fail)
    assert transfer.main(["--source", str(source), "--allow-local-test-db"]) == 1
    output = capsys.readouterr().out
    assert all(secret not in output for secret in ("SECRET", "private payload", "password_hash"))


def test_real_pg_transfer_preserves_hashes_history_nulls_large_json_and_timestamps(source, postgres):
    checked = transfer.transfer(source, postgres)
    assert checked["status"] == "checked_no_writes"
    assert count(postgres, "users") == 0
    applied = transfer.transfer(source, postgres, apply=True)
    assert applied["destination_verified"]
    assert applied["tables"] == checked["tables"]
    with postgres.connect() as connection:
        assert connection.exec_driver_sql("SELECT password_hash, created_at FROM users WHERE id='author'").one() == ("synthetic-hash", 1729000000.125)
        assert connection.exec_driver_sql("SELECT public_data->>'huge' FROM challenges").scalar_one() == str(2**2048)
        assert connection.exec_driver_sql("SELECT source_metadata IS NULL FROM notebook_notes WHERE id='note-sql'").scalar_one()
        assert not connection.exec_driver_sql("SELECT source_metadata IS NULL FROM notebook_notes WHERE id='note-json'").scalar_one()
        assert connection.exec_driver_sql("SELECT decision, reviewer_id, test_only_self_review FROM notebook_publications").one() == ("approved", "reviewer", False)
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM beta_memberships").scalar_one() == 0
    with pytest.raises(transfer.TransferError, match="contains application data"):
        transfer.transfer(source, postgres, apply=True)
    assert count(postgres, "users") == 2


def test_real_pg_partial_failure_rolls_back_every_table(source, postgres, monkeypatch):
    original = transfer.insert_table
    def fail_after_users(src, dst, name, info):
        original(src, dst, name, info)
        if name == "users":
            raise RuntimeError("synthetic interruption")
    monkeypatch.setattr(transfer, "insert_table", fail_after_users)
    with pytest.raises(RuntimeError):
        transfer.transfer(source, postgres, apply=True)
    with postgres.connect() as connection:
        for name in transfer.dependency_order(transfer.expected_schema()[1]):
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM " + transfer.quoted(name)).scalar_one() == 0


def test_real_pg_verification_mismatch_rolls_back(source, postgres, monkeypatch):
    original = transfer.fingerprint
    def changed(connection, name, info):
        result = original(connection, name, info)
        if connection.dialect.name == "postgresql" and name == "users":
            result["sha256"] = "bad"
        return result
    monkeypatch.setattr(transfer, "fingerprint", changed)
    with pytest.raises(transfer.TransferError, match="verification failed"):
        transfer.transfer(source, postgres, apply=True)
    assert count(postgres, "users") == 0


def test_real_pg_lock_conflict_does_not_copy(source, postgres):
    with postgres.begin() as connection:
        connection.exec_driver_sql("LOCK TABLE users IN ROW EXCLUSIVE MODE")
        with pytest.raises(sa.exc.DBAPIError):
            transfer.transfer(source, postgres, apply=True)
    assert count(postgres, "users") == 0


def test_real_pg_schema_drift_does_not_copy(source, postgres):
    with postgres.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN unexpected TEXT")
    with pytest.raises(transfer.TransferError, match="schema"):
        transfer.transfer(source, postgres, apply=True)
    assert count(postgres, "users") == 0


@pytest.fixture
def all_table_source(tmp_path):
    """One synthetic record in every application table, including history."""
    path = tmp_path / "every-table.sqlite3"
    head, revisions = transfer.migration_chain()
    _, schema = transfer.expected_schema()
    engine = sa.create_engine("sqlite:///" + str(path))
    records = {}
    with engine.begin() as connection:
        transfer.migrate_fixture(connection, revisions, head)
        for name in transfer.dependency_order(schema):
            info = schema[name]
            row = {}
            for column, kind, nullable in info["columns"]:
                family = kind[0]
                if family == "integer":
                    value = 1
                elif family == "float":
                    value = 1729000000.125
                elif family == "boolean":
                    value = 1
                elif family == "json":
                    value = json.dumps({"integer": 2**1024, "nested": [None, True, False]})
                elif family == "binary":
                    value = b"synthetic\x00binary"
                else:
                    value = ("fixture-" + column)[:kind[1]] if family == "string" else "synthetic text α\nβ"
                row[column] = value
            if name == "proposal_retractions":
                row["target_kind"] = "scheme_admission"
            for columns, parent, parent_columns in info["foreign_keys"]:
                for column, parent_column in zip(columns, parent_columns):
                    row[column] = records[parent][parent_column]
            names = list(row)
            statement = sa.text("INSERT INTO " + transfer.quoted(name) + " (" +
                ", ".join(transfer.quoted(column) for column in names) + ") VALUES (" +
                ", ".join(":v" + str(index) for index in range(len(names))) + ")")
            connection.execute(statement, {"v" + str(index): row[column] for index, column in enumerate(names)})
            records[name] = row
    engine.dispose()
    return path


def test_real_pg_all_migrated_tables_are_copied(all_table_source, postgres):
    """Even historical/access/operator tables must not silently disappear."""
    _, schema = transfer.expected_schema()
    receipt = transfer.transfer(all_table_source, postgres, apply=True)
    assert receipt["application_rows"] == len(schema) - 1
    assert set(receipt["tables"]) == set(schema)
    assert all(table["rows"] == 1 for table in receipt["tables"].values())


def test_real_pg_backup_restores_every_table(all_table_source, postgres, tmp_path):
    """An actual logical backup restores to a separate empty fixture database."""
    dump_binary, restore_binary = shutil.which("pg_dump"), shutil.which("pg_restore")
    if not dump_binary or not restore_binary:
        pytest.skip("pg_dump and pg_restore binaries are required for recovery rehearsal")
    receipt = transfer.transfer(all_table_source, postgres, apply=True)
    head, schema = transfer.expected_schema()
    with postgres.connect() as connection:
        server_version = connection.exec_driver_sql("SHOW server_version").scalar_one()
    archive = tmp_path / "synthetic-recovery.dump"
    archive.touch(mode=0o600)
    def pg_environment(url):
        # Avoid inherited service/connection settings; credentials stay in env.
        env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        env.update({"PGHOST": url.host, "PGPORT": str(url.port or 5432),
                    "PGUSER": url.username, "PGPASSWORD": url.password or "",
                    "PGDATABASE": url.database, "PGCONNECT_TIMEOUT": "10",
                    "PGSSLMODE": url.query.get("sslmode", "disable")})
        if "sslrootcert" in url.query:
            env["PGSSLROOTCERT"] = url.query["sslrootcert"]
        return env
    dumped = subprocess.run([dump_binary, "--format=custom", "--no-owner", "--no-privileges",
                             "--file=" + str(archive)], env=pg_environment(postgres.url),
                            capture_output=True, text=True, timeout=60)
    assert dumped.returncode == 0, "Synthetic pg_dump failed; no backup evidence claimed"
    assert archive.stat().st_size > 0
    admin = transfer.target_engine(os.environ["CRUCIBLE_BETA_TRANSFER_TEST_DATABASE_URL"], allow_local_test=True)
    restored_name = "crucible_transfer_test_" + uuid.uuid4().hex
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("CREATE DATABASE " + transfer.quoted(restored_name))
    restore_url = admin.url.set(database=restored_name)
    restored = transfer.target_engine(restore_url.render_as_string(hide_password=False), allow_local_test=True)
    try:
        recovered = subprocess.run([restore_binary, "--exit-on-error", "--single-transaction",
                                    "--no-owner", "--no-privileges", "--dbname=" + restored_name,
                                    str(archive)], env=pg_environment(restore_url),
                                   capture_output=True, text=True, timeout=60)
        assert recovered.returncode == 0, "Synthetic pg_restore failed; no recovery evidence claimed"
        with restored.connect() as connection:
            transfer.verify_schema(connection, head, schema, "Restored fixture")
            actual = {name: transfer.fingerprint(connection, name, schema[name]) for name in sorted(schema)}
        assert actual == receipt["tables"]
        print(json.dumps({"recovery_rehearsal": "passed", "python": platform.python_version(),
                          "postgresql": server_version, "schema_head": head,
                          "tables_verified": len(actual), "application_rows": receipt["application_rows"],
                          "method": "pg_dump custom archive; pg_restore single transaction",
                          "database_counts_and_digests_match": True, "cloud_backup_tested": False}, sort_keys=True))
    finally:
        restored.dispose()
        with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("DROP DATABASE " + transfer.quoted(restored_name))
        admin.dispose()
