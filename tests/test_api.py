from uuid import uuid4
import hashlib
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge, LoginSession, Submission, User
from scripts.solve_teaching import recover


class Actor:
    def __init__(self, app):
        self.client = TestClient(app)
        self.csrf = self.client.get("/api/session").json()["csrf_token"]

    def post(self, path, body):
        return self.client.post(path, json=body, headers={"Origin": "http://testserver", "X-CSRF-Token": self.csrf})

    def register(self, handle):
        response = self.post("/api/auth/register", {"handle": handle, "password": "correct horse battery staple"})
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]
        return response.json()["user"]

    def challenge(self, profile="dh-20"):
        response = self.post("/api/challenges", {"profile_id": profile})
        assert response.status_code == 200, response.text
        return response.json()["challenge"]

    def method(self, name="BSGS reference"):
        response = self.post("/api/methods", {"name": name, "pseudocode": "Find x with BSGS; compute c^x; apply the specified transcript KDF."})
        assert response.status_code == 200, response.text
        return response.json()["method"]

    def answer(self, challenge, method=None):
        method = method or self.method()
        return {"candidate": recover(challenge["public"]), "method_id": method["id"],
                "compute": {"hardware": "Local test CPU", "workers": 1, "wall_seconds": 0.1,
                            "precomputation": "None", "peak_memory": "Not measured", "notes": "Automated fixture"},
                "idempotency_key": str(uuid4())}


@pytest.fixture
def system(tmp_path):
    clock = [3600600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'test.db'}", b"s" * 32, origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    yield app, clock, settings
    app.state.db.engine.dispose()


