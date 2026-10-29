"""Curated stage navigation must not relabel configuration evidence or credit."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from crucible import registry
from crucible.db import Challenge, Submission
from test_api import Actor
from test_calibrated_api import current_system
from test_people import add_user, person, registered_receipt


def catalogue(app):
    response = TestClient(app).get("/api/schemes")
    assert response.status_code == 200, response.text
    return {scheme["id"]: scheme for scheme in response.json()["schemes"]}


def archived_module(app):
    return next(release for release in catalogue(app)["ml-kem"]["previous_releases"]
                if release["version"] == "packed-research-v3")


def test_current_module_route_has_six_exact_configurations_and_no_inherited_recovery(current_system):
    app, _, _ = current_system
    user = add_user(app,"compressed_endpoint_solver")
    adapter = registry.adapter_for("ml-kem")
    assert adapter.SCHEME_VERSION == "compressed-research-v4"
    solved = registered_receipt(app,user,adapter,6,1)
    scheme = catalogue(app)["ml-kem"]
    assert [p["parameters"]["n"] for p in scheme["profiles"]] == [8,16,32,64,128,256]
    assert scheme["default_progression"]["additional_configuration_ids"] == []
    assert scheme["stage_range"] == {"min":1,"max":6,"step":1}
    assert scheme["largest_recovered_profile_id"] == "module-lwe-v4-tier-6"
    assert scheme["recovered_profile_ids"] == ["module-lwe-v4-tier-6"]
    assert scheme["next_profile_id"] == "module-lwe-v4-tier-1"
    assert scheme["profiles"][-1]["standard_equivalence"].startswith("FIPS203 ML-KEM-512")
    assert all(p.get("initial_security_estimate") is None for p in scheme["profiles"])
    data = person(app,"compressed_endpoint_solver")
    assert data["points"] == 100
    assert data["records"][0]["submission_id"] == solved["submission_id"]
    board = TestClient(app).get("/api/leaderboard",params={"scheme_id":"ml-kem","profile_id":"module-lwe-v4-tier-1"})
    assert board.status_code == 200 and board.json()["entries"] == []


def test_current_module_challenge_download_matches_frozen_manifest_and_closure(current_system):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register("compressed_download_tester")
    adapter = registry.adapter_for("ml-kem")
    profile_id = "module-lwe-v4-tier-1"
    expected = adapter.get_manifest(profile_id)
    response = actor.post("/api/challenges",{"scheme_id":"ml-kem","profile_id":profile_id})
    assert response.status_code == 200,response.text
    challenge = response.json()["challenge"]
    package = actor.client.get(challenge["download_url"])
    assert package.status_code == 200
    assert package.json()["scheme_manifest"] == expected
    assert not any(key.startswith("stage_") for key in package.json()["scheme_manifest"])
    assert len(bytes.fromhex(package.json()["challenge"]["public"]["public_key"])) == 56
    repeated = actor.post("/api/challenges",{"scheme_id":"ml-kem","instance_size":1})
    assert repeated.status_code == 200 and repeated.json()["challenge"]["id"] == challenge["id"]
    artifact = actor.client.get("/api/protocol/artifacts",params={"scheme_id":"ml-kem","version":adapter.SCHEME_VERSION})
    assert artifact.status_code == 200
    assert artifact.json()["artifact_sha256"] == expected["implementation"]["artifact_sha256"]


def test_api_exposes_377_default_stages_without_hiding_the_379_configurations(current_system):
    app, _, _ = current_system
    schemes = catalogue(app)
    assert len(schemes) == 8
    assert sum(s["default_progression"]["stage_count"] for s in schemes.values()) == 377
    assert sum(len(s["profiles"]) for s in schemes.values()) == 379
    for scheme in schemes.values():
        progression = scheme["default_progression"]
        assert progression["configuration_count"] == len(scheme["profiles"])
        assert scheme["stage_range"] == {"min": 1, "max": progression["stage_count"], "step": 1}
        by_id = {p["id"]: p for p in scheme["profiles"]}
        for stage in progression["stages"]:
            profile = by_id[stage["profile_id"]]
            assert profile["stage_number"] == profile["stage_position"] == stage["number"]
            assert profile["stage_label"] == f'Stage {stage["number"]}'
            assert profile["native_parameter_label"]
            assert profile["stage_context"] is None
        assert scheme["next_profile_id"] == progression["stages"][0]["profile_id"]
        for old in scheme["previous_releases"]:
            if (old["id"],old["version"]) == ("ml-kem","packed-research-v3"):
                assert old["default_progression"]["stage_count"] == 10
                assert len(old["profiles"]) == 11
                continue
            if (old["id"],old["version"]) == ("hqc","growth-research-v2"):
                assert old["default_progression"]["stage_count"] == 25
                assert len(old["profiles"]) == 27
                continue
            assert old["default_progression"] is None
            assert old["stage_range"] is None
            assert all("stage_number" not in p for p in old["profiles"])


@pytest.mark.parametrize("scheme_id,profile_id,before,after", [
    ("ml-kem", "module-lwe-v3-tier-7", 6, 7),
    ("sntrup", "sntrup-v3-tier-17", 16, 17),
    ("sntrup", "sntrup-v3-tier-24", 22, 23),
    ("hqc", "hqc-growth-research-v2-tier-24", 23, 24),
    ("hqc", "hqc-growth-research-v2-tier-26", 24, 25),
])
def test_intermediate_geometry_is_navigation_without_a_fake_stage_number(scheme_id, profile_id, before, after):
    adapter = registry.adapter_for(scheme_id, {"ml-kem": "packed-research-v3", "hqc": "growth-research-v2"}.get(scheme_id))
    frozen = deepcopy(adapter.get_manifest(profile_id))
    descriptor = registry.descriptor(adapter)
    profile = next(p for p in descriptor["profiles"] if p["id"] == profile_id)
    assert profile["stage_number"] is None
    assert profile["stage_context"] == {"before_stage": before, "after_stage": after}
    assert profile["stage_position"] == (before + after) / 2
    assert profile["stage_label"] == f'Configuration · {profile["native_parameter_label"]}'
    assert "Stage" not in profile["stage_label"]
    # Every cryptographic field remains exact; additions belong only to the
    # display copy. The original reference is neither interpolated nor replaced.
    assert {key: profile[key] for key in frozen} == frozen
    assert adapter.get_manifest(profile_id) == frozen
    assert registry.manifest_for(profile_id, scheme_id, adapter.SCHEME_VERSION) == frozen
    assert next(p for p in registry.presentation_manifests(adapter) if p["id"] == profile_id) == frozen


def test_archived_off_path_recovery_keeps_evidence_without_current_credit_or_stage_alias(current_system):
    app, _, _ = current_system
    user = add_user(app, "stage_auditor")
    adapter = registry.adapter_for("ml-kem", "packed-research-v3")
    for ordinal in range(1, 7):
        registered_receipt(app, user, adapter, ordinal, ordinal)
    intermediate = registered_receipt(app, user, adapter, 7, 7)
    with app.state.db.transaction() as session:
        result = session.get(Submission, intermediate["submission_id"])
        original = session.get(Challenge, result.challenge_id)
        frozen_manifest = deepcopy(original.manifest)
        frozen_id, frozen_mac = original.profile_id, result.payload_mac
    scheme = archived_module(app)
    default_seven = scheme["default_progression"]["stages"][6]
    assert default_seven == {"number": 7, "profile_id": "module-lwe-v3-tier-8"}
    assert scheme["next_profile_id"] == default_seven["profile_id"]
    assert scheme["largest_recovered_profile_id"] == "module-lwe-v3-tier-7"
    assert default_seven["profile_id"] not in scheme["recovered_profile_ids"]
    displayed = next(r for r in scheme["profile_recoveries"] if r["profile_id"] == frozen_id)
    assert displayed["profile_label"].startswith("Configuration ·")
    assert displayed["submission_id"] == intermediate["submission_id"]
    data = person(app, "stage_auditor")
    assert data["points"] == 0
    assert data["score_breakdown"]["progress"] == 0
    assert data["score_breakdown"]["early_recovery"] == 0
    assert data["records"][0]["profile_id"] == frozen_id
    assert data["records"][0]["tier"] == 7  # Frozen API compatibility field.
    assert data["records"][0]["scoring_eligible"] is False
    response = TestClient(app).get(f'/api/submissions/{intermediate["submission_id"]}')
    assert response.status_code == 200, response.text
    assert response.json()["submission"]["profile_id"] == frozen_id
    assert response.json()["submission"]["instance_size"] == 7
    with app.state.db.transaction() as session:
        result = session.get(Submission, intermediate["submission_id"])
        original = session.get(Challenge, result.challenge_id)
        assert original.profile_id == frozen_id
        assert original.manifest == frozen_manifest
        assert result.payload_mac == frozen_mac
    # Solving displayed Stage7 means the separate configuration8, preserving
    # its historical identity. Neither archived result earns current credit.
    default_receipt = registered_receipt(app, user, adapter, 8, 8)
    data = person(app, "stage_auditor")
    assert data["points"] == 0
    assert data["records"][0]["submission_id"] == default_receipt["submission_id"]
    assert archived_module(app)["next_profile_id"] == "module-lwe-v3-tier-9"
    assert catalogue(app)["ml-kem"]["next_profile_id"] == "module-lwe-v4-tier-1"


def test_completing_default_path_does_not_backfill_an_unsolved_intermediate(current_system):
    app, _, _ = current_system
    user = add_user(app, "default_finisher")
    adapter = registry.adapter_for("ml-kem", "packed-research-v3")
    for ordinal in [1, 2, 3, 4, 5, 6, 8, 9, 10, 11]:
        registered_receipt(app, user, adapter, ordinal, ordinal)
    scheme = archived_module(app)
    assert scheme["next_profile_id"] is None
    assert "module-lwe-v3-tier-7" not in scheme["recovered_profile_ids"]
    assert "module-lwe-v3-tier-7" in {p["id"] for p in scheme["profiles"]}
    assert person(app, "default_finisher")["points"] == 0
    board = TestClient(app).get("/api/leaderboard", params={"scheme_id": "ml-kem", "scheme_version": "packed-research-v3", "profile_id": "module-lwe-v3-tier-7"})
    assert board.status_code == 200 and board.json()["entries"] == []


def test_legacy_native_challenges_for_intermediate_and_default_keep_exact_downloads(current_system, monkeypatch):
    app, _, _ = current_system
    monkeypatch.setitem(registry.CURRENT, "hqc", "growth-research-v2")
    actor = Actor(app)
    actor.register("configuration_tester")
    adapter = registry.adapter_for("hqc")
    scheme = catalogue(app)["hqc"]
    chosen = ["hqc-growth-research-v2-tier-24", scheme["default_progression"]["stages"][23]["profile_id"]]
    challenge_ids = []
    for profile_id in chosen:
        expected = adapter.get_manifest(profile_id)
        response = actor.post("/api/challenges", {"scheme_id": "hqc", "scheme_version": adapter.SCHEME_VERSION, "profile_id": profile_id})
        assert response.status_code == 200, response.text
        challenge = response.json()["challenge"]
        challenge_ids.append(challenge["id"])
        assert challenge["profile_id"] == profile_id
        package = actor.client.get(challenge["download_url"])
        assert package.status_code == 200
        assert package.json()["scheme_manifest"] == expected
        assert not any(key.startswith("stage_") for key in package.json()["scheme_manifest"])
        # Existing numeric API callers retain their original exact selector.
        legacy = actor.post("/api/challenges", {"scheme_id": "hqc", "scheme_version": adapter.SCHEME_VERSION, "instance_size": expected["instance_size"]})
        assert legacy.status_code == 200
        assert legacy.json()["challenge"]["id"] == challenge["id"]
    assert len(set(challenge_ids)) == 2


def test_archived_module_configs_remain_readable_but_are_not_new_issuance(current_system):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register("archive_inspector")
    response = actor.post("/api/challenges", {"scheme_id":"ml-kem", "scheme_version":"packed-research-v3",
                                               "profile_id":"module-lwe-v3-tier-7"})
    assert response.status_code == 409
    archived = archived_module(app)
    assert archived["default_progression"]["additional_configuration_ids"] == ["module-lwe-v3-tier-7"]
    assert "module-lwe-v3-tier-7" in {row["id"] for row in archived["profiles"]}
    assert catalogue(app)["ml-kem"]["recovered_profile_ids"] == []
