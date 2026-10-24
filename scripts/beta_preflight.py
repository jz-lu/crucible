"""Read-only checks for an explicitly configured hosted beta.

Never starts the app, generates a challenge, changes schema, sends mail, or
prints credentials. Passing these checks is not a deployment certification.
Database access and native registry inspection are opt-in.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from crucible.config import load_settings
from crucible import implementation_builds

ROOT = Path(__file__).resolve().parents[1]


def result(name, status, message):
    return {"check": name, "status": status, "message": message}


def verified_network_database(url):
    overrides = {"host", "hostaddr", "port", "service", "servicefile", "dbname", "database"}
    return bool(url.drivername == "postgresql+psycopg" and url.host and url.database
                and not any(character.isspace() or character in "/\\" for character in url.host)
                and not overrides.intersection(url.query) and url.query.get("sslmode") == "verify-full")


def configuration_checks(settings):
    checks = [result("private_production", "pass" if settings.production and
                     settings.access_mode == "beta" and not settings.allow_self_review else "fail",
                     "Hosted beta requires production mode, beta membership, and independent review.")]
    url = make_url(settings.database_url)
    checks.append(result("database_transport", "pass" if verified_network_database(url) else "fail",
        "Use PostgreSQL with psycopg, an explicit host/database, sslmode=verify-full, and the provider CA."))
    checks.append(result("invitation_mail", "pass" if settings.mail_backend == "smtp" else "fail",
                         "SMTP must be configured; actual invitation delivery still needs a test."))
    checks.append(result("reviewers", "pass" if settings.reviewer_ids else "fail",
                         "At least one reviewer account UUID must be configured explicitly."))
    if len(set(settings.reviewer_ids)) == 1:
        checks.append(result("independent_reviewer", "warn",
                             "One reviewer cannot approve their own work; add a second reviewer or leave it pending."))
    checks.append(result("access_operator", "pass" if settings.access_admin_ids else "warn",
                         "Configure an access administrator, or deliberately operate membership through the trusted host CLI."))
    checks.append(result("release_allowlist", "pass" if settings.beta_releases or settings.beta_issuance_disabled else "fail",
                         "New challenge issuance is intentionally disabled; existing records remain available."
                         if settings.beta_issuance_disabled else
                         "Reviewed scheme_id@version entries are required to issue hosted challenges."))
    return checks


def inspect_runtime(releases):
    # Import only trusted repository adapters. Validation reads their pinned
    # manifests/artifacts; it does not invoke challenge generation or builders.
    from crucible import registry
    unknown = set(releases) - {f"{scheme}@{version}" for scheme, version in registry.CURRENT.items()}
    if unknown:
        return [result("runtime_registry", "fail", "The beta allowlist contains an unregistered or archived release.")], None
    registry.validate_registry()
    pins = {}
    for identity, adapter in registry.ADAPTERS.items():
        manifests = adapter.manifests()
        if not manifests:
            raise ValueError("Empty adapter")
        implementations = {}
        for item in manifests:
            implementation_builds.additional_platform_admission(item)
            implementation = item["implementation"]
            pin = {"source_sha256": implementation["source_sha256"],
                   "artifact_sha256": implementation.get("artifact_sha256")}
            if "admission" in implementation:
                pin["admission"] = deepcopy(implementation["admission"])
            implementations[json.dumps(pin, sort_keys=True, separators=(",", ":"))] = pin
        if len(implementations) != 1:
            raise ValueError("Inconsistent release implementation")
        pins[identity] = next(iter(implementations.values()))
    return [result("runtime_registry", "pass", "All registered current and archived artifact manifests validated.")], pins


def inspect_database(connection, settings, heads, runtime_pins):
    """Called only inside a PostgreSQL READ ONLY transaction with timeouts."""
    actual = set(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if actual != set(heads):
        return [result("database_schema", "fail", "Database schema does not match this checkout's migration head.")]
    checks = [result("database_schema", "pass", "Database schema matches this checkout's migration head.")]
    for label, identifiers in (("reviewer_membership", settings.reviewer_ids),
                               ("administrator_membership", settings.access_admin_ids)):
        if not identifiers:
            continue
        allowed = all(connection.execute(text(
            "SELECT u.id FROM users u JOIN beta_memberships b ON b.user_id = u.id "
            "LEFT JOIN account_states a ON a.user_id = u.id "
            "WHERE u.id = :identifier AND b.status = 'active' AND a.suspended_at IS NULL"
        ), {"identifier": identifier}).first() is not None for identifier in identifiers)
        checks.append(result(label, "pass" if allowed else "fail",
                             "Every configured role needs an existing, active, unsuspended beta account."))
    if runtime_pins is None:
        checks.append(result("historical_implementation_pins", "not_checked",
                             "Also select --runtime to compare saved implementation identities with this installation."))
    else:
        stored = connection.execute(text(
            "SELECT scheme_id, version, source_sha256, artifact_sha256 FROM adapter_versions"
        )).mappings().all()
        # Bind only to the caller's already-open read-only transaction. Closing
        # this read-only Session must not commit or roll back that transaction;
        # rollback_only leaves its lifetime and final rollback with the caller.
        with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as session:
            mismatch = False
            additional, invalid = set(), set()
            for identity, implementation in runtime_pins.items():
                try:
                    platform = implementation_builds.additional_platform_admission({"implementation": implementation})
                except ValueError:
                    invalid.add(identity)
                    mismatch = True
                else:
                    if platform is not None:
                        additional.add(identity)
            stored_identities = {(row["scheme_id"], row["version"]) for row in stored}
            # A freshly built platform cannot pass merely because this table
            # is empty or its release has not previously issued a challenge.
            if additional - stored_identities:
                mismatch = True
            for row in stored:
                identity = row["scheme_id"], row["version"]
                if identity in invalid:
                    continue
                current = runtime_pins.get(identity)
                if (current is not None and identity not in additional
                        and (current.get("source_sha256"), current.get("artifact_sha256")) ==
                            (row["source_sha256"], row["artifact_sha256"])):
                    continue
                manifest = {"scheme_id": row["scheme_id"], "version": row["version"],
                            "implementation": current} if current is not None else None
                if not implementation_builds.implementation_is_compatible(session, manifest):
                    mismatch = True
                    break
        checks.append(result("historical_implementation_pins", "fail" if mismatch else "pass",
                             "Runtime implementations must match original pins or active build reviews; additional platforms always require an existing original pin and an exact platform review."))
        # The app also checks historical challenge manifests when their release
        # predates the adapter_versions table. Stream these instead of treating
        # absence of an adapter row as evidence of an empty release history.
        legacy = connection.execute(text(
            "SELECT c.scheme_id, c.scheme_version, c.manifest FROM challenges c "
            "WHERE NOT EXISTS (SELECT 1 FROM adapter_versions a "
            "WHERE a.scheme_id = c.scheme_id AND a.version = c.scheme_version)"
        ).execution_options(stream_results=True)).mappings()
        conflict = False
        try:
            for row in legacy:
                manifest = json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"]
                implementation = manifest.get("implementation", {})
                current = runtime_pins.get((row["scheme_id"], row["scheme_version"]))
                if not isinstance(current, dict) or (current.get("source_sha256"), current.get("artifact_sha256")) != (
                        implementation.get("source_sha256"), implementation.get("artifact_sha256")):
                    conflict = True
                    break
        finally:
            legacy.close()
        checks.append(result("legacy_challenge_implementations", "fail" if conflict else "pass",
                             "Challenge history without an adapter pin must retain compatible implementation provenance."))
    return checks


def database_checks(settings, runtime_pins):
    # A command-line DSN would leak through process arguments. Use only the
    # deployment environment. Never log the URL or a driver exception.
    url = make_url(settings.database_url)
    if not verified_network_database(url):
        return [result("database", "fail", "Database inspection requires verified PostgreSQL TLS.")]
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    engine = create_engine(url, poolclass=NullPool, connect_args={
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=3000",
    })
    try:
        with engine.connect() as connection:
            # Roll back even successful read checks. No DDL or write can run.
            transaction = connection.begin()
            try:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                return inspect_database(connection, settings, heads, runtime_pins)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true", help="Inspect all registered native artifacts; do not generate instances.")
    parser.add_argument("--database", action="store_true", help="Read schema, role membership and implementation pins using verified PostgreSQL TLS.")
    args = parser.parse_args(argv)
    checks, settings, pins = [], None, None
    # load_settings can create local state in development. Refuse before calling
    # it, so this command cannot generate a key or touch the development database.
    if os.environ.get("CRUCIBLE_ENV") != "production":
        checks.append(result("configuration", "fail", "Set an explicit production environment; local development was not accessed."))
    else:
        try:
            settings = load_settings()
            checks.extend(configuration_checks(settings))
        except Exception:
            settings = None
            checks.append(result("configuration", "fail", "Invalid production configuration. Check the deployment environment; values are redacted."))
    if settings is not None and args.runtime:
        try:
            found, pins = inspect_runtime(settings.beta_releases)
            checks.extend(found)
        except Exception:
            checks.append(result("runtime_registry", "fail", "Runtime artifact validation failed. Reproduce and review the exact build; do not bypass its pins."))
    elif not args.runtime:
        checks.append(result("runtime_registry", "not_checked", "Use --runtime on the prepared server installation."))
    if settings is not None and args.database:
        try:
            checks.extend(database_checks(settings, pins))
        except Exception:
            checks.append(result("database", "fail", "Database read checks failed. Check TLS, connectivity, permissions and schema; connection details are redacted."))
    elif not args.database:
        checks.append(result("database", "not_checked", "Use --database against the prepared beta database."))
    passed = not any(item["status"] == "fail" for item in checks)
    print(json.dumps({"format": "crucible-beta-preflight-v1", "selected_checks_passed": passed,
        "checks": checks, "deployment_approval": False,
        "still_required": ["Real email and account-recovery exercise", "Real PostgreSQL concurrency and migration tests",
                           "Verified backup restoration", "Hosted browser/agent access and workload tests",
                           "Independent review of newly built runtime artifacts"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
