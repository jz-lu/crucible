"""Copy an explicit SQLite snapshot into a fresh, migrated PostgreSQL database.

No application/native imports, schema changes, data deletion, or implicit DSNs.
Identity/sequence columns remain unsupported, including notebook_numbers at
schema 0025+. Do not bypass this guard: allocator high-watermarks need a separate
reviewed transfer. This script is not used for PostgreSQL backup/restore rollout.
The source backup and checks are read-only. --apply is the only write mode.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from decimal import Decimal
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
TARGET_ENV = "CRUCIBLE_BETA_TRANSFER_DATABASE_URL"

# Exact schema 0028 seed only. Keep independent of application/native imports;
# adding another migration seed requires an explicit transfer review.
INITIAL_BOUNTY_POLICY = {
    "id": "c392e762f76b1dd9e1db2e3db06e33ef238e0eda4977618ae53592ef6753a0b7",
    "effective_month": "2026-10",
    "configuration": {"monthly_units": 10, "proposal_cost_units": 1, "boost_cost_units": 1,
        "points_per_boost": 100, "proposal_reward_points": 100, "carryover": "expires",
        "refund_rule": "original_month", "claim_order": "earliest_qualifying"},
    "created_by": None, "created_at": 1793232000.0,
}
JOURNAL_SELF_FK = (("original_event_id",), "bounty_allowance_events", ("id",))


class TransferError(Exception):
    """A deliberately payload-free operator error."""


def quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def migration_chain(root=ROOT):
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise TransferError("The checkout must have exactly one migration head.")
    revisions = list(reversed(list(scripts.walk_revisions())))
    previous = None
    for revision in revisions:
        if revision.down_revision != previous or revision.dependencies:
            raise TransferError("Branched migrations need a reviewed transfer update.")
        previous = revision.revision
    return heads[0], revisions


def migrate_fixture(connection, revisions, head):
    """Run trusted checkout migrations on an EMPTY fixture; never on a target."""
    if sa.inspect(connection).get_table_names():
        raise TransferError("Schema fixture must be empty.")
    with Operations.context(MigrationContext.configure(connection)):
        for revision in revisions:
            revision.module.upgrade()
    connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    connection.execute(sa.text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head})


def type_signature(kind):
    if isinstance(kind, sa.Boolean):
        return ("boolean",)
    if isinstance(kind, sa.JSON):
        return ("json",)
    if isinstance(kind, sa.Float):
        return ("float",)
    if isinstance(kind, sa.BigInteger):
        return ("integer", 64)
    if isinstance(kind, sa.SmallInteger):
        return ("integer", 16)
    if isinstance(kind, sa.Integer):
        return ("integer", 32)
    if isinstance(kind, sa.LargeBinary):
        return ("binary",)
    if isinstance(kind, sa.Text):
        return ("text",)
    if isinstance(kind, sa.String):
        return ("string", kind.length)
    raise TransferError("Unsupported column type; review the transfer for this schema.")


def schema_signature(connection):
    inspector = sa.inspect(connection)
    if inspector.get_view_names() or inspector.get_temp_table_names():
        raise TransferError("Unexpected database views or temporary tables.")
    if connection.dialect.name == "postgresql" and inspector.get_materialized_view_names():
        raise TransferError("Unexpected database materialized views.")
    result = {}
    for name in sorted(inspector.get_table_names()):
        columns = inspector.get_columns(name)
        if any(column.get("computed") or column.get("identity") or
               "nextval(" in str(column.get("default", "")) for column in columns):
            raise TransferError("Generated columns or sequences need a reviewed transfer update.")
        foreign_keys = []
        for fk in inspector.get_foreign_keys(name):
            if fk.get("referred_schema") not in (None, "public"):
                raise TransferError("Cross-schema foreign keys are unsupported.")
            foreign_keys.append((tuple(fk["constrained_columns"]), fk["referred_table"],
                                 tuple(fk["referred_columns"])))
        result[name] = {
            "columns": [(column["name"], type_signature(column["type"]),
                         bool(column["nullable"])) for column in columns],
            "primary_key": tuple(inspector.get_pk_constraint(name)["constrained_columns"]),
            "foreign_keys": sorted(foreign_keys),
            "unique": sorted(tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)),
        }
        if not result[name]["primary_key"]:
            raise TransferError("Every transferred table must have a primary key.")
    return result


def expected_schema(root=ROOT):
    head, revisions = migration_chain(root)
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            migrate_fixture(connection, revisions, head)
            return head, schema_signature(connection)
    finally:
        engine.dispose()


def reviewed_journal_reference(name, info):
    """Only the existing nullable compensation→original-event edge is supported."""
    self_refs = [fk for fk in info["foreign_keys"] if fk[1] == name]
    columns = {column: (kind, nullable) for column, kind, nullable in info.get("columns", [])}
    return (name == "bounty_allowance_events" and self_refs == [JOURNAL_SELF_FK]
            and info.get("primary_key") == ("id",)
            and columns.get("id") == (("string", 36), False)
            and columns.get("original_event_id") == (("string", 36), True))


def dependency_order(schema):
    """Tables are acyclic except the reviewed, separately ordered journal edge."""
    pending = {name: {fk[1] for fk in info["foreign_keys"]
                      if not (reviewed_journal_reference(name, info) and fk == JOURNAL_SELF_FK)}
               for name, info in schema.items() if name != "alembic_version"}
    order = []
    while pending:
        available = sorted(name for name, dependencies in pending.items() if not dependencies)
        if not available:
            raise TransferError("Foreign-key cycles or self references need a reviewed transfer update.")
        order.extend(available)
        for name in available:
            del pending[name]
        for dependencies in pending.values():
            dependencies.difference_update(available)
    return order


@contextmanager
def sqlite_snapshot(source, *, timeout=60):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file():
        raise TransferError("Source must be an existing SQLite database file.")
    with tempfile.TemporaryDirectory(prefix="crucible-beta-transfer-") as directory:
        snapshot = Path(directory) / "snapshot.sqlite3"
        # mkstemp directory is private; set the file mode before writing any data.
        descriptor = os.open(snapshot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        deadline = time.monotonic() + timeout
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TransferError("Source snapshot timed out; stop local writes and retry.")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as original:
            original.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(snapshot)) as backup:
                original.backup(backup, pages=256, progress=progress, sleep=0.05)
                if backup.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise TransferError("Source snapshot failed its SQLite integrity check.")
                if backup.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise TransferError("Source snapshot contains an invalid foreign-key reference.")
        # Backup API includes committed WAL data; never copy only the main file.
        engine = sa.create_engine("sqlite://", creator=lambda: sqlite3.connect(
            snapshot.as_uri() + "?mode=ro", uri=True), poolclass=NullPool)
        try:
            with engine.connect() as connection:
                yield connection
        finally:
            engine.dispose()


def json_tree(value):
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (int, Decimal)):
        decimal = Decimal(value)
        if not decimal.is_finite():
            raise TransferError("Non-finite JSON numbers are not portable.")
        # as_tuple avoids Decimal.normalize() rounding at the context precision.
        sign, digits, exponent = decimal.as_tuple()
        digits = list(digits)
        while len(digits) > 1 and digits[-1] == 0:
            digits.pop()
            exponent += 1
        if not any(digits):
            sign, digits, exponent = 0, [0], 0
        return ["number", sign, digits, exponent]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, list):
        return ["array", [json_tree(item) for item in value]]
    if isinstance(value, dict):
        return ["object", [[key, json_tree(value[key])] for key in sorted(value)]]
    raise TransferError("Unsupported JSON value.")


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransferError("Duplicate JSON object keys need explicit review before transfer.")
        result[key] = value
    return result


def normalized(value, kind):
    if value is None:
        return ["sql-null"]
    family = kind[0]
    if family == "json":
        try:
            parsed = json.loads(value, parse_int=int, parse_float=Decimal,
                                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                                object_pairs_hook=reject_duplicate_keys)
        except (ValueError, TypeError):
            raise TransferError("Invalid or non-finite JSON in the source or destination.") from None
        return ["json", json_tree(parsed)]
    if family == "boolean":
        if value not in (0, 1, False, True):
            raise TransferError("Non-Boolean value in a Boolean column.")
        return ["boolean", bool(value)]
    if family == "float":
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise TransferError("Non-finite or non-numeric value in a floating-point column.")
        return ["float", float(value).hex()]
    if family == "integer":
        if not isinstance(value, int):
            raise TransferError("Non-integer value in an integer column.")
        bits = kind[1]
        if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise TransferError("Integer value exceeds the destination column range.")
        return ["integer", str(value)]
    if family == "binary":
        return ["binary", bytes(value).hex()]
    if not isinstance(value, str):
        raise TransferError("Non-text value in a text column.")
    if "\x00" in value or (family == "string" and kind[1] is not None and len(value) > kind[1]):
        raise TransferError("Text does not fit the destination column.")
    return ["text", value]


def selected_columns(info):
    # JSON is read as text so SQL NULL, JSON null and arbitrary-size numbers
    # survive driver deserialization without rounding or being conflated.
    return [f"CAST({quoted(column)} AS TEXT)" if kind[0] == "json" else quoted(column)
            for column, kind, nullable in info["columns"]]


def table_rows(connection, name, info, *, primary_key=None):
    query = f"SELECT {', '.join(selected_columns(info))} FROM {quoted(name)}"
    if primary_key is None:
        return connection.exec_driver_sql(query)
    if len(primary_key) != len(info["primary_key"]):
        raise TransferError("Seed key does not match the reviewed schema.")
    query += " WHERE " + " AND ".join(f"{quoted(column)} = :k{i}" for i, column in enumerate(info["primary_key"]))
    return connection.execute(sa.text(query), {f"k{i}": value for i, value in enumerate(primary_key)})


def journal_order(source, name, info):
    """Validate the one reviewed self-FK without loading journal payloads.

    Retain only IDs and parent links; full rows are read in batches of 250 later.
    Reject cycles/missing parents before any destination write, even in dry run.
    """
    if not reviewed_journal_reference(name, info):
        raise TransferError("Unreviewed row dependency ordering.")
    parents = {identifier: parent for identifier, parent in source.exec_driver_sql(
        'SELECT "id", "original_event_id" FROM "bounty_allowance_events"')}
    children = {}
    ready = []
    for identifier, parent in parents.items():
        if parent is None:
            ready.append(identifier)
        elif parent not in parents:
            raise TransferError("Bounty journal references a missing original event.")
        else:
            children.setdefault(parent, []).append(identifier)
    heapq.heapify(ready)
    order = []
    while ready:
        identifier = heapq.heappop(ready)
        order.append(identifier)
        for child in children.get(identifier, ()):
            heapq.heappush(ready, child)
    if len(order) != len(parents):
        raise TransferError("Bounty journal contains a cyclic original-event reference.")
    return order


def ordered_journal_rows(source, name, info, order):
    id_index = next(i for i, column in enumerate(info["columns"]) if column[0] == "id")
    for offset in range(0, len(order), 250):
        keys = order[offset:offset + 250]
        parameters = {f"k{i}": key for i, key in enumerate(keys)}
        query = f"SELECT {', '.join(selected_columns(info))} FROM {quoted(name)} WHERE id IN (" + ",".join(":"+key for key in parameters) + ")"
        rows = {row[id_index]: row for row in source.execute(sa.text(query), parameters)}
        if set(rows) != set(keys):
            raise TransferError("Bounty journal changed within its read-only snapshot.")
        yield from (rows[key] for key in keys)


def checked_seed_rows(source, destination, schema):
    """Only the exact immutable migration seed may pre-exist in a fresh target."""
    if "bounty_policies" not in schema:
        return {}
    info = schema["bounty_policies"]
    if info["primary_key"] != ("id",) or {column for column, _, _ in info["columns"]} != set(INITIAL_BOUNTY_POLICY):
        raise TransferError("Bounty policy seed schema needs a reviewed transfer update.")
    expected = [normalized(json.dumps(INITIAL_BOUNTY_POLICY[column]) if kind[0] == "json"
                           else INITIAL_BOUNTY_POLICY[column], kind)
                for column, kind, _ in info["columns"]]
    key = (INITIAL_BOUNTY_POLICY["id"],)
    for connection in (source, destination):
        rows = list(table_rows(connection, "bounty_policies", info, primary_key=key))
        if len(rows) != 1 or [normalized(value, kind) for value, (_, kind, _) in zip(rows[0], info["columns"])] != expected:
            raise TransferError("The reviewed initial bounty policy must match exactly in source and destination.")
    if destination.exec_driver_sql('SELECT count(*) FROM "bounty_policies"').scalar_one() != 1:
        raise TransferError("Destination contains application data beyond its reviewed migration seed; no records were transferred.")
    return {"bounty_policies": {key}}


def fingerprint(connection, name, info):
    # Sort row digests in Python: database locale/collation cannot alter receipts.
    digests = []
    for row in table_rows(connection, name, info):
        payload = [normalized(value, kind) for value, (_, kind, _) in zip(row, info["columns"])]
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        digests.append(hashlib.sha256(encoded).digest())
    aggregate = hashlib.sha256()
    aggregate.update(json.dumps(info["columns"], separators=(",", ":")).encode())
    for digest in sorted(digests):
        aggregate.update(digest)
    return {"rows": len(digests), "sha256": aggregate.hexdigest()}


def verify_schema(connection, head, expected, label):
    if schema_signature(connection) != expected:
        raise TransferError(f"{label} schema does not exactly match the checkout's migrated schema.")
    versions = list(connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalars())
    if versions != [head]:
        raise TransferError(f"{label} migration head does not match the checkout.")


def target_engine(dsn, *, allow_local_test=False):
    try:
        url = make_url(dsn)
        overrides = {"host", "hostaddr", "port", "service", "servicefile", "dbname", "database", "options"}
        explicit = (url.drivername == "postgresql+psycopg" and url.host and url.database
                    and not any(char.isspace() or char in "/\\" for char in url.host)
                    and not overrides.intersection(url.query))
        local = url.host in ("127.0.0.1", "localhost", "::1")
        if not explicit or (url.query.get("sslmode") != "verify-full" and not (allow_local_test and local)):
            raise ValueError()
        return sa.create_engine(url, poolclass=NullPool, hide_parameters=True,
                                connect_args={"connect_timeout": 10})
    except Exception:
        raise TransferError("Use an explicit PostgreSQL/psycopg DSN with verified TLS; local rehearsal requires its explicit flag.") from None


def insert_table(source, destination, name, info, *, skip_primary_keys=(), row_order=None):
    columns = info["columns"]
    expressions = [f"CAST(:v{i} AS JSON)" if kind[0] == "json" else f":v{i}"
                   for i, (_, kind, _) in enumerate(columns)]
    statement = sa.text(f"INSERT INTO {quoted(name)} ({', '.join(quoted(c[0]) for c in columns)}) "
                        f"VALUES ({', '.join(expressions)})")
    batch = []
    key_indexes = [next(i for i, column in enumerate(columns) if column[0] == key) for key in info["primary_key"]]
    rows = ordered_journal_rows(source, name, info, row_order) if row_order is not None else table_rows(source, name, info)
    for row in rows:
        if tuple(row[i] for i in key_indexes) in skip_primary_keys:
            continue
        values = {f"v{i}": bool(value) if kind[0] == "boolean" and value is not None else value
                  for i, (value, (_, kind, _)) in enumerate(zip(row, columns))}
        batch.append(values)
        if len(batch) == 250:
            destination.execute(statement, batch)
            batch = []
    if batch:
        destination.execute(statement, batch)


def transfer(source_path, engine, *, apply=False, root=ROOT):
    head, expected = expected_schema(root)
    order = dependency_order(expected)
    with sqlite_snapshot(source_path) as source:
        verify_schema(source, head, expected, "Source")
        evidence = {name: fingerprint(source, name, expected[name]) for name in sorted(expected)}
        row_orders = {name: journal_order(source, name, expected[name]) for name in order
                      if reviewed_journal_reference(name, expected[name])}
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as destination:
            with destination.begin():
                if not apply:
                    destination.exec_driver_sql("SET TRANSACTION READ ONLY")
                destination.exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
                destination.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                destination.exec_driver_sql("SET LOCAL statement_timeout = '120s'")
                # Locks are acquired before checking emptiness. No transfer can
                # race application writers or silently overwrite their records.
                mode = "EXCLUSIVE" if apply else "ACCESS SHARE"
                tables = ', '.join('public.' + quoted(name) for name in sorted(expected))
                destination.exec_driver_sql(f"LOCK TABLE {tables} IN {mode} MODE NOWAIT")
                verify_schema(destination, head, expected, "Destination")
                seeds = checked_seed_rows(source, destination, expected)
                for name in order:
                    if name in seeds:
                        continue
                    if destination.exec_driver_sql(f"SELECT 1 FROM {quoted(name)} LIMIT 1").first():
                        raise TransferError("Destination contains application data; no records were transferred.")
                if apply:
                    for name in order:
                        insert_table(source, destination, name, expected[name],
                                     skip_primary_keys=seeds.get(name, ()), row_order=row_orders.get(name))
                    actual = {name: fingerprint(destination, name, expected[name]) for name in sorted(expected)}
                    if actual != evidence:
                        raise TransferError("Destination verification failed; the transfer was rolled back.")
                # Commit occurs only after every table's typed digest matches.
        return {"status": "transferred" if apply else "checked_no_writes", "schema_head": head,
                "tables": evidence, "application_rows": sum(evidence[name]["rows"] for name in order),
                "destination_verified": bool(apply)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Explicit SQLite database path (read-only backup).")
    parser.add_argument("--apply", action="store_true", help="Commit a verified copy into a fresh target containing only reviewed migration seeds.")
    parser.add_argument("--allow-local-test-db", action="store_true", help="Allow non-TLS loopback PostgreSQL rehearsal only.")
    args = parser.parse_args(argv)
    engine = None
    try:
        if not os.environ.get(TARGET_ENV):
            raise TransferError(f"Set {TARGET_ENV}; DATABASE_URL is never read by this tool.")
        engine = target_engine(os.environ[TARGET_ENV], allow_local_test=args.allow_local_test_db)
        print(json.dumps(transfer(args.source, engine, apply=args.apply), sort_keys=True))
        return 0
    except TransferError as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1
    except Exception:
        # Database exceptions can contain DSNs, hashes, query parameters or note
        # text. Never render their message/traceback into terminal logs.
        print(json.dumps({"status": "failed", "error": "Transfer failed; no partial transfer was committed. Check connectivity, locks and schema with trusted operator tools."}))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
