"""Reviewed-build provenance tests use synthetic hashes and isolated databases."""
import hashlib
from copy import deepcopy
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select

from crucible import implementation_builds as builds
from crucible.db import AdapterVersion, Base, Challenge, Database, User


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


ORIGINAL = (digest("original fixture source"), digest("original fixture artifact"))
CANDIDATE = (digest("candidate fixture source"), digest("candidate fixture artifact"))


def pair(values):
    return {"source_sha256": values[0], "artifact_sha256": values[1]}


def document(**changes):
    result = {"format": builds.FORMAT, "scheme_id": "fixture-kem", "scheme_version": "v1",
        "baseline": pair(ORIGINAL), "implementation": pair(CANDIDATE),
        "platform": {"system": "Linux", "machine": "x86_64"},
        "build": {"recipe_sha256": digest("fixture recipe"), "toolchain": "Synthetic compiler, not an actual Linux build"},
        "evidence": [{"name": "Synthetic test evidence", "sha256": digest("fixture evidence")} ]}
    result.update(changes)
    return result


def encoded(value=None):
    raw = json.dumps(value if value is not None else document(), indent=2).encode() + b"\n"
    return raw, hashlib.sha256(raw).hexdigest()


def runtime(values=CANDIDATE, **changes):
    return {"scheme_id": "fixture-kem", "version": "v1", "implementation": pair(values), **changes}


def admission(**changes):
    return {"format": "crucible-platform-admission-v1", "kind": "reviewed-additional-platform",
            "platform": {"system": "Linux", "machine": "x86_64"}, **changes}


def marked_runtime(values=CANDIDATE, marker=None):
    manifest = runtime(values)
    manifest["implementation"]["admission"] = admission() if marker is None else deepcopy(marker)
    return manifest


def mark_adapter(adapter, monkeypatch, marker):
    original = adapter.get_manifest
    def get_manifest(profile_id):
        manifest = original(profile_id)
        manifest["implementation"]["admission"] = deepcopy(marker)
        return manifest
    monkeypatch.setattr(adapter, "get_manifest", get_manifest)


def register(session, raw=None, **changes):
    content, expected = encoded()
    if raw is not None:
        content, expected = raw, hashlib.sha256(raw).hexdigest()
    arguments = {"expected_sha256": expected, "reviewed_by": "Fixture operator",
                 "review_basis": "Synthetic unchanged-release review fixture", "now": 1000.0, **changes}
    return builds.register_build(session, content, **arguments)


@pytest.fixture
def db(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'builds.db'}")
    Base.metadata.create_all(db.engine)
    with db.transaction(write=True) as session:
        session.add(AdapterVersion(scheme_id="fixture-kem", version="v1",
                                   source_sha256=ORIGINAL[0], artifact_sha256=ORIGINAL[1], created_at=1.0))
        session.add(User(id="fixture-user", handle="fixture", password_hash="synthetic", created_at=1.0))
        session.flush()
        session.add(Challenge(id="old-challenge", user_id="fixture-user", scheme_id="fixture-kem",
            scheme_version="v1", profile_id="fixture-1", round_id=0, issued_at=1.0, starts_at=0,
            ends_at=3600, public_data={"opaque": "historical inputs"}, manifest=runtime(ORIGINAL),
            target_mac=digest("historical fixture MAC"), correctness_passed=True))
    yield db
    db.engine.dispose()


def snapshots(db):
    with db.engine.connect() as connection:
        return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
                for model in (AdapterVersion, Challenge, User)}


def test_observing_new_pair_never_trusts_it_or_changes_existing_evidence(db):
    before = snapshots(db)
    with db.transaction() as session:
        assert builds.implementation_is_compatible(session, runtime(ORIGINAL))
        assert not builds.implementation_is_compatible(session, runtime())
        assert builds.reviewed_build_for(session, runtime()) is None
        assert list(session.scalars(select(builds.ImplementationBuild))) == []
    assert snapshots(db) == before


