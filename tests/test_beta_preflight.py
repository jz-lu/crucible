"""Portable preflight checks; these do not claim real PostgreSQL validation."""
import base64
from dataclasses import replace
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from crucible.config import Settings
from scripts import beta_preflight as check

REVIEWER = "00000000-0000-0000-0000-000000000001"
ADMIN = "00000000-0000-0000-0000-000000000002"
SECRET = "private-preflight-test-value"


def runtime_pin(source, artifact, **extra):
    return {"source_sha256": source, "artifact_sha256": artifact, **extra}


@pytest.fixture
def settings():
    return Settings(database_url=f"postgresql+psycopg://app:{SECRET}@db.example.invalid/beta?sslmode=verify-full",
        secret=b"s" * 32, origins=("https://beta.example.invalid",), production=True,
        access_mode="beta", reviewer_ids=(REVIEWER,), access_admin_ids=(ADMIN,),
        beta_releases=("example@1",), mail_backend="smtp", mail_from="beta@example.invalid",
        smtp_host="smtp.example.invalid")


@pytest.fixture
def clean_environment(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith("CRUCIBLE_") or name == "DATABASE_URL":
            monkeypatch.delenv(name)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path / "must-not-be-created"))
    return tmp_path / "must-not-be-created"


def test_default_refuses_local_environment_without_loading_or_creating_state(clean_environment, monkeypatch, capsys):
    monkeypatch.setattr(check, "load_settings", lambda: pytest.fail("Local configuration must not load"))
    assert check.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["selected_checks_passed"] is False
    assert report["deployment_approval"] is False
    assert not clean_environment.exists()


