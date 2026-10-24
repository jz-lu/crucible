"""Initialize missing original release anchors before a platform cutover.

Default: a read-only plan against an explicitly configured PostgreSQL target.
Apply only during maintenance, after transfer verification. Exact reviewed
original-runtime evidence is required; no adapter is imported or executed.
Existing pins/history remain immutable. This never admits additional builds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

TARGET_ENV = "CRUCIBLE_BASELINE_DATABASE_URL"
FORMAT = "crucible-original-runtime-evidence-v1"
MAX_EVIDENCE_BYTES = 16 * 1024**2
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class BaselineError(ValueError):
    """Payload-free operator error."""


class CommitUncertain(BaselineError):
    pass


def unique_object(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise BaselineError("Duplicate JSON fields are not allowed.")
        value[name] = item
    return value


def reject_constant(_):
    raise BaselineError("Nonfinite JSON values are not allowed.")


def identifier(value, maximum):
    if type(value) is not str or len(value) > maximum or not IDENTIFIER.fullmatch(value):
        raise BaselineError("Invalid release identity.")
    return value


def pair(value):
    if type(value) is not dict:
        raise BaselineError("Missing original implementation provenance.")
    source, artifact = value.get("source_sha256"), value.get("artifact_sha256")
    if (type(source) is not str or not DIGEST.fullmatch(source)
            or (artifact is not None and (type(artifact) is not str or not DIGEST.fullmatch(artifact)))):
        raise BaselineError("Invalid original implementation digest.")
    return source, artifact


def parse_evidence(raw, expected_sha256):
    if (type(expected_sha256) is not str or not DIGEST.fullmatch(expected_sha256)
            or type(raw) is not bytes or not 0 < len(raw) <= MAX_EVIDENCE_BYTES
            or hashlib.sha256(raw).hexdigest() != expected_sha256):
        raise BaselineError("Original evidence bytes do not match the reviewed digest or size limit.")
    try:
        evidence = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise BaselineError("Invalid original-runtime JSON evidence.") from None
    if (type(evidence) is not dict or set(evidence) != {"format", "platform", "releases", "database_read", "database_admission"}
            or evidence["format"] != FORMAT or evidence["database_read"] is not False
            or evidence["database_admission"] is not False):
        raise BaselineError("Unsupported original-runtime evidence declaration.")
    platform = evidence["platform"]
    if type(platform) is not dict or set(platform) != {"system", "machine", "python"}:
        raise BaselineError("Invalid original-runtime platform declaration.")
    for key in platform:
        identifier(platform[key], 100)
    releases = evidence["releases"]
    if type(releases) is not list or not 1 <= len(releases) <= 256:
        raise BaselineError("Original evidence needs a bounded release inventory.")
    result = {}
    for release in releases:
        if type(release) is not dict or set(release) != {"scheme_id", "version", "source_sha256", "artifact_sha256", "manifests"}:
            raise BaselineError("Invalid original release fields.")
        identity = identifier(release["scheme_id"], 100), identifier(release["version"], 32)
        original = pair(release)
        profiles = release["manifests"]
        if identity in result or type(profiles) is not list or not 1 <= len(profiles) <= 10000:
            raise BaselineError("Duplicate, empty or oversized original release inventory.")
        profile_ids = set()
        for manifest in profiles:
            if (type(manifest) is not dict or (manifest.get("scheme_id"), manifest.get("version")) != identity
                    or pair(manifest.get("implementation")) != original
                    or "admission" in manifest["implementation"]):
                raise BaselineError("Original profiles must agree and cannot declare additional-platform admission.")
            profile = identifier(manifest.get("id"), 250)
            if profile in profile_ids:
                raise BaselineError("Duplicate profile within an original release.")
            profile_ids.add(profile)
        result[identity] = {"source_sha256": original[0], "artifact_sha256": original[1], "profiles": len(profiles)}
    return result


def select_releases(evidence, requested):
    if not requested:
        return sorted(evidence)
    selected = []
    for value in requested:
        parts = value.split("@")
        if len(parts) != 2:
            raise BaselineError("Select releases as scheme_id@version.")
        identity = identifier(parts[0], 100), identifier(parts[1], 32)
        if identity not in evidence or identity in selected:
            raise BaselineError("Selected releases must be present and distinct.")
        selected.append(identity)
    return sorted(selected)


def plan(connection, evidence, selected):
    rows = []
    for scheme, version in selected:
        original = evidence[(scheme, version)]
        parameters = {"scheme": scheme, "version": version}
        existing = connection.execute(sa.text("SELECT source_sha256, artifact_sha256, created_at FROM adapter_versions "
            "WHERE scheme_id=:scheme AND version=:version"), parameters).mappings().first()
        if existing is not None and pair(dict(existing)) != pair(original):
            raise BaselineError("An existing baseline conflicts with the reviewed original evidence; nothing was changed.")
        history_count = 0
        if existing is None:
            history = connection.execute(sa.text("SELECT manifest FROM challenges WHERE scheme_id=:scheme "
                "AND scheme_version=:version"), parameters).yield_per(100)
            try:
                for (manifest,) in history:
                    try:
                        if isinstance(manifest, str):
                            manifest = json.loads(manifest, object_pairs_hook=unique_object, parse_constant=reject_constant)
                        implementation = manifest.get("implementation") if type(manifest) is dict else None
                        if pair(implementation) != pair(original) or "admission" in implementation:
                            raise BaselineError("Conflicting historical implementation.")
                    except (ValueError, TypeError, RecursionError):
                        raise BaselineError("Historical challenges have missing or conflicting provenance; nothing was changed.") from None
                    history_count += 1
            finally:
                history.close()
        rows.append({"scheme_id": scheme, "version": version, "action": "preserve" if existing else "initialize",
                     **original, "created_at": existing["created_at"] if existing else None,
                     "historical_challenges_checked": history_count})
    return rows


def operate(engine, evidence, selected, *, apply=False, now=None, before_commit=None):
    """Only synthetic tests use SQLite; CLI targets PostgreSQL, with no schema creation."""
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise BaselineError("A finite operation timestamp is required.")
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if not sqlite and connection.dialect.name != "postgresql":
            raise BaselineError("Unsupported baseline target database.")
        old_query_only = None
        commit_attempted = False
        try:
            if sqlite:
                old_query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
                connection.commit()
                if not apply:
                    connection.exec_driver_sql("PRAGMA query_only=ON")
                    connection.commit()
                connection.exec_driver_sql("BEGIN IMMEDIATE" if apply else "BEGIN")
            else:
                connection.execution_options(isolation_level="REPEATABLE READ")
                connection.begin()
                if not apply:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL search_path TO public, pg_catalog")
                connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
                if apply:
                    connection.exec_driver_sql("LOCK TABLE public.adapter_versions, public.challenges IN EXCLUSIVE MODE NOWAIT")
            rows = plan(connection, evidence, selected)
            if apply:
                for row in rows:
                    if row["action"] == "initialize":
                        connection.execute(sa.text("INSERT INTO adapter_versions "
                            "(scheme_id,version,source_sha256,artifact_sha256,created_at) "
                            "VALUES (:scheme_id,:version,:source_sha256,:artifact_sha256,:created_at)"), {**row, "created_at": now})
                        row["created_at"] = now
                result = {"status": "commit_pending", "releases": rows, "timestamp": now}
                if before_commit is not None:
                    before_commit(result)
                commit_attempted = True
                try:
                    connection.commit()
                except BaseException as error:
                    raise CommitUncertain("Commit outcome needs verification; do not infer rollback or retry blindly.") from error
                result["status"] = "committed"
            else:
                connection.rollback()
                result = {"status": "checked_no_writes", "releases": rows, "timestamp": now}
            return result
        finally:
            try:
                connection.rollback()
                if sqlite and old_query_only is not None:
                    connection.exec_driver_sql(f"PRAGMA query_only={int(old_query_only)}")
            except BaseException as error:
                if commit_attempted:
                    raise CommitUncertain("Commit outcome needs verification after connection cleanup failed.") from error
                raise


def receipt_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write_receipt(path, value, *, create=False):
    """Exclusive initial receipt; atomic updates retain pending evidence on failure."""
    target = path if create else path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    created = False
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as output:
            output.write(receipt_bytes(value))
            output.flush()
            os.fsync(output.fileno())
        if not create:
            os.replace(target, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if not create and created and target.exists():
            target.unlink()


def target_engine(url, *, allow_local_test=False):
    try:
        parsed = make_url(url)
        # Match preflight/transfer's explicit target requirement and the backup
        # job's small TLS query allowance. Query host/service/dbname/options or
        # credential overrides must never replace the operator-reviewed URL.
        if (parsed.drivername != "postgresql+psycopg" or not parsed.host or not parsed.database
                or any(char.isspace() or char in "/\\," or ord(char) < 32 or ord(char) == 127 for char in parsed.host)
                or parsed.database != parsed.database.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in parsed.database)
                or set(parsed.query) - {"sslmode", "sslrootcert"}
                or any(type(value) is not str or not value or any(ord(char) < 32 or ord(char) == 127 for char in value)
                       for value in parsed.query.values())):
            raise ValueError()
        port = parsed.port if parsed.port is not None else 5432
        if not 1 <= port <= 65535:
            raise ValueError()
        if "sslrootcert" in parsed.query and not Path(parsed.query["sslrootcert"]).is_absolute():
            raise ValueError()
        local = parsed.host in {"127.0.0.1", "localhost", "::1"}
        if parsed.query.get("sslmode") != "verify-full" and not (allow_local_test and local):
            raise ValueError()
        return sa.create_engine(parsed, poolclass=NullPool, hide_parameters=True, connect_args={"connect_timeout": 10})
    except Exception:
        raise BaselineError("Use an explicit PostgreSQL/psycopg host and database with verified TLS and no connection overrides; loopback fixtures need --allow-local-test-db.") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--evidence-sha256", required=True)
    parser.add_argument("--release", action="append", help="Select scheme_id@version; default is all evidence releases")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewed-by")
    parser.add_argument("--review-basis")
    parser.add_argument("--receipt", type=Path, help="New private operator receipt; required for apply")
    parser.add_argument("--allow-local-test-db", action="store_true")
    args = parser.parse_args(argv)
    engine = None
    receipt = None
    receipt_created = False
    committed = False
    try:
        with args.evidence.open("rb") as source:
            evidence = parse_evidence(source.read(MAX_EVIDENCE_BYTES + 1), args.evidence_sha256)
        selected = select_releases(evidence, args.release)
        url = os.environ.get(TARGET_ENV)
        if not url:
            raise BaselineError(f"Set {TARGET_ENV} explicitly; no development database fallback is available.")
        if args.apply:
            if (args.receipt is None or not args.reviewed_by or len(args.reviewed_by) > 200
                    or not args.reviewed_by.strip() or not args.review_basis or not args.review_basis.strip()
                    or len(args.review_basis) > 10000 or "\x00" in args.reviewed_by + args.review_basis):
                raise BaselineError("Apply requires a new receipt path, operator identity and review basis.")
            receipt = {"format": "crucible-original-baseline-operation-v1", "operation_id": uuid.uuid4().hex,
                       "evidence_sha256": args.evidence_sha256, "selected_releases": [list(x) for x in selected],
                       "reviewed_by": args.reviewed_by, "review_basis": args.review_basis,
                       "status": "pending", "requested_at": time.time()}
            write_receipt(args.receipt, receipt, create=True)
            receipt_created = True
        engine = target_engine(url, allow_local_test=args.allow_local_test_db)

        def before_commit(result):
            receipt.update(result)
            write_receipt(args.receipt, receipt)

        result = operate(engine, evidence, selected, apply=args.apply, now=time.time(),
                         before_commit=before_commit if args.apply else None)
        committed = args.apply
        if receipt_created:
            receipt.update(result)
            write_receipt(args.receipt, receipt)
        print(json.dumps({"evidence_sha256": args.evidence_sha256, **result}, indent=2))
        return 0
    except (BaselineError, OSError, SQLAlchemyError) as error:
        uncertain = committed or isinstance(error, CommitUncertain)
        if receipt_created:
            receipt["status"] = "verification_required" if uncertain else "not_applied"
            try:
                write_receipt(args.receipt, receipt)
            except OSError:
                pass
        if uncertain:
            print("Database commit completed or its outcome is uncertain, but verification is incomplete. Retain the pending receipt and run a read-only plan before any retry.", file=sys.stderr)
            return 2
        print(str(error) if isinstance(error, BaselineError) else
              "Baseline operation failed before commit; check evidence, receipt path, database schema and operator permissions.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
