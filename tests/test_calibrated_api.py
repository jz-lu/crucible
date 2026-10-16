"""Current DH7/RSA4 integration, independent of archived release fixtures."""
import hashlib
import math
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import calibration_v2 as calibration
from crucible import registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge, ProfileIncident, Submission
from crucible.protocol_specs import ProtocolSpec, canonical_spec
from scripts.solve_rsa_frontier import recover as recover_rsa
from scripts.solve_teaching_frontier import recover as recover_dh
from test_api import Actor


IDENTITIES = (("teaching-dh-kem", "7", "dh-v7-tier-1", recover_dh),
              ("rsa-kem", "4", "rsa-v4-tier-1", recover_rsa))


@pytest.fixture
def current_system(tmp_path):
    # Deliberately do not import/use the archive-pinning system fixture.
    assert {k: registry.CURRENT[k] for k in ("teaching-dh-kem", "rsa-kem")} == {"teaching-dh-kem": "7", "rsa-kem": "4"}
    clock = [3_600_600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'calibrated.db'}", b"c" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    yield app, clock, settings
    app.state.db.engine.dispose()


def catalogue(app):
    response = TestClient(app).get("/api/schemes")
    assert response.status_code == 200, response.text
    return {scheme["id"]: scheme for scheme in response.json()["schemes"]}


def count(app, model):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def answer(actor, challenge, recover):
    method = actor.method("Calibrated public-input reference")
    return {"candidate": recover(challenge["public"]), "method_id": method["id"],
            "compute": {"hardware": "Isolated test CPU", "workers": 1, "wall_seconds": 0.1,
                        "precomputation": "No precomputed table", "peak_memory": "Not measured", "notes": "Test declaration"},
            "idempotency_key": str(uuid4())}


def test_actual_current_catalogue_has_one_hundred_calibrated_profiles_and_unmodified_archives(current_system):
    app, _, _ = current_system
    schemes = catalogue(app)
    calibrated = {key: schemes[key] for key, *_ in IDENTITIES}
    assert sum(len(scheme["profiles"]) for scheme in calibrated.values()) == 100
    all_ids = set()
    for scheme_id, version, first_id, _ in IDENTITIES:
        scheme = schemes[scheme_id]
        adapter = registry.adapter_for(scheme_id)
        assert scheme["version"] == version == adapter.SCHEME_VERSION
        assert scheme["instance_size_range"] == {"min": 1, "max": 50, "step": 1}
        assert [profile["instance_size"] for profile in scheme["profiles"]] == list(range(1, 51))
        assert scheme["next_profile_id"] == first_id
        assert scheme["participant_count"] == 0 and scheme["recovered_profile_ids"] == []
        assert scheme["compatible_releases"] == []
        for profile in scheme["profiles"]:
            assert profile["id"] not in all_ids
            all_ids.add(profile["id"])
            tier = profile["instance_size"]
            bits = calibration.modulus_bits_for_tier(tier)
            assert profile["modulus_bits"] == bits
            q = int(profile["parameters"]["q"], 16) if scheme_id == "teaching-dh-kem" else None
            assert profile["parameter_bits"] == (q.bit_length() if q is not None else bits)
            expected = calibration.profile_estimate(tier, bits, q)
            if q is not None:
                # New explicit interval distribution has a separately declared cost.
                exponent_limit = int(profile["parameters"]["exponent_upper_bound"], 16)
                expected["bits"] = round(min(expected["bits"], math.log2(exponent_limit) / 2), 6)
                expected["model_id"] = "dh-interval-rho-nfs-v1"
            assert profile["initial_security_estimate"] == expected
            width = (bits + 7) // 8
            assert scheme["profile_metrics"][profile["id"]] == {
                "public_key_bytes": width if q is not None else width + 3, "ciphertext_bytes": width,
            }
            assert profile["shared_secret_bytes"] == 32
        last = scheme["profiles"][-1]
        assert last["modulus_bits"] == 3072
        assert last["initial_security_estimate"] == {"model_id": "dh-interval-rho-nfs-v1" if scheme_id == "teaching-dh-kem" else "classical-baseline-v2", "bits": 128.0, "target_bits": 128.0}
        for archive in scheme["previous_releases"]:
            assert archive["profiles"] == registry.adapter_for(scheme_id, archive["version"]).manifests()
            old_calibrated = (scheme_id, archive["version"]) in {("teaching-dh-kem", "5"), ("rsa-kem", "2")}
            if old_calibrated:
                assert archive["initial_security_model"]["id"] == "classical-baseline-v1"
                assert archive["profiles"][24]["initial_security_estimate"]["target_bits"] == 32
            elif (scheme_id, archive["version"]) in {("teaching-dh-kem", "6"), ("rsa-kem", "3")}:
                assert archive["initial_security_model"]["id"] == "classical-baseline-v2"
                assert archive["profiles"][-1]["initial_security_estimate"]["target_bits"] == 128
            else:
                assert all("initial_security_estimate" not in item for item in archive["profiles"])
    assert count(app, Challenge) == count(app, Submission) == 0