def test_actual_production_config_is_read_without_mail_database_or_native_operations(clean_environment, monkeypatch, capsys, settings):
    for key, value in {
        "CRUCIBLE_ENV": "production", "CRUCIBLE_ACCESS_MODE": "beta",
        "CRUCIBLE_ORIGIN": settings.origins[0], "DATABASE_URL": settings.database_url,
        "CRUCIBLE_SECRET": base64.b64encode(settings.secret).decode(),
        "CRUCIBLE_REVIEWER_IDS": REVIEWER, "CRUCIBLE_ACCESS_ADMIN_IDS": ADMIN,
        "CRUCIBLE_BETA_RELEASES": "example@1", "CRUCIBLE_MAIL_BACKEND": "smtp",
        "CRUCIBLE_MAIL_FROM": settings.mail_from, "CRUCIBLE_SMTP_HOST": settings.smtp_host,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(check, "inspect_runtime", lambda *_: pytest.fail("Runtime was not selected"))
    monkeypatch.setattr(check, "database_checks", lambda *_: pytest.fail("Database was not selected"))
    assert check.main([]) == 0
    raw = capsys.readouterr().out
    report = json.loads(raw)
    assert SECRET not in raw and settings.database_url not in raw
    assert {item["check"] for item in report["checks"] if item["status"] == "not_checked"} == {"runtime_registry", "database"}
    assert report["still_required"] and report["deployment_approval"] is False
    assert not clean_environment.exists()


@pytest.mark.parametrize("change,failed", [
    ({"database_url": "postgresql+psycopg://app@db.invalid/beta?sslmode=require"}, "database_transport"),
    ({"database_url": "sqlite:///must-not-be-created.db"}, "database_transport"),
    ({"mail_backend": "disabled"}, "invitation_mail"),
    ({"reviewer_ids": ()}, "reviewers"),
    ({"beta_releases": ()}, "release_allowlist"),
])
def test_missing_launch_configuration_fails_without_echoing_values(settings, change, failed):
    rows = check.configuration_checks(replace(settings, **change))
    assert next(row for row in rows if row["check"] == failed)["status"] == "fail"
    assert SECRET not in json.dumps(rows)


def test_failures_redact_exception_messages(clean_environment, settings, monkeypatch, capsys):
    monkeypatch.setenv("CRUCIBLE_ENV", "production")
    def fail(*_):
        raise RuntimeError(settings.database_url + SECRET)
    monkeypatch.setattr(check, "load_settings", fail)
    assert check.main(["--runtime", "--database"]) == 1
    assert SECRET not in capsys.readouterr().out
    monkeypatch.setattr(check, "load_settings", lambda: settings)
    monkeypatch.setattr(check, "inspect_runtime", fail)
    monkeypatch.setattr(check, "database_checks", fail)
    assert check.main(["--runtime", "--database"]) == 1
    raw = capsys.readouterr().out
    assert SECRET not in raw
    assert sum(row["status"] == "fail" for row in json.loads(raw)["checks"]) == 2


def test_runtime_validates_archive_and_allowlist_without_generation(monkeypatch):
    import crucible
    calls = []
    adapter = SimpleNamespace(manifests=lambda: [{"implementation": {"source_sha256": "source", "artifact_sha256": "artifact"}}])
    fake = SimpleNamespace(ADAPTERS={("example", "1"): adapter, ("archive", "0"): adapter},
                           CURRENT={"example": "1"},
                           validate_registry=lambda: calls.append("validate"))
    monkeypatch.setattr(crucible, "registry", fake, raising=False)
    rows, pins = check.inspect_runtime(["missing@1"])
    assert rows[0]["status"] == "fail" and pins is None and not calls
    rows, pins = check.inspect_runtime(["archive@0"])
    assert rows[0]["status"] == "fail" and pins is None and not calls
    rows, pins = check.inspect_runtime(["example@1"])
    assert rows[0]["status"] == "pass" and calls == ["validate"]
    assert pins == {("example", "1"): runtime_pin("source", "artifact"),
                    ("archive", "0"): runtime_pin("source", "artifact")}


def test_runtime_retains_platform_admission_and_rejects_profile_disagreement(monkeypatch):
    import crucible
    from test_implementation_builds import CANDIDATE, admission
    implementation = runtime_pin(*CANDIDATE, admission=admission())
    manifests = [{"implementation": implementation}, {"implementation": dict(implementation)}]
    adapter = SimpleNamespace(manifests=lambda: manifests)
    fake = SimpleNamespace(ADAPTERS={("example", "1"): adapter}, CURRENT={"example": "1"},
                           validate_registry=lambda: None)
    monkeypatch.setattr(crucible, "registry", fake, raising=False)
    rows, pins = check.inspect_runtime(["example@1"])
    assert rows[0]["status"] == "pass"
    assert pins[("example", "1")] == implementation
    assert pins[("example", "1")]["admission"] is not implementation["admission"]
    manifests[1] = {"implementation": runtime_pin(*CANDIDATE)}
    with pytest.raises(ValueError, match="Inconsistent release"):
        check.inspect_runtime(["example@1"])
    manifests[:] = [{"implementation": runtime_pin(*CANDIDATE, admission=admission(format="unknown"))}]
    with pytest.raises(ValueError, match="Unsupported platform admission"):
        check.inspect_runtime(["example@1"])


@pytest.fixture
def platform_database(tmp_path):
    from crucible.db import Base, Database
    db = Database(f"sqlite:///{tmp_path / 'platform-preflight.db'}")
    Base.metadata.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('fixture')"))
    yield db
    db.engine.dispose()


@pytest.mark.parametrize("unrelated_baseline", [False, True])
def test_preflight_checks_additional_candidates_absent_from_saved_table(settings, platform_database, unrelated_baseline):
    from crucible.db import AdapterVersion
    from test_implementation_builds import CANDIDATE, ORIGINAL, admission
    db = platform_database
    pins = {("new-kem", "v1"): runtime_pin(*CANDIDATE, admission=admission())}
    if unrelated_baseline:
        with db.transaction(write=True) as session:
            session.add(AdapterVersion(scheme_id="old-kem", version="v1", source_sha256=ORIGINAL[0],
                                       artifact_sha256=ORIGINAL[1], created_at=1))
        pins[("old-kem", "v1")] = runtime_pin(*ORIGINAL)
    configured = replace(settings, reviewer_ids=(), access_admin_ids=())
    with db.engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM adapter_versions")).all()
        rows = check.inspect_database(connection, configured, ["fixture"], pins)
        assert next(row for row in rows if row["check"] == "historical_implementation_pins")["status"] == "fail"
        assert connection.execute(text("SELECT * FROM adapter_versions")).all() == before
        assert connection.execute(text("SELECT COUNT(*) FROM implementation_builds")).scalar_one() == 0
        # A genuinely original release with no history retains the old empty
        # fixture policy; preflight still does not establish a saved baseline.
        pins[("new-kem", "v1")] = runtime_pin(*CANDIDATE)
        rows = check.inspect_database(connection, configured, ["fixture"], pins)
        assert all(row["status"] == "pass" for row in rows)
        assert connection.execute(text("SELECT * FROM adapter_versions")).all() == before


@pytest.mark.parametrize("marker", [None, {"format": "unknown"}, "additional-platform"])
def test_preflight_unknown_marker_fails_even_with_empty_history(settings, platform_database, marker):
    from test_implementation_builds import ORIGINAL
    with platform_database.engine.connect() as connection:
        rows = check.inspect_database(connection, replace(settings, reviewer_ids=(), access_admin_ids=()),
            ["fixture"], {("fixture-kem", "v1"): runtime_pin(*ORIGINAL, admission=marker)})
        assert next(row for row in rows if row["check"] == "historical_implementation_pins")["status"] == "fail"
        assert connection.execute(text("SELECT COUNT(*) FROM adapter_versions")).scalar_one() == 0


def test_preflight_additional_marker_does_not_take_matching_original_pair_shortcut(settings, platform_database):
    from crucible.db import AdapterVersion
    from test_implementation_builds import ORIGINAL, admission
    with platform_database.transaction(write=True) as session:
        session.add(AdapterVersion(scheme_id="fixture-kem", version="v1", source_sha256=ORIGINAL[0],
                                   artifact_sha256=ORIGINAL[1], created_at=1))
    with platform_database.engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM adapter_versions")).all()
        rows = check.inspect_database(connection, replace(settings, reviewer_ids=(), access_admin_ids=()),
            ["fixture"], {("fixture-kem", "v1"): runtime_pin(*ORIGINAL, admission=admission())})
        assert next(row for row in rows if row["check"] == "historical_implementation_pins")["status"] == "fail"
        assert connection.execute(text("SELECT * FROM adapter_versions")).all() == before


def test_database_reports_schema_membership_and_platform_conflicts(settings):
    # Real SQL over a synthetic SQLite fixture exercises joins/comparison only.
    # Production transport/read-only enforcement is a separate boundary below.
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE alembic_version (version_num TEXT)",
            "INSERT INTO alembic_version VALUES ('0019')",
            "CREATE TABLE users (id TEXT PRIMARY KEY)",
            "CREATE TABLE beta_memberships (user_id TEXT, status TEXT)",
            "CREATE TABLE account_states (user_id TEXT, suspended_at REAL)",
            "CREATE TABLE adapter_versions (scheme_id TEXT, version TEXT, source_sha256 TEXT, artifact_sha256 TEXT)",
            "CREATE TABLE challenges (scheme_id TEXT, scheme_version TEXT, manifest TEXT)",
            "INSERT INTO adapter_versions VALUES ('example', '1', 'source', 'mac-artifact')",
        ):
            connection.execute(text(statement))
        for identifier in (REVIEWER, ADMIN):
            connection.execute(text("INSERT INTO users VALUES (:id)"), {"id": identifier})
            connection.execute(text("INSERT INTO beta_memberships VALUES (:id, 'active')"), {"id": identifier})
        before = connection.execute(text("SELECT * FROM adapter_versions")).all()
        rows = check.inspect_database(connection, settings, ["different-head"], None)
        assert len(rows) == 1 and rows[0]["status"] == "fail"
        pins = {("example", "1"): runtime_pin("source", "linux-artifact")}
        rows = check.inspect_database(connection, settings, ["0019"], pins)
        assert [row["check"] for row in rows if row["status"] == "fail"] == ["historical_implementation_pins"]
        connection.execute(text("INSERT INTO account_states VALUES (:id, 1)"), {"id": REVIEWER})
        connection.execute(text("UPDATE beta_memberships SET status = 'revoked' WHERE user_id = :id"), {"id": ADMIN})
        pins[("example", "1")] = runtime_pin("source", "mac-artifact")
        rows = check.inspect_database(connection, settings, ["0019"], pins)
        assert {row["check"] for row in rows if row["status"] == "fail"} == {"reviewer_membership", "administrator_membership"}
        assert connection.execute(text("SELECT * FROM adapter_versions")).all() == before
        assert check.inspect_database(connection, settings, ["0019"], None)[-1]["status"] == "not_checked"
        connection.execute(text("INSERT INTO challenges VALUES ('legacy', '0', :manifest)"), {
            "manifest": json.dumps({"implementation": {"source_sha256": "old-source", "artifact_sha256": "old-artifact"}})})
        pins[("legacy", "0")] = runtime_pin("old-source", "new-artifact")
        rows = check.inspect_database(connection, settings, ["0019"], pins)
        assert rows[-1]["check"] == "legacy_challenge_implementations" and rows[-1]["status"] == "fail"
        pins[("legacy", "0")] = runtime_pin("old-source", "old-artifact")
        assert check.inspect_database(connection, settings, ["0019"], pins)[-1]["status"] == "pass"
    engine.dispose()


