"""Finite bridge compatibility over disposable SQLite, using exact expansion SQL."""
import json
from types import SimpleNamespace

from alembic import command
from alembic.script import ScriptDirectory
from fastapi import HTTPException
import pytest
from sqlalchemy import MetaData, inspect, select, text

from crucible import app as application, beta_access as beta, schema_bridge as bridge
from crucible.db import Base, Database
from scripts import beta_access as operator, beta_preflight
from schema_bridge_fixtures import attach_signup_source, expansion_config
from test_beta_invitations import beta_system, invitation, redeem, start
from test_researcher_profiles_migration import migration_config, snapshot


@pytest.fixture(params=["0032", "0033"])
def expanded(request, beta_system, tmp_path, monkeypatch):
    system = beta_system
    metadata = MetaData()
    metadata.reflect(bind=system.db.engine, only=sorted(set(inspect(system.db.engine).get_table_names()) - {"alembic_version"}))
    with system.db.engine.connect() as connection:
        before = snapshot(connection, metadata)
        assert bridge.schema_head(connection, full=True) == "0031"
    config = expansion_config(str(system.db.engine.url), tmp_path, monkeypatch)
    command.upgrade(config, request.param)
    with system.db.engine.connect() as connection:
        assert snapshot(connection, metadata) == before
        assert bridge.schema_head(connection, full=True) == request.param
    return system


def sourced(system, *, expires_at=None):
    record, token = invitation(system)
    with system.db.transaction(write=True) as session:
        attach_signup_source(session, session.get(beta.BetaInvitation, record["id"]), system.now[0], expires_at=expires_at)
    return record, token


