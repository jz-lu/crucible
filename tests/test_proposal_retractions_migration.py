"""Author withdrawals add state without rewriting existing account or evidence history."""
import base64
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, func, inspect, select

from crucible import notebook, notebook_sharing, proposal_notes, protocol_proposals as proposals, research_reviews
from crucible.access_policy import PUBLIC
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Database, LoginSession, User
from crucible.proposal_retractions import ProposalRetraction
from crucible.security import passwords, token_hash, verify_password


def isolated_database(tmp_path, monkeypatch):
    # Alembic loads settings from the environment. Never inherit an operator's
    # database, data directory, mail transport, or credentials.
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    url = f"sqlite:///{tmp_path / 'before-retractions.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"w" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "open")
    monkeypatch.setenv("CRUCIBLE_MAIL_BACKEND", "disabled")
    return url


def test_retractions_migration_preserves_accounts_and_frozen_evidence(tmp_path, monkeypatch):
    url = isolated_database(tmp_path, monkeypatch)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0014")
    db = Database(url)
    author_id, reviewer_id, note_id, proposal_id, request_id = [str(uuid4()) for _ in range(5)]
    now = 3_600_600.125
    password = "Synthetic withdrawal migration fixture only"
    encoded = passwords.hash(password)
    browser_digest = token_hash("synthetic-retractions-browser-session")
    first_content = {"title": "Frozen α evidence", "body": "First line\nSecond line  ",
                     "extensions": {"ordered": [3, 1, 2], "optional": None}}
    first_digest = notebook._hash(first_content)
    try:
        old_tables = sorted(set(inspect(db.engine).get_table_names()) - {"alembic_version"})
        assert "proposal_retractions" not in old_tables
        # Reflect the actual 0014 schema so current ORM defaults cannot add
        # columns from later migrations to these historical fixture inserts.
        original_schema = MetaData()
        original_schema.reflect(bind=db.engine, only=old_tables)
        with db.engine.begin() as connection:
            def insert(model, **values):
                connection.execute(original_schema.tables[model.__tablename__].insert().values(**values))

            for identifier, handle in ((author_id, "migration_author"), (reviewer_id, "migration_reviewer")):
                insert(User, id=identifier, handle=handle, password_hash=encoded, created_at=now)
            insert(LoginSession, token_hash=browser_digest, user_id=author_id, expires_at=now + 86400)
            insert(notebook.NotebookNote, id=note_id, author_id=author_id, created_at=now, latest_revision=2,
                   idempotency_key=str(uuid4()), request_sha256=first_digest,
                   source_id="imported:original-note", source_metadata={"label": "Original α", "extra": [False, None]})
            insert(notebook_sharing.PrivateNoteShare, id=str(uuid4()), note_id=note_id,
                   recipient_id=reviewer_id, from_revision=1, shared_at=now + 1, revoked_at=None)
            insert(notebook_sharing.PrivateNoteShareChange, id=str(uuid4()), note_id=note_id,
                   author_id=author_id, sequence=1, created_at=now + 1, revision=1,
                   content_sha256=first_digest, before_recipients=[], after_recipients=[reviewer_id],
                   before_sharing_sha256=notebook._hash([]), after_sharing_sha256=notebook._hash([reviewer_id]),
                   idempotency_key=str(uuid4()), request_sha256="e" * 64)
            latest_spec = {"name": "Revised construction", "revision": 2, "assumptions": ["A", "B"]}
            insert(proposals.ProtocolProposal, id=proposal_id, author_id=author_id,
                   idempotency_key=str(uuid4()), created_at=now, status="submitted", revision=2,
                   content_sha256=notebook._hash(latest_spec), specification=latest_spec)
            for revision in (1, 2):
                note_revision_id, proposal_revision_id = str(uuid4()), str(uuid4())
                content = first_content if revision == 1 else {**first_content, "body": "Later draft; frozen packet stays unchanged."}
                spec = {"name": "Original construction", "revision": 1} if revision == 1 else latest_spec
                digest = notebook._hash(spec)
                insert(notebook.NotebookRevision, id=note_revision_id, note_id=note_id, revision=revision,
                       created_at=now + revision, content=content, content_sha256=notebook._hash(content),
                       idempotency_key=str(uuid4()), request_sha256=notebook._hash(content),
                       source_revision={"original_timestamp": "2026-01-02T03:04:05Z", "revision": revision})
                insert(proposals.ProtocolProposalRevision, id=proposal_revision_id, proposal_id=proposal_id,
                       revision=revision, created_at=now + revision, idempotency_key=str(uuid4()),
                       request_sha256=digest, content_sha256=digest, specification=spec)
                insert(proposal_notes.ProposalNoteLink, proposal_revision_id=proposal_revision_id,
                       proposal_id=proposal_id, note_revision_id=note_revision_id, note_id=note_id,
                       author_id=author_id, created_at=now + revision, origin="notebook",
                       idempotency_key=str(uuid4()), request_sha256=digest)
                if revision == 1:
                    insert(proposals.ProtocolProposalReview, id=str(uuid4()), proposal_id=proposal_id,
                           revision=1, reviewer_id=reviewer_id, created_at=now + 10,
                           decision="changes_requested", content_sha256=digest, implementation_sha256=None,
                           checks={"correctness": False, "specification_complete": True},
                           notes="Preserve the original review.\n", evidence="Exact evidence α  ",
                           idempotency_key=str(uuid4()), request_sha256="a" * 64)
                    insert(notebook.NotebookPublication, id=str(uuid4()), revision_id=note_revision_id,
                           author_id=author_id, requested_at=now + 11, idempotency_key=str(uuid4()),
                           request_sha256=first_digest, decision="approved", reviewer_id=reviewer_id,
                           reviewed_at=now + 12, reason="Scoped publication only.", review_key=str(uuid4()),
                           review_sha256="b" * 64, test_only_self_review=False)
            insert(research_reviews.ResearchRequest, id=request_id, author_id=author_id, created_at=now + 20,
                   purpose="technical_review", title="Frozen first-revision packet", question="Does claim α follow?",
                   sources=[{"note_id": note_id, "revision": 1, "content_sha256": first_digest, "content": first_content}],
                   idempotency_key=str(uuid4()), request_sha256="c" * 64)
            insert(research_reviews.ResearchAssessment, request_id=request_id, reviewer_id=reviewer_id,
                   created_at=now + 21, outcome="changes_requested", checks={"sources_checked": True},
                   findings="Keep the original finding.\nα", limitations="This assessment covers revision 1 only.",
                   test_only_self_review=False, idempotency_key=str(uuid4()), request_sha256="d" * 64)

        def snapshots():
            # Bypass SQLAlchemy JSON decoding so even serialization, whitespace,
            # key order, Unicode and null representation must remain identical.
            # Select original columns; later additive migrations may add more.
            with db.engine.connect() as connection:
                return {table: sorted((tuple(value.encode("utf-8") if isinstance(value, str) else value
                                             for value in row)
                                       for row in connection.exec_driver_sql('SELECT ' + ', '.join(
                                           f'"{column.name}"' for column in original_schema.tables[table].columns)
                                           + f' FROM "{table}"')), key=repr)
                        for table in old_tables}

        before = snapshots()
        for table in ("users", "login_sessions", "notebook_notes", "notebook_revisions",
                      "protocol_proposals", "protocol_proposal_revisions", "protocol_proposal_reviews",
                      "proposal_note_links", "notebook_publications", "research_review_requests", "research_assessments",
                      "notebook_private_shares", "notebook_private_share_changes"):
            assert before[table], table
        assert len(before["notebook_revisions"]) == len(before["protocol_proposal_revisions"]) == 2

        command.upgrade(config, "head")
        command.check(config)
        assert snapshots() == before
        assert set(inspect(db.engine).get_table_names()) == set(old_tables) | {
            "alembic_version", "proposal_retractions", "notebook_private_share_responses",
            "notebook_publication_withdrawals", "notebook_references", "note_ratings"}
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ProposalRetraction)) == 0
            assert verify_password(session.get(User, author_id).password_hash, password)
            assert session.get(LoginSession, browser_digest).user_id == author_id

        command.downgrade(config, "0014")
        assert "proposal_retractions" not in inspect(db.engine).get_table_names()
        assert snapshots() == before
        command.upgrade(config, "head")
        command.check(config)
        assert snapshots() == before
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(ProposalRetraction)) == 0
    finally:
        db.engine.dispose()


def test_retraction_asset_is_readable_anonymously_in_beta(tmp_path, monkeypatch):
    url = isolated_database(tmp_path, monkeypatch)
    assert ("GET", "/proposal-retractions.js") in PUBLIC
    app = create_app(Settings(url, b"w" * 32, origins=("http://testserver",), access_mode="beta", mail_backend="disabled",
                              rate_limits=False), initialize=True)
    try:
        with TestClient(app) as client:
            assert client.get("/api/session").json()["user"] is None
            response = client.get("/proposal-retractions.js")
            assert response.status_code == 200, response.text
            assert "javascript" in response.headers["content-type"]
            assert response.content == (Path(__file__).resolve().parents[1] / "web/proposal-retractions.js").read_bytes()
            assert client.get("/api/schemes").status_code == 401
    finally:
        app.state.db.engine.dispose()
