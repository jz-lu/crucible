"""Exact receipts, public correct results, and private unsuccessful attempts."""
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import registry
from crucible.db import Challenge, LoginSession, Submission, User
from test_api import Actor, system


DETAIL_FIELDS = {"id", "challenge_id", "handle", "status", "scheme_id", "scheme_version",
                 "profile_id", "instance_size", "received_at", "elapsed_seconds", "method", "compute",
                 "scoring_eligible", "scoring_policy_id", "ai_model"}
COMPUTE_FIELDS = {"hardware", "workers", "wall_seconds", "precomputation", "peak_memory", "notes"}


def submit(actor, challenge, *, name="Exact receipt method", correct=True):
    method = actor.method(name)
    answer = actor.answer(challenge, method)
    if not correct:
        answer["candidate"] = ("00" if answer["candidate"][:2] != "00" else "01") + answer["candidate"][2:]
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission"]
    assert receipt["status"] == ("correct" if correct else "incorrect")
    return receipt, method, answer


def test_correct_detail_is_exact_public_and_available_when_not_on_leaderboard(system):
    app, clock, settings = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    first = alice.challenge("dh-20")
    first_receipt, _, _ = submit(alice, first, name="Earlier method")
    clock[0] += 3600
    second = alice.challenge("dh-20")
    clock[0] += 17
    receipt, method, answer = submit(alice, second, name="Later exact method")
    board = bob.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"]
    assert [item["challenge_id"] for item in board] == [first["id"]]
    history = alice.client.get("/api/me/submissions").json()["submissions"]
    assert [item["id"] for item in history] == [receipt["id"], first_receipt["id"]]
    expected = {"id": receipt["id"], "challenge_id": second["id"], "handle": "alice", "status": "correct",
                "scheme_id": second["scheme_id"], "scheme_version": second["scheme_version"],
                "profile_id": "dh-20", "instance_size": 20, "received_at": clock[0], "elapsed_seconds": 17,
                # The archived receipt stays readable without entering today's points policy.
                "scoring_eligible": False, "scoring_policy_id": "crucible-points-v10",
                "ai_model": "",
                "method": {**{key: method[key] for key in ("id", "name", "pseudocode")}, "source": None}, "compute": answer["compute"]}
    url = f'/api/submissions/{receipt["id"]}'
    anonymous = TestClient(app)
    with app.state.db.transaction() as session:
        session_count = session.scalar(select(func.count()).select_from(LoginSession))
    for client in (alice.client, bob.client, anonymous):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["submission"] == expected
        assert set(response.json()) == {"submission", "server_time", "round"}
        assert answer["candidate"] not in response.text
    assert not anonymous.cookies, "Reading a public result does not create a session"
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(LoginSession)) == session_count
    assert alice.post("/api/auth/logout", {}).status_code == 200
    assert alice.client.get(url).json()["submission"] == expected
    # Public correctness does not depend on an expired or invalid viewer cookie.
    anonymous.cookies.set(settings.cookie_name, "invalid-session")
    assert anonymous.get(url).json()["submission"] == expected


def test_incorrect_detail_is_owner_only_with_uniform_missing_response(system):
    app, clock, settings = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    challenge = alice.challenge()
    receipt, method, answer = submit(alice, challenge, correct=False)
    url = f'/api/submissions/{receipt["id"]}'
    owned = alice.client.get(url)
    assert owned.status_code == 200
    assert owned.headers["cache-control"] == "no-store"
    assert owned.json()["submission"]["status"] == "incorrect"
    assert owned.json()["submission"]["method"]["id"] == method["id"]
    assert answer["candidate"] not in owned.text
    missing_ids = (str(uuid4()), "missing", "not-a-uuid", challenge["id"])
    anonymous = TestClient(app)
    for client in (anonymous, bob.client):
        denied = client.get(url)
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "no-store"
        assert denied.json() == {"detail": "Submission not found."}
        for missing in missing_ids:
            response = client.get(f"/api/submissions/{missing}")
            assert response.status_code == 404 and response.json() == denied.json()
    cookie = alice.client.cookies[settings.cookie_name]
    assert alice.post("/api/auth/logout", {}).status_code == 200
    assert alice.client.get(url).status_code == 404
    stale = TestClient(app)
    stale.cookies.set(settings.cookie_name, cookie)
    assert stale.get(url).status_code == 404
    # A known owner session also loses private access at its server expiry.
    alice.csrf = alice.client.get("/api/session").json()["csrf_token"]
    login = alice.post("/api/auth/login", {"handle": "alice", "password": "correct horse battery staple"})
    assert login.status_code == 200
    assert alice.client.get(url).status_code == 200
    clock[0] += settings.session_seconds + 1
    assert alice.client.get(url).status_code == 404


