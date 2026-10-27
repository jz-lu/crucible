"""The real 0023→0024 migration adds a table without rewriting existing evidence."""
import base64
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, inspect, select, text

import crucible
from crucible.db import Database
from crucible.security import passwords, token_hash, verify_password


def snapshot(connection, metadata):
    return {table.name: list(connection.execute(select(table).order_by(*table.primary_key.columns)).mappings())
            for table in metadata.sorted_tables}


def user_columns(engine):
    return [{**column, "type": str(column["type"])} for column in inspect(engine).get_columns("users")]


def migration_config(database_url, tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"p" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "open")
    monkeypatch.setenv("CRUCIBLE_MAIL_BACKEND", "disabled")
    # Native validation mounts tests at /tests, separately from /opt/crucible.
    # Migrate the same installed source that Database/models below are using.
    root = Path(crucible.__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    return config


def test_migrations_resolve_installed_package_when_tests_are_mounted_separately(tmp_path, monkeypatch):
    separate_test = tmp_path / "tests" / "test_researcher_profiles_migration.py"
    separate_test.parent.mkdir()
    separate_test.write_text("# Synthetic separately mounted test path.\n")
    monkeypatch.setitem(migration_config.__globals__, "__file__", str(separate_test))
    assert not (tmp_path / "migrations").exists()
    url = f"sqlite:///{tmp_path / 'native-layout.db'}"
    config = migration_config(url, tmp_path, monkeypatch)
    root = Path(crucible.__file__).resolve().parents[1]
    assert config.config_file_name == str(root / "alembic.ini")
    assert config.get_main_option("script_location") == str(root / "migrations")
    # Execute the real chain and reverse only the deliberately empty alias table.
    command.upgrade(config, "0025")
    db = Database(url)
    try:
        assert len(inspect(db.engine).get_table_names()) == 47
        with db.engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0025"
        command.downgrade(config, "0024")
        assert "notebook_numbers" not in inspect(db.engine).get_table_names()
    finally:
        db.engine.dispose()


def seed_original(connection, metadata):
    ids = {name: str(uuid4()) for name in ("user", "method", "challenge", "submission", "note", "revision", "deletion")}
    password = "Synthetic migration password only"
    password_hash = passwords.hash(password)
    now = 3_600_600.125
    def insert(table_name, **values):
        connection.execute(metadata.tables[table_name].insert().values(**values))
    insert("users", id=ids["user"], handle="researcher", password_hash=password_hash, created_at=now)
    insert("login_sessions", token_hash=token_hash("synthetic-preserved-session"), user_id=ids["user"], expires_at=now + 86400)
    insert("beta_memberships", user_id=ids["user"], status="active", updated_at=now)
    insert("github_identities", github_user_id="123456789", user_id=ids["user"], github_login="synthetic-researcher",
           created_at=now, last_login_at=now)
    insert("methods", id=ids["method"], user_id=ids["user"], name="Original method", pseudocode="Original α\nexact text  ", created_at=now)
    insert("challenges", id=ids["challenge"], user_id=ids["user"], scheme_id="synthetic-frozen", scheme_version="1",
           profile_id="original-profile", round_id=1000, issued_at=now, starts_at=3_600_000, ends_at=3_603_600,
           public_data={"encoding": {"bytes": [3, 1, 2]}, "text": "α  "}, manifest={"source_sha256": "a" * 64},
           target_mac="b" * 64, correctness_passed=True)
    insert("submissions", id=ids["submission"], challenge_id=ids["challenge"], user_id=ids["user"], method_id=ids["method"],
           profile_id="original-profile", scheme_version="1", round_id=1000, idempotency_key=str(uuid4()), payload_mac="c" * 64,
           correct=True, received_at=now + 1, elapsed_seconds=1, compute={"workers": 1, "ordered": [2, 1]}, ai_model=None)
    insert("adapter_versions", scheme_id="synthetic-frozen", version="1", source_sha256="d" * 64, artifact_sha256=None, created_at=now)
    insert("notebook_notes", id=ids["note"], author_id=ids["user"], created_at=now, latest_revision=1,
           idempotency_key=str(uuid4()), request_sha256="e" * 64, source_id="original:fixture", source_metadata={"untouched": True})
    insert("notebook_revisions", id=ids["revision"], note_id=ids["note"], revision=1, created_at=now,
           content={"title": "Original α", "body": "First\nSecond  ", "extensions": {"optional": None}},
           content_sha256="f" * 64, idempotency_key=str(uuid4()), request_sha256="0" * 64,
           source_revision={"original_timestamp": "2026-01-02T03:04:05Z"})
    insert("notebook_deletions", id=ids["deletion"], note_id=ids["note"], author_id=ids["user"], revision=1,
           content_sha256="f" * 64, deleted_at=now + 2, idempotency_key=str(uuid4()), restored_at=None, restore_key=None)
    return ids, password


def test_0023_upgrade_preserves_accounts_credentials_notes_receipts_and_release_anchors(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'researcher-profile-migration.db'}"
    config = migration_config(database_url, tmp_path, monkeypatch)
    command.upgrade(config, "0023")
    db = Database(database_url)
    old_names = set(inspect(db.engine).get_table_names()) - {"alembic_version"}
    assert len(old_names) == 44  # 45 total at 0023, including Alembic's version marker.
    assert "researcher_profiles" not in old_names
    metadata = MetaData()
    metadata.reflect(bind=db.engine, only=sorted(old_names))
    try:
        with db.engine.begin() as connection:
            _, password = seed_original(connection, metadata)
        with db.engine.connect() as connection:
            before = snapshot(connection, metadata)
        users_before = user_columns(db.engine)
        command.upgrade(config, "0024")
        assert set(inspect(db.engine).get_table_names()) - {"alembic_version"} == old_names | {"researcher_profiles"}
        assert user_columns(db.engine) == users_before
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert connection.scalar(text("SELECT COUNT(*) FROM researcher_profiles")) == 0
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0024"
            assert not connection.execute(text("PRAGMA foreign_key_check")).all()
            assert verify_password(connection.scalar(text("SELECT password_hash FROM users")), password)
        # Full current-head metadata parity is checked by test_notebook_numbers.
        # This downgrade is deliberately exercised only on an empty new table.
        # A deployed profile table would need its own preservation decision.
        command.downgrade(config, "0023")
        with db.engine.connect() as connection:
            assert snapshot(connection, metadata) == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0023"
        assert "researcher_profiles" not in inspect(db.engine).get_table_names()
    finally:
        db.engine.dispose()