def test_marked_platform_needs_exact_active_review_and_never_uses_original_shortcut(db):
    before = snapshots(db)
    with db.transaction(write=True) as session:
        assert not builds.implementation_is_compatible(session, marked_runtime(ORIGINAL))
        assert not builds.implementation_is_compatible(session, marked_runtime())
        review = register(session)
        assert builds.implementation_is_compatible(session, marked_runtime())
        assert not builds.implementation_is_compatible(session, marked_runtime(ORIGINAL))
        other_platform = admission(platform={"system": "Linux", "machine": "aarch64"})
        assert not builds.implementation_is_compatible(session, marked_runtime(marker=other_platform))
        assert builds.reviewed_build_for(session, marked_runtime(marker=other_platform)) is None
        builds.revoke_build(session, review.id, reason="Synthetic finding", now=1100)
        assert not builds.implementation_is_compatible(session, marked_runtime())
    assert snapshots(db) == before


@pytest.mark.parametrize("marker", [None, False, {}, {"format": "unknown"},
    admission(format="crucible-platform-admission-v2"), admission(kind="original"),
    admission(platform={"system": "Linux"}), admission(platform={"system": "Linux", "machine": ""}),
    admission(platform={"system": "Linux", "machine": "x86_64", "extra": True}), admission(extra=True)])
def test_unknown_platform_admission_fails_closed_even_for_matching_original(db, marker):
    manifest = runtime(ORIGINAL)
    manifest["implementation"]["admission"] = marker
    with pytest.raises(ValueError):
        builds.additional_platform_admission(manifest)
    with db.transaction() as session:
        assert not builds.implementation_is_compatible(session, manifest)
        assert builds.reviewed_build_for(session, manifest) is None


def test_review_exact_bytes_allows_only_its_pair_and_keeps_original_pin_and_history(db):
    before = snapshots(db)
    raw, expected = encoded()
    with db.transaction(write=True) as session:
        reviewed = register(session)
        assert reviewed.manifest_text.encode() == raw
        assert reviewed.manifest_sha256 == expected
        assert reviewed.baseline_source_sha256 == ORIGINAL[0]
        assert builds.implementation_is_compatible(session, runtime())
        assert builds.implementation_is_compatible(session, runtime(ORIGINAL))
        assert not builds.implementation_is_compatible(session, runtime((digest("changed source"), CANDIDATE[1])))
        assert not builds.implementation_is_compatible(session, runtime((CANDIDATE[0], digest("changed artifact"))))
        assert not builds.implementation_is_compatible(session, runtime(version="v2"))
        assert not builds.implementation_is_compatible(session, runtime(scheme_id="other-kem"))
    assert snapshots(db) == before


def test_additional_review_is_idempotent_and_cannot_overwrite_its_evidence(db):
    with db.transaction(write=True) as session:
        original = register(session)
        repeated = register(session, reviewed_by="Another operator", review_basis="Different commentary", now=5000)
        assert repeated.id == original.id
        assert repeated.reviewed_by == "Fixture operator" and repeated.reviewed_at == 1000
        changed, _ = encoded(document(platform={"system": "Linux", "machine": "aarch64"}))
        with pytest.raises(ValueError, match="different retained review"):
            register(session, changed)
        assert len(list(session.scalars(select(builds.ImplementationBuild)))) == 1


def test_registration_cannot_create_a_baseline_or_claim_another_baseline(db):
    with db.transaction(write=True) as session:
        missing, _ = encoded(document(scheme_version="v2"))
        with pytest.raises(ValueError, match="existing release baseline"):
            register(session, missing)
        assert session.get(AdapterVersion, ("fixture-kem", "v2")) is None
        wrong, _ = encoded(document(baseline=pair(CANDIDATE)))
        with pytest.raises(ValueError, match="different original"):
            register(session, wrong)
        unchanged, _ = encoded(document(implementation=pair(ORIGINAL)))
        with pytest.raises(ValueError, match="does not need"):
            register(session, unchanged)
        assert list(session.scalars(select(builds.ImplementationBuild))) == []


def test_multiple_explicitly_reviewed_pairs_can_coexist(db):
    third_pair = (digest("third source"), digest("third artifact"))
    with db.transaction(write=True) as session:
        first = register(session)
        other, _ = encoded(document(implementation=pair(third_pair)))
        second = register(session, other)
        assert first.id != second.id
        assert builds.implementation_is_compatible(session, runtime())
        assert builds.implementation_is_compatible(session, runtime(third_pair))
        assert len(list(session.scalars(select(builds.ImplementationBuild)))) == 2


