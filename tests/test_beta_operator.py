"""Operator commands exercise only the isolated invitation fixture database."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from scripts import beta_access as operator
from crucible import beta_access as beta
from crucible.db import User
from test_beta_invitations import beta_system


@pytest.fixture
def cli(beta_system, monkeypatch):
    system = beta_system
    system.settings.database_url = str(system.db.engine.url)
    monkeypatch.setattr(operator, "load_settings", lambda: system.settings)
    monkeypatch.setattr(operator, "time", SimpleNamespace(time=lambda: system.now[0]))
    return system


def test_operator_lists_and_withdraws_invites_without_revealing_tokens(cli, capsys):
    assert operator.main(["invite", "Alice@EXAMPLE.ORG"]) == 0
    created = json.loads(capsys.readouterr().out)
    token = created["invitation_url"].split("token=", 1)[1]
    assert operator.main(["invitations"]) == 0
    listed_output = capsys.readouterr().out
    listed = json.loads(listed_output)
    assert listed == [{"id": created["id"], "email": "Alice@example.org", "status": "available", "expires_at": created["expires_at"]}]
    assert token not in listed_output and "token_hash" not in listed_output
    # Creating/listing an invitation does not deliver any email automatically.
    from pathlib import Path
    assert not Path(cli.settings.mail_outbox_dir).exists()
    assert operator.main(["revoke-invite", created["id"], "--reason", "Wrong recipient"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": created["id"], "status": "revoked"}
    assert cli.post("/api/access/invitations/verify/start", {"token": token}).status_code == 403
    assert operator.main(["invitations"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "revoked"
    with cli.db.transaction() as session:
        events = session.scalars(select(beta.AccessEvent).where(beta.AccessEvent.action == "invitation_revoked")).all()
        assert len(events) == 1 and events[0].details["reason"] == "Operator: Wrong recipient"


def test_operator_account_controls_keep_identity_and_report_suspension(cli, capsys):
    for action in ("grant", "suspend", "revoke"):
        assert operator.main([action, "alice", "--reason", "Isolated fixture"]) == 0
        assert json.loads(capsys.readouterr().out)["id"] == cli.users["alice"]
    assert operator.main(["list"]) == 0
    alice = next(row for row in json.loads(capsys.readouterr().out) if row["handle"] == "alice")
    assert alice == {"id": cli.users["alice"], "handle": "alice", "membership": "revoked", "suspended": True}
    assert operator.main(["restore", "alice", "--reason", "Restore fixture"]) == 0
    capsys.readouterr()
    with cli.db.transaction() as session:
        assert session.get(User, cli.users["alice"]).password_hash == "fixture"
        assert session.get(beta.AccountState, cli.users["alice"]).suspended_at is None
        assert session.get(beta.BetaMembership, cli.users["alice"]).status == "revoked"


def test_operator_rejects_missing_reason_unknown_id_and_invalid_listing(cli, capsys):
    with pytest.raises(SystemExit):
        operator.main(["revoke-invite", str(uuid4())])
    capsys.readouterr()
    assert operator.main(["revoke-invite", str(uuid4()), "--reason", "Fixture"]) == 1
    assert "Invitation not found" in capsys.readouterr().err
    assert operator.main(["invitations", "--limit", "0"]) == 1
    assert "--limit" in capsys.readouterr().err
    assert operator.main(["grant", "alice", "--reason", " "]) == 1
    assert "reason" in capsys.readouterr().err
