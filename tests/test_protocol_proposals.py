"""Private protocol intake: format-only validation, provenance and safe retries."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, inspect, select

from crucible import registry
from crucible.app import create_app
from crucible.config import ROOT, Settings, load_settings
from crucible.db import AdapterVersion, Challenge, LoginSession, Submission, User
from crucible.protocol_proposals import ProtocolProposal, ProtocolProposalRevision, ProtocolProposalReview, VALIDATION_SCOPE
from crucible.protocol_specs import ProtocolSpec, SPEC_MAX_BYTES, builtin_spec, canonical_spec
from scripts.solve_teaching import recover
from test_api import pin_legacy_releases
from proposal_fixtures import tier_design


ORIGIN = "http://testserver"
VALIDATE = "/api/protocol/proposals/validate"
SUBMIT = "/api/protocol/proposals"


class Actor:
    def __init__(self, app, handle):
        self.client = TestClient(app)
        self.csrf = self.client.get("/api/session").json()["csrf_token"]
        response = self.post("/api/auth/register", {
            "handle": handle, "password": "Isolated private proposal fixture"})
        assert response.status_code == 200, response.text
        self.csrf, self.user = response.json()["csrf_token"], response.json()["user"]

    def post(self, path, body):
        return self.client.post(path, json=body,
            headers={"Origin": ORIGIN, "X-CSRF-Token": self.csrf})


@pytest.fixture
def proposal_system(tmp_path):
    clock = [3_600_600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'proposals.db'}", b"p" * 32,
                        origins=(ORIGIN,), rate_limits=False)
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    yield app, clock, settings
    app.state.db.engine.dispose()


def specification():
    return {**deepcopy(builtin_spec("teaching-dh-kem", "4")), "tier_design": tier_design()}


def canonical(value):
    return canonical_spec(ProtocolSpec.model_validate(value))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def submit(actor, spec=None, key=None):
    response = actor.post(SUBMIT, {"specification": spec if spec is not None else specification(),
                                 "idempotency_key": key or str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()["proposal"]


def count(app, model):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_public_schema_template_and_registered_specs_have_exact_version_downloads(proposal_system):
    app, _, _ = proposal_system
    public = TestClient(app)
    schema = public.get("/api/protocol/spec-schema")
    assert schema.status_code == 200
    assert schema.json() == ProtocolSpec.model_json_schema()
    assert "attachment" in schema.headers["content-disposition"]
    template = public.get("/api/protocol/spec-template")
    assert template.status_code == 200 and "attachment" in template.headers["content-disposition"]
    invalid = public.post(VALIDATE, json={"specification": template.json()}, headers={"Origin": ORIGIN})
    assert invalid.status_code == 422
    assert invalid.json()["errors"]
    for scheme_id, version in registry.ADAPTERS:
        response = public.get("/api/protocol/spec", params={"scheme_id": scheme_id, "version": version})
        assert response.status_code == 200, response.text
        assert response.json() == builtin_spec(scheme_id, version)
        assert response.json() == canonical(response.json())
        assert len(json.dumps(response.json(), ensure_ascii=False).encode()) <= SPEC_MAX_BYTES
        assert response.headers["cache-control"] == "no-store"
    for params in ({"scheme_id": "nonexistent"}, {"scheme_id": "teaching-dh-kem", "version": "999"},
                   {"scheme_id": "../.local/server.key"}):
        response = public.get("/api/protocol/spec", params=params)
        assert response.status_code == 404
        assert response.json() == {"detail": "Unknown scheme or version."}
    assert not public.cookies
    assert count(app, LoginSession) == count(app, ProtocolProposal) == 0


def test_anonymous_validation_is_format_only_without_proposal_or_session(proposal_system):
    app, _, _ = proposal_system
    public = TestClient(app)
    spec = specification()
    response = public.post(VALIDATE, json={"specification": spec}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["specification"] == canonical(spec)
    assert result["content_sha256"] == digest(result["specification"])
    assert result["validation"] == {"status": "format-valid", "scope": VALIDATION_SCOPE}
    assert "unverified" in result["validation"]["scope"]
    assert "pending review" in result["validation"]["scope"]
    assert "not an active challenge" in result["validation"]["scope"]
    assert "proposal" not in result and "set-cookie" not in response.headers
    assert count(app, ProtocolProposal) == count(app, LoginSession) == count(app, Challenge) == 0


def test_origin_json_body_and_48kib_document_boundaries(proposal_system):
    app, _, _ = proposal_system
    public = TestClient(app)
    body = {"specification": specification()}
    assert public.post(VALIDATE, json=body).status_code == 403
    assert public.post(VALIDATE, json=body, headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert public.post(VALIDATE, content=json.dumps(body), headers={"Origin": ORIGIN, "Content-Type": "text/plain"}).status_code == 415
    # The nested document has its own bound below the overall 64 KiB wrapper.
    large = {"specification": {"oversized": "x" * SPEC_MAX_BYTES}}
    assert public.post(VALIDATE, json=large, headers={"Origin": ORIGIN}).status_code == 413
    assert public.post(VALIDATE, content="x" * 65537,
        headers={"Origin": ORIGIN, "Content-Type": "application/json"}).status_code == 413
    assert count(app, ProtocolProposal) == 0


@pytest.mark.parametrize("payload", [
    {}, {"specification": []}, {"specification": None},
    {"specification": {}, "status": "admitted"},
    {"specification": {"unknown": "PRIVATE_INVALID_VALUE"}},
])
def test_invalid_specs_have_safe_field_errors_and_never_echo_values(proposal_system, payload, caplog):
    app, _, _ = proposal_system
    response = TestClient(app).post(VALIDATE, json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 422
    assert response.json()["errors"]
    assert all(set(item) == {"path", "message"} for item in response.json()["errors"])
    assert "PRIVATE_INVALID_VALUE" not in response.text
    assert "PRIVATE_INVALID_VALUE" not in caplog.text
    assert count(app, ProtocolProposal) == 0


def test_malformed_json_unicode_and_nonfinite_numbers_fail_without_500(proposal_system):
    app, _, _ = proposal_system
    public = TestClient(app)
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    for payload in ('{"specification":', '{"specification":{"bad":"\\ud800"}}',
                    '{"specification":{},"\\ud800":"private"}',
                    '{"specification":{"bad":NaN}}', '{"specification":{"bad":Infinity}}'):
        response = public.post(VALIDATE, content=payload, headers=headers)
        assert response.status_code == 422, response.text
    assert count(app, ProtocolProposal) == 0


def test_submissions_require_account_and_csrf_and_reject_client_status(proposal_system):
    app, _, _ = proposal_system
    body = {"specification": specification(), "idempotency_key": str(uuid4())}
    public = TestClient(app)
    assert public.post(SUBMIT, json=body, headers={"Origin": ORIGIN}).status_code == 401
    alice = Actor(app, "alice")
    assert alice.client.post(SUBMIT, json=body, headers={"Origin": ORIGIN}).status_code == 403
    assert alice.post(SUBMIT, {**body, "status": "admitted"}).status_code == 422
    assert alice.post(SUBMIT, {**body, "author_id": alice.user["id"]}).status_code == 422
    assert alice.post(SUBMIT, {**body, "idempotency_key": "not-a-uuid"}).status_code == 422
    assert count(app, ProtocolProposal) == 0


def test_frozen_private_record_roundtrips_without_mutating_catalogue_or_sources(proposal_system, caplog):
    app, clock, settings = proposal_system
    alice = Actor(app, "alice")
    public = TestClient(app)
    before = public.get("/api/schemes").json()["schemes"]
    source_hashes = {key: hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
                     for key, adapter in registry.ADAPTERS.items()}
    spec = specification()
    result = submit(alice, spec)
    assert result == {"id": result["id"], "created_at": clock[0], "status": "submitted", "revision": 1,
                      "content_sha256": digest(canonical(spec)), "specification": canonical(spec)}
    assert alice.client.get(f'{SUBMIT}/{result["id"]}').json()["proposal"] == result
    assert alice.client.get("/api/me/protocol-proposals").json() == {"proposals": [{**result, "author": alice.user}]}
    assert "specification" not in caplog.text
    assert public.get("/api/schemes").json()["schemes"] == before
    assert source_hashes == {key: hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
                             for key, adapter in registry.ADAPTERS.items()}
    assert count(app, AdapterVersion) == count(app, Challenge) == count(app, Submission) == 0
    for verb in ("put", "patch", "delete"):
        response = getattr(alice.client, verb)(f'{SUBMIT}/{result["id"]}',
            json={"status": "admitted"}, headers={"Origin": ORIGIN, "X-CSRF-Token": alice.csrf}) if verb != "delete" else alice.client.request(
                "DELETE", f'{SUBMIT}/{result["id"]}', json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": alice.csrf})
        assert response.status_code == 405
    # A restart uses the stored snapshot instead of re-resolving a current spec.
    restarted = create_app(settings, clock=lambda: clock[0])
    with TestClient(restarted) as client:
        client.cookies.update(alice.client.cookies)
        assert client.get(f'{SUBMIT}/{result["id"]}').json()["proposal"] == result
    restarted.state.db.engine.dispose()


def test_canonical_reordered_retry_is_one_record_and_mismatch_is_409(proposal_system):
    app, clock, _ = proposal_system
    alice = Actor(app, "alice")
    spec, key = specification(), str(uuid4())
    first = submit(alice, spec, key)
    # Ordering and explanatory template instructions are not research content.
    reordered = dict(reversed(list(spec.items())))
    reordered["_instructions"] = {"note": "This is only a local editing instruction."}
    clock[0] += 20
    replay = submit(alice, reordered, key)
    assert replay == first
    other = builtin_spec("teaching-dh-kem", "3")
    response = alice.post(SUBMIT, {"specification": other, "idempotency_key": key})
    assert response.status_code == 409
    assert count(app, ProtocolProposal) == 1
    assert alice.client.get(f'{SUBMIT}/{first["id"]}').json()["proposal"] == first


def test_legacy_draft_submission_keeps_original_canonical_hash(proposal_system):
    app, _, _ = proposal_system
    alice = Actor(app, "legacy_author")
    spec = deepcopy(builtin_spec("teaching-dh-kem", "4"))
    assert "tier_design" not in spec
    record = submit(alice, spec)
    assert record["specification"] == spec
    assert record["content_sha256"] == digest(spec)
    assert record["status"] == "submitted"


def test_concurrent_replays_are_one_record_and_key_is_scoped_per_author(proposal_system):
    app, _, _ = proposal_system
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    key, spec = str(uuid4()), specification()
    cookies = dict(alice.client.cookies)
    def run(_):
        with TestClient(app) as client:
            client.cookies.update(cookies)
            return client.post(SUBMIT, json={"specification": spec, "idempotency_key": key},
                headers={"Origin": ORIGIN, "X-CSRF-Token": alice.csrf})
    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = list(executor.map(run, range(3)))
    assert [item.status_code for item in responses] == [200] * 3
    assert len({item.json()["proposal"]["id"] for item in responses}) == 1
    bobs = submit(bob, spec, key)
    assert bobs["id"] != responses[0].json()["proposal"]["id"]
    assert count(app, ProtocolProposal) == 2


def test_list_and_detail_never_reveal_another_authors_proposal(proposal_system):
    app, _, _ = proposal_system
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    first, second = submit(alice), submit(bob)
    public = TestClient(app)
    anonymous = TestClient(app)
    anonymous.get("/api/session")
    assert public.get("/api/me/protocol-proposals").status_code == 401
    assert anonymous.get("/api/me/protocol-proposals").status_code == 401
    assert alice.client.get("/api/me/protocol-proposals").json() == {"proposals": [{**first, "author": alice.user}]}
    assert bob.client.get("/api/me/protocol-proposals").json() == {"proposals": [{**second, "author": bob.user}]}
    for client in (public, anonymous, bob.client):
        missing = client.get(f'{SUBMIT}/{uuid4()}')
        private = client.get(f'{SUBMIT}/{first["id"]}')
        malformed = client.get(f'{SUBMIT}/not-a-uuid')
        assert private.status_code == missing.status_code == malformed.status_code == 404
        assert private.json() == missing.json() == malformed.json() == {"detail": "Proposal not found."}
        assert first["id"] not in private.text
        assert "specification" not in private.text


def test_listing_is_bounded_with_deterministic_newest_first_order(proposal_system):
    app, clock, _ = proposal_system
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    spec = canonical(specification())
    with app.state.db.transaction(write=True) as session:
        for number in range(55):
            session.add(ProtocolProposal(id=f"{number:036d}", author_id=alice.user["id"],
                idempotency_key=str(uuid4()), created_at=clock[0] + number // 2,
                status="submitted", content_sha256=digest(spec), specification=spec))
    result = alice.client.get("/api/me/protocol-proposals").json()["proposals"]
    assert [record["id"] for record in result] == [f"{number:036d}" for number in range(54, 4, -1)]
    assert bob.client.get("/api/me/protocol-proposals").json() == {"proposals": []}


def test_validation_and_intake_are_rate_limited_independently(proposal_system):
    app, clock, settings = proposal_system
    actor = Actor(app, "alice")
    limited = create_app(replace(settings, rate_limits=True), clock=lambda: clock[0])
    with TestClient(limited) as client:
        spec = specification()
        for _ in range(30):
            assert client.post(VALIDATE, json={"specification": spec}, headers={"Origin": ORIGIN}).status_code == 200
        blocked = client.post(VALIDATE, json={"specification": spec}, headers={"Origin": ORIGIN})
        assert blocked.status_code == 429 and "retry-after" in blocked.headers
        assert not client.cookies
        client.cookies.update(actor.client.cookies)
        for _ in range(10):
            response = client.post(SUBMIT, json={"specification": spec, "idempotency_key": str(uuid4())},
                headers={"Origin": ORIGIN, "X-CSRF-Token": actor.csrf})
            assert response.status_code == 200, response.text
        assert client.post(SUBMIT, json={"specification": spec, "idempotency_key": str(uuid4())},
            headers={"Origin": ORIGIN, "X-CSRF-Token": actor.csrf}).status_code == 429
    assert count(app, ProtocolProposal) == 10
    limited.state.db.engine.dispose()


def test_additive_migration_preserves_existing_account_challenge_and_receipt(tmp_path, monkeypatch):
    # Reproduce the DH4 challenge that existed before proposal migration0003.
    pin_legacy_releases(monkeypatch)
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ORIGIN", ORIGIN)
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    # Seed an authentic receipt with today's ORM, then downgrade its schema to
    # the historical checkpoint. The old service binary is not under test.
    command.upgrade(config, "head")
    settings = Settings(database_url, b"m" * 32, origins=(ORIGIN,), rate_limits=False)
    old = create_app(settings, clock=lambda: 3_600_600)
    alice = Actor(old, "migration_alice")
    challenge = alice.post("/api/challenges", {"profile_id": "dh-20"}).json()["challenge"]
    method = alice.post("/api/methods", {"name": "Migration method", "pseudocode": "Public-input BSGS."}).json()["method"]
    response = alice.post(f'/api/challenges/{challenge["id"]}/submit', {
        "candidate": recover(challenge["public"]), "method_id": method["id"], "idempotency_key": str(uuid4()),
        "compute": {"hardware": "Fixture CPU", "workers": 1, "wall_seconds": 0.1,
                    "precomputation": "None", "peak_memory": "1 KiB"}})
    assert response.status_code == 200, response.text
    receipt_id = response.json()["submission"]["id"]
    # The old schema predates source artifacts. Reproduce its absent descriptor
    # only while reading the pre-migration receipt; the upgraded path is real.
    from crucible import method_sources
    with monkeypatch.context() as old_schema:
        old_schema.setattr(method_sources, "source_descriptor", lambda *_: None)
        before = alice.client.get(f"/api/submissions/{receipt_id}").json()["submission"]
    contract = alice.client.get(challenge["download_url"]).json()
    old.state.db.engine.dispose()
    command.downgrade(config, "0002")
    command.upgrade(config, "head")
    command.check(config)
    current = create_app(settings, clock=lambda: 3_600_600)
    with TestClient(current) as client:
        client.cookies.update(alice.client.cookies)
        assert client.get(f"/api/submissions/{receipt_id}").json()["submission"] == before
        assert client.get(challenge["download_url"]).json() == contract
        assert client.get("/api/me/protocol-proposals").json() == {"proposals": []}
        assert "protocol_proposals" in inspect(current.state.db.engine).get_table_names()
    assert count(current, User) == count(current, Challenge) == count(current, Submission) == count(current, AdapterVersion) == 1
    current.state.db.engine.dispose()
    command.downgrade(config, "0002")
    reverted = create_app(settings, clock=lambda: 3_600_600)
    assert "protocol_proposals" not in inspect(reverted.state.db.engine).get_table_names()
    assert count(reverted, User) == count(reverted, Challenge) == count(reverted, Submission) == 1
    reverted.state.db.engine.dispose()


@pytest.fixture
def review_system(proposal_system):
    original, clock, settings = proposal_system
    alice, reviewer, bob = Actor(original, "author"), Actor(original, "reviewer"), Actor(original, "bystander")
    configured = replace(settings, reviewer_ids=(reviewer.user["id"],))
    app = create_app(configured, clock=lambda: clock[0])
    for actor in (alice, reviewer, bob):
        cookies = dict(actor.client.cookies)
        actor.client.close()
        actor.client = TestClient(app)
        actor.client.cookies.update(cookies)
    yield app, clock, configured, alice, reviewer, bob
    app.state.db.engine.dispose()


def review_body(record, **changes):
    return {"expected_revision": record["revision"], "content_sha256": record["content_sha256"],
            "decision": "approved", "implementation_sha256": (record["specification"].get("implementation") or {}).get("sha256"),
            "checks": {name: True for name in ("specification_complete", "correctness", "tier_design", "parameter_selection", "implementation_conformance", "input_resource_safety", "reproduction")},
            "notes": "Independent manual review of the precise frozen document and source artifact.",
            "evidence": "Ran public-input fixture reproduction and reviewed the proof and malformed-input checks; artifact digest recorded above.",
            "idempotency_key": str(uuid4()), **changes}


def review(actor, record, **changes):
    response = actor.post(f'{SUBMIT}/{record["id"]}/reviews', review_body(record, **changes))
    assert response.status_code == 200, response.text
    return response.json()


def revision_body(record, spec=None, **changes):
    spec = deepcopy(spec if spec is not None else record["specification"])
    spec["summary"] = "Revised specification: " + str(record["revision"] + 1)
    return {"expected_revision": record["revision"], "content_sha256": record["content_sha256"],
            "specification": spec, "idempotency_key": str(uuid4()), **changes}


def test_reviewer_capability_is_explicit_stable_account_id_and_defaults_false(review_system):
    app, clock, settings, alice, reviewer, bob = review_system
    for actor, allowed in ((alice, False), (reviewer, True), (bob, False)):
        result = actor.client.get("/api/session").json()
        assert result["capabilities"] == {"review_protocol_proposals": allowed, "self_review_protocol_proposals": False,
                                          "manage_beta_access": False}
    assert TestClient(app).get("/api/session").json()["capabilities"] == {"review_protocol_proposals": False, "self_review_protocol_proposals": False, "manage_beta_access": False}
    unconfigured = create_app(replace(settings, reviewer_ids=()), clock=lambda: clock[0])
    client = TestClient(unconfigured)
    client.cookies.update(reviewer.client.cookies)
    assert client.get("/api/session").json()["capabilities"] == {"review_protocol_proposals": False, "self_review_protocol_proposals": False, "manage_beta_access": False}
    assert client.get("/api/protocol/review-queue").status_code == 403
    unconfigured.state.db.engine.dispose()
    assert alice.client.get("/api/protocol/review-queue").status_code == 403
    assert TestClient(app).get("/api/protocol/review-queue").status_code == 401


def test_manual_approval_records_exact_revision_artifact_checks_and_evidence_without_activation(review_system):
    app, clock, _, alice, reviewer, _ = review_system
    before = TestClient(app).get("/api/schemes").json()["schemes"]
    record = submit(alice)
    queue = reviewer.client.get("/api/protocol/review-queue").json()
    assert queue["proposals"] == [{**record, "author": alice.user}]
    assert queue["next_after"] is None
    assert reviewer.client.get(f'{SUBMIT}/{record["id"]}').status_code == 200
    body = review_body(record)
    response = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["proposal"] == {**record, "status": "approved"}
    audit = result["review"]
    assert audit["decision"] == "approved" and audit["revision"] == 1
    assert audit["content_sha256"] == record["content_sha256"]
    assert audit["implementation_sha256"] == record["specification"]["implementation"]["sha256"]
    assert audit["reviewer"] == reviewer.user and audit["created_at"] == clock[0]
    assert audit["checks"] == body["checks"] and audit["notes"] == body["notes"] and audit["evidence"] == body["evidence"]
    assert "Manual reviewer assessment" in audit["scope"] and "does not activate" in audit["scope"]
    detail = alice.client.get(f'{SUBMIT}/{record["id"]}').json()
    assert detail["reviews"] == [audit]
    assert detail["revisions"][0]["specification"] == record["specification"]
    assert reviewer.client.get("/api/protocol/review-queue").json()["proposals"] == []
    assert TestClient(app).get("/api/schemes").json()["schemes"] == before
    assert count(app, Challenge) == count(app, AdapterVersion) == 0


def test_legacy_review_exact_retry_is_readable_without_granting_new_approval(review_system):
    app, clock, _, alice, reviewer, _ = review_system
    record = submit(alice, deepcopy(builtin_spec("teaching-dh-kem", "4")))
    body = review_body(record)
    body["checks"].pop("tier_design")
    body["checks"].pop("parameter_selection")
    commitment = digest({"proposal_id": record["id"],
        **{key:value for key,value in body.items() if key != "idempotency_key"}})
    with app.state.db.transaction(write=True) as session:
        session.get(ProtocolProposal, record["id"]).status = "approved"
        session.add(ProtocolProposalReview(id=str(uuid4()), proposal_id=record["id"],
            revision=1, reviewer_id=reviewer.user["id"], created_at=clock[0],
            decision="approved", content_sha256=record["content_sha256"],
            implementation_sha256=body["implementation_sha256"], checks=body["checks"],
            notes=body["notes"], evidence=body["evidence"],
            idempotency_key=body["idempotency_key"], request_sha256=commitment))
    path = f'{SUBMIT}/{record["id"]}/reviews'
    retry = reviewer.post(path, body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["review"]["checks"] == body["checks"]
    assert retry.json()["proposal"]["specification"] == record["specification"]
    assert count(app, ProtocolProposalReview) == 1
    changed = reviewer.post(path, {**body, "checks": {**body["checks"], "tier_design": True}})
    assert changed.status_code == 409
    assert count(app, ProtocolProposalReview) == 1


def test_reviews_require_csrf_configured_other_person_and_current_revision_hash(review_system):
    app, _, _, alice, reviewer, bob = review_system
    record, own = submit(alice), submit(reviewer)
    path = f'{SUBMIT}/{record["id"]}/reviews'
    assert alice.post(path, review_body(record)).status_code == 403
    assert bob.post(path, review_body(record)).status_code == 403
    assert reviewer.client.post(path, json=review_body(record), headers={"Origin": ORIGIN}).status_code == 403
    assert reviewer.post(f'{SUBMIT}/{own["id"]}/reviews', review_body(own)).status_code == 403
    assert reviewer.post(f'{SUBMIT}/{own["id"]}/reviews', review_body(own, decision="changes_requested")).status_code == 403
    assert reviewer.post(path, review_body(record, expected_revision=2)).status_code == 409
    assert reviewer.post(path, review_body(record, content_sha256="0" * 64)).status_code == 409
    assert reviewer.post(path, review_body(record, implementation_sha256="0" * 64)).status_code == 422
    assert reviewer.post(path, {**review_body(record), "status": "approved"}).status_code == 422
    assert count(app, ProtocolProposalReview) == 0


@pytest.mark.parametrize("case", ["unknown", "no-implementation", "no-sha", "false-check", "short-evidence", "blank-notes", "unpaired-unicode", "missing-plan", "missing-tier-check", "false-tier-check", "missing-selection", "missing-selection-check", "false-selection-check"])
def test_approval_rejects_missing_correctness_artifact_checks_or_supporting_evidence(review_system, case):
    app, _, _, alice, reviewer, _ = review_system
    spec = specification()
    if case == "unknown":
        spec["correctness"]["kind"], spec["correctness"]["failure_bound"] = "unknown", None
    elif case == "no-implementation":
        spec["implementation"] = None
    elif case == "no-sha":
        spec["implementation"]["sha256"] = None
    elif case == "missing-plan":
        spec.pop("tier_design")
    elif case == "missing-selection":
        spec["tier_design"].pop("parameter_selection")
    record = submit(alice, spec)
    body = review_body(record)
    if case == "false-check":
        body["checks"]["reproduction"] = False
    elif case == "missing-tier-check":
        body["checks"].pop("tier_design")
    elif case == "false-tier-check":
        body["checks"]["tier_design"] = False
    elif case == "missing-selection-check":
        body["checks"].pop("parameter_selection")
    elif case == "false-selection-check":
        body["checks"]["parameter_selection"] = False
    elif case == "short-evidence":
        body["evidence"] = "Done."
    elif case == "blank-notes":
        body["notes"] = "   "
    elif case == "unpaired-unicode":
        # The raw JSON transport may contain this sequence; it must not reach a
        # UTF-8 storage/response encoder as a server error.
        body["notes"] = "\ud800"
    if case == "unpaired-unicode":
        response = reviewer.client.post(f'{SUBMIT}/{record["id"]}/reviews', content=json.dumps(body),
            headers={"Origin": ORIGIN, "Content-Type": "application/json", "X-CSRF-Token": reviewer.csrf})
    else:
        response = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', body)
    assert response.status_code == 422, response.text
    assert count(app, ProtocolProposalReview) == 0
    assert alice.client.get(f'{SUBMIT}/{record["id"]}').json()["proposal"]["status"] == "submitted"


def test_changes_requested_are_private_visible_to_author_and_new_revision_is_submitted(review_system):
    app, _, _, alice, reviewer, bob = review_system
    record = submit(alice)
    body = review_body(record, decision="changes_requested", notes="Explain rejection sampling precisely.",
                       evidence="The source and stated distribution do not yet agree.")
    body["checks"]["implementation_conformance"] = False
    changed = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', body)
    assert changed.status_code == 200
    changed = changed.json()
    assert changed["proposal"]["status"] == "changes_requested"
    detail = alice.client.get(f'{SUBMIT}/{record["id"]}').json()
    assert detail["reviews"][0]["notes"] == body["notes"]
    assert detail["reviews"][0]["evidence"] == body["evidence"]
    assert bob.client.get(f'{SUBMIT}/{record["id"]}').status_code == 404
    assert bob.client.get(f'{SUBMIT}/{record["id"]}/revisions').status_code == 404
    payload = revision_body(record)
    path = f'{SUBMIT}/{record["id"]}/revisions'
    assert reviewer.post(path, payload).status_code == 404
    assert bob.post(path, payload).status_code == 404
    revised = alice.post(path, payload)
    assert revised.status_code == 200, revised.text
    latest = revised.json()["proposal"]
    assert latest["revision"] == 2 and latest["status"] == "submitted"
    assert latest["content_sha256"] != record["content_sha256"]
    history = alice.client.get(path).json()
    assert [row["revision"] for row in history["revisions"]] == [2, 1]
    assert history["reviews"] == [changed["review"]]
    assert history["revisions"][1]["specification"] == record["specification"]
    assert count(app, ProtocolProposalRevision) == 2


def test_revision_after_approval_resets_approval_and_old_review_cannot_approve_new_content(review_system):
    app, _, _, alice, reviewer, _ = review_system
    initial_key = str(uuid4())
    record = submit(alice, key=initial_key)
    old_body = review_body(record)
    approved = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', old_body).json()
    path = f'{SUBMIT}/{record["id"]}/revisions'
    body = revision_body(record)
    result = alice.post(path, body)
    assert result.status_code == 200
    latest = result.json()["proposal"]
    assert latest["status"] == "submitted" and latest["revision"] == 2
    # Safe retry returns the old decision, and the current unapproved revision.
    replay = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', old_body)
    assert replay.status_code == 200
    assert replay.json()["proposal"] == latest and replay.json()["review"] == approved["review"]
    stale = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', {**old_body, "idempotency_key": str(uuid4())})
    assert stale.status_code == 409
    # The initial intake retry still resolves its original frozen document.
    original_retry = submit(alice, record["specification"], initial_key)
    assert original_retry["revision"] == 1 and original_retry["specification"] == record["specification"]
    assert original_retry["status"] == "approved"
    assert alice.client.get(f'{SUBMIT}/{record["id"]}').json()["proposal"] == latest
    assert count(app, ProtocolProposalReview) == 1


def test_revision_retries_are_idempotent_and_changed_or_stale_requests_conflict(review_system):
    app, _, _, alice, _, _ = review_system
    record = submit(alice)
    path, body = f'{SUBMIT}/{record["id"]}/revisions', revision_body(record)
    result = alice.post(path, body)
    assert result.status_code == 200
    assert alice.post(path, body).json()["proposal"] == result.json()["proposal"]
    altered = deepcopy(body)
    altered["specification"]["summary"] = "A different revision request."
    assert alice.post(path, altered).status_code == 409
    assert alice.post(path, {**body, "idempotency_key": str(uuid4())}).status_code == 409
    latest = result.json()["proposal"]
    wrong_hash = revision_body(latest, content_sha256="0" * 64)
    assert alice.post(path, wrong_hash).status_code == 409
    assert count(app, ProtocolProposalRevision) == 2


def test_review_retry_is_stable_and_only_one_concurrent_decision_wins(review_system):
    app, _, _, alice, reviewer, _ = review_system
    record = submit(alice)
    path, body = f'{SUBMIT}/{record["id"]}/reviews', review_body(record)
    cookies = dict(reviewer.client.cookies)
    def run(payload):
        with TestClient(app) as client:
            client.cookies.update(cookies)
            return client.post(path, json=payload, headers={"Origin": ORIGIN, "X-CSRF-Token": reviewer.csrf})
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(run, [body, body]))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["review"] == responses[1].json()["review"]
    assert reviewer.post(path, {**body, "notes": "A different evaluation."}).status_code == 409
    assert reviewer.post(path, {**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert count(app, ProtocolProposalReview) == 1
    # Independent decisions against a fresh revision cannot both win either.
    revised = alice.post(f'{SUBMIT}/{record["id"]}/revisions', revision_body(record)).json()["proposal"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(run, [review_body(revised), review_body(revised, decision="changes_requested")]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert count(app, ProtocolProposalReview) == 2


def test_review_queue_and_revision_audit_are_bounded_and_paginated(review_system):
    app, clock, _, alice, reviewer, _ = review_system
    records = []
    for _ in range(4):
        clock[0] += 1
        records.append(submit(alice))
    own = submit(reviewer)
    first = reviewer.client.get("/api/protocol/review-queue?limit=2").json()
    assert [row["id"] for row in first["proposals"]] == [row["id"] for row in records[:2]]
    second = reviewer.client.get("/api/protocol/review-queue", params={"limit": 2, "after": first["next_after"]}).json()
    assert [row["id"] for row in second["proposals"]] == [row["id"] for row in records[2:]]
    assert second["next_after"] is None
    assert own["id"] not in str([first, second])
    current = records[0]
    for _ in range(4):
        current = alice.post(f'{SUBMIT}/{current["id"]}/revisions', revision_body(current)).json()["proposal"]
    first_history = alice.client.get(f'{SUBMIT}/{current["id"]}/revisions?limit=2').json()
    assert [row["revision"] for row in first_history["revisions"]] == [5, 4]
    assert first_history["next_before_revision"] == 4
    second_history = alice.client.get(f'{SUBMIT}/{current["id"]}/revisions?limit=2&before_revision=4').json()
    assert [row["revision"] for row in second_history["revisions"]] == [3, 2]
    assert second_history["proposal"] == current
    last = alice.client.get(f'{SUBMIT}/{current["id"]}/revisions?before_revision=2').json()
    assert [row["revision"] for row in last["revisions"]] == [1]
    assert last["next_before_revision"] is None
    for query in ("limit=0", "limit=51", "before_revision=0"):
        assert alice.client.get(f'{SUBMIT}/{current["id"]}/revisions?{query}').status_code == 422
    assert reviewer.client.get("/api/protocol/review-queue?after=unknown").status_code == 422


def test_reviewer_configuration_uses_ids_default_none_and_explicit_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'config.db'}")
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"c" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.delenv("CRUCIBLE_REVIEWER_IDS", raising=False)
    assert load_settings().reviewer_ids == ()
    first, second = str(uuid4()), str(uuid4())
    config = tmp_path / "reviewers.json"
    config.write_text(json.dumps({"reviewer_ids": [first]}))
    assert load_settings().reviewer_ids == (first,)
    monkeypatch.setenv("CRUCIBLE_REVIEWER_IDS", second + ", " + second)
    assert load_settings().reviewer_ids == (second,)
    monkeypatch.setenv("CRUCIBLE_REVIEWER_IDS", "")
    assert load_settings().reviewer_ids == ()
    monkeypatch.setenv("CRUCIBLE_REVIEWER_IDS", "countableclouds")
    with pytest.raises(RuntimeError, match="account UUIDs"):
        load_settings()
    monkeypatch.delenv("CRUCIBLE_REVIEWER_IDS")
    for malformed in ({"reviewer_ids": ["countableclouds"]}, {"reviewer_ids": [17]},
                      {"reviewer_ids": first}, {"reviewer_ids": [], "unexpected": True}):
        config.write_text(json.dumps(malformed))
        with pytest.raises(RuntimeError, match="account UUIDs"):
            load_settings()


def test_development_self_review_requires_explicit_flag_and_remains_test_only(review_system):
    from crucible.protocol_admission import check_admission
    from crucible.scoring_awards import AdmissionCreditError, SchemeAdmissionCredit, award_scheme_credit

    original, clock, settings, alice, reviewer, _ = review_system
    enabled = replace(settings, allow_self_review=True)
    app = create_app(enabled, clock=lambda: clock[0])
    with TestClient(app) as client:
        client.cookies.update(reviewer.client.cookies)
        prior = reviewer.client
        reviewer.client = client
        try:
            assert client.get('/api/session').json()['capabilities'] == {
                'review_protocol_proposals': True, 'self_review_protocol_proposals': True, 'manage_beta_access': False}
            own = submit(reviewer)
            assert own['id'] in {row['id'] for row in client.get('/api/protocol/review-queue').json()['proposals']}
            body = review_body(own)
            path = f'{SUBMIT}/{own["id"]}/reviews'
            result = reviewer.post(path, body)
            assert result.status_code == 200, result.text
            data = result.json()
            assert data['review']['checks']['test_only_self_review'] is True
            assert 'Development self-review only' in data['review']['scope']
            assert data['proposal']['status'] == 'approved'
            assert reviewer.post(path, body).json()['review'] == data['review']
            assert count(app, ProtocolProposalReview) == 1
            assert count(app, SchemeAdmissionCredit) == 0
            with app.state.db.transaction() as session, pytest.raises(ValueError, match='independent'):
                check_admission(session, own['id'], 'teaching-dh-kem', '4')
            with pytest.raises(AdmissionCreditError, match='self-review'):
                award_scheme_credit(app.state.db, enabled, family_id='test-self-review',
                    scheme_id='teaching-dh-kem', scheme_version='4', proposal_id=own['id'],
                    review_id=data['review']['id'], reviewer_id=reviewer.user['id'],
                    evidence='Local test approval must never produce scientific admission credit.',
                    confirm_new_family=True)
            assert count(app, SchemeAdmissionCredit) == 0
            assert count(app, Challenge) == count(app, AdapterVersion) == 0
        finally:
            reviewer.client = prior
    # The persisted marker stays visible when the testing flag is turned off.
    historical = reviewer.client.get(f'{SUBMIT}/{own["id"]}').json()
    assert historical['reviews'][0]['checks']['test_only_self_review'] is True
    assert reviewer.post(path, body).status_code == 403
    # A flag does not grant reviewer authority or permit forged metadata.
    unauthorized = TestClient(app)
    unauthorized.cookies.update(alice.client.cookies)
    assert unauthorized.get('/api/session').json()['capabilities']['self_review_protocol_proposals'] is False
    assert unauthorized.post(path, json=body, headers={'Origin': ORIGIN, 'X-CSRF-Token': alice.csrf}).status_code == 403
    app.state.db.engine.dispose()


def test_client_cannot_assign_test_only_marker_or_bypass_new_checks(review_system):
    _, _, _, alice, reviewer, _ = review_system
    record = submit(alice)
    body = review_body(record)
    body['checks']['test_only_self_review'] = False
    result = reviewer.post(f'{SUBMIT}/{record["id"]}/reviews', body)
    assert result.status_code == 422


def test_self_review_flag_defaults_off_is_strict_and_fails_closed_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv('CRUCIBLE_ENV', 'development')
    monkeypatch.setenv('CRUCIBLE_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "config.db"}')
    monkeypatch.setenv('CRUCIBLE_SECRET', base64.b64encode(b'c' * 32).decode())
    monkeypatch.delenv('CRUCIBLE_ALLOW_SELF_REVIEW', raising=False)
    monkeypatch.delenv('CRUCIBLE_REVIEWER_IDS', raising=False)
    assert load_settings().allow_self_review is False
    monkeypatch.setenv('CRUCIBLE_ALLOW_SELF_REVIEW', 'true')
    assert load_settings().allow_self_review is True
    monkeypatch.setenv('CRUCIBLE_ENV', 'production')
    with pytest.raises(RuntimeError, match='cannot be enabled in production'):
        load_settings()
    with pytest.raises(RuntimeError, match='development'):
        Settings('sqlite://', b'x' * 32, production=True, allow_self_review=True)
    monkeypatch.setenv('CRUCIBLE_ENV', 'development')
    for invalid in ('1', 'TRUE', '', 'yes'):
        monkeypatch.setenv('CRUCIBLE_ALLOW_SELF_REVIEW', invalid)
        with pytest.raises(RuntimeError, match='exactly true or false'):
            load_settings()
