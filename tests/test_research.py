"""Evidence provenance, public version history, and contribution authorization."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import dh_v4 as kem
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge
from crucible.research import Contribution, ContributionRevision
from scripts.solve_teaching import recover
from test_api import pin_legacy_releases


class Actor:
    def __init__(self, app, handle):
        self.client = TestClient(app)
        self.csrf = self.client.get("/api/session").json()["csrf_token"]
        self.user = None
        if handle:
            result = self.post("/api/auth/register", {"handle": handle, "password": "correct horse battery staple"})
            assert result.status_code == 200, result.text
            self.csrf, self.user = result.json()["csrf_token"], result.json()["user"]

    def post(self, path, body):
        return self.client.post(path, json=body, headers={"Origin": "http://testserver", "X-CSRF-Token": self.csrf})


@pytest.fixture
def research_system(tmp_path, monkeypatch):
    pin_legacy_releases(monkeypatch)
    clock = [3600600.0]
    settings = Settings(f"sqlite:///{tmp_path / 'research.db'}", b"r" * 32,
                        origins=("http://testserver",), rate_limits=False)
    app = create_app(settings, clock=lambda: clock[0], initialize=True)
    yield app, clock
    app.state.db.engine.dispose()


def finding(**changes):
    target_id = changes.get("target_id", "finite-field-dlog")
    return {"target_id": target_id, "target_version": kem.SCHEME_VERSION if target_id == kem.SCHEME_ID else "1", "profile_ids": [],
            "title": "A testable discrete-log argument", "claim": "This method solves the stated family under the stated conditions.",
            "conditions": "Prime-order groups; exact arithmetic; no preprocessing is included.",
            "body": "Describe the argument, its steps, and the remaining limitation here.",
            "model": "classical", "evidence_kind": "argument", "time_cost": "O(sqrt(q)) group operations",
            "memory_cost": "O(sqrt(q)) group elements", "success_probability": "Claimed: 1 for valid inputs",
            **changes}


def publish(actor, body=None):
    response = actor.post("/api/contributions", body or finding())
    assert response.status_code == 200, response.text
    return response.json()["contribution"]


def solve(actor, *, correct=True):
    challenge = actor.post("/api/challenges", {"profile_id": "dh-20"}).json()["challenge"]
    method = actor.post("/api/methods", {"name": "Reference BSGS", "pseudocode": "Find x using BSGS; derive K from c^x and the public transcript."}).json()["method"]
    answer = {"candidate": recover(challenge["public"]) if correct else "00" * 32, "method_id": method["id"],
              "compute": {"hardware": "Local CPU", "workers": 1, "wall_seconds": 0.2,
                          "precomputation": "None", "peak_memory": "Not measured"}, "idempotency_key": str(uuid4())}
    response = actor.post(f'/api/challenges/{challenge["id"]}/submit', answer)
    assert response.status_code == 200, response.text
    return challenge, method, answer, response.json()["submission"]


def test_catalogue_separates_scheme_and_problem_without_hardness_claim(research_system):
    app, _ = research_system
    response = TestClient(app).get("/api/targets")
    assert response.status_code == 200
    targets = {item["id"]: item for item in response.json()["targets"]}
    assert set(targets) == {"teaching-dh-kem", "finite-field-dlog"}
    assert targets["teaching-dh-kem"]["kind"] == "scheme"
    problem = targets["finite-field-dlog"]
    assert problem["kind"] == "assumption"
    assert "No hardness claim" in problem["definition"]
    assert [item["parameter_bits"] for item in problem["profiles"]] == list(range(20, 301))
    assert problem["related_targets"][0]["id"] == "teaching-dh-kem"
    versions = targets["teaching-dh-kem"]["versions"]
    assert [item["version"] for item in versions] == ["4", "7", "6", "5", "3", "2", "1"]
    legacy_one = next(item for item in versions if item["version"] == "1")
    assert [item["parameter_bits"] for item in legacy_one["profiles"]] == [20, 24, 28, 32, 36]
    assert [item["version"] for item in problem["versions"]] == ["1"]


def test_argument_is_public_without_a_numerical_attempt_and_stays_unreviewed(research_system):
    app, _ = research_system
    actor = Actor(app, "alice")
    record = publish(actor, finding(artifact_url="https://example.org/research"))
    assert record["author"] == actor.user
    assert record["editable"] is True
    assert record["latest"]["review_status"] == "unreviewed"
    assert record["latest"]["verification"] is None
    public = TestClient(app)
    listing = public.get("/api/contributions?target_id=finite-field-dlog").json()
    assert [item["id"] for item in listing["contributions"]] == [record["id"]]
    assert public.get("/api/contributions?target_id=teaching-dh-kem").json()["contributions"] == []
    detail = public.get(f'/api/contributions/{record["id"]}').json()["contribution"]
    assert detail["revisions"] == [record["latest"]]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0


def test_revisions_require_author_and_preserve_history_with_optimistic_concurrency(research_system):
    app, clock = research_system
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    original = publish(alice)
    path = f'/api/contributions/{original["id"]}/revisions'
    revision = finding(expected_revision=1, claim="A corrected and narrower claim.")
    assert bob.post(path, revision).status_code == 403
    clock[0] += 1
    response = alice.post(path, revision)
    assert response.status_code == 200, response.text
    updated = response.json()["contribution"]
    assert updated["created_at"] == original["created_at"]
    assert updated["updated_at"] == clock[0]
    assert updated["latest"]["revision"] == 2
    assert alice.post(path, revision).status_code == 409
    history = bob.client.get(f'/api/contributions/{original["id"]}').json()["contribution"]["revisions"]
    assert [item["revision"] for item in history] == [2, 1]
    assert history[1] == original["latest"]
    assert all(item["review_status"] == "unreviewed" for item in history)


def test_concurrent_revision_requests_append_once(research_system):
    app, _ = research_system
    actor = Actor(app, "alice")
    record = publish(actor)
    path = f'/api/contributions/{record["id"]}/revisions'
    cookies = dict(actor.client.cookies)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": actor.csrf}

    def revise(index):
        with TestClient(app) as client:
            client.cookies.update(cookies)
            return client.post(path, headers=headers, json=finding(expected_revision=1, body=f"Concurrent revision {index}")).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, (1, 2)))
    assert sorted(results) == [200, 409]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ContributionRevision)) == 2


def test_history_pages_are_bounded_and_keep_the_actual_latest_revision(research_system):
    app, clock = research_system
    actor = Actor(app, "alice")
    record = publish(actor)
    path = f'/api/contributions/{record["id"]}'
    for number in range(2, 27):
        clock[0] += 1
        response = actor.post(path + "/revisions", finding(expected_revision=number - 1, body=f"Revision {number}"))
        assert response.status_code == 200, response.text
    first = actor.client.get(path).json()["contribution"]
    assert first["latest"]["revision"] == 26
    assert [item["revision"] for item in first["revisions"]] == list(range(26, 6, -1))
    assert first["next_before_revision"] == 7
    second = actor.client.get(path, params={"before_revision": first["next_before_revision"]}).json()["contribution"]
    assert second["latest"] == first["latest"]
    assert second["updated_at"] == first["updated_at"]
    assert [item["revision"] for item in second["revisions"]] == list(range(6, 0, -1))
    assert second["next_before_revision"] is None
    assert len({item["id"] for page in (first, second) for item in page["revisions"]}) == 26
    small_page = actor.client.get(path, params={"limit": 2, "before_revision": 5}).json()["contribution"]
    assert [item["revision"] for item in small_page["revisions"]] == [4, 3]
    assert small_page["next_before_revision"] == 3
    assert small_page["latest"] == first["latest"]
    exhausted = actor.client.get(path, params={"before_revision": 1}).json()["contribution"]
    assert exhausted["revisions"] == [] and exhausted["next_before_revision"] is None
    assert exhausted["latest"] == first["latest"]
    for query in ({"limit": 0}, {"limit": 51}, {"before_revision": 0}, {"before_revision": -1},
                  {"before_revision": 2**63}):
        assert actor.client.get(path, params=query).status_code == 422


@pytest.mark.parametrize("changes,status", [
    ({"claim": "   "}, 422), ({"conditions": ""}, 422), ({"body": "x" * 15001}, 422),
    ({"review_status": "instance-verified"}, 422), ({"verified_submission_id": str(uuid4())}, 422),
    ({"model": "verified"}, 422), ({"evidence_kind": "proof-checked"}, 422),
    ({"target_id": "unknown"}, 404), ({"target_version": "2"}, 422),
    ({"profile_ids": ["unknown"]}, 422), ({"profile_ids": ["dh-20", "dh-20"]}, 422),
    ({"related_contribution_id": str(uuid4())}, 404),
    ({"artifact_url": "javascript:alert(1)"}, 422), ({"artifact_url": "http://example.org"}, 422),
    ({"artifact_url": "https://user:password@example.org"}, 422),
    ({"artifact_url": "https://example.org:invalid"}, 422),
])
def test_untrusted_claims_cannot_choose_verification_or_escape_target_contract(research_system, changes, status):
    app, _ = research_system
    actor = Actor(app, "alice")
    response = actor.post("/api/contributions", finding(**changes))
    assert response.status_code == status, response.text
    assert actor.client.get("/api/contributions").json()["contributions"] == []


def test_auth_csrf_and_followups(research_system):
    app, _ = research_system
    anonymous = Actor(app, None)
    assert anonymous.post("/api/contributions", finding()).status_code == 401
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    assert alice.client.post("/api/contributions", json=finding(), headers={"Origin": "http://testserver"}).status_code == 403
    root = publish(alice)
    followup = publish(bob, finding(related_contribution_id=root["id"], title="A limitation of the original argument"))
    assert followup["latest"]["related_contribution_id"] == root["id"]
    assert alice.post(f'/api/contributions/{root["id"]}/revisions',
                      finding(expected_revision=1, related_contribution_id=root["id"])).status_code == 422


def test_latest_revision_pagination_is_bounded_and_handles_ties(research_system):
    app, clock = research_system
    actor = Actor(app, "alice")
    records = [publish(actor, finding(title=f"Finding {index}")) for index in range(3)]
    first = actor.client.get("/api/contributions?limit=2").json()
    assert len(first["contributions"]) == 2 and first["next_before"]
    second = actor.client.get("/api/contributions", params={"limit": 2, "before": first["next_before"]}).json()
    assert len(second["contributions"]) == 1 and second["next_before"] is None
    ids = [item["id"] for page in (first, second) for item in page["contributions"]]
    assert len(set(ids)) == 3 and set(ids) == {item["id"] for item in records}
    clock[0] += 1
    actor.post(f'/api/contributions/{records[0]["id"]}/revisions', finding(expected_revision=1, title="Updated finding"))
    latest = actor.client.get("/api/contributions?limit=1").json()["contributions"][0]
    assert latest["id"] == records[0]["id"] and latest["latest"]["revision"] == 2
    assert actor.client.get("/api/contributions?limit=101").status_code == 422
    malformed = base64.urlsafe_b64encode(json.dumps([1, 123]).encode()).decode()
    assert actor.client.get("/api/contributions", params={"before": malformed}).status_code == 422


def test_correct_run_only_becomes_evidence_when_author_explicitly_attaches_it(research_system):
    app, clock = research_system
    actor = Actor(app, "alice")
    challenge, method, answer, submission = solve(actor)
    assert actor.client.get("/api/contributions").json()["contributions"] == []
    record = publish(actor, finding(target_id="teaching-dh-kem", profile_ids=["dh-20"],
                                   submission_id=submission["id"], evidence_kind="mixed"))
    response = actor.client.get("/api/contributions?target_id=teaching-dh-kem")
    assert answer["candidate"] not in response.text
    records = response.json()["contributions"]
    assert len(records) == 1
    record = records[0]
    revision = record["latest"]
    assert record["editable"] is True
    assert revision["review_status"] == "unreviewed"
    assert revision["verification"]["submission_id"] == submission["id"]
    assert revision["verification"]["challenge_id"] == challenge["id"]
    assert "not independently verified" in revision["verification"]["scope"]
    assert "finding remains unreviewed" in revision["verification"]["scope"]
    assert revision["verification"]["profile_id"] == "dh-20"
    assert revision["profile_ids"] == ["dh-20"]
    revised_body = finding(target_id="teaching-dh-kem", profile_ids=["dh-20"], submission_id=submission["id"],
                           expected_revision=1, claim="A corrected interpretation of this recovery.")
    revised = actor.post(f'/api/contributions/{record["id"]}/revisions', revised_body)
    assert revised.status_code == 200, revised.text
    assert revised.json()["contribution"]["latest"]["review_status"] == "unreviewed"
    followup = publish(actor, finding(target_id="teaching-dh-kem", profile_ids=["dh-20"],
                                     submission_id=submission["id"], related_contribution_id=record["id"]))
    assert followup["latest"]["review_status"] == "unreviewed"
    assert actor.post(f'/api/challenges/{challenge["id"]}/submit', answer).status_code == 200
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Contribution)) == 2
        assert session.scalar(select(func.count()).select_from(ContributionRevision)
                              .where(ContributionRevision.verified_submission_id.is_not(None))) == 3
    detached = actor.post(f'/api/contributions/{record["id"]}/revisions',
                          finding(target_id="teaching-dh-kem", expected_revision=2, submission_id=None))
    assert detached.status_code == 200, detached.text
    history = actor.client.get(f'/api/contributions/{record["id"]}').json()["contribution"]["revisions"]
    assert history[0]["verification"] is None
    assert history[1]["verification"]["submission_id"] == submission["id"]
    assert history[2]["verification"]["submission_id"] == submission["id"]


def test_wrong_answer_has_no_verified_research_record(research_system):
    app, clock = research_system
    actor = Actor(app, "alice")
    challenge, method, _, submission = solve(actor, correct=False)
    assert actor.client.get("/api/contributions").json()["contributions"] == []
    response = actor.post("/api/contributions", finding(target_id="teaching-dh-kem", profile_ids=["dh-20"],
                                                       submission_id=submission["id"]))
    assert response.status_code == 422


def test_supporting_recovery_requires_ownership_and_precise_target_coverage(research_system):
    app, _ = research_system
    alice, bob = Actor(app, "alice"), Actor(app, "bob")
    _, _, _, submission = solve(alice)
    matching = finding(target_id="teaching-dh-kem", profile_ids=["dh-20"], submission_id=submission["id"])
    assert bob.post("/api/contributions", matching).status_code == 404
    assert alice.post("/api/contributions", finding(submission_id=submission["id"], profile_ids=["dh-20"])).status_code == 422
    assert alice.post("/api/contributions", {**matching, "profile_ids": []}).status_code == 422
    assert alice.post("/api/contributions", {**matching, "profile_ids": ["dh-24"]}).status_code == 422
    assert alice.post("/api/contributions", {**matching, "target_version": "1"}).status_code == 422
    assert alice.post("/api/contributions", {**matching, "submission_id": str(uuid4())}).status_code == 404
    assert publish(alice, matching)["latest"]["review_status"] == "unreviewed"


def test_contributions_survive_process_restart(research_system):
    app, clock = research_system
    actor = Actor(app, "alice")
    record = publish(actor)
    restarted = create_app(app.state.settings, clock=lambda: clock[0])
    try:
        result = TestClient(restarted).get(f'/api/contributions/{record["id"]}')
        assert result.status_code == 200
        assert result.json()["contribution"]["latest"] == record["latest"]
    finally:
        restarted.state.db.engine.dispose()


def test_legacy_findings_keep_their_profiles_and_supporting_receipts_after_upgrade(research_system, monkeypatch):
    import crucible.app as app_module
    import crucible.research as research_module
    from crucible.legacy import teaching_dh_v1
    app, clock = research_system
    actor = Actor(app, "alice")
    with monkeypatch.context() as previous_release:
        previous_release.setitem(app_module.registry.CURRENT, teaching_dh_v1.SCHEME_ID, "1")
        previous_release.setattr(research_module, "kem", teaching_dh_v1)
        _, _, _, submission = solve(actor)
        original = publish(actor, finding(target_id="teaching-dh-kem", target_version="1",
                                         profile_ids=["dh-20"], submission_id=submission["id"]))
    cookies = dict(actor.client.cookies)
    restarted = create_app(app.state.settings, clock=lambda: clock[0])
    actor.client = TestClient(restarted)
    actor.client.cookies.update(cookies)
    try:
        path = f'/api/contributions/{original["id"]}'
        assert actor.client.get(path).json()["contribution"]["latest"] == original["latest"]
        revised_body = finding(target_id="teaching-dh-kem", target_version="1", profile_ids=["dh-20"],
                               submission_id=submission["id"], expected_revision=1, claim="A narrower claim about the archived release.")
        revised = actor.post(path + "/revisions", revised_body)
        assert revised.status_code == 200, revised.text
        latest = revised.json()["contribution"]["latest"]
        assert latest["target_version"] == "1" and latest["revision"] == 2
        assert latest["verification"]["scheme_version"] == "1"
        assert latest["review_status"] == "unreviewed"
        assert actor.client.get(path).json()["contribution"]["revisions"][1] == original["latest"]
        new_legacy = publish(actor, finding(target_id="teaching-dh-kem", target_version="1", profile_ids=["dh-24"],
                                           related_contribution_id=original["id"]))
        assert new_legacy["latest"]["target_version"] == "1"
        invalid = finding(target_id="teaching-dh-kem", target_version="1", profile_ids=["dh-21"])
        assert actor.post("/api/contributions", invalid).status_code == 422
        assert actor.post(path + "/revisions", {**invalid, "expected_revision": 2}).status_code == 422
        assert actor.post("/api/contributions", finding(target_id="teaching-dh-kem", target_version="unknown")).status_code == 422
    finally:
        restarted.state.db.engine.dispose()