def test_fresh_bridge_keeps_exact_0031_migration_and_orm_parity(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'bridge.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    assert ScriptDirectory.from_config(config).get_heads() == ["0031"]
    assert set(Base.metadata.tables).isdisjoint(bridge.EXPANSION_TABLES)
    command.upgrade(config, "head")
    command.check(config)
    db = Database(url)
    try:
        with db.engine.connect() as connection:
            assert bridge.schema_head(connection, full=True) == "0031"
    finally:
        db.engine.dispose()


def test_source_reads_refresh_after_expansion_on_same_engine(expanded):
    # The fixture has already read 0031 before migrating using this same pool.
    system = expanded
    record, token = sourced(system)
    with system.db.transaction() as session:
        assert beta.validate_registration_invitation(session, token, system.now[0]).id == record["id"]
    with system.db.transaction(write=True) as session:
        bridge.launch_link_status(session, system.now[0], close_reason="Synthetic early closure")
    with system.db.transaction() as session:
        with pytest.raises(HTTPException) as error:
            beta.validate_registration_invitation(session, token, system.now[0], lock=True)
        assert error.value.status_code == 403
    # Closing a source never closes an independent operator invitation.
    _, ordinary = invitation(system, "bob@example.org")
    with system.db.transaction() as session:
        assert beta.validate_registration_invitation(session, ordinary, system.now[0])


@pytest.mark.parametrize("closure", ["early", "expired"])
def test_parent_closure_prevents_code_send_and_pending_redemption(expanded, closure):
    system = expanded
    record, token = sourced(system, expires_at=system.now[0] + 100)
    _, code, _, _ = start(system, token)
    if closure == "early":
        with system.db.transaction(write=True) as session:
            bridge.launch_link_status(session, system.now[0], close_reason="Synthetic early closure")
    else:
        system.now[0] += 100  # Exact parent deadline, before child/code expiry.
    assert system.post("/api/access/invitations/verify/start", {"token": token}).status_code == 403
    assert redeem(system, token, code).status_code == 403
    with system.db.transaction() as session:
        assert session.get(beta.BetaMembership, system.users["alice"]) is None
        assert session.get(beta.BetaInvitation, record["id"]).redeemed_at is None


def test_link_child_can_never_restore_revoked_access_but_new_operator_invite_can(expanded):
    system = expanded
    with system.db.transaction(write=True) as session:
        beta.set_membership(session, system.users["alice"], "revoked", system.now[0])
    system.now[0] += 1
    _, token = sourced(system)
    refused = system.post("/api/access/invitations/verify/start", {"token": token})
    assert refused.status_code == 403 and "cannot restore" in refused.text
    _, ordinary = invitation(system)
    _, code, _, _ = start(system, ordinary)
    assert redeem(system, ordinary, code).status_code == 200


def test_closure_keeps_admitted_members_but_replay_cannot_revive_revocation(expanded):
    system = expanded
    _, token = sourced(system)
    _, code, _, _ = start(system, token)
    assert redeem(system, token, code).status_code == 200
    with system.db.transaction(write=True) as session:
        bridge.launch_link_status(session, system.now[0], close_reason="Synthetic closure")
    assert redeem(system, token, code).status_code == 200  # Existing receipt, no new grant.
    with system.db.transaction(write=True) as session:
        beta.set_membership(session, system.users["alice"], "revoked", system.now[0])
    assert redeem(system, token, code).status_code == 403


def test_operator_can_close_only_existing_campaign_without_issuing_or_exposing_tokens(expanded, monkeypatch, capsys):
    system = expanded
    monkeypatch.setattr(operator, "load_settings", lambda: SimpleNamespace(**vars(system.settings), database_url=str(system.db.engine.url)))
    monkeypatch.setattr(operator, "time", SimpleNamespace(time=lambda: system.now[0]))
    assert operator.main(["close-launch-link", "--reason", "Not yet created"]) == 1
    capsys.readouterr()
    _, token = sourced(system)
    assert operator.main(["launch-link-status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "available"
    for _ in range(2):
        assert operator.main(["close-launch-link", "--reason", "Synthetic early closure"]) == 0
        output = capsys.readouterr().out
        assert json.loads(output)["status"] == "revoked"
        assert token not in output and "token_hash" not in output and "url" not in output
    with system.db.transaction() as session:
        events = session.scalars(select(beta.AccessEvent).where(beta.AccessEvent.action == "signup_link_revoked")).all()
        assert len(events) == 1
    with pytest.raises(SystemExit):
        operator.main(["launch-link"])


@pytest.mark.parametrize("head", [None, "0030", "0032", "0033", "0034", "garbage"])
def test_unversioned_unknown_or_missing_expansion_fails_closed(beta_system, head):
    system = beta_system
    _, token = invitation(system)
    with system.db.engine.begin() as connection:
        if head is None:
            connection.execute(text("DROP TABLE alembic_version"))
        else:
            connection.execute(text("UPDATE alembic_version SET version_num=:head"), {"head": head})
    with system.db.engine.connect() as connection:
        with pytest.raises(bridge.SchemaBridgeError):
            bridge.schema_head(connection, full=True)
    response = system.post("/api/access/invitations/verify/start", {"token": token})
    assert response.status_code == 503
    assert "alembic" not in response.text and "SELECT" not in response.text


def test_version_label_cannot_hide_expansion_or_a_partial_table_shape(expanded):
    system = expanded
    with system.db.engine.begin() as connection:
        saved = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.execute(text("UPDATE alembic_version SET version_num='0031'"))
        with pytest.raises(bridge.SchemaBridgeError):
            bridge.schema_head(connection, full=True)
        connection.execute(text("UPDATE alembic_version SET version_num=:head"), {"head": saved})
        connection.execute(text("ALTER TABLE beta_signup_links RENAME COLUMN expires_at TO wrong_name"))
        with pytest.raises(bridge.SchemaBridgeError):
            bridge.schema_head(connection, full=True)


def test_preflight_accepts_only_bridge_source_head_and_keeps_other_checks(expanded):
    settings = SimpleNamespace(reviewer_ids=(), access_admin_ids=())
    with expanded.db.engine.connect() as connection:
        checks = beta_preflight.inspect_database(connection, settings, ["0031"], None)
        assert checks[0]["status"] == "pass" and "bridge" in checks[0]["message"]
        assert checks[1]["check"] == "historical_implementation_pins" and checks[1]["status"] == "not_checked"
        for heads in (["0033"], ["0031", "0032"], ["0034"]):
            checks = beta_preflight.inspect_database(connection, settings, heads, None)
            assert len(checks) == 1 and checks[0]["status"] == "fail"


def test_hosted_factory_rechecks_schema_before_application_or_worker(beta_system, monkeypatch):
    from crucible import app_platform, config, publication_screening_supervisor as supervisor
    settings = SimpleNamespace(production=True, access_mode="beta", origins=("https://example.invalid",),
                               database_url=str(beta_system.db.engine.url))
    monkeypatch.setenv("CRUCIBLE_ENV", "production")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "beta")
    monkeypatch.setattr(config, "load_settings", lambda: settings)
    monkeypatch.setattr(supervisor, "enabled_from_environment", lambda: False)
    calls = []
    monkeypatch.setattr(application, "create_app", lambda settings: calls.append("application") or object())
    monkeypatch.setattr(supervisor, "install", lambda app, *a, **k: calls.append("worker") or app)
    assert app_platform.create_app()
    assert calls == ["application", "worker"]
    calls.clear()
    with beta_system.db.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0034'"))
    with pytest.raises(bridge.SchemaBridgeError):
        app_platform.create_app()
    assert calls == []
