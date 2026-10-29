"""Actual loopback ASGI app with disposable data and a portable test catalogue.

This entry point is test-only: never installed in the runtime image, never reads
Settings.from_env, and never connects to PostgreSQL, SMTP, GitHub or native KEMs.
"""
from pathlib import Path
import secrets
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from crucible import beta_access, bounty_allowance, dh_v3, dh_v4, github_auth, kem, notebook, registry
from crucible.bounty_models import BountyPolicy
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import LoginSession, User
from crucible.security import passwords, token_hash
import uvicorn

ORIGIN = "http://127.0.0.1:8029"
PASSWORD = "Synthetic browser fixture password"


def main():
    # Keep the current portable release and its declared record-comparison
    # sources together. Record pages must be able to resolve v2/v3 receipts.
    # Native pin checks are not bypassed or altered in application code.
    registry.ADAPTERS = {(adapter.SCHEME_ID, adapter.SCHEME_VERSION): adapter
                         for adapter in (kem, dh_v3, dh_v4)}
    registry.CURRENT = {dh_v4.SCHEME_ID: dh_v4.SCHEME_VERSION}
    with tempfile.TemporaryDirectory(prefix="crucible-browser-checks-") as tmp:
        queue_reviewer_id = str(uuid4())
        settings = Settings("sqlite:///" + tmp + "/fixture.db", secrets.token_bytes(32), bounties_enabled=True,
                            origins=(ORIGIN,), access_mode="beta", rate_limits=False,
                            reviewer_ids=(queue_reviewer_id,),
                            github_login_enabled=True, github_client_id="synthetic-browser-client",
                            github_client_secret="synthetic-browser-provider-secret")
        app = create_app(settings, initialize=True)
        # Browser tests intercept the provider navigation. The application still
        # performs its real state/binding/session checks; no external token call.
        github_auth.fetch_identity = lambda *args: ("123456", "browser-alice")
        now = time.time()
        encoded = passwords.hash(PASSWORD)
        with app.state.db.transaction(write=True) as session:
            # create_all is test-only and does not execute migration policy seeds.
            # Use the reviewed initial policy, never illustrative tasks or awards.
            session.add(BountyPolicy(id=bounty_allowance.INITIAL_ID,
                effective_month=bounty_allowance.INITIAL_MONTH,
                configuration=dict(bounty_allowance.INITIAL_CONFIGURATION), created_at=now))
            for actor in ("alice", "bob", "revoked", "bulkdesktop", "bulkmobile", "queueauthor", "queuereviewer", "numbers"):
                user = User(id=queue_reviewer_id if actor == "queuereviewer" else str(uuid4()), handle="browser_" + actor,
                            password_hash=encoded, created_at=now)
                session.add(user)
                session.flush()
                beta_access.set_membership(session, user.id,
                                           "revoked" if actor == "revoked" else "active", now)
                if actor == "alice":
                    session.add(github_auth.GitHubIdentity(github_user_id="123456", user_id=user.id,
                        github_login="browser-alice", created_at=now, last_login_at=now))
                if actor == "revoked":
                    session.add(LoginSession(token_hash=token_hash("synthetic-revoked-browser-cookie"),
                                             user_id=user.id, expires_at=now + 3600))
                    continue
                content = {"title": actor.title() + " browser-only private note",
                           "claim": "Synthetic browser fixture, not research.",
                           "conditions": "Private to this disposable test account.",
                           "body": actor.upper() + "_PRIVATE_BROWSER_BODY", "tags": ["fixture"]}
                note = notebook.NotebookNote(id=str(uuid4()), author_id=user.id, created_at=now,
                    latest_revision=1, idempotency_key=str(uuid4()), request_sha256="0" * 64)
                session.add(note)
                session.flush()
                revision = notebook.NotebookRevision(id=str(uuid4()), note_id=note.id, revision=1,
                    created_at=now, content=content, content_sha256=notebook._hash(content),
                    idempotency_key=str(uuid4()), request_sha256="1" * 64)
                session.add(revision)
                if actor == "queueauthor":
                    session.flush()
                    session.add(notebook.NotebookPublication(id=str(uuid4()), revision_id=revision.id,
                        author_id=user.id, requested_at=now, idempotency_key=str(uuid4()),
                        request_sha256="2" * 64, test_only_self_review=False))
                    # Three queue pages exercise real authorized cursor reads.
                    # Keep the named reader fixture first in the oldest-first queue.
                    for index in range(1, 52):
                        queued_content = {**content, "title": f"Later publication fixture {index:02d}"}
                        queued_note = notebook.NotebookNote(id=str(uuid4()), author_id=user.id,
                            created_at=now + index / 1000, latest_revision=1, idempotency_key=str(uuid4()),
                            request_sha256="3" * 64)
                        session.add(queued_note)
                        session.flush()
                        queued_revision = notebook.NotebookRevision(id=str(uuid4()), note_id=queued_note.id,
                            revision=1, created_at=now + index / 1000, content=queued_content,
                            content_sha256=notebook._hash(queued_content), idempotency_key=str(uuid4()),
                            request_sha256="4" * 64)
                        session.add(queued_revision)
                        session.flush()
                        session.add(notebook.NotebookPublication(id=str(uuid4()), revision_id=queued_revision.id,
                            author_id=user.id, requested_at=now + index / 1000, idempotency_key=str(uuid4()),
                            request_sha256="5" * 64, test_only_self_review=False))
        print("Disposable browser fixture ready; portable DH catalogue only.", flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=8029, access_log=False, log_level="warning")
        finally:
            app.state.db.engine.dispose()


if __name__ == "__main__":
    main()
