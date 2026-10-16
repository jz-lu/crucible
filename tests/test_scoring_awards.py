"""Scheme approval and explicit new-family admission remain separate events."""
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import func, inspect, select

from crucible import registry
from crucible.config import ROOT, Settings
from crucible.db import AdapterVersion, Base, Database, User
from crucible.protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview, ReviewChecks
from crucible.protocol_specs import builtin_spec
from crucible.scoring_awards import AdmissionCreditError, SchemeAdmissionCredit, award_scheme_credit


CURRENT_RSA = registry.adapter_for("rsa-kem")

def digest(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@pytest.fixture
def admission(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'admissions.db'}")
    Base.metadata.create_all(db.engine)
    author_id, reviewer_id = str(uuid4()), str(uuid4())
    proposal_id, review_id = str(uuid4()), str(uuid4())
    spec = deepcopy(builtin_spec("rsa-kem", CURRENT_RSA.SCHEME_VERSION))
    content_sha = digest(spec)
    with db.transaction(write=True) as session:
        session.add_all([User(id=author_id, handle="author", password_hash="unused", created_at=1),
                         User(id=reviewer_id, handle="reviewer", password_hash="unused", created_at=1)])
        session.flush()
        session.add(ProtocolProposal(id=proposal_id, author_id=author_id,
            idempotency_key=str(uuid4()), created_at=2, status="approved", revision=1,
            content_sha256=content_sha, specification=spec))
        session.flush()
        session.add(ProtocolProposalRevision(id=str(uuid4()), proposal_id=proposal_id,
            revision=1, created_at=2, idempotency_key=str(uuid4()), request_sha256=content_sha,
            content_sha256=content_sha, specification=spec))
        session.add(ProtocolProposalReview(id=review_id, proposal_id=proposal_id,
            revision=1, reviewer_id=reviewer_id, created_at=3, decision="approved",
            content_sha256=content_sha, implementation_sha256=CURRENT_RSA.IMPLEMENTATION_SHA256,
            checks={name: True for name in ReviewChecks.model_fields},
            notes="Reviewed exact source and specification.", evidence="Independent verification commands and results.",
            idempotency_key=str(uuid4()), request_sha256="a" * 64))
    settings = Settings(db.engine.url.render_as_string(), b"a" * 32, reviewer_ids=(reviewer_id,))
    args = dict(family_id="rsa-kem", scheme_id="rsa-kem", scheme_version=CURRENT_RSA.SCHEME_VERSION,
                proposal_id=proposal_id, review_id=review_id, reviewer_id=reviewer_id,
                evidence="Explicit reviewed admission of a new family; integrated source matches the approval.",
                confirm_new_family=True, clock=lambda: 10.0)
    yield db, settings, args, author_id
    db.engine.dispose()


def count(db):
    with db.transaction() as session:
        return session.scalar(select(func.count()).select_from(SchemeAdmissionCredit))


def test_approval_alone_awards_nothing_dry_run_is_inert_and_exact_retry_is_idempotent(admission):
    db, settings, args, author = admission
    assert count(db) == 0
    preview = award_scheme_credit(db, settings, **args, dry_run=True)
    assert preview["created"] is False and preview["dry_run"] is True
    assert preview["credit"]["user_id"] == author and count(db) == 0
    first = award_scheme_credit(db, settings, **args)
    assert first["created"] is True and first["credit"] == preview["credit"]
    assert first["credit"]["source_sha256"] == CURRENT_RSA.IMPLEMENTATION_SHA256
    again = award_scheme_credit(db, settings, **args)
    assert again["created"] is False and again["credit"] == first["credit"] and count(db) == 1


@pytest.mark.parametrize("changes", [
    {"confirm_new_family": False}, {"family_id": "Invalid family"}, {"evidence": "short"},
    {"scheme_id": "unregistered"}, {"scheme_version": "2"},
    {"review_id": "missing"}, {"proposal_id": "missing"},
])
def test_bad_operator_requests_never_create_credit(admission, changes):
    db, settings, args, _ = admission
    with pytest.raises(AdmissionCreditError):
        award_scheme_credit(db, settings, **{**args, **changes})
    assert count(db) == 0


def test_reviewer_must_be_configured_and_cannot_be_proposer(admission):
    db, settings, args, author = admission
    with pytest.raises(AdmissionCreditError, match="configured"):
        award_scheme_credit(db, replace(settings, reviewer_ids=()), **args)
    with pytest.raises(AdmissionCreditError, match="recipient"):
        award_scheme_credit(db, replace(settings, reviewer_ids=(author,)), **{**args, "reviewer_id": author})
    assert count(db) == 0


@pytest.mark.parametrize("tamper", ["unapproved", "old-review", "wrong-reviewer", "wrong-spec-hash",
                                  "wrong-revision", "wrong-source", "unchecked", "bad-pin"])
def test_stale_review_mismatch_and_incomplete_checks_fail_closed(admission, tamper):
    db, settings, args, author = admission
    with db.transaction(write=True) as session:
        proposal = session.get(ProtocolProposal, args["proposal_id"])
        review = session.get(ProtocolProposalReview, args["review_id"])
        if tamper == "unapproved": proposal.status = "submitted"
        elif tamper == "old-review": proposal.revision = 2
        elif tamper == "wrong-reviewer": review.reviewer_id = author
        elif tamper == "wrong-spec-hash": proposal.content_sha256 = review.content_sha256 = "b" * 64
        elif tamper == "wrong-revision":
            revision = session.scalar(select(ProtocolProposalRevision))
            revision.specification = {**revision.specification, "summary": "Changed frozen specification."}
        elif tamper == "wrong-source": review.implementation_sha256 = "b" * 64
        elif tamper == "unchecked": review.checks = {**review.checks, "correctness": False}
        else: session.add(AdapterVersion(scheme_id="rsa-kem", version=CURRENT_RSA.SCHEME_VERSION, source_sha256="b" * 64, created_at=1))
    with pytest.raises(AdmissionCreditError):
        award_scheme_credit(db, settings, **args)
    assert count(db) == 0


def test_mutated_source_manifest_pin_is_not_accepted(admission, monkeypatch):
    db, settings, args, _ = admission
    monkeypatch.setattr(CURRENT_RSA, "IMPLEMENTATION_SHA256", "0" * 64)
    with pytest.raises(AdmissionCreditError, match="match exactly"):
        award_scheme_credit(db, settings, **args)
    assert count(db) == 0


def test_family_alias_and_changed_retry_cannot_mint_second_credit(admission):
    db, settings, args, _ = admission
    award_scheme_credit(db, settings, **args)
    with pytest.raises(AdmissionCreditError, match="already has"):
        award_scheme_credit(db, settings, **{**args, "family_id": "rsa-alias"})
    with pytest.raises(AdmissionCreditError, match="immutable"):
        award_scheme_credit(db, settings, **{**args, "evidence": "A changed attestation must not rewrite an existing award."})
    assert count(db) == 1


def test_duplicate_exact_operator_requests_are_safe_across_connections(admission):
    from concurrent.futures import ThreadPoolExecutor
    db, settings, args, _ = admission
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: award_scheme_credit(db, settings, **args), range(2)))
    assert sorted(result["created"] for result in results) == [False, True]
    assert count(db) == 1


