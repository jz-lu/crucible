"""Model attribution is optional, immutable, and follows receipt visibility."""
import base64

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from crucible.app import create_app
from crucible.config import ROOT
from crucible.db import Submission
from crucible.security import payload_mac
from test_api import Actor, system


def test_model_annotation_survives_receipts_history_leaderboard_and_restart(system):
    app, clock, settings = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge()
    answer = {**alice.answer(challenge), "ai_model": "  Model Alpha v2; Model Beta v3  "}
    endpoint = f'/api/challenges/{challenge["id"]}/submit'
    response = alice.post(endpoint, answer)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission"]
    assert receipt["ai_model"] == "Model Alpha v2; Model Beta v3"
    assert alice.post(endpoint, answer).json()["submission"] == receipt
    assert alice.post(endpoint, {**answer, "ai_model": "Another model"}).status_code == 409
    assert alice.post(endpoint, {key: value for key, value in answer.items() if key != "ai_model"}).status_code == 409
    for item in (
        TestClient(app).get(f'/api/submissions/{receipt["id"]}').json()["submission"],
        alice.client.get("/api/me/submissions").json()["submissions"][0],
        alice.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"][0],
        alice.client.get(challenge["download_url"]).json()["challenge"]["submission"],
    ):
        assert item["ai_model"] == receipt["ai_model"]
    restarted = create_app(settings, clock=lambda: clock[0])
    try:
        assert TestClient(restarted).get(f'/api/submissions/{receipt["id"]}').json()["submission"]["ai_model"] == receipt["ai_model"]
    finally:
        restarted.state.db.engine.dispose()


def test_blank_annotation_preserves_pre_annotation_retry_commitments(system):
    app, _, settings = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge()
    answer = alice.answer(challenge)
    endpoint = f'/api/challenges/{challenge["id"]}/submit'
    response = alice.post(endpoint, answer)
    receipt = response.json()["submission"]
    assert receipt["ai_model"] == ""
    with app.state.db.transaction() as session:
        stored = session.get(Submission, receipt["id"])
        assert stored.ai_model is None
        assert stored.payload_mac == payload_mac(settings.secret, {"challenge_id": challenge["id"], **answer})
    assert alice.post(endpoint, {**answer, "ai_model": "   "}).json()["submission"] == receipt
    assert alice.post(endpoint, {**answer, "ai_model": "New attribution"}).status_code == 409


@pytest.mark.parametrize("annotation", [None, True, 1, [], {}, "x" * 301, "x\x00y"])
def test_invalid_model_metadata_does_not_consume_an_attempt(system, annotation):
    app, _, _ = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge()
    answer = alice.answer(challenge)
    endpoint = f'/api/challenges/{challenge["id"]}/submit'
    assert alice.post(endpoint, {**answer, "ai_model": annotation}).status_code == 422
    assert alice.post(endpoint, {**answer, "ai_model": "🧪" * 300}).status_code == 200


def test_unsuccessful_model_annotation_is_private(system):
    app, _, _ = system
    alice, bob = Actor(app), Actor(app)
    alice.register("alice")
    bob.register("bob")
    challenge = alice.challenge()
    answer = alice.answer(challenge)
    answer["candidate"] = ("00" if answer["candidate"][:2] != "00" else "01") + answer["candidate"][2:]
    response = alice.post(f'/api/challenges/{challenge["id"]}/submit', {**answer, "ai_model": "Private model attribution"})
    receipt = response.json()["submission"]
    assert receipt["status"] == "incorrect"
    path = f'/api/submissions/{receipt["id"]}'
    assert alice.client.get(path).json()["submission"]["ai_model"] == "Private model attribution"
    assert bob.client.get(path).status_code == TestClient(app).get(path).status_code == 404
    assert not bob.client.get("/api/leaderboard?profile_id=dh-20").json()["entries"]


def test_additive_migration_preserves_legacy_receipt_and_retry(system, monkeypatch, tmp_path):
    app, clock, settings = system
    alice = Actor(app)
    alice.register("alice")
    challenge = alice.challenge()
    answer = alice.answer(challenge)
    path = f'/api/challenges/{challenge["id"]}/submit'
    receipt = alice.post(path, answer).json()["submission"]
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(settings.secret).decode())
    monkeypatch.setenv("CRUCIBLE_ORIGIN", "http://testserver")
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    # The fixture starts with today's ORM. Recreate the actual pre-0010 schema
    # through migrations so future additive tables are not falsely preinstalled.
    command.stamp(config, "head")
    command.downgrade(config, "0009")
    command.upgrade(config, "head")
    assert "ai_model" in {column["name"] for column in inspect(app.state.db.engine).get_columns("submissions")}
    command.check(config)
    clock[0] += 3600
    assert alice.post(path, answer).json()["submission"] == receipt
    assert alice.client.get(f'/api/submissions/{receipt["id"]}').json()["submission"]["ai_model"] == ""
