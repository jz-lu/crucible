"""Reviewer eligibility changes scores, never the underlying research evidence."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import note_ratings, registry, scoring
from crucible.app import create_app
from crucible.bounty_models import BountyAward
from crucible.db import Submission
from crucible.scoring_awards import SchemeAdmissionCredit, award_scheme_credit
from test_bounties import assess, boost, opened, published, submit
from test_bounties import system as bounty_system
from test_calibrated_api import current_system
from test_note_ratings import agent, publish, read, vote
from test_notebook import Actor, content, create
from test_notebook import system as notebook_system
from test_people import add_user, people, person, receipt
from test_scoring_awards import admission


ZERO_BREAKDOWN = {
    "progress": 0, "early_recovery": 0, "scheme_contributions": 0,
    "note_contributions": 0, "bounty_points": 0,
}


@pytest.fixture(autouse=True)
def portable_scoring_catalogue(monkeypatch):
    """Role/ledger checks need the real portable schemes, not native PQ builds."""
    monkeypatch.setattr(registry, "ADAPTERS", {
        key: value for key, value in registry.ADAPTERS.items()
        if key[0] in ("teaching-dh-kem", "rsa-kem")
    })
    monkeypatch.setattr(registry, "CURRENT", {"teaching-dh-kem": "7", "rsa-kem": "4"})


@contextmanager
def reviewer_app(app, *reviewers, **settings_changes):
    """Restart against the same disposable database with a changed role config."""
    settings = replace(app.state.settings,
        reviewer_ids=tuple(set(app.state.settings.reviewer_ids) | set(reviewers)),
        **settings_changes)
    restarted = create_app(settings, clock=app.state.clock)
    try:
        yield restarted
    finally:
        restarted.state.db.engine.dispose()


def assert_unscored_reviewer(profile):
    assert profile["score_status"] == "reviewer"
    assert profile["points"] == 0
    assert profile["rank"] is None
    assert profile["score_breakdown"] == ZERO_BREAKDOWN
    assert profile["scheme_admission_breakdown"]["admission_points"] == 0
    assert profile["scheme_admission_breakdown"]["weekly_points"] == 0


@pytest.mark.parametrize("include_records", [True, False])
def test_reviewers_are_removed_before_ranking_and_pagination_but_access_admins_are_not(
        current_system, include_records):
    app, _, _ = current_system
    reviewer = add_user(app, "aaa_reviewer")
    copier = add_user(app, "bob")
    admin = add_user(app, "carol")
    add_user(app, "dave")
    receipt(app, reviewer, 4)
    receipt(app, copier, 4, 1)
    receipt(app, admin, 5, 2)
    assert person(app, "aaa_reviewer")["points"] == 100

    with reviewer_app(app, reviewer, access_admin_ids=(admin,)) as updated:
        first = people(updated, limit=1, include_records=include_records)
        assert first["pagination"] == {
            "limit": 1, "offset": 0, "total": 3, "has_more": True,
        }
        assert [(row["handle"], row["rank"], row["points"]) for row in first["people"]] == [
            ("carol", 1, 100),
        ]
        last = people(updated, limit=2, offset=1, include_records=include_records)
        assert [(row["handle"], row["rank"], row["points"]) for row in last["people"]] == [
            ("bob", 2, 0), ("dave", 2, 0),
        ]
        assert last["pagination"]["total"] == 3 and not last["pagination"]["has_more"]
        assert people(updated, offset=3)["people"] == []
        # Excluding a reviewer must not award a copy of their existing recovery.
        assert person(updated, "bob")["score_breakdown"]["early_recovery"] == 0
        assert_unscored_reviewer(person(updated, "aaa_reviewer"))
        assert first["scoring"]["reviewers_eligible"] is False


def test_reviewer_profiles_and_first_records_keep_actual_recovery_evidence(current_system):
    app, _, _ = current_system
    reviewer = add_user(app, "reviewer")
    first = receipt(app, reviewer, 2)
    highest = receipt(app, reviewer, 4, 1)
    add_user(app, "participant")
    with reviewer_app(app, reviewer) as updated:
        profile = person(updated, "reviewer")
        assert_unscored_reviewer(profile)
        assert profile["correct_submission_count"] == 2
        assert profile["schemes_recovered"] == 1
        assert profile["records"][0]["submission_id"] == highest["submission_id"]
        assert profile["records"][0]["score"] is None
        assert profile["first_records"][0]["submission_id"] == highest["submission_id"]
        assert profile["first_records"][0]["place"] == 1
        assert {row["submission_id"] for row in profile["recent_recoveries"]} == {
            first["submission_id"], highest["submission_id"],
        }
        for section in ("records", "recent_recoveries", "first_records"):
            assert all(row["scoring_eligible"] is False for row in profile[section])
        for section in ("highlights", "history"):
            assert profile["first_ten"][section]
            assert all(not row["scoring_eligible"] and not row["counts_toward_bonus"]
                       for row in profile["first_ten"][section])

        response = TestClient(updated).get("/api/people/reviewer/milestones")
        assert response.status_code == 200, response.text
        milestones = response.json()["milestones"]
        assert {row["submission_id"] for row in milestones} == {
            first["submission_id"], highest["submission_id"],
        }
        assert all(row["place"] == 1 and not row["counts_toward_bonus"]
                   and not row["scoring_eligible"] for row in milestones)
        assert [(row["handle"], row["rank"]) for row in people(updated)["people"]] == [
            ("participant", 1),
        ]
        with updated.state.db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 2
            _, families = scoring.score_relations(reviewer_ids=(reviewer,))
            family = session.execute(select(families).where(families.c.user_id == reviewer)).one()
            assert family.early_recovery == 0 and family.early_recovery_submission_id is None
            assert family.highest_frontier_submission_id == highest["submission_id"]


def test_reviewers_keep_admission_history_and_active_weeks_without_any_admission_points(
        admission, monkeypatch):
    db, settings, args, author = admission
    award_scheme_credit(db, settings, **args)
    monkeypatch.setattr(scoring, "time", SimpleNamespace(time=lambda: 10 + 2 * 7 * 24 * 3600))
    app = create_app(replace(settings, origins=("http://testserver",)))
    try:
        before = person(app, "author")
        assert before["points"] == before["score_breakdown"]["scheme_contributions"] == 2200
        assert before["scheme_admission_breakdown"]["completed_active_weeks"] == 2
        with db.transaction() as session:
            credit = session.scalar(select(SchemeAdmissionCredit))
            original = {column.name: getattr(credit, column.name) for column in credit.__table__.columns}
        with reviewer_app(app, author) as updated:
            profile = person(updated, "author")
            assert_unscored_reviewer(profile)
            assert profile["accepted_proposal_count"] == 1
            assert profile["scheme_admission_breakdown"]["completed_active_weeks"] == 2
            assert people(updated)["people"] == []
            with db.transaction() as session:
                credit = session.scalar(select(SchemeAdmissionCredit))
                assert {column.name: getattr(credit, column.name) for column in credit.__table__.columns} == original
    finally:
        app.state.db.engine.dispose()


def test_reviewer_note_authorship_earns_no_points_but_note_votes_still_exist(notebook_system):
    app, author, reviewer, reader, _ = notebook_system
    note = publish(author, reviewer, create(author, content(kind="Review and synthesis")))
    voting_agent = agent(reader)
    assert vote(voting_agent, note, 2).status_code == 200
    assert person(app, "author")["score_breakdown"]["note_contributions"] == 2
    with reviewer_app(app, author.identifier) as updated:
        assert_unscored_reviewer(person(updated, "author"))
        assert [row["handle"] for row in people(updated)["people"]] == ["reader"]
        note_reader = agent(Actor(updated, reader.identifier))
        assert read(note_reader, note)["rating"]["score"] == 2
        with updated.state.db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(note_ratings.NoteRating)) == 1
            assert session.scalar(select(note_ratings.NoteRating.value)) == 2


def test_completed_bounty_award_is_retained_when_recipient_becomes_reviewer(bounty_system):
    app, _, reviewer, solver, supporter, *_ = bounty_system
    value = boost(supporter, opened(bounty_system))
    value = submit(solver, value, published(solver, reviewer))
    value = assess(reviewer, value, "pipeline")
    assert value["bounty"]["award"]["points"] == 200
    assert solver.client.get("/api/people/solver").json()["person"]["score_breakdown"]["bounty_points"] == 200
    with app.state.db.transaction() as session:
        award = session.get(BountyAward, value["bounty"]["id"])
        original = {column.name: getattr(award, column.name) for column in award.__table__.columns}
    with reviewer_app(app, solver.identifier) as updated:
        actor = Actor(updated, solver.identifier)
        response = actor.client.get("/api/people/solver")
        assert response.status_code == 200, response.text
        assert_unscored_reviewer(response.json()["person"])
        board = actor.client.get("/api/people").json()
        assert {row["handle"] for row in board["people"]} == {"poster", "supporter"}
        assert all(row["rank"] == 1 and row["points"] == 0 for row in board["people"])
        with updated.state.db.transaction() as session:
            award = session.get(BountyAward, value["bounty"]["id"])
            assert {column.name: getattr(award, column.name) for column in award.__table__.columns} == original