@pytest.mark.parametrize("scheme_id,version,profile_id,recover", IDENTITIES)
def test_tier_one_public_recovery_receipt_and_exact_downloads(current_system, scheme_id, version, profile_id, recover):
    app, clock, _ = current_system
    alice, bob = Actor(app), Actor(app)
    alice.register("calibration_alice")
    bob.register("calibration_bob")
    response = alice.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": 1})
    assert response.status_code == 200, response.text
    challenge = response.json()["challenge"]
    assert (challenge["scheme_id"], challenge["scheme_version"], challenge["profile_id"]) == (scheme_id, version, profile_id)
    assert challenge["public"]["instance_size"] == 1
    modulus_name = "p" if scheme_id == "teaching-dh-kem" else "N"
    assert int(challenge["public"]["parameters"][modulus_name], 16).bit_length() == 32
    contract = alice.client.get(challenge["download_url"])
    assert contract.status_code == 200
    assert recover(contract.json()) == recover(challenge["public"])
    assert bob.client.get(challenge["download_url"]).status_code == 404
    submitted = answer(alice, challenge, recover)
    clock[0] += 1
    response = alice.post(f'/api/challenges/{challenge["id"]}/submit', submitted)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission"]
    assert receipt["status"] == "correct"
    assert submitted["candidate"] not in response.text
    board = alice.client.get("/api/leaderboard", params={"scheme_id": scheme_id, "scheme_version": version, "profile_id": profile_id}).json()
    assert board["includes_compatible_versions"] == []
    assert [(row["handle"], row["profile_id"], row["scheme_version"], row["instance_size"]) for row in board["entries"]] == [("calibration_alice", profile_id, version, 1)]
    scheme = catalogue(app)[scheme_id]
    assert scheme["largest_recovered_profile_id"] == profile_id
    assert scheme["participant_count"] == 1
    assert scheme["first_recovery"]["submission_id"] == receipt["id"]
    public_receipt = TestClient(app).get(f'/api/submissions/{receipt["id"]}')
    assert public_receipt.status_code == 200
    assert public_receipt.json()["submission"]["instance_size"] == 1
    assert submitted["candidate"] not in public_receipt.text


@pytest.mark.parametrize("scheme_id,version,profile_id,recover", IDENTITIES)
def test_current_source_specification_and_archive_pins_are_exact(current_system, scheme_id, version, profile_id, recover):
    app, _, _ = current_system
    client = TestClient(app)
    scheme = catalogue(app)[scheme_id]
    for release in [scheme, *scheme["previous_releases"]]:
        adapter = registry.adapter_for(scheme_id, release["version"])
        frozen = Path(adapter.__file__).read_bytes()
        with patch.object(adapter, "generate", side_effect=AssertionError("Source/spec downloads cannot generate")):
            source = client.get(release["protocol"]["source_url"])
            spec = client.get("/api/protocol/spec", params={"scheme_id": scheme_id, "version": release["version"]})
        assert source.status_code == spec.status_code == 200
        assert source.content == frozen
        assert hashlib.sha256(frozen).hexdigest() == adapter.IMPLEMENTATION_SHA256
        document = spec.json()
        assert document == canonical_spec(ProtocolSpec.model_validate(document))
        assert document["version"] == release["version"]
        assert document["implementation"]["sha256"] == adapter.IMPLEMENTATION_SHA256
        if release["version"] == version:
            assert document["parameters"]["supported_tiers"] == {"kind": "range", "min": 1, "max": 50, "step": 1}
    assert count(app, Challenge) == 0


@pytest.mark.parametrize("scheme_id,version,profile_id,recover", IDENTITIES)
def test_invalid_tiers_and_wrong_release_cannot_alias_legacy_profiles(current_system, scheme_id, version, profile_id, recover):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register("selector_alice")
    adapter = registry.adapter_for(scheme_id)
    legacy_version, legacy_profile = ("4", "dh-20") if scheme_id == "teaching-dh-kem" else ("1", "rsa-20")
    with patch.object(adapter, "generate", side_effect=AssertionError("Invalid selectors must fail before generation")):
        for tier, status in ((0, 422), (51, 404), (True, 422), (1.5, 422)):
            response = actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": tier})
            assert response.status_code == status, response.text
        assert actor.post("/api/challenges", {"scheme_id": scheme_id, "scheme_version": legacy_version, "profile_id": profile_id}).status_code == 404
        assert actor.post("/api/challenges", {"scheme_id": scheme_id, "scheme_version": legacy_version, "profile_id": legacy_profile}).status_code == 409
    assert count(app, Challenge) == 0


