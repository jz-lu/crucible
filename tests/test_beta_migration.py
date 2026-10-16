"""A real pre-beta database upgrades without changing account credentials."""
import base64
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select

from crucible import beta_access as beta
from crucible.db import Database, LoginSession, User
from crucible.machine_auth import DeviceConnection, MachineToken
from crucible.security import passwords, token_hash, verify_password


def test_beta_migration_preserves_existing_accounts_sessions_and_agent_credentials(tmp_path, monkeypatch):
    # Never inherit a service database, mail transport, or credentials from the
    # operator's environment. All migration and application reads use tmp_path.
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    url = f"sqlite:///{tmp_path / 'pre-beta.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "open")
    monkeypatch.setenv("CRUCIBLE_MAIL_BACKEND", "disabled")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0008")
    db = Database(url)
    user_id, token_id, device_id = [str(uuid4()) for _ in range(3)]
    password = "Synthetic migration fixture only"
    encoded = passwords.hash(password)
    browser_digest = token_hash("synthetic-browser-session")
    agent_digest = token_hash("cru_" + "a" * 43)
    now, expiry = 3_600_600.0, 3_700_000.0
    try:
        assert "beta_memberships" not in inspect(db.engine).get_table_names()
        with db.transaction(write=True) as session:
            session.add(User(id=user_id, handle="preserved_beta_user", password_hash=encoded, created_at=now))
            session.flush()
            session.add(LoginSession(token_hash=browser_digest, user_id=user_id, expires_at=expiry))
            session.add(MachineToken(id=token_id, user_id=user_id, token_hash=agent_digest,
                label="Existing CLI connection", scopes=["challenges:read", "challenges:write"],
                created_at=now, expires_at=expiry, revoked_at=None))
            session.flush()
            session.add(DeviceConnection(id=device_id, device_hash=token_hash("synthetic-device-secret"),
                user_code="ABCD-EFGH", label="Existing approved connection", scopes=["challenges:read"],
                created_at=now, expires_at=now + 600, approved_user_id=user_id, approved_at=now,
                token_id=token_id))

        def snapshots():
            with db.engine.connect() as connection:
                return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
                        for model in (User, LoginSession, MachineToken, DeviceConnection)}

        before = snapshots()
        command.upgrade(config, "head")
        command.check(config)
        assert snapshots() == before
        with db.transaction() as session:
            user = session.get(User, user_id)
            assert user.password_hash == encoded and verify_password(user.password_hash, password)
            assert session.get(LoginSession, browser_digest).user_id == user_id
            assert session.get(MachineToken, token_id).token_hash == agent_digest
            assert session.get(DeviceConnection, device_id).token_id == token_id
            # Migration never invents consent, verified mailboxes, suspension,
            # invitation redemption, or membership for historical accounts.
            for model in (beta.BetaMembership, beta.AccountState, beta.VerifiedMailbox,
                          beta.BetaInvitation, beta.MailboxVerification, beta.AccessEvent):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        command.downgrade(config, "0008")
        assert snapshots() == before
        assert "beta_memberships" not in inspect(db.engine).get_table_names()
        command.upgrade(config, "head")
        assert snapshots() == before
    finally:
        db.engine.dispose()