def test_null_artifact_pairs_are_unambiguous(db):
    new = (digest("python only fixture source"), None)
    with db.transaction(write=True) as session:
        raw, _ = encoded(document(implementation=pair(new)))
        reviewed = register(session, raw)
        assert register(session, raw).id == reviewed.id
        assert builds.implementation_is_compatible(session, runtime(new))
        assert not builds.implementation_is_compatible(session, runtime((new[0], digest("new binary"))))
        assert len(list(session.scalars(select(builds.ImplementationBuild)))) == 1


def test_revocation_blocks_future_use_without_deleting_registration_or_old_challenges(db):
    before = snapshots(db)
    with db.transaction(write=True) as session:
        reviewed = register(session)
        original_manifest = reviewed.manifest_text
        builds.revoke_build(session, reviewed.id, reason="Fixture audit finding", now=1200)
        assert not builds.implementation_is_compatible(session, runtime())
        assert builds.implementation_is_compatible(session, runtime(ORIGINAL))
        assert reviewed.manifest_text == original_manifest and reviewed.reviewed_at == 1000
        with pytest.raises(ValueError, match="revoked"):
            register(session)
        builds.revoke_build(session, reviewed.id, reason="Another reason", now=1300)
        assert reviewed.revoked_at == 1200 and reviewed.revocation_reason == "Fixture audit finding"
    assert snapshots(db) == before


@pytest.mark.parametrize("field,value", [
    ("manifest_text", "{}"), ("manifest_sha256", digest("wrong manifest")),
    ("source_sha256", digest("wrong source")), ("artifact_sha256", digest("wrong artifact")),
    ("baseline_source_sha256", digest("wrong baseline")), ("version", "v2")])
def test_corrupt_registration_never_matches(db, field, value):
    with db.transaction(write=True) as session:
        reviewed = register(session)
        setattr(reviewed, field, value)
        # Do not flush the intentionally corrupt fixture (version violates FK).
        with session.no_autoflush:
            assert not builds.implementation_is_compatible(session, runtime())
        session.rollback()


def test_changed_database_baseline_invalidates_additional_approval(db):
    with db.transaction(write=True) as session:
        register(session)
        baseline = session.get(AdapterVersion, ("fixture-kem", "v1"))
        baseline.source_sha256 = digest("changed baseline")
        assert not builds.implementation_is_compatible(session, runtime())
        session.rollback()


def test_exact_bytes_digest_and_duplicate_key_validation():
    raw, expected = encoded()
    assert builds.parse_manifest(raw, expected) == document()
    with pytest.raises(ValueError, match="does not match"):
        builds.parse_manifest(raw + b" ", expected)
    duplicate = raw.replace(b'"format":', b'"format":"duplicate","format":', 1)
    with pytest.raises(ValueError, match="Duplicate JSON"):
        builds.parse_manifest(duplicate, hashlib.sha256(duplicate).hexdigest())


@pytest.mark.parametrize("changes", [
    {"format": "other"}, {"scheme_id": "../x"}, {"scheme_version": "a" * 33},
    {"implementation": {"source_sha256": "invalid", "artifact_sha256": None}},
    {"implementation": {"source_sha256": CANDIDATE[0]}},
    {"platform": {"system": "Linux"}}, {"platform": {"system": "", "machine": "x86_64"}},
    {"build": {"recipe_sha256": "bad", "toolchain": "fixture"}}, {"evidence": []},
    {"evidence": [{"name": "fixture", "sha256": "bad"}]}, {"unexpected": True},
])
def test_incomplete_or_ambiguous_build_metadata_is_rejected(changes):
    raw, expected = encoded(document(**changes))
    with pytest.raises(ValueError):
        builds.parse_manifest(raw, expected)


@pytest.mark.parametrize("manifest", [None, {}, {"scheme_id": []},
    {"scheme_id": "fixture-kem", "version": "v1", "implementation": []},
    runtime(("invalid", None))])
def test_invalid_runtime_manifest_fails_closed(db, manifest):
    with db.transaction() as session:
        assert not builds.implementation_is_compatible(session, manifest)
        assert builds.reviewed_build_for(session, manifest) is None