def test_current_shared_round_needs_one_explicit_start_per_scheme_and_rollover(current_system):
    app, clock, _ = current_system
    actor = Actor(app)
    actor.register("round_alice")
    first = actor.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 1}).json()
    round_id = first["round_started_round_id"]
    second = actor.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 2, "active_round_id": round_id})
    assert second.status_code == 200
    assert second.json()["challenge"]["ends_at"] == first["challenge"]["ends_at"]
    repeat = actor.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 2, "active_round_id": round_id})
    assert repeat.json()["challenge"] == second.json()["challenge"]
    assert actor.post("/api/challenges", {"scheme_id": "rsa-kem", "instance_size": 1, "active_round_id": round_id}).status_code == 409
    rsa = actor.post("/api/challenges", {"scheme_id": "rsa-kem", "instance_size": 1})
    assert rsa.status_code == 200
    assert rsa.json()["challenge"]["ends_at"] == first["challenge"]["ends_at"]
    clock[0] = first["challenge"]["ends_at"]
    assert actor.post("/api/challenges", {"scheme_id": "teaching-dh-kem", "instance_size": 3, "active_round_id": round_id}).status_code == 409
    assert count(app, Challenge) == 3


@pytest.mark.parametrize("scheme_id,version,profile_id,recover", IDENTITIES)
def test_legacy_receipts_do_not_become_calibrated_progress_or_round_activation(current_system, monkeypatch, scheme_id, version, profile_id, recover):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register("archive_alice")
    old_version, old_profile = ("5", "dh-v5-tier-1") if scheme_id == "teaching-dh-kem" else ("2", "rsa-v2-tier-1")
    with monkeypatch.context() as historical:
        historical.setitem(registry.CURRENT, scheme_id, old_version)
        old = actor.challenge(old_profile)
        submission = actor.post(f'/api/challenges/{old["id"]}/submit', answer(actor, old, recover))
        assert submission.status_code == 200 and submission.json()["submission"]["status"] == "correct"
    current = catalogue(app)[scheme_id]
    assert current["version"] == version
    assert current["participant_count"] == 0
    assert current["recovered_profile_ids"] == [] and current["first_recovery"] is None
    archive = next(item for item in current["previous_releases"] if item["version"] == old_version)
    assert archive["participant_count"] == 1 and archive["recovered_profile_ids"] == [old_profile]
    current_board = actor.client.get("/api/leaderboard", params={"scheme_id": scheme_id, "scheme_version": version, "profile_id": profile_id})
    assert current_board.status_code == 200 and current_board.json()["entries"] == []
    assert current_board.json()["includes_compatible_versions"] == []
    assert actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": 1, "active_round_id": old["round_id"]}).status_code == 409
    assert actor.client.get(old["download_url"]).json()["scheme_manifest"]["version"] == old_version
    assert actor.client.get(f'/api/submissions/{submission.json()["submission"]["id"]}').json()["submission"]["scheme_version"] == old_version


@pytest.mark.parametrize("scheme_id,version,profile_id,recover", IDENTITIES)
def test_changed_current_source_pin_blocks_new_instances(current_system, monkeypatch, scheme_id, version, profile_id, recover):
    app, clock, _ = current_system
    actor = Actor(app)
    actor.register("pin_alice")
    assert actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": 1}).status_code == 200
    monkeypatch.setattr(registry.adapter_for(scheme_id), "IMPLEMENTATION_SHA256", "0" * 64)
    clock[0] += 3600
    assert actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": 1}).status_code == 409
    assert count(app, Challenge) == 1


def test_bounded_rsa_generation_exhaustion_is_private_retryable_and_not_a_correctness_incident(current_system, monkeypatch):
    app, _, _ = current_system
    actor = Actor(app)
    actor.register("limit_alice")
    adapter = registry.adapter_for("rsa-kem")
    original = adapter.generate
    def exhausted(profile_id):
        raise adapter.PrimeGenerationLimit("PRIVATE_GENERATOR_STATE_17")
    monkeypatch.setattr(adapter, "generate", exhausted)
    response = actor.post("/api/challenges", {"scheme_id": "rsa-kem", "instance_size": 1})
    assert response.status_code == 503
    assert "PRIVATE_GENERATOR_STATE_17" not in response.text
    assert count(app, Challenge) == count(app, ProfileIncident) == 0
    state = actor.client.get("/api/challenges", params={"scheme_id": "rsa-kem", "scheme_version": "4", "profile_id": "rsa-v4-tier-1"}).json()
    assert state["challenge"] is None and state["round_started"] is False
    monkeypatch.setattr(adapter, "generate", original)
    retry = actor.post("/api/challenges", {"scheme_id": "rsa-kem", "instance_size": 1})
    assert retry.status_code == 200, retry.text
    assert count(app, ProfileIncident) == 0
