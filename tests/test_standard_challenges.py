"""Published-profile integration, isolated from all real accounts and receipts."""
import hashlib
import json
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import generation, registry, standard_schemes
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import AdapterVersion, Challenge, ProfileIncident
from crucible.protocol_specs import builtin_spec
from test_api import Actor


@pytest.fixture
def beta(tmp_path, monkeypatch):
    # Exercise the unchanged published adapters in an isolated registration.
    # The live catalogue now selects mathematical research-tier releases.
    for adapter in (registry.dhkem_x25519, registry.pq_mlkem, registry.pq_frodokem,
                    registry.sntrup, registry.pq_hqc, registry.pq_mceliece):
        monkeypatch.setitem(registry.CURRENT, adapter.SCHEME_ID, adapter.SCHEME_VERSION)
    clock = [3_600_600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'standard.db'}", b"b" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    actor = Actor(app)
    actor.register("beta_tester")
    yield app, actor, clock, settings
    app.state.db.engine.dispose()


def issue(actor, adapter, profile_id):
    return actor.post("/api/challenges", {"scheme_id": adapter.SCHEME_ID,
        "scheme_version": adapter.SCHEME_VERSION, "profile_id": profile_id})


def submit(actor, challenge, target, method):
    return actor.post(f'/api/challenges/{challenge["id"]}/submit', {
        "candidate": target.hex(), "method_id": method["id"], "idempotency_key": str(uuid4()),
        "compute": {"hardware": "Isolated verification fixture", "workers": 1, "wall_seconds": 0.1,
                    "precomputation": "No attack: test observes the generator target", "peak_memory": "Not measured", "notes": "Synthetic private test only"}})