def test_operator_cli_validates_without_database_and_registers_only_explicit_review(db, tmp_path, monkeypatch, capsys):
    raw, expected = encoded()
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    common = [str(path), "--manifest-sha256", expected]
    assert builds.main(["validate", *common]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["structure_valid"] and output["build_approved"] is False
    with pytest.raises(SystemExit):
        builds.main(["register", *common])
    capsys.readouterr()
    approval = ["register", *common, "--reviewed-by", "Fixture operator", "--review-basis", "Synthetic review"]
    assert builds.main(approval) == 1
    assert "DATABASE_URL explicitly" in capsys.readouterr().err
    monkeypatch.setenv("DATABASE_URL", str(db.engine.url))
    assert builds.main(approval) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["status"] == "reviewed"
    assert builds.main(["list"]) == 0
    assert json.loads(capsys.readouterr().out) == [created]
    assert builds.main(["revoke", created["build_id"], "--reason", "Fixture finding"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "revoked"


def test_migration_adds_empty_ledger_and_preserves_old_rows(tmp_path, monkeypatch):
    # Full Alembic chain, a synthetic DB, no developer credentials or data.
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migration.db'}")
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_MAIL_BACKEND", "disabled")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0019")
    db = Database(os.environ["DATABASE_URL"])
    try:
        with db.transaction(write=True) as session:
            session.add(User(id="old-user", handle="retained", password_hash="retained hash", created_at=1))
            session.add(AdapterVersion(scheme_id="fixture-kem", version="v1",
                source_sha256=ORIGINAL[0], artifact_sha256=ORIGINAL[1], created_at=1))
            session.flush()
            session.add(Challenge(id="old-challenge", user_id="old-user", scheme_id="fixture-kem",
                scheme_version="v1", profile_id="fixture-1", round_id=0, issued_at=1, starts_at=0,
                ends_at=3600, public_data={"old": "unchanged"}, manifest=runtime(ORIGINAL),
                target_mac=digest("old MAC"), correctness_passed=True))
        before = snapshots(db)
        assert "implementation_builds" not in inspect(db.engine).get_table_names()
        command.upgrade(config, "0020")
        assert snapshots(db) == before
        with db.transaction() as session:
            assert list(session.scalars(select(builds.ImplementationBuild))) == []
            assert not builds.implementation_is_compatible(session, runtime())
        # Metadata describes today's head, not the historical ledger revision.
        command.upgrade(config, "head")
        assert snapshots(db) == before
        command.check(config)
        command.downgrade(config, "0019")
        assert "implementation_builds" not in inspect(db.engine).get_table_names()
        assert snapshots(db) == before
    finally:
        db.engine.dispose()


@pytest.fixture
def source_only_app(tmp_path, monkeypatch):
    # A deliberately restricted real Python DH adapter tests the service's pin
    # boundary without depending on or pretending to validate native workers.
    from crucible import dh_v4, registry
    from crucible.app import create_app
    from crucible.config import Settings
    from test_api import Actor
    monkeypatch.setattr(registry, "ADAPTERS", {(dh_v4.SCHEME_ID, dh_v4.SCHEME_VERSION): dh_v4})
    monkeypatch.setattr(registry, "CURRENT", {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION})
    settings = Settings(f"sqlite:///{tmp_path / 'app.db'}", b"s" * 32,
                        origins=("http://testserver",), rate_limits=False)
    clock = [3_600_600.0]
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    actor = Actor(app)
    actor.register("fixture")
    yield app, actor, dh_v4
    actor.client.close()
    app.state.db.engine.dispose()


@pytest.mark.parametrize("marked", [False, True])
def test_app_issues_only_explicitly_reviewed_changed_pair_and_keeps_old_receipts(source_only_app, monkeypatch, marked):
    app, actor, adapter = source_only_app
    original = actor.challenge("dh-20")
    original_answer = actor.answer(original)
    original_digest = adapter.IMPLEMENTATION_SHA256
    candidate_digest = digest("Synthetic changed source identity; real DH generation remains unchanged")
    monkeypatch.setattr(adapter, "IMPLEMENTATION_SHA256", candidate_digest)
    if marked:
        mark_adapter(adapter, monkeypatch, admission())
    denied = actor.post("/api/challenges", {"profile_id": "dh-21"})
    assert denied.status_code == 409
    assert ("additional platform" if marked else "new scheme version") in denied.json()["detail"]
    raw, expected = encoded(document(scheme_id=adapter.SCHEME_ID, scheme_version=adapter.SCHEME_VERSION,
        baseline=pair((original_digest, None)), implementation=pair((candidate_digest, None))))
    with app.state.db.transaction(write=True) as session:
        reviewed = builds.register_build(session, raw, expected_sha256=expected,
            reviewed_by="Fixture operator", review_basis="Synthetic unchanged-algorithm review", now=1000)
        reviewed_id = reviewed.id
    accepted = actor.challenge("dh-21")
    accepted_answer = actor.answer(accepted)
    with app.state.db.transaction(write=True) as session:
        baseline = session.get(AdapterVersion, (adapter.SCHEME_ID, adapter.SCHEME_VERSION))
        assert baseline.source_sha256 == original_digest and baseline.artifact_sha256 is None
        assert session.get(Challenge, original["id"]).manifest["implementation"]["source_sha256"] == original_digest
        assert session.get(Challenge, accepted["id"]).manifest["implementation"]["source_sha256"] == candidate_digest
        builds.revoke_build(session, reviewed_id, reason="Synthetic finding", now=1100)
    assert actor.post("/api/challenges", {"profile_id": "dh-22"}).status_code == 409
    # Approval/revocation never changes the commitments or access to already
    # issued evidence. Both original and reviewed-build answers remain valid.
    for challenge, answer in ((original, original_answer), (accepted, accepted_answer)):
        assert actor.client.get(challenge["download_url"]).status_code == 200
        response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
        assert response.status_code == 200 and response.json()["submission"]["status"] == "correct"


@pytest.mark.parametrize("historical", [False, True])
def test_app_additional_platform_cannot_seed_its_own_missing_baseline(source_only_app, monkeypatch, historical):
    from sqlalchemy import delete
    app, actor, adapter = source_only_app
    if historical:
        actor.challenge("dh-20")
        with app.state.db.transaction(write=True) as session:
            session.execute(delete(AdapterVersion))
    before = snapshots(app.state.db)
    # Even identical historical source/artifact claims cannot establish a
    # baseline for a platform explicitly marked as an additional build.
    mark_adapter(adapter, monkeypatch, admission())
    monkeypatch.setattr(adapter, "generate", lambda *_: pytest.fail("Unadmitted platform generated an instance"))
    response = actor.post("/api/challenges", {"profile_id": "dh-21"})
    assert response.status_code == 409 and "existing original release baseline" in response.json()["detail"]
    assert snapshots(app.state.db) == before
    with app.state.db.transaction() as session:
        assert session.get(AdapterVersion, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)) is None
        assert list(session.scalars(select(builds.ImplementationBuild))) == []


def test_app_additional_marker_cannot_borrow_matching_existing_baseline(source_only_app, monkeypatch):
    app, actor, adapter = source_only_app
    actor.challenge("dh-20")
    before = snapshots(app.state.db)
    mark_adapter(adapter, monkeypatch, admission())
    monkeypatch.setattr(adapter, "generate", lambda *_: pytest.fail("Unreviewed platform generated an instance"))
    response = actor.post("/api/challenges", {"profile_id": "dh-21"})
    assert response.status_code == 409 and "active review" in response.json()["detail"]
    assert snapshots(app.state.db) == before


@pytest.mark.parametrize("existing", [False, True])
def test_app_unknown_admission_does_not_create_or_reuse_baseline(source_only_app, monkeypatch, existing):
    app, actor, adapter = source_only_app
    if existing:
        actor.challenge("dh-20")
    before = snapshots(app.state.db)
    mark_adapter(adapter, monkeypatch, admission(format="future-unknown-format"))
    monkeypatch.setattr(adapter, "generate", lambda *_: pytest.fail("Unknown admission generated an instance"))
    response = actor.post("/api/challenges", {"profile_id": "dh-21"})
    assert response.status_code == 409 and "unsupported platform admission" in response.json()["detail"]
    assert snapshots(app.state.db) == before


def test_app_missing_baseline_still_rejects_conflicting_historical_manifest(source_only_app, monkeypatch):
    from sqlalchemy import delete
    app, actor, adapter = source_only_app
    actor.challenge("dh-20")
    with app.state.db.transaction(write=True) as session:
        session.execute(delete(AdapterVersion))
    monkeypatch.setattr(adapter, "IMPLEMENTATION_SHA256", digest("unreviewed fixture change"))
    response = actor.post("/api/challenges", {"profile_id": "dh-21"})
    assert response.status_code == 409 and "Historical instances" in response.json()["detail"]
    with app.state.db.transaction() as session:
        assert session.get(AdapterVersion, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)) is None
        assert list(session.scalars(select(builds.ImplementationBuild))) == []
