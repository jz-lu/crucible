"""Exact larger-parameter correctness and explicitly compatible old receipts."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible import dh_v3, dh_v4, kem, registry, rsa
from scripts.solve_teaching import recover
from test_api import Actor, system


def test_certified_dh_ladder_covers_each_size_and_rejects_bad_certificates():
    assert [item["instance_size"] for item in dh_v3.manifests()] == list(range(20, 129))
    for size in range(37, 129):
        profile = dh_v3._profile(f"dh-{size}")
        assert profile.q.bit_length() == size
        assert profile.p.bit_length() == size + 1
        assert profile.p == 2 * profile.q + 1
        # Explicit Pocklington conditions, independent of the adapter's helper.
        known_factor = 2 ** ((size + 1) // 2)
        assert (profile.q - 1) % known_factor == 0
        assert known_factor ** 2 > profile.q
        assert pow(3, profile.q - 1, profile.q) == 1
        assert pow(3, (profile.q - 1) // 2, profile.q) == profile.q - 1
        assert profile.q ** 2 > profile.p
        assert pow(2, profile.p - 1, profile.p) == 1
        assert profile.p % 3 != 0
        assert pow(profile.g, profile.q, profile.p) == 1 and profile.g != 1
        with pytest.raises(ValueError):
            dh_v3._verify_safe_prime_certificate(size, profile.p + 2, profile.q)
        with pytest.raises(ValueError):
            dh_v3._verify_safe_prime_certificate(size, 2 * (profile.q + 2) + 1, profile.q + 2)


def test_dh_v2_compatibility_is_exact_for_every_declared_profile():
    declared = registry.COMPATIBLE[(dh_v3.SCHEME_ID, "3")]["2"]
    assert declared == tuple(f"dh-{size}" for size in range(20, 37))
    for profile_id in declared:
        before, after = kem.get_manifest(profile_id), dh_v3.get_manifest(profile_id)
        for field in ("parameters", "encoding", "kdf", "algorithms", "distribution", "shared_secret_bytes"):
            assert before[field] == after[field], (profile_id, field)
        q = int(before["parameters"]["q"], 16)
        for x, r in ((1, 1), (7, 11), (q - 1, q - 1)):
            with patch.object(kem.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                previous = kem.generate(profile_id)
            with patch.object(dh_v3.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                current = dh_v3.generate(profile_id)
            assert previous.target == current.target
            old_public = {key: value for key, value in previous.public.items() if key not in ("version", "implementation_sha256")}
            new_public = {key: value for key, value in current.public.items() if key not in ("version", "implementation_sha256")}
            assert old_public == new_public
    generated = dh_v3.generate("dh-20")
    assert recover(generated.public) == generated.target.hex()


def test_new_dh_boundary_round_trips_and_frozen_sources():
    for size in (37, 63, 64, 65, 96, 127, 128):
        profile_id = f"dh-{size}"
        profile = dh_v3._profile(profile_id)
        for x, r in ((1, profile.q - 1), (profile.q - 1, 1), (profile.q - 1, profile.q - 1)):
            with patch.object(dh_v3.secrets, "randbelow", side_effect=[x - 1, r - 1]):
                generated = dh_v3.generate(profile_id)
            assert generated.diagnostics["shared_secret_matches"]
            assert len(generated.target) == 32
    assert hashlib.sha256(Path(kem.__file__).read_bytes()).hexdigest() == "d0c5c9845d85676b4ccdf582cbb242b31419518abef9ebde12ab59c214d8163b"
    assert hashlib.sha256(Path(dh_v3.__file__).read_bytes()).hexdigest() == dh_v3.IMPLEMENTATION_SHA256


def test_rsa_exact_prime_test_stays_in_its_proven_range():
    for composite in (0, 1, 9, 341, 561, 2047, 1373653, 3215031751, 341550071728321, 3825123056546413051):
        assert not rsa._is_prime(composite)
    assert rsa._is_prime(18446744073709551557)
    with pytest.raises(ValueError):
        rsa._is_prime(1 << 64)
    for profile in ("rsa-129", "rsa-999", "rsa-19"):
        with pytest.raises(ValueError):
            rsa.generate(profile)


def test_previous_receipts_keep_source_versions_while_compatible_progress_survives(system, monkeypatch):
    monkeypatch.setitem(registry.CURRENT, dh_v3.SCHEME_ID, "3")
    app, clock, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, kem.SCHEME_ID, "2")
        old = alice.challenge("dh-23")
    clock[0] += 1
    accepted = alice.post(f'/api/challenges/{old["id"]}/submit', alice.answer(old))
    assert accepted.status_code == 200
    current = bob.challenge("dh-23")
    assert current["public"]["version"] == "3"
    clock[0] += 1
    assert bob.post(f'/api/challenges/{current["id"]}/submit', bob.answer(current)).status_code == 200
    client = alice.client
    scheme = client.get("/api/schemes").json()["schemes"][0]
    assert scheme["version"] == "3"
    assert scheme["instance_size_range"] == {"min": 20, "max": 128, "step": 1}
    assert scheme["largest_recovered_profile_id"] == "dh-23"
    assert scheme["recovered_profile_ids"] == ["dh-23"]
    assert scheme["next_profile_id"] == "dh-24"
    assert scheme["first_recovery"]["handle"] == "alice"
    assert scheme["first_recovery"]["scheme_version"] == "2"
    previous = {release["version"]: release for release in scheme["previous_releases"]}
    assert {"1", "2"}.issubset(previous)
    assert previous["2"]["largest_recovered_profile_id"] == "dh-23"
    assert previous["2"]["recovered_profile_ids"] == ["dh-23"]
    assert previous["1"]["recovered_profile_ids"] == []
    assert previous["1"]["first_recovery"] is None
    board = client.get("/api/leaderboard?profile_id=dh-23&scheme_version=3").json()
    assert board["includes_compatible_versions"] == ["2"]
    assert [(entry["handle"], entry["scheme_version"]) for entry in board["entries"]] == [("alice", "2"), ("bob", "3")]
    older_board = client.get("/api/leaderboard?profile_id=dh-23&scheme_version=2").json()
    assert older_board["includes_compatible_versions"] == []
    assert [(entry["handle"], entry["scheme_version"]) for entry in older_board["entries"]] == [("alice", "2")]
    assert client.get("/api/challenges?profile_id=dh-23&scheme_version=2").json()["challenge"]["id"] == old["id"]
    assert client.get("/api/challenges?profile_id=dh-23&scheme_version=3").json()["challenge"] is None
    assert client.get(old["download_url"]).json()["scheme_manifest"]["version"] == "2"
    source = client.get(previous["2"]["protocol"]["source_url"])
    assert hashlib.sha256(source.content).hexdigest() == kem.IMPLEMENTATION_SHA256
    assert client.get("/api/leaderboard?profile_id=dh-37&scheme_version=3").json()["includes_compatible_versions"] == []
    # A later smaller current-release solve is sorted by profile size. Gaps
    # remain unrecovered, and current-only evidence must not enter the archive.
    current_lower = bob.challenge("dh-21")
    clock[0] += 1
    assert bob.post(f'/api/challenges/{current_lower["id"]}/submit', bob.answer(current_lower)).status_code == 200
    updated = client.get("/api/schemes").json()["schemes"][0]
    assert updated["recovered_profile_ids"] == ["dh-21", "dh-23"]
    archived = next(release for release in updated["previous_releases"] if release["version"] == "2")
    assert archived["recovered_profile_ids"] == ["dh-23"]


def test_largest_sizes_issue_and_unknown_sizes_are_rejected(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    for scheme_id, profile, tier in ((dh_v4.SCHEME_ID, "dh-300", 300), (rsa.SCHEME_ID, "rsa-128", 128)):
        response = actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": tier})
        assert response.status_code == 200, response.text
        assert response.json()["challenge"]["profile_id"] == profile
        assert response.json()["challenge"]["public"]["instance_size"] == tier
        assert actor.post("/api/challenges", {"scheme_id": scheme_id, "instance_size": tier + 1}).status_code == 404


def test_v4_preserves_v3_receipt_credit_and_source_without_reverse_archive_credit(system, monkeypatch):
    from uuid import uuid4
    app, clock, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    captured = {}
    for adapter in (dh_v3, dh_v4):
        real_generate = adapter.generate
        def capture(profile_id, generate=real_generate, version=adapter.SCHEME_VERSION):
            generated = generate(profile_id)
            captured[(version, profile_id)] = generated.target.hex()
            return generated
        monkeypatch.setattr(adapter, "generate", capture)

    def submit_captured(actor, challenge):
        version, profile = challenge["public"]["version"], challenge["profile_id"]
        method = actor.method("Captured target fixture")
        body = {"candidate": captured[(version, profile)], "method_id": method["id"],
                "compute": {"hardware": "Test fixture CPU", "workers": 1, "wall_seconds": 0.1,
                            "precomputation": "None", "peak_memory": "Not measured"},
                "idempotency_key": str(uuid4())}
        result = actor.post(f'/api/challenges/{challenge["id"]}/submit', body)
        assert result.status_code == 200 and result.json()["submission"]["status"] == "correct"

    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, dh_v3.SCHEME_ID, "3")
        old = alice.challenge("dh-128")
    clock[0] += 1
    submit_captured(alice, old)
    assert old["public"]["version"] == "3"
    scheme = alice.client.get("/api/schemes").json()["schemes"][0]
    assert scheme["version"] == "4" and scheme["instance_size_range"]["max"] == 300
    assert scheme["recovered_profile_ids"] == ["dh-128"]
    assert scheme["largest_recovered_profile_id"] == "dh-128"
    assert scheme["next_profile_id"] == "dh-129"
    assert scheme["first_recovery"]["handle"] == "alice" and scheme["first_recovery"]["scheme_version"] == "3"
    for target, inherited in (("dh-128", False), ("dh-127", True), ("dh-20", True)):
        board = alice.client.get("/api/leaderboard", params={"profile_id": target, "scheme_version": "4"}).json()
        assert len(board["entries"]) == 1
        row = board["entries"][0]
        assert row["handle"] == "alice" and row["profile_id"] == "dh-128"
        assert row["scheme_version"] == "3" and row["instance_size"] == 128
        assert row["is_inherited"] is inherited and row["challenge_id"] == old["id"]
    assert alice.client.get("/api/leaderboard?profile_id=dh-129&scheme_version=4").json()["entries"] == []
    downloaded = alice.client.get(old["download_url"]).json()
    assert downloaded["scheme_manifest"]["version"] == "3"
    assert downloaded["scheme_manifest"]["implementation"]["source_sha256"] == dh_v3.IMPLEMENTATION_SHA256
    archived = next(release for release in scheme["previous_releases"] if release["version"] == "3")
    source = alice.client.get(archived["protocol"]["source_url"])
    assert hashlib.sha256(source.content).hexdigest() == dh_v3.IMPLEMENTATION_SHA256

    new = bob.challenge("dh-300")
    clock[0] += 1
    submit_captured(bob, new)
    assert new["public"]["version"] == "4"
    current_board = alice.client.get("/api/leaderboard?profile_id=dh-128&scheme_version=4").json()["entries"]
    assert [(row["handle"], row["scheme_version"], row["instance_size"]) for row in current_board] == [
        ("alice", "3", 128), ("bob", "4", 300)]
    for target in ("dh-128", "dh-20"):
        archived_board = alice.client.get("/api/leaderboard", params={"profile_id": target, "scheme_version": "3"}).json()["entries"]
        assert [(row["handle"], row["scheme_version"]) for row in archived_board] == [("alice", "3")]
    updated = alice.client.get("/api/schemes").json()["schemes"][0]
    assert updated["recovered_profile_ids"] == ["dh-128", "dh-300"]
    archived = next(release for release in updated["previous_releases"] if release["version"] == "3")
    assert archived["recovered_profile_ids"] == ["dh-128"]
    assert archived["first_recovery"]["scheme_version"] == "3"