def test_full_public_input_recovery_and_durable_receipt(system):
    app, clock, settings = system
    alice = Actor(app)
    alice.register("Alice")
    challenge = alice.challenge()
    assert challenge["starts_at"] == 3600000
    assert challenge["ends_at"] == 3603600
    assert challenge["issued_at"] == 3600600
    downloaded = alice.client.get(challenge["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.headers["cache-control"] == "no-store"
    assert recover(downloaded.json()) == recover(challenge["public"])
    answer = alice.answer(challenge)
    clock[0] += 42
    response = alice.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert response.status_code == 200, response.text
    assert response.json()["submission"]["status"] == "correct"
    board = alice.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"]
    assert len(board) == 1
    assert board[0]["handle"] == "alice"
    assert board[0]["elapsed_seconds"] == 42
    assert board[0]["compute"]["wall_seconds"] == 0.1
    assert answer["candidate"] not in response.text
    assert answer["candidate"] not in str(board)
    restarted = create_app(settings, clock=lambda: clock[0])
    assert TestClient(restarted).get("/api/leaderboard?profile_id=dh-20").json()["entries"] == board
    restarted.state.db.engine.dispose()


def test_auth_rotation_logout_and_session_storage(system):
    app, clock, _ = system
    actor = Actor(app)
    anonymous = actor.client.cookies["crucible_session"]
    actor.register("researcher")
    authenticated = actor.client.cookies["crucible_session"]
    assert authenticated != anonymous
    with app.state.db.transaction() as session:
        user = session.scalar(select(User))
        assert user.password_hash.startswith("$argon2id$")
        assert "correct horse" not in user.password_hash
        assert all(record.token_hash not in {anonymous, authenticated} for record in session.scalars(select(LoginSession)))
    stolen_old = TestClient(app)
    stolen_old.cookies.set("crucible_session", anonymous)
    assert stolen_old.get("/api/methods").status_code == 401
    response = actor.post("/api/auth/logout", {})
    assert response.status_code == 200
    actor.csrf = response.json()["csrf_token"]
    assert actor.client.get("/api/methods").status_code == 401
    stolen_old.cookies.set("crucible_session", authenticated)
    assert stolen_old.get("/api/methods").status_code == 401
    response = actor.post("/api/auth/login", {"handle": "RESEARCHER", "password": "correct horse battery staple"})
    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    clock[0] += 86401
    assert actor.client.get("/api/methods").status_code == 401


def test_csrf_origin_and_host_enforcement(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    for headers in ({"Origin": "http://evil.example", "X-CSRF-Token": actor.csrf},
                    {"Origin": "http://testserver"}, {},
                    {"Origin": "null", "X-CSRF-Token": actor.csrf}):
        assert actor.client.post("/api/challenges", json={"profile_id": "dh-20"}, headers=headers).status_code == 403
    assert actor.client.get("/api/session", headers={"Host": "evil.example"}).status_code == 400


def test_ownership_and_bad_input_do_not_consume_attempt(system):
    app, _, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    challenge = alice.challenge()
    bob_challenge = bob.challenge()
    assert challenge["id"] != bob_challenge["id"]
    assert challenge["public"] != bob_challenge["public"]
    assert bob.client.get(challenge["download_url"]).status_code == 404
    foreign = alice.answer(challenge, bob.method())
    url = f'/api/challenges/{challenge["id"]}/submit'
    assert bob.post(url, foreign).status_code == 404
    assert alice.post(url, foreign).status_code == 404
    answer = alice.answer(challenge)
    malformed = {**answer, "candidate": "private-value-that-must-not-be-echoed"}
    response = alice.post(url, malformed)
    assert response.status_code == 422
    assert malformed["candidate"] not in response.text
    assert alice.post(url, answer).json()["submission"]["status"] == "correct"


def test_one_answer_and_idempotency_after_round_closes(system):
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    body = actor.answer(challenge)
    url = f'/api/challenges/{challenge["id"]}/submit'
    original = actor.post(url, body).json()["submission"]
    assert actor.post(url, {**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert actor.post(url, {**body, "candidate": "00" * 32}).status_code == 409
    clock[0] = challenge["ends_at"] + 1
    assert actor.post(url, body).json()["submission"] == original
    assert actor.client.get("/api/challenges?profile_id=dh-20").json()["challenge"] is None
    assert actor.challenge()["id"] != challenge["id"]


@pytest.mark.parametrize("offset,expected", [(-0.001, 200), (0, 409), (0.001, 409)])
def test_exact_hour_boundary(system, offset, expected):
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    body = actor.answer(challenge)
    clock[0] = challenge["ends_at"] + offset
    assert actor.post(f'/api/challenges/{challenge["id"]}/submit', body).status_code == expected


def test_wrong_answer_is_recorded_but_not_ranked(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    body = {**actor.answer(challenge), "candidate": "00" * 32}
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', body)
    assert response.json()["submission"]["status"] == "incorrect"
    assert actor.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"] == []
    history = actor.client.get("/api/me/submissions").json()["submissions"]
    assert history[0]["status"] == "incorrect"


def test_leaderboard_first_accounts_at_or_above_profile_and_receipt_order(system):
    app, clock, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    a, b = alice.challenge(), bob.challenge()
    clock[0] += 1
    bob.post(f'/api/challenges/{b["id"]}/submit', bob.answer(b))
    clock[0] += 1
    alice.post(f'/api/challenges/{a["id"]}/submit', alice.answer(a))
    clock[0] = a["ends_at"] + 1
    again = bob.challenge()
    bob.post(f'/api/challenges/{again["id"]}/submit', bob.answer(again))
    board = alice.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"]
    assert [row["handle"] for row in board] == ["bob", "alice"]
    assert board[0]["challenge_id"] == b["id"]
    assert alice.client.get("/api/leaderboard?profile_id=dh-24").json()["entries"] == []


def test_profile_manifest_contains_correctness_and_no_security_bit_claim(system):
    from crucible import dh_v3 as kem
    app, _, _ = system
    schemes = TestClient(app).get("/api/schemes").json()["schemes"]
    profiles = schemes[0]["profiles"]
    assert schemes[0]["version"] == kem.SCHEME_VERSION == "3"
    assert [profile["parameter_bits"] for profile in profiles] == list(range(20, 129))
    assert [profile["instance_size"] for profile in profiles] == list(range(20, 129))
    size_parameter = schemes[0]["size_parameter"]
    assert set(size_parameter) == set(kem.SIZE_PARAMETER) | {"mapping_math"}
    assert {key: value for key, value in size_parameter.items() if key != "mapping_math"} == kem.SIZE_PARAMETER
    assert r"\(" in size_parameter["mapping_math"]
    for profile in profiles:
        assert profile["correctness"]["failure_bound"] == "0"
        assert profile["correctness"]["argument"]
        assert profile["admission"]["official_ranked"] is False
        assert profile["parameter_label"] == "subgroup-order bits"
        assert "security_bits" not in profile


def test_instance_size_selector_maps_to_the_same_pinned_profile_and_hourly_challenge(system):
    from crucible import dh_v3 as kem
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    for size in (24, 21):
        body = {"scheme_id": kem.SCHEME_ID, "instance_size": size}
        response = actor.post("/api/challenges", body)
        assert response.status_code == 200, response.text
        challenge = response.json()["challenge"]
        assert challenge["profile_id"] == f"dh-{size}"
        assert challenge["public"]["instance_size"] == size
        assert actor.challenge(f"dh-{size}")["id"] == challenge["id"]
        assert actor.post("/api/challenges", body).json()["challenge"]["id"] == challenge["id"]
        with app.state.db.transaction() as session:
            stored = session.get(Challenge, challenge["id"])
            assert stored.manifest == kem.get_manifest(f"dh-{size}")
            assert stored.manifest["instance_size"] == size
    old_selector = actor.challenge("dh-20")
    response = actor.post("/api/challenges", {"scheme_id": kem.SCHEME_ID, "instance_size": 20})
    assert response.json()["challenge"]["id"] == old_selector["id"]


@pytest.mark.parametrize("body,status", [
    ({"scheme_id": "teaching-dh-kem", "instance_size": 19}, 404),
    ({"scheme_id": "teaching-dh-kem", "instance_size": 129}, 404),
    ({"scheme_id": "unknown-scheme", "instance_size": 24}, 404),
    ({}, 422), ({"profile_id": None}, 422),
    ({"scheme_id": "teaching-dh-kem"}, 422), ({"instance_size": 24}, 422),
    ({"scheme_id": None, "instance_size": 24}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": None}, 422),
    ({"profile_id": "dh-24", "scheme_id": "teaching-dh-kem", "instance_size": 24}, 422),
    ({"profile_id": "dh-24", "instance_size": 24}, 422),
    ({"profile_id": "dh-24", "scheme_id": None}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": True}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": False}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": 24.0}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": 24.5}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": "24"}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": 0}, 422),
    ({"scheme_id": "teaching-dh-kem", "instance_size": -1}, 422),
])
def test_instance_size_selector_rejects_unsupported_or_ambiguous_input(system, body, status):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    response = actor.post("/api/challenges", body)
    assert response.status_code == status, response.text
    assert "unknown-scheme" not in response.text
    with app.state.db.transaction() as session:
        assert session.scalar(select(Challenge)) is None


def test_candidate_encoding_and_exact_manifest_length_do_not_consume_attempt(system):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    answer = actor.answer(challenge)
    path = f'/api/challenges/{challenge["id"]}/submit'
    for candidate in ("11" * 31, "11" * 33, "", "f", "fg", "11" * 4097):
        response = actor.post(path, {**answer, "candidate": candidate})
        assert response.status_code == 422, response.text
        if len(candidate) >= 16:
            assert candidate not in response.text
        with app.state.db.transaction() as session:
            assert session.scalar(select(Submission).where(Submission.challenge_id == challenge["id"])) is None
    assert actor.post(path, answer).json()["submission"]["status"] == "correct"


@pytest.mark.parametrize("declaration", [None, True, 0, "32", 4097])
def test_invalid_stored_shared_secret_length_fails_closed_without_using_attempt(system, declaration):
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    answer = actor.answer(challenge)
    with app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, challenge["id"])
        original = dict(stored.manifest)
        invalid = dict(original)
        if declaration is None:
            invalid.pop("shared_secret_bytes")
        else:
            invalid["shared_secret_bytes"] = declaration
        stored.manifest = invalid
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert response.status_code == 503, response.text
    assert answer["candidate"] not in response.text
    with app.state.db.transaction(write=True) as session:
        assert session.scalar(select(Submission).where(Submission.challenge_id == challenge["id"])) is None
        session.get(Challenge, challenge["id"]).manifest = original
    assert actor.post(f'/api/challenges/{challenge["id"]}/submit', answer).json()["submission"]["status"] == "correct"


def test_adapter_declared_sixteen_byte_secret_can_be_verified(system, monkeypatch):
    from copy import deepcopy
    from crucible import dh_v3 as kem
    app, _, _ = system
    original_manifest, original_generate = kem.get_manifest, kem.generate

    def encoding():
        return {"shared_secret_bytes": 16, "shared_secret": "exactly 32 hexadecimal characters (16 complete bytes)"}

    def shorter_manifest(profile_id):
        manifest = deepcopy(original_manifest(profile_id))
        manifest["shared_secret_bytes"] = 16
        manifest["encoding"].update(encoding())
        return manifest

    def shorter_generate(profile_id):
        generated = original_generate(profile_id)
        public = deepcopy(generated.public)
        public["encoding"].update(encoding())
        return kem.GeneratedChallenge(public=public, target=generated.target[:16], diagnostics=dict(generated.diagnostics))

    monkeypatch.setattr(kem, "get_manifest", shorter_manifest)
    monkeypatch.setattr(kem, "generate", shorter_generate)
    actor = Actor(app)
    actor.register("alice")
    challenge = actor.challenge()
    answer = actor.answer(challenge)
    answer["candidate"] = answer["candidate"][:32]
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert response.status_code == 200, response.text
    assert response.json()["submission"]["status"] == "correct"
    downloaded = actor.client.get(challenge["download_url"]).json()
    assert downloaded["scheme_manifest"]["shared_secret_bytes"] == 16
    assert "32-byte" not in downloaded["objective"]


def test_legacy_challenge_receipts_and_source_survive_version_two_restart(system, monkeypatch):
    import crucible.app as app_module
    from crucible import dh_v3 as kem
    from crucible.legacy import teaching_dh_v1
    app, clock, settings = system
    actor = Actor(app)
    actor.register("alice")
    with monkeypatch.context() as previous_release:
        previous_release.setitem(app_module.registry.CURRENT, teaching_dh_v1.SCHEME_ID, "1")
        old = actor.challenge("dh-24")
    assert old["public"]["version"] == "1"
    answer = actor.answer(old)
    with app.state.db.transaction() as session:
        stored_hash = session.get(Challenge, old["id"]).manifest["implementation"]["source_sha256"]
    cookies = dict(actor.client.cookies)
    restarted = create_app(settings, clock=lambda: clock[0])
    actor.client = TestClient(restarted)
    actor.client.cookies.update(cookies)
    try:
        accepted = actor.post(f'/api/challenges/{old["id"]}/submit', answer)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["submission"]["status"] == "correct"
        legacy_board = actor.client.get("/api/leaderboard?profile_id=dh-24&scheme_version=1").json()
        assert legacy_board["scheme_version"] == "1"
        assert [item["handle"] for item in legacy_board["entries"]] == ["alice"]
        assert actor.client.get("/api/leaderboard?profile_id=dh-24").json()["entries"] == []
        assert actor.client.get("/api/leaderboard?profile_id=dh-21&scheme_version=1").status_code == 404
        assert actor.client.get("/api/leaderboard?profile_id=dh-24&scheme_version=unknown").status_code == 404
        legacy_source = actor.client.get("/api/protocol/source?version=1")
        assert legacy_source.status_code == 200
        assert hashlib.sha256(legacy_source.content).hexdigest() == stored_hash == teaching_dh_v1.IMPLEMENTATION_SHA256
        assert stored_hash == "d3cfc485212a60cefc9903b5410797c3942391e6280cfbd51c95559d59eb537c"
        source = actor.client.get("/api/protocol/source")
        assert hashlib.sha256(source.content).hexdigest() == kem.IMPLEMENTATION_SHA256
        assert actor.client.get("/api/protocol/source?version=3").content == source.content
        assert actor.client.get("/api/protocol/source?version=unknown").status_code == 404
        assert actor.client.get("/api/me/submissions").json()["submissions"][0]["scheme_version"] == "1"
        current = actor.challenge("dh-24")
        assert current["id"] != old["id"] and current["public"]["version"] == "3"
    finally:
        restarted.state.db.engine.dispose()


def test_progress_uses_only_current_successes_and_next_numeric_profile(system, monkeypatch):
    import crucible.app as app_module
    from crucible.legacy import teaching_dh_v1
    app, _, _ = system
    actor = Actor(app)
    actor.register("alice")

    def progress():
        return actor.client.get("/api/schemes").json()["schemes"][0]

    assert progress()["next_profile_id"] == "dh-20"
    assert progress()["largest_recovered_profile_id"] is None
    with monkeypatch.context() as previous_release:
        previous_release.setitem(app_module.registry.CURRENT, teaching_dh_v1.SCHEME_ID, "1")
        old = actor.challenge("dh-32")
    assert actor.post(f'/api/challenges/{old["id"]}/submit', actor.answer(old)).status_code == 200
    assert progress()["next_profile_id"] == "dh-20"
    assert progress()["largest_recovered_profile_id"] is None
    middle = actor.challenge("dh-23")
    assert actor.post(f'/api/challenges/{middle["id"]}/submit', actor.answer(middle)).status_code == 200
    assert progress()["largest_recovered_profile_id"] == "dh-23"
    assert progress()["next_profile_id"] == "dh-24"
    wrong = actor.challenge("dh-25")
    bad_answer = {**actor.answer(wrong), "candidate": "00" * 32}
    assert actor.post(f'/api/challenges/{wrong["id"]}/submit', bad_answer).json()["submission"]["status"] == "incorrect"
    assert progress()["next_profile_id"] == "dh-24"
    maximum = actor.challenge("dh-36")
    assert actor.post(f'/api/challenges/{maximum["id"]}/submit', actor.answer(maximum)).status_code == 200
    assert progress()["largest_recovered_profile_id"] == "dh-36"
    assert progress()["next_profile_id"] == "dh-37"
    inherited = actor.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"]
    assert [(entry["handle"], entry["profile_id"], entry["is_inherited"]) for entry in inherited] == [("alice", "dh-23", True)]


def test_source_change_requires_new_scheme_version(system, monkeypatch):
    from crucible import dh_v3 as kem
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    original = actor.challenge()
    answer = actor.answer(original)
    monkeypatch.setattr(kem, "IMPLEMENTATION_SHA256", "f" * 64)
    denied = actor.post("/api/challenges", {"profile_id": "dh-24"})
    assert denied.status_code == 409
    assert "new scheme version" in denied.json()["detail"]
    # The stored answer commitment remains valid for the original instance.
    assert actor.post(f'/api/challenges/{original["id"]}/submit', answer).json()["submission"]["status"] == "correct"


def test_legacy_database_cannot_pin_new_code_over_old_instances(system, monkeypatch):
    from crucible import dh_v3 as kem
    from crucible.db import AdapterVersion
    from sqlalchemy import delete
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    actor.challenge()
    with app.state.db.transaction(write=True) as session:
        session.execute(delete(AdapterVersion))  # Simulate the schema-0001 data.
    old_hash = kem.IMPLEMENTATION_SHA256
    monkeypatch.setattr(kem, "IMPLEMENTATION_SHA256", "e" * 64)
    clock[0] += 3600
    assert actor.post("/api/challenges", {"profile_id": "dh-24"}).status_code == 409
    with app.state.db.transaction() as session:
        assert session.scalar(select(AdapterVersion)) is None
    monkeypatch.setattr(kem, "IMPLEMENTATION_SHA256", old_hash)
    assert actor.post("/api/challenges", {"profile_id": "dh-24"}).status_code == 200
    with app.state.db.transaction() as session:
        assert session.scalar(select(AdapterVersion)).source_sha256 == old_hash


def test_database_rate_limit_counts_failed_logins_and_recovers(system):
    from dataclasses import replace
    app, clock, settings = system
    limited = create_app(replace(settings, rate_limits=True), clock=lambda: clock[0])
    actor = Actor(limited)
    actor.register("alice")
    for _ in range(10):
        response = actor.post("/api/auth/login", {"handle": "alice", "password": "incorrect password but valid length"})
        assert response.status_code == 401
    response = actor.post("/api/auth/login", {"handle": "alice", "password": "correct horse battery staple"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    clock[0] += 901
    assert actor.post("/api/auth/login", {"handle": "alice", "password": "correct horse battery staple"}).status_code == 200
    limited.state.db.engine.dispose()


def test_adapter_exception_persists_pause_without_leaking_exception(system, monkeypatch):
    from crucible import dh_v3 as kem
    from crucible.db import ProfileIncident
    app, clock, _ = system
    actor = Actor(app)
    actor.register("alice")
    calls = []
    def fail(profile):
        calls.append(profile)
        raise ValueError("PRIVATE-EXCEPTION-MATERIAL")
    monkeypatch.setattr(kem, "generate", fail)
    response = actor.post("/api/challenges", {"profile_id": "dh-20"})
    assert response.status_code == 503
    assert "PRIVATE-EXCEPTION" not in response.text
    clock[0] += 3600
    assert actor.post("/api/challenges", {"profile_id": "dh-20"}).status_code == 503
    assert len(calls) == 1
    with app.state.db.transaction() as session:
        fault = session.scalar(select(ProfileIncident))
        assert fault.diagnostics["reason"] == "adapter_execution_failed"
        assert "PRIVATE-EXCEPTION" not in str(fault.diagnostics)
