"""Exact expansion and closure races on the existing fixed disposable PG17 service."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace

from alembic import command
from fastapi import HTTPException
import pytest
from sqlalchemy import event, text

from crucible import beta_access as beta, schema_bridge as bridge
from crucible.db import Database
from schema_bridge_fixtures import attach_signup_source, expansion_config
from test_researcher_profiles_postgresql import postgres, assert_original_unchanged


@pytest.fixture(params=["0032", "0033"])
def expanded_pg(request, postgres, tmp_path, monkeypatch):
    # Fixture verifies database/user/version before creating unique synthetic
    # roles and schema. It ignores ambient DATABASE_URL and provider secrets.
    command.upgrade(postgres.config, "0031")
    command.check(postgres.config)
    with postgres.runtime_engine.connect() as connection:
        assert bridge.schema_head(connection, full=True) == "0031"
    config = expansion_config(postgres.owner_engine.url.render_as_string(hide_password=False), tmp_path, monkeypatch)
    command.upgrade(config, request.param)
    assert_original_unchanged(postgres)
    with postgres.runtime_engine.connect() as connection:
        assert bridge.schema_head(connection, full=True) == request.param
        assert not connection.scalar(text("SELECT has_schema_privilege(current_user, current_schema(), 'CREATE')"))
        for name in bridge.EXPANSION_TABLES[:2 if request.param == "0032" else 5]:
            assert all(connection.scalar(text("SELECT has_table_privilege(current_user, :name, :permission)"),
                {"name": name, "permission": permission}) for permission in ("SELECT", "INSERT", "UPDATE", "DELETE"))
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, :name, 'TRUNCATE')"), {"name": name})
    with postgres.backup_engine.connect() as connection:
        for name in bridge.SIGNUP_TABLES:
            assert connection.scalar(text("SELECT has_table_privilege(current_user, :name, 'SELECT')"), {"name": name})
            assert not connection.scalar(text("SELECT has_table_privilege(current_user, :name, 'UPDATE')"), {"name": name})
    db = Database(postgres.runtime_url)
    settings = SimpleNamespace(origins=("https://example.invalid",))
    now = 1_000_000.0
    with db.transaction(write=True) as session:
        invitation, url = beta.create_invitation(session, settings, "synthetic@example.invalid", now)
        attach_signup_source(session, invitation, now)
    try:
        yield SimpleNamespace(db=db, fixture=postgres, token=url.split("token=", 1)[1], invitation=invitation.id, now=now)
    finally:
        db.engine.dispose()


def test_pg_closure_serializes_before_bridge_validation_without_inverting_locks(expanded_pg):
    s = expanded_pg
    attempting_lock = Event()
    def observed(_connection, _cursor, statement, *_args):
        if "beta_signup_links" in statement and "FOR UPDATE" in statement:
            attempting_lock.set()
    def validate():
        try:
            with s.db.transaction(write=True) as session:
                beta.validate_registration_invitation(session, s.token, s.now, lock=True)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with s.db.transaction(write=True) as closing:
            bridge.launch_link_status(closing, s.now, close_reason="Synthetic PG early closure")
            # Only observe the competing transaction, after the owner has its lock.
            event.listen(s.db.engine, "before_cursor_execute", observed)
            try:
                future = pool.submit(validate)
                assert attempting_lock.wait(5), "Competing source-parent lock was not reached"
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.2)
                # The waiting bridge must not already hold the child row. A
                # candidate issuer/closer may lock it after its parent safely.
                closing.execute(text("SELECT id FROM beta_invitations WHERE id=:id FOR UPDATE NOWAIT"), {"id": s.invitation})
            finally:
                event.remove(s.db.engine, "before_cursor_execute", observed)
        assert future.result(timeout=5) == 403
    with s.db.transaction() as session:
        row = session.get(beta.BetaInvitation, s.invitation)
        assert row.redeemed_at is None and row.send_count == 0


def test_pg_revoked_member_cannot_use_new_campaign_invitation(expanded_pg):
    s = expanded_pg
    with s.db.transaction(write=True) as session:
        from crucible.db import User
        user = session.get(User, s.fixture.ids["user"])
        beta.set_membership(session, user.id, "revoked", s.now - 1)
        row = beta.validate_registration_invitation(session, s.token, s.now, lock=True)
        assert row.created_at > s.now - 1
        with pytest.raises(HTTPException, match="cannot restore"):
            beta._check_regrant(session, user, row)