def test_people_api_counts_explicit_admission_once_and_does_not_expose_private_review(admission):
    from fastapi.testclient import TestClient
    from crucible.app import create_app
    db, settings, args, _ = admission
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        before = client.get("/api/people/author").json()["person"]
        assert before["points"] == before["score_breakdown"]["scheme_contributions"] == 0
        assert before["accepted_proposal_count"] == 0  # Approved proposal, not yet admitted.
        assert all(person["accepted_proposal_count"] == 0 for person in client.get("/api/people").json()["people"])
        award_scheme_credit(db, settings, **args)
        after = client.get("/api/people/author").json()["person"]
        assert after["points"] == after["score_breakdown"]["scheme_contributions"] == 1000
        assert after["accepted_proposal_count"] == 1
        award_scheme_credit(db, settings, **args)
        retried = client.get("/api/people/author").json()["person"]
        assert retried["points"] == 1000 and retried["accepted_proposal_count"] == 1
        board = client.get("/api/people").json()
        assert board["people"][0]["handle"] == "author"
        assert board["people"][0]["accepted_proposal_count"] == 1
        assert all(person["accepted_proposal_count"] == 0 for person in board["people"][1:])
        for private in (args["evidence"], args["proposal_id"], args["review_id"], args["reviewer_id"]):
            assert private not in json.dumps(board) + json.dumps(after)
    app.state.db.engine.dispose()


def test_migration_is_additive_and_schema_matches_metadata(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "0003")
    db = Database(url)
    identifier = str(uuid4())
    with db.transaction(write=True) as session:
        session.add(User(id=identifier, handle="preserved", password_hash="unchanged", created_at=1))
    command.upgrade(config, "head")
    inspector = inspect(db.engine)
    assert "scheme_admission_credits" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("scheme_admission_credits")} == set(SchemeAdmissionCredit.__table__.columns.keys())
    assert {tuple(item["column_names"]) for item in inspector.get_unique_constraints("scheme_admission_credits")} == {
        ("scheme_id",), ("proposal_id",), ("review_id",), ("source_sha256",)}
    assert count(db) == 0
    with db.transaction() as session:
        assert session.get(User, identifier).password_hash == "unchanged"
    command.downgrade(config, "0003")
    assert "scheme_admission_credits" not in inspect(db.engine).get_table_names()
    db.engine.dispose()