def test_catalogue_and_specifications_bind_exact_profiles(beta):
    _, actor, _, _ = beta
    response = actor.client.get("/api/schemes")
    assert response.status_code == 200
    schemes = {s["id"]: s for s in response.json()["schemes"]}
    assert set(schemes) == {"teaching-dh-kem", "rsa-kem", *standard_schemes.SCHEMES}
    for scheme_id in standard_schemes.SCHEMES:
        scheme = schemes[scheme_id]
        assert scheme["selection_kind"] == "parameter-set"
        assert scheme["record_inheritance"] is False and scheme["scoring_eligible"] is False
        assert scheme["initial_security_model"] is None
        assert scheme["profile_recoveries"] == []
        spec = builtin_spec(scheme_id)
        closure = actor.client.get(scheme["protocol"]["artifacts_url"]).json()
        digest = hashlib.sha256(json.dumps({k: v for k, v in closure.items() if k != "artifact_sha256"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert digest == closure["artifact_sha256"] == spec["implementation"]["artifact_sha256"]
        source = actor.client.get(scheme["protocol"]["source_url"]).content
        assert hashlib.sha256(source).hexdigest() == spec["implementation"]["sha256"]
        for profile in scheme["profiles"]:
            assert profile["implementation"]["artifact_sha256"] == digest
            assert profile["id"] in spec["parameters"]["mapping"]
        if scheme_id == "frodokem":
            assert spec["encoding"]["shared_secret_bytes"] is None
            assert {p["shared_secret_bytes"] for p in scheme["profiles"]} == {16, 24, 32}


@pytest.mark.parametrize("scheme_id", list(standard_schemes.SCHEMES))
def test_every_published_profile_issues_and_verifies_without_scoring(beta, monkeypatch, scheme_id):
    app, actor, _, _ = beta
    adapter = registry.adapter_for(scheme_id)
    original = adapter.generate
    captured = {}
    def observed(profile_id):
        result = original(profile_id)
        captured[profile_id] = result.target
        return result
    monkeypatch.setattr(adapter, "generate", observed)
    method = actor.method("Isolated conformance fixture")
    profiles = adapter.manifests()
    for profile in reversed(profiles):
        response = issue(actor, adapter, profile["id"])
        assert response.status_code == 200, response.text
        challenge = response.json()["challenge"]
        assert challenge["profile_label"] == profile["profile_label"]
        package = actor.client.get(challenge["download_url"]).json()
        assert package["scheme_manifest"] == profile
        assert captured[profile["id"]].hex() not in json.dumps(package)
        public = challenge["public"]
        assert len(bytes.fromhex(public["public_key"])) == profile["encoding"]["public_key_bytes"]
        assert len(bytes.fromhex(public["ciphertext"])) == profile["encoding"]["ciphertext_bytes"]
        assert issue(actor, adapter, profile["id"]).json()["challenge"]["id"] == challenge["id"]
        result = submit(actor, challenge, captured[profile["id"]], method)
        assert result.status_code == 200 and result.json()["submission"]["status"] == "correct"
        for earlier in profiles[:profiles.index(profile)]:
            board = actor.client.get("/api/leaderboard", params={"profile_id": earlier["id"], "scheme_id": scheme_id}).json()
            assert board["entries"] == [], "Named-set ordinal must never backfill recovery"
    person = actor.client.get("/api/people/beta_tester").json()["person"]
    assert person["points"] == 0
    assert len(person["records"]) == 1
    assert len(person["recent_recoveries"]) == len(profiles)
    assert person["first_records"] == []
    assert all(r["tier"] is None and r["profile_label"] and not r["scoring_eligible"] for r in person["records"])
    assert person["first_ten"]["history"] == []
    with app.state.db.transaction() as session:
        attempts = session.scalars(select(generation.GenerationAttempt)).all()
        assert len(attempts) == len(profiles)
        assert all(row.outcome == "agreement" and row.disposition == "issued" for row in attempts)


def test_exact_set_selection_and_ownership_are_required(beta):
    _, actor, _, _ = beta
    adapter = registry.adapter_for("dhkem-x25519")
    response = actor.post("/api/challenges", {"scheme_id": adapter.SCHEME_ID, "instance_size": 1})
    assert response.status_code == 422
    profile = adapter.manifests()[0]
    challenge = issue(actor, adapter, profile["id"]).json()["challenge"]
    assert TestClient(actor.client.app).get(challenge["download_url"]).status_code == 401


def test_mismatch_is_quarantined_once_with_denominator_and_no_target(beta, monkeypatch):
    app, actor, _, _ = beta
    adapter = registry.adapter_for("dhkem-x25519")
    profile = adapter.manifests()[0]
    original = adapter.generate
    calls = []
    def mismatch(profile_id):
        result = original(profile_id)
        calls.append(1)
        return replace(result, diagnostics={**result.diagnostics, "shared_secret_matches": False})
    monkeypatch.setattr(adapter, "generate", mismatch)
    assert issue(actor, adapter, profile["id"]).status_code == 503
    assert issue(actor, adapter, profile["id"]).status_code == 503
    assert len(calls) == 1
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0
        attempt = session.scalar(select(generation.GenerationAttempt))
        assert attempt.outcome == "agreement_failure_under_review"
        assert attempt.disposition == "quarantined"
        assert session.scalar(select(ProfileIncident)).diagnostics["reason"] == "agreement_failure_under_review"
    ledger = actor.client.get("/api/protocol/correctness", params={"scheme_id": adapter.SCHEME_ID}).json()
    assert ledger["outcomes"][0]["count"] == 1
    assert "user_id" not in str(ledger) and "target" not in str(ledger)


def test_resource_abort_busy_and_expired_generation_never_issue(beta, monkeypatch):
    app, actor, clock, _ = beta
    adapter = registry.adapter_for("ml-kem")
    profile = adapter.manifests()[0]
    original = adapter.generate
    def abort(_):
        raise adapter.GenerationLimit("Synthetic resource exhaustion")
    monkeypatch.setattr(adapter, "generate", abort)
    assert issue(actor, adapter, profile["id"]).status_code == 503
    with app.state.generation_limiter.slot("capacity-test-a"), app.state.generation_limiter.slot("capacity-test-b"):
        busy = issue(actor, adapter, profile["id"])
        assert busy.status_code == 503 and busy.headers["retry-after"] == "3"
    def expired(profile_id):
        result = original(profile_id)
        clock[0] += 3600
        return result
    monkeypatch.setattr(adapter, "generate", expired)
    assert issue(actor, adapter, profile["id"]).status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0
        assert session.scalar(select(func.count()).select_from(ProfileIncident)) == 0
        rows = session.scalars(select(generation.GenerationAttempt).order_by(generation.GenerationAttempt.started_at)).all()
        assert {(r.outcome, r.disposition) for r in rows} == {("generation_abort", "unissued"), ("agreement", "generated_unissued")}


def test_changed_dependency_pin_blocks_a_release(beta):
    app, actor, _, _ = beta
    adapter = registry.adapter_for("dhkem-x25519")
    profile = adapter.manifests()[0]
    assert issue(actor, adapter, profile["id"]).status_code == 200
    with app.state.db.transaction(write=True) as session:
        session.get(AdapterVersion, (adapter.SCHEME_ID, adapter.SCHEME_VERSION)).artifact_sha256 = "0" * 64
    assert issue(actor, adapter, profile["id"]).status_code == 409


def test_beta_availability_is_not_production_admission(beta):
    _, actor, clock, settings = beta
    production = create_app(replace(settings, production=True, access_mode="beta"), clock=lambda: clock[0])
    visitor = Actor(production)
    adapter = registry.adapter_for("dhkem-x25519")
    assert issue(visitor, adapter, adapter.manifests()[0]["id"]).status_code == 401