def test_compatible_catalogue_links_original_receipt_without_relabeling_it(system, monkeypatch):
    app, clock, _ = system
    alice = Actor(app)
    alice.register("alice")
    with monkeypatch.context() as previous:
        previous.setitem(registry.CURRENT, "teaching-dh-kem", "3")
        challenge = alice.challenge("dh-23")
    clock[0] += 5
    receipt, method, _ = submit(alice, challenge, name="Original release method")
    scheme = alice.client.get("/api/schemes").json()["schemes"][0]
    assert scheme["version"] == "4"
    assert scheme["first_recovery"]["submission_id"] == receipt["id"]
    assert scheme["first_recovery"]["scheme_version"] == "3"
    archived = next(item for item in scheme["previous_releases"] if item["version"] == "3")
    assert archived["first_recovery"]["submission_id"] == receipt["id"]
    public = TestClient(app)
    before = public.get(f'/api/submissions/{receipt["id"]}').json()["submission"]
    assert before["scheme_version"] == "3" and before["profile_id"] == "dh-23"
    assert before["instance_size"] == 23 and before["method"]["id"] == method["id"]
    # Detail must not consult the mutable catalogue or reinterpret the receipt.
    def unavailable(*args, **kwargs):
        raise AssertionError("Receipt details must use stored provenance")
    monkeypatch.setattr(registry, "manifest_for", unavailable)
    monkeypatch.setattr(registry, "adapter_for", unavailable)
    assert public.get(f'/api/submissions/{receipt["id"]}').json()["submission"] == before


def test_detail_whitelist_excludes_private_fields_and_unrecognized_compute_metadata(system):
    app, _, _ = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge()
    receipt, _, answer = submit(alice, challenge)
    with app.state.db.transaction(write=True) as session:
        result = session.get(Submission, receipt["id"])
        stored_challenge = session.get(Challenge, challenge["id"])
        user = session.get(User, result.user_id)
        private_values = [result.payload_mac, result.idempotency_key, stored_challenge.target_mac,
                          user.password_hash, answer["candidate"], "unexpected-private-metadata"]
        result.compute = {**result.compute, "private_metadata": private_values[-1]}
    response = TestClient(app).get(f'/api/submissions/{receipt["id"]}')
    assert response.status_code == 200
    detail = response.json()["submission"]
    assert set(detail) == DETAIL_FIELDS
    assert set(detail["method"]) == {"id", "name", "pseudocode", "source"}
    assert detail["method"]["source"] is None
    assert set(detail["compute"]) == COMPUTE_FIELDS
    for value in private_values:
        assert value not in response.text
    for field in ("candidate", "target_mac", "payload_mac", "idempotency_key", "password_hash", "user_id", "public", "manifest"):
        assert field not in detail


def test_detail_uses_stored_legacy_size_and_does_not_invent_missing_size(system):
    app, _, _ = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge("dh-23")
    receipt, _, _ = submit(alice, challenge)
    url = f'/api/submissions/{receipt["id"]}'
    with app.state.db.transaction(write=True) as session:
        stored = session.get(Challenge, challenge["id"])
        legacy = dict(stored.manifest)
        legacy.pop("instance_size")
        stored.manifest = legacy
    assert alice.client.get(url).json()["submission"]["instance_size"] == 23
    for invalid in (None, "23", True, -1):
        with app.state.db.transaction(write=True) as session:
            stored = session.get(Challenge, challenge["id"])
            stored.manifest = {**stored.manifest, "instance_size": invalid}
        assert alice.client.get(url).json()["submission"]["instance_size"] is None
