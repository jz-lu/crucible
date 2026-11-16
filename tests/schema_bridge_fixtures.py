"""Explicit synthetic schemas only; never infer a database from caller settings."""
import hashlib
import json
from pathlib import Path
import shutil

from sqlalchemy import text

import crucible
from test_researcher_profiles_migration import migration_config


def mark_metadata_fixture(engine):
    # Only tests that intentionally build Base.metadata rather than migrate
    # call this helper. Production runtime never creates/stamps a version.
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0031')"))


def expansion_config(url, tmp_path, monkeypatch):
    root = Path(crucible.__file__).resolve().parents[1]
    receipt = json.loads((root / "docs/schema-bridge/expansion.json").read_text())
    assert receipt["format"] == "crucible-schema-bridge-expansion-v1"
    assert receipt["expansion_source"] == "618c707deeea94ee933ad799495111d575aa616f"
    config = migration_config(url, tmp_path, monkeypatch)
    target = tmp_path / "reviewed-expansion-migrations"
    shutil.copytree(root / "migrations", target, ignore=shutil.ignore_patterns("__pycache__"))
    for original, record in receipt["files"].items():
        raw = (root / record["copy"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        assert original in {"migrations/versions/0032_signup_links.py", "migrations/versions/0033_statement_workflow_trial.py"}
        (target / "versions" / Path(original).name).write_bytes(raw)
    config.set_main_option("script_location", str(target))
    return config


def attach_signup_source(session, invitation, now, *, expires_at=None):
    from crucible.schema_bridge import LAUNCH_SIGNUP_ID
    session.flush()
    if not session.scalar(text("SELECT id FROM beta_signup_links WHERE id=:id"), {"id": LAUNCH_SIGNUP_ID}):
        session.execute(text("INSERT INTO beta_signup_links "
            "(id, token_hash, label, created_by, created_at, expires_at, revoked_at) "
            "VALUES (:id, :hash, 'Synthetic launch only', NULL, :now, :expires, NULL)"),
            {"id": LAUNCH_SIGNUP_ID, "hash": "f" * 64, "now": now,
             "expires": expires_at if expires_at is not None else now + 1000})
    session.execute(text("INSERT INTO beta_signup_invitations (invitation_id, signup_link_id, email) "
        "VALUES (:invitation, :link, :email)"),
        {"invitation": invitation.id, "link": LAUNCH_SIGNUP_ID, "email": invitation.email})