def test_database_connection_is_opt_in_bounded_read_only_and_rolled_back(settings, monkeypatch):
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_): calls.append("closed")
        def begin(self): return SimpleNamespace(rollback=lambda: calls.append("rollback"))
        def execute(self, query): calls.append(str(query))
    engine = SimpleNamespace(connect=lambda: Connection(), dispose=lambda: calls.append("dispose"))
    def create(url, **kwargs):
        assert url.query["sslmode"] == "verify-full"
        assert kwargs["connect_args"]["connect_timeout"] == 5
        options = kwargs["connect_args"]["options"]
        assert "default_transaction_read_only=on" in options and "statement_timeout=5000" in options
        return engine
    monkeypatch.setattr(check, "create_engine", create)
    def inspect(connection, configured, heads, pins):
        assert "SET TRANSACTION READ ONLY" in calls and heads
        raise RuntimeError("simulated query failure")
    monkeypatch.setattr(check, "inspect_database", inspect)
    with pytest.raises(RuntimeError):
        check.database_checks(settings, None)
    assert calls == ["SET TRANSACTION READ ONLY", "rollback", "closed", "dispose"]


@pytest.mark.parametrize("review_status,expected", [("unregistered", "fail"), ("reviewed", "pass"), ("revoked", "fail")])
@pytest.mark.parametrize("marked", [False, True])
def test_database_checks_reviewed_builds_without_finishing_outer_transaction(settings, tmp_path, review_status, expected, marked):
    from crucible import implementation_builds as builds
    from crucible.db import AdapterVersion, Base, Database
    from test_implementation_builds import CANDIDATE, ORIGINAL, admission, encoded

    db = Database(f"sqlite:///{tmp_path / 'preflight-builds.db'}")
    Base.metadata.create_all(db.engine)
    with db.engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0020')"))
    with db.transaction(write=True) as session:
        session.add(AdapterVersion(scheme_id="fixture-kem", version="v1", source_sha256=ORIGINAL[0],
                                   artifact_sha256=ORIGINAL[1], created_at=1))
        session.flush()
        if review_status != "unregistered":
            raw, expected_digest = encoded()
            reviewed = builds.register_build(session, raw, expected_sha256=expected_digest,
                reviewed_by="Fixture operator", review_basis="Synthetic unchanged-release review", now=1000)
            if review_status == "revoked":
                builds.revoke_build(session, reviewed.id, reason="Synthetic finding", now=1100)
    try:
        with db.engine.connect() as connection:
            outer = connection.begin()
            # A deliberately uncommitted caller write proves both boundaries:
            # inspect_database must neither commit nor roll back its caller.
            connection.execute(text("INSERT INTO users VALUES ('sentinel', 'sentinel', 'fixture', 1)"))
            before = connection.execute(text("SELECT * FROM adapter_versions")).all()
            implementation = runtime_pin(*CANDIDATE, **({"admission": admission()} if marked else {}))
            rows = check.inspect_database(connection, replace(settings, reviewer_ids=(), access_admin_ids=()),
                                          ["0020"], {("fixture-kem", "v1"): implementation})
            assert next(row for row in rows if row["check"] == "historical_implementation_pins")["status"] == expected
            assert outer.is_active and connection.in_transaction()
            assert connection.execute(text("SELECT id FROM users WHERE id='sentinel'")).scalar_one() == "sentinel"
            assert connection.execute(text("SELECT * FROM adapter_versions")).all() == before
            outer.rollback()
        with db.engine.connect() as connection:
            assert connection.execute(text("SELECT id FROM users WHERE id='sentinel'")).first() is None
    finally:
        db.engine.dispose()


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://db/beta",
    "postgresql+psycopg:///beta?sslmode=verify-full",
    "postgresql+psycopg://db/beta?sslmode=verify-full&host=/var/run/postgresql",
    "postgresql+psycopg://db/beta?sslmode=verify-full&service=local",
    "postgresql+psycopg://db/beta?sslmode=verify-full&hostaddr=127.0.0.1",
])
def test_database_never_connects_with_unverified_transport(settings, monkeypatch, url):
    monkeypatch.setattr(check, "create_engine", lambda *_args, **_kwargs: pytest.fail("Must not connect"))
    rows = check.database_checks(replace(settings, database_url=url), None)
    assert rows[0]["status"] == "fail"
