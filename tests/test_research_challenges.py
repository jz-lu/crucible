"""End-to-end tier semantics and bounded first-party reference downloads."""
import hashlib
import json
import pytest
from sqlalchemy import select
from crucible import artifact_sources, generation, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.protocol_specs import builtin_spec
from test_api import Actor
from test_standard_challenges import issue, submit


@pytest.fixture
def beta(tmp_path):
    app = create_app(Settings(f"sqlite:///{tmp_path / 'research.db'}", b"r"*32,
        origins=("http://testserver",), rate_limits=False), clock=lambda: 3600600.0, initialize=True)
    actor = Actor(app)
    actor.register("research_tester")
    yield app, actor
    app.state.db.engine.dispose()


@pytest.mark.parametrize("scheme_id", ["dhkem-x25519", "sntrup", "hqc", "classic-mceliece", "frodokem", "ml-kem"])
def test_real_tiers_issue_verify_and_preserve_exact_evidence(beta, monkeypatch, scheme_id):
    app, actor = beta
    adapter = registry.adapter_for(scheme_id)
    profiles = adapter.manifests()
    assert profiles[0]["profile_kind"] == "research-tier"
    captured, original = {}, adapter.generate
    def observed(profile_id):
        result = original(profile_id)
        captured[profile_id] = result.target
        return result
    monkeypatch.setattr(adapter, "generate", observed)
    method = actor.method("Isolated integration fixture")
    for index in (len(profiles)-1, 0, len(profiles)//2):
        profile = profiles[index]
        response = actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": profile["instance_size"]})
        assert response.status_code == 200, response.text
        challenge = response.json()["challenge"]
        package = actor.client.get(challenge["download_url"]).json()
        assert package["scheme_manifest"] == profile
        assert captured[profile["id"]].hex() not in json.dumps(package)
        assert issue(actor, adapter, profile["id"]).json()["challenge"]["id"] == challenge["id"]
        result = submit(actor, challenge, captured[profile["id"]], method)
        assert result.status_code == 200 and result.json()["submission"]["status"] == "correct"
        if index == len(profiles)-1 and profile.get("record_inheritance") is False:
            board = actor.client.get("/api/leaderboard", params={"scheme_id": scheme_id, "profile_id": profiles[0]["id"]}).json()
            assert board["entries"] == []
            catalogue = actor.client.get("/api/schemes").json()["schemes"]
            scheme = next(s for s in catalogue if s["id"] == scheme_id)
            assert scheme["next_profile_id"] == profiles[0]["id"]
            assert scheme["recovered_profile_ids"] == [profile["id"]]
    person = actor.client.get("/api/people/research_tester").json()["person"]
    assert person["points"] == 2500, "Policy v9 pins the same endpoint ceiling for every current family"
    assert all(record["tier"] is not None and record["scoring_eligible"] for record in person["records"])
    assert sum(record["score"] is not None for record in person["records"]) == 1
    assert len(person["records"]) == 1
    assert len(person["first_records"]) == 1
    assert person["first_records"][0]["tier"] == len(profiles)
    with app.state.db.transaction() as session:
        attempts = session.scalars(select(generation.GenerationAttempt)).all()
        assert len(attempts) == 3 and all(a.outcome == "agreement" and a.disposition == "issued" for a in attempts)


def test_catalogue_remains_eight_families_with_truthful_common_contract(beta):
    _, actor = beta
    schemes = actor.client.get("/api/schemes").json()["schemes"]
    assert len(schemes) == 8
    for scheme in schemes:
        assert scheme["selection_kind"] == "tier"
        spec = builtin_spec(scheme["id"])
        supported = spec["parameters"]["supported_tiers"]
        tiers = supported.get("values") or range(supported["min"], supported["max"] + 1, supported["step"])
        assert len(tiers) == len(scheme["profiles"])
        if scheme.get("release_kind") != "research-tier":
            continue
        assert scheme["educational"] is False
        assert scheme["review_status"] == "local-beta"
        assert any(old["selection_kind"] == "parameter-set" for old in scheme["previous_releases"])
        if scheme["id"] == "frodokem":
            assert spec["correctness"]["kind"] == "statistical"
            assert "Perfect honest agreement" not in scheme["protocol"]["correctness"]
        assert len(scheme["profiles"]) == len(registry.adapter_for(scheme["id"]).manifests())


@pytest.mark.parametrize("scheme_id", ["dhkem-x25519", "sntrup", "hqc", "classic-mceliece"])
def test_first_party_closure_is_downloadable_and_pinned(beta, scheme_id):
    _, actor = beta
    adapter = registry.adapter_for(scheme_id)
    closure = adapter.artifact_manifest()
    for name, digest in artifact_sources.source_files(closure).items():
        response = actor.client.get("/api/protocol/artifact-source", params={"scheme_id": scheme_id, "path": name})
        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == digest
    for invalid in ("../.local/server.key", ".local/server.key", "crucible/../../.local/server.key", "crucible/app.py", "/etc/passwd"):
        response = actor.client.get("/api/protocol/artifact-source", params={"scheme_id": scheme_id, "path": invalid})
        assert response.status_code == 404
