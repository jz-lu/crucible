"""Real submissions combine on profiles and ranking, without fabricated awards.

The admission fixture reuses registered RSA source to test the operator gates in
an empty local ledger. Its explicit synthetic approval is not scientific novelty
evidence. No hosted data or cryptographic implementation is changed.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import registry, scoring
from crucible.app import create_app
from crucible.bounty_models import BountyAward
from crucible.protocol_specs import builtin_spec
from crucible.scoring_awards import (SchemeAdmissionCredit, WEEK_SECONDS,
                                    award_scheme_credit, retire_scheme_credit)
from proposal_fixtures import correctness_review_spec, tier_design
from scripts.solve_rsa_frontier import recover as recover_rsa
from scripts.solve_teaching_frontier import recover as recover_dh
from test_bounties import (make_system, create as create_bounty, request, decision,
                          body, post, boost, submit as contribute, assess, available)
from test_notebook import Actor, create as create_note, content, request_publication, approve
from test_note_ratings import agent, vote
from test_protocol_proposals import submit as propose_scheme, review
from test_research_reviews import submit as propose_research


def rehearse(system, monkeypatch, *, recovery="teaching-dh-kem", first_approval="poster"):
    """Return durable evidence for every checked step, also used by browser QA."""
    app, poster, reviewer, solver, supporter, other_reviewer, now = system
    monkeypatch.setattr(scoring, "time", SimpleNamespace(time=lambda: now[0]))
    actors = (poster, reviewer, solver, supporter, other_reviewer)
    handles = ("poster", "reviewer", "solver", "supporter", "reviewer2")
    expected = {handle: dict(progress=0, early_recovery=0, scheme_contributions=0,
                             note_contributions=0, bounty_points=0) for handle in handles}
    accepted = {handle: 0 for handle in handles}
    checkpoints = []

    def check(label):
        response = solver.client.get("/api/people?include_records=false")
        assert response.status_code == 200, response.text
        board = response.json()["people"]
        assert len(board) == len(handles)
        for row in board:
            handle = row["handle"]
            profile = solver.client.get(f"/api/people/{handle}?include_milestones=false").json()["person"]
            for actual in (row, profile):
                assert actual["score_breakdown"] == expected[handle], (label, handle, actual)
                assert actual["points"] == sum(expected[handle].values()), (label, handle, actual)
                assert actual["accepted_proposal_count"] == accepted[handle], (label, handle, actual)
                assert actual["rank"] == 1 + sum(sum(parts.values()) > actual["points"]
                                                for parts in expected.values()), (label, handle, actual)
        assert [(row["points"], row["handle"]) for row in board] == sorted(
            [(sum(parts.values()), handle) for handle, parts in expected.items()], key=lambda item: (-item[0], item[1]))
        checkpoints.append({"step": label, "points": {row["handle"]: row["points"] for row in board},
                            "solver_breakdown": deepcopy(expected["solver"]), "accepted_proposals": accepted["solver"]})

    check("empty accounts")
    research_note = create_note(solver, content(title="Synthetic scoring rehearsal: research note"))
    attack = create_note(solver, content(title="Synthetic scoring rehearsal: attack", tags=["attack-analysis"]))
    procedure = propose_research(solver, attack, purpose="procedure",
        title="Synthetic procedure proposal", question="Review this disposable workflow, not a scientific claim.")
    check("private notes, attack and submitted procedure proposal earn zero")
    request_publication(solver, research_note)
    check("publication request earns zero")
    approve(reviewer, research_note)
    check("publication approval alone earns zero")

    rater, second_rater = agent(supporter), agent(reviewer)
    assert vote(rater, research_note, 1).status_code == 200
    expected["solver"]["note_contributions"] = 1
    check("one independent note rating")
    assert vote(rater, research_note, 2, 1).status_code == 200
    expected["solver"]["note_contributions"] = 2
    check("rating changes from one to two")
    assert vote(rater, research_note, 0, 2).status_code == 200
    expected["solver"]["note_contributions"] = 0
    check("removing a rating removes its points")
    assert vote(rater, research_note, 1, 3).status_code == 200
    assert vote(second_rater, research_note, 2).status_code == 200
    assert vote(agent(solver), research_note, 2).status_code == 403
    assert vote(agent(supporter), research_note, 1, 3).status_code == 200
    expected["solver"]["note_contributions"] = 3
    check("two accounts supply three points; self-ratings fail and retries do not stack")

    method = post(solver, "/api/methods", {"name": "Synthetic public-input reference",
        "pseudocode": "Run the bundled bounded teaching solver on the downloaded public transcript."})["method"]
    def recover(actor, tier, *, wrong=False, archived=False, scheme=None):
        now[0] += 1
        scheme_id = "teaching-dh-kem" if archived else (scheme or recovery)
        selector = {"scheme_id": scheme_id,
                    "instance_size": tier, **({"scheme_version": "4"} if archived else {})}
        challenge = post(actor, "/api/challenges", selector)["challenge"]
        downloaded = actor.client.get(challenge["download_url"])
        assert downloaded.status_code == 200, downloaded.text
        candidate = "0" * 64 if wrong else (recover_dh if scheme_id == "teaching-dh-kem" else recover_rsa)(downloaded.json())
        own_method = method if actor is solver else post(actor, "/api/methods", {
            "name": "Synthetic second participant", "pseudocode": "Use the public-input reference solver."})["method"]
        answer = {"candidate": candidate, "method_id": own_method["id"], "idempotency_key": str(uuid4()),
            "compute": {"hardware": "Isolated test CPU", "workers": 1, "wall_seconds": 0.1,
                        "precomputation": "None", "peak_memory": "Not measured",
                        "notes": "Synthetic declaration; not benchmark evidence."}}
        path = f'/api/challenges/{challenge["id"]}/submit'
        receipt = post(actor, path, answer)["submission"]
        assert receipt["status"] == ("incorrect" if wrong else "correct")
        assert post(actor, path, answer)["submission"]["id"] == receipt["id"]
        return receipt

    # An archived release cannot issue new challenges after replacement. Model
    # a genuine older receipt while DH4 is current, then restore DH7 unchanged.
    post(solver, "/api/challenges", {"scheme_id": "teaching-dh-kem", "scheme_version": "4", "instance_size": 20}, 409)
    with monkeypatch.context() as previous_release:
        previous_release.setitem(registry.CURRENT, "teaching-dh-kem", "4")
        unscored_receipt = recover(solver, 20, archived=True)
    check("correct older-release recovery is retained but unscored after replacement")
    recover(solver, 3, wrong=True)
    check("incorrect recovery earns zero")
    first_receipt = recover(solver, 1)
    expected["solver"]["early_recovery"] = 100
    check("genuine enrolled-release recovery adds one hundred")
    if recovery == "teaching-dh-kem":
        recover(solver, 2)
    check("same participant's exact retries and later DH advance do not stack")
    recover(poster, 1)
    check("another participant copying a lower recovery earns zero")
    # The bounded RSA example solves tier one only. Its companion path uses
    # DH's next frontier, without weakening RSA parameters or inventing a solve.
    recover(poster, 3 if recovery == "teaching-dh-kem" else 1, scheme="teaching-dh-kem")
    expected["poster"]["early_recovery"] = 100
    check("another participant advancing the global frontier earns one hundred")

    spec = correctness_review_spec(builtin_spec("rsa-kem", "4"))
    spec["tier_design"] = tier_design()
    proposal = propose_scheme(solver, spec)
    check("submitted scheme proposal earns zero")
    reviewed = review(reviewer, proposal,
        notes="Synthetic contract test against registered portable RSA; no new research approval.",
        evidence="Disposable scoring fixture: verify exact specification, source hash and complete review gates.")
    check("independent scheme review approval alone earns zero")
    credit_args = dict(family_id="rsa-kem", scheme_id="rsa-kem", scheme_version="4",
        proposal_id=proposal["id"], review_id=reviewed["review"]["id"], reviewer_id=reviewer.identifier,
        evidence="Synthetic empty-ledger admission fixture using registered RSA source; not scientific novelty evidence.",
        confirm_new_family=True, clock=lambda: now[0])
    settings = app.state.settings
    preview = award_scheme_credit(app.state.db, settings, **credit_args, dry_run=True)
    assert not preview["created"]
    check("operator admission dry run earns zero")
    credit = award_scheme_credit(app.state.db, settings, **credit_args)
    assert credit["created"]
    admitted_at = now[0]
    expected["solver"]["scheme_contributions"] = 2000
    accepted["solver"] = 1
    check("actual operator admission adds two thousand")
    assert award_scheme_credit(app.state.db, settings, **credit_args)["credit"] == credit["credit"]
    check("admission retry does not duplicate points or accepted proposals")

    bounty = create_bounty(poster, title="Synthetic combined-points rehearsal")
    assert available(poster) == 10
    check("private bounty draft costs no allowance and earns zero")
    bounty = request(poster, bounty)
    assert available(poster) == 9
    check("bounty submission reserves allowance and earns zero")
    bounty = decision(reviewer, bounty)
    assert bounty["bounty"]["reward_points"] == 100
    check("bounty admission offers one hundred; poster earns zero")
    before_boost, boost_key = bounty, str(uuid4())
    bounty = boost(supporter, before_boost, key=boost_key)
    assert boost(supporter, before_boost, key=boost_key)["receipt"] == bounty["receipt"]
    assert available(supporter) == 9 and bounty["bounty"]["reward_points"] == 200
    check("independent boost offers two hundred and spends allowance exactly once")
    pin = {"note_id": research_note["id"], "revision": research_note["revision"],
           "content_sha256": research_note["content_sha256"]}
    bounty = contribute(solver, bounty, pin)
    submission_id = bounty["receipt"]["submission_id"]
    assert bounty["bounty"]["award"] is None
    check("submitted bounty solution earns zero pending both decisions")
    first_actor, last_actor = (poster, reviewer) if first_approval == "poster" else (reviewer, poster)
    last_kind = "pipeline" if first_approval == "poster" else "poster"
    bounty = assess(first_actor, bounty, first_approval, submission_id=submission_id)
    assert bounty["bounty"]["award"] is None
    check("one bounty approval earns zero")
    final_body = body(bounty, kind=last_kind, decision="approved", reason="Synthetic exact contribution meets the disposable criteria.")
    final_path = f'/api/bounties/{bounty["bounty"]["id"]}/submissions/{submission_id}/decisions'
    bounty = post(last_actor, final_path, final_body)
    assert bounty["bounty"]["award"]["points"] == 200
    expected["solver"]["bounty_points"] = 200
    check("both bounty approvals add two hundred; combined total is 2303")
    assert post(last_actor, final_path, final_body)["receipt"] == bounty["receipt"]
    check("completion retry and repeated reads do not duplicate the award")
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(SchemeAdmissionCredit)) == 1
        assert session.scalar(select(func.count()).select_from(BountyAward)) == 1

    # Reopen the same database through a fresh application, not a cached score.
    restarted = create_app(settings, clock=lambda: now[0])
    try:
        client = TestClient(restarted)
        client.cookies.update(solver.client.cookies)
        assert client.get("/api/people/solver").json()["person"]["points"] == 2303
        assert next(row for row in client.get("/api/people").json()["people"] if row["handle"] == "solver")["points"] == 2303
    finally:
        restarted.state.db.engine.dispose()
    check("all four categories survive reopening the same database")

    def refresh_sessions():
        # New test authentication after controlled expiry; no award rows change.
        for actor in actors:
            actor.client.close()
            renewed = Actor(app, actor.identifier)
            actor.client, actor.csrf = renewed.client, renewed.csrf

    now[0] = admitted_at + WEEK_SECONDS - 0.01
    refresh_sessions()
    check("one instant before a full active week remains 2303")
    now[0] = admitted_at + WEEK_SECONDS
    expected["solver"]["scheme_contributions"] = 2100
    check("one complete active week reaches 2403")
    retire_scheme_credit(app.state.db, settings, family_id="rsa-kem", reviewer_id=reviewer.identifier,
        reason="Synthetic retirement after one full active week, to verify accrual stops.", clock=lambda: now[0])
    now[0] = admitted_at + 5 * WEEK_SECONDS
    refresh_sessions()
    check("retirement preserves earned points and stops future weeks")
    for client in (rater, second_rater):
        client.close()
    return {"checkpoints": checkpoints, "solver_points": 2403, "solver_breakdown": expected["solver"],
            "proposal_id": proposal["id"], "procedure_id": procedure["id"],
            "bounty_id": bounty["bounty"]["id"], "recovery_receipt": first_receipt["id"],
            "contribution_note": pin,
            "unscored_receipt": unscored_receipt["id"]}


@pytest.mark.parametrize("recovery", ["teaching-dh-kem", "rsa-kem"])
@pytest.mark.parametrize("first_approval", ["poster", "pipeline"])
def test_every_points_category_combines_through_real_workflows(tmp_path, monkeypatch, recovery, first_approval):
    system = make_system(f"sqlite:///{tmp_path / 'points.db'}", monkeypatch)
    try:
        evidence = rehearse(system, monkeypatch, recovery=recovery, first_approval=first_approval)
        assert len(evidence["checkpoints"]) >= 30
    finally:
        for actor in system[1:-1]:
            actor.client.close()
        system[0].state.db.engine.dispose()
