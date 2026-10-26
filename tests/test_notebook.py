"""Durability, immutable history, exact publication and legacy file boundaries."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, func, inspect, select

from crucible import beta_access, notebook
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Database, LoginSession, User
from crucible.machine_auth import MachineToken, DEFAULT_SCOPES
from crucible.security import mac, passwords, token_hash

ORIGIN = "http://testserver"


class Actor:
    def __init__(self, app, identifier):
        self.app, self.identifier = app, identifier
        self.client = TestClient(app)
        raw = "notebook-fixture-" + str(uuid4())
        with app.state.db.transaction(write=True) as session:
            session.add(LoginSession(token_hash=token_hash(raw), user_id=identifier, expires_at=app.state.clock() + 3600))
        self.client.cookies.set(app.state.settings.cookie_name, raw)
        self.csrf = mac(app.state.settings.secret, "csrf-v1", raw.encode("ascii"))

    def post(self, path, body):
        return self.client.post(path, json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": self.csrf})


@pytest.fixture
def system(tmp_path):
    now = [3_600_600.0]
    owner_id, reviewer_id, reader_id = [str(uuid4()) for _ in range(3)]
    settings = Settings(f"sqlite:///{tmp_path / 'notebook.db'}", b"n" * 32,
        origins=(ORIGIN,), reviewer_ids=(reviewer_id,), rate_limits=False)
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in ((owner_id, "author"), (reviewer_id, "reviewer"), (reader_id, "reader")):
            session.add(User(id=identifier, handle=handle, password_hash="unused-fixture-only", created_at=now[0]))
    yield app, Actor(app, owner_id), Actor(app, reviewer_id), Actor(app, reader_id), now
    app.state.db.engine.dispose()


def content(**changes):
    return {"title": "A conditional observation", "claim": "Bound B holds under condition H.",
            "conditions": "Assume H in the specified model.", "body": "A supporting informal argument.",
            "tags": ["finite groups"], **changes}


def create(actor, value=None, key=None):
    response = actor.post("/api/notebook/notes", {"content": value or content(), "idempotency_key": key or str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()["note"]


def revision_body(note, value=None, key=None):
    return {"content": value or content(title="A revised observation"), "expected_revision": note["revision"],
            "content_sha256": note["content_sha256"], "idempotency_key": key or str(uuid4())}


def revise(actor, note, value=None):
    response = actor.post(f'/api/notebook/notes/{note["id"]}/revisions', revision_body(note, value))
    assert response.status_code == 200, response.text
    return response.json()["note"]


def publication_body(note, **extra):
    return {"revision": note["revision"], "content_sha256": note["content_sha256"],
            "idempotency_key": str(uuid4()), **extra}


def request_publication(actor, note):
    response = actor.post(f'/api/notebook/notes/{note["id"]}/publication-requests', publication_body(note))
    assert response.status_code == 200, response.text
    return response.json()["note"]


def approve(actor, note, **extra):
    response = actor.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
        publication_body(note, decision="approved", reason="Suitable for sharing; conjecture and limitations are explicit.", **extra))
    assert response.status_code == 200, response.text
    return response.json()["note"]


def workspace(identifier="local-note", revisions=1):
    return {"format": notebook.FORMAT, "notes": [{"id": identifier,
        "revisions": [{**content(title=f"Imported revision {number}"), "revision": number, "createdAt": 1000 + number}
                      for number in range(1, revisions + 1)]}], "views": []}


def import_workspace(actor, value, key=None):
    return actor.post("/api/notebook/import", {"workspace": value, "idempotency_key": key or str(uuid4())})


def count(app, model):
    with app.state.db.transaction() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_publication_queue_serves_oldest_requests_before_random_identifiers(system):
    app, owner, reviewer, _, now = system
    for _ in range(3):
        request_publication(owner, create(owner))
    with app.state.db.transaction(write=True) as session:
        rows = session.scalars(select(notebook.NotebookPublication)
            .order_by(notebook.NotebookPublication.id)).all()
        for index, row in enumerate(rows):
            row.requested_at = now[0] - index
        expected = [session.get(notebook.NotebookRevision, row.revision_id).note_id for row in reversed(rows)]
    identifiers, cursor = [], None
    for _ in expected:
        page = reviewer.client.get('/api/notebook/review-queue', params={
            'limit': 1, **({'after': cursor} if cursor else {})}).json()
        identifiers.extend(note['id'] for note in page['notes'])
        cursor = page['next_after']
    assert identifiers == expected
    assert cursor is None


def test_publication_queue_includes_own_pending_requests_without_self_review_permission(system):
    app, owner, reviewer, reader, now = system
    own = create(reviewer, content(title="Reviewer's submitted note"))
    request_publication(reviewer, own)
    now[0] += 1
    other = create(owner, content(title="Another author's submitted note"))
    request_publication(owner, other)
    create(reviewer, content(title="Reviewer's unsubmitted private note"))
    create(owner, content(title="Another author's unsubmitted private note"))

    first_response = reviewer.client.get('/api/notebook/review-queue', params={'limit': 1})
    assert first_response.status_code == 200
    assert first_response.headers['cache-control'] == 'no-store'
    first = first_response.json()
    assert [note['id'] for note in first['notes']] == [own['id']]
    assert first['notes'][0]['publication_status'] == 'submitted'
    assert first['notes'][0]['can_review'] is False
    assert first['notes'][0]['can_self_review'] is False
    second = reviewer.client.get('/api/notebook/review-queue', params={
        'limit': 1, 'after': first['next_after']}).json()
    assert [note['id'] for note in second['notes']] == [other['id']]
    assert second['notes'][0]['can_review'] is True
    assert second['next_after'] is None

    decision = reviewer.post(f'/api/notebook/notes/{own["id"]}/publication-reviews',
        publication_body(own, decision='approved', reason='Own request remains visible only.'))
    assert decision.status_code == 403
    with app.state.db.transaction() as session:
        own_publication = session.scalar(select(notebook.NotebookPublication)
            .where(notebook.NotebookPublication.author_id == reviewer.identifier))
        assert own_publication.decision is None
        assert own_publication.reviewer_id is None
    assert reader.client.get(f'/api/notebook/notes/{own["id"]}').status_code == 404
    for actor in (owner, reader):
        assert actor.client.get('/api/notebook/review-queue').status_code == 403
    assert TestClient(app).get('/api/notebook/review-queue').status_code == 401


def test_private_notes_survive_restart_with_exact_immutable_revision_hashes(system):
    app, owner, reviewer, reader, now = system
    first = create(owner, content(references=[{"url": "https://example.org/paper"}], extensions={"label": "unproved"}))
    second = revise(owner, first)
    for actor in (reviewer, reader):
        assert actor.client.get(f'/api/notebook/notes/{first["id"]}').status_code == 404
        assert actor.client.get("/api/notebook/notes").json()["notes"] == []
    restarted = create_app(app.state.settings, clock=lambda: now[0])
    try:
        restored = Actor(restarted, owner.identifier).client.get(f'/api/notebook/notes/{first["id"]}').json()["note"]
        assert [r["content_sha256"] for r in restored["revisions"]] == [first["content_sha256"], second["content_sha256"]]
        assert restored["revisions"][0]["content"]["extensions"] == {"label": "unproved"}
        assert all(r["publication_status"] == "private" for r in restored["revisions"])
    finally:
        restarted.state.db.engine.dispose()


def test_create_and_revision_retries_are_stable_after_later_changes(system):
    app, owner, *_ = system
    key = str(uuid4())
    first = create(owner, key=key)
    body = revision_body(first)
    path = f'/api/notebook/notes/{first["id"]}/revisions'
    second = owner.post(path, body).json()["note"]
    third = revise(owner, second, content(title="Third revision"))
    retry = owner.post(path, body)
    assert retry.status_code == 200
    assert retry.json()["note"]["revision"] == 2
    assert create(owner, key=key)["revision"] == 1
    assert count(app, notebook.NotebookNote) == 1
    assert count(app, notebook.NotebookRevision) == 3
    assert owner.post(path, {**body, "content": content(title="Different")}).status_code == 409
    assert owner.post(path, {**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert owner.post("/api/notebook/notes", {"content": content(title="Different"), "idempotency_key": key}).status_code == 409
    assert third["revision"] == 3


def test_concurrent_stale_edits_cannot_overwrite_history(system):
    app, owner, *_ = system
    note = create(owner)
    clients = [Actor(app, owner.identifier), Actor(app, owner.identifier)]
    bodies = [revision_body(note, content(title=f"Writer {i}")) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: pair[0].post(f'/api/notebook/notes/{note["id"]}/revisions', pair[1]), zip(clients, bodies)))
    assert sorted(result.status_code for result in results) == [200, 409]
    assert count(app, notebook.NotebookRevision) == 2


def test_list_is_a_bounded_summary_and_pages_without_loading_note_bodies(system):
    _, owner, *_ = system
    first = create(owner, content(body="PRIVATE_BODY_SENTINEL"))
    revise(owner, first, content(body="ANOTHER_BODY_SENTINEL", conditions="PRIVATE_CONDITIONS_SENTINEL"))
    create(owner, content(title="Another note"))
    page = owner.client.get("/api/notebook/notes?limit=1").json()
    assert len(page["notes"]) == 1 and page["next_after"]
    assert "BODY_SENTINEL" not in json.dumps(page)
    assert "PRIVATE_CONDITIONS_SENTINEL" not in json.dumps(page)
    next_page = owner.client.get("/api/notebook/notes?limit=1&after=" + page["next_after"]).json()
    assert next_page["notes"][0]["id"] != page["notes"][0]["id"]
    assert next_page["next_after"] is None


def test_exact_revision_review_does_not_expose_earlier_or_future_private_drafts(system):
    _, owner, reviewer, reader, _ = system
    first = create(owner, content(title="Earlier private material"))
    second = revise(owner, first, content(title="Ready to share"))
    requested = request_publication(owner, second)
    assert requested["publication_status"] == "submitted"
    assert reader.client.get(f'/api/notebook/notes/{first["id"]}').status_code == 404
    review_note = reviewer.client.get(f'/api/notebook/notes/{first["id"]}').json()["note"]
    assert [r["revision"] for r in review_note["revisions"]] == [2]
    assert review_note["revision_count"] == 1
    assert reviewer.client.get(f'/api/notebook/notes/{first["id"]}?revision=1').status_code == 404
    assert reviewer.client.get("/api/notebook/review-queue").json()["notes"][0]["revision"] == 2
    third = revise(owner, second, content(title="Future private material"))
    approve(reviewer, second)
    visible = reader.client.get(f'/api/notebook/notes/{first["id"]}').json()["note"]
    assert [r["revision"] for r in visible["revisions"]] == [2]
    assert visible["revision_count"] == 1
    assert visible["shared_revision"]["revision"] == 2
    assert reader.client.get(f'/api/notebook/notes/{first["id"]}?revision=3').status_code == 404
    listed = owner.client.get("/api/notebook/notes").json()["notes"][0]
    assert listed["revision"] == third["revision"] == 3
    assert listed["publication_status"] == "private"
    assert listed["shared_revision"]["content"]["title"] == "Ready to share"
    assert listed["shared_revision"]["revision"] == 2
    assert reviewer.client.get("/api/notebook/review-queue").json()["notes"] == []


def test_publication_actions_require_owner_exact_hash_reviewer_and_one_decision(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner)
    request_path = f'/api/notebook/notes/{note["id"]}/publication-requests'
    review_path = f'/api/notebook/notes/{note["id"]}/publication-reviews'
    assert reader.post(request_path, publication_body(note)).status_code == 404
    assert owner.post(request_path, publication_body(note, content_sha256="0" * 64)).status_code == 409
    assert reviewer.post(review_path, publication_body(note, decision="approved", reason="Not submitted.")).status_code == 404
    request = publication_body(note)
    assert owner.post(request_path, request).status_code == 200
    assert owner.post(request_path, request).status_code == 200
    decision = publication_body(note, decision="changes_requested", reason="Please clarify the scope before sharing.")
    assert reader.post(review_path, decision).status_code == 403
    assert owner.post(review_path, decision).status_code == 403
    assert reviewer.post(review_path, {**decision, "content_sha256": "0" * 64}).status_code == 409
    assert reviewer.post(review_path, decision).status_code == 200
    assert reviewer.post(review_path, decision).status_code == 200
    assert reviewer.post(review_path, {**decision, "decision": "approved"}).status_code == 409
    assert reviewer.post(review_path, {**decision, "idempotency_key": str(uuid4())}).status_code == 409
    assert reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404
    assert count(app, notebook.NotebookPublication) == 1


def test_compact_publication_receipt_and_retry_never_load_revision_bodies(system):
    app, owner, _, _, now = system
    first = create(owner, content(body="FIRST_PRIVATE_BODY_NOT_LOADED"))
    latest = revise(owner, first, content(body="LATEST_PRIVATE_BODY_NOT_LOADED"))
    with app.state.db.transaction(write=True) as session:
        session.get(notebook.NotebookNote, latest["id"]).source_metadata = {"private": "IMPORTED_METADATA_NOT_LOADED"}
    statements = []

    def inspect_read(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
            assert not re.search(r"\bnotebook_revisions\.(?:content|source_revision)\b", statement)
            assert "notebook_notes.source_metadata" not in statement
            assert "notebook_publications.reason" not in statement

    path = f'/api/notebook/notes/{latest["id"]}/publication-requests?summary=true'
    body = publication_body(latest)
    event.listen(app.state.db.engine, "before_cursor_execute", inspect_read)
    try:
        response = owner.post(path, body)
        retry = owner.post(path, body)
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", inspect_read)
    assert response.status_code == retry.status_code == 200
    assert response.json() == retry.json()
    receipt = response.json()
    assert set(receipt) == {"format", "publication_request"}
    assert receipt["format"] == "crucible-publication-request-v1"
    record = receipt["publication_request"]
    assert record == {"id": record["id"], "note_id": latest["id"], "revision": latest["revision"],
        "content_sha256": latest["content_sha256"], "requested_at": now[0], "status": "submitted"}
    assert re.fullmatch(r"[0-9a-f-]{36}", record["id"])
    assert len(response.content) < 500
    assert response.headers["cache-control"] == "no-store"
    assert any("FROM notebook_revisions" in sql for sql in statements)
    assert count(app, notebook.NotebookPublication) == 1


@pytest.mark.parametrize("compact_first", [False, True])
def test_publication_response_projection_does_not_change_idempotency_or_full_history(system, compact_first):
    app, owner, _, _, _ = system
    first = create(owner, content(body="Original exact history"))
    latest = revise(owner, first, content(body="Latest exact history"))
    path = f'/api/notebook/notes/{latest["id"]}/publication-requests'
    body = publication_body(latest)
    initial = owner.post(path + ("?summary=true" if compact_first else ""), body)
    full = owner.post(path, body)
    compact = owner.post(path + "?summary=true", body)
    assert initial.status_code == full.status_code == compact.status_code == 200
    assert set(full.json()) == {"note"}
    assert [row["content"]["body"] for row in full.json()["note"]["revisions"]] == [
        "Original exact history", "Latest exact history"]
    assert full.json()["note"]["publication_status"] == "submitted"
    assert compact.json()["publication_request"]["revision"] == latest["revision"]
    assert count(app, notebook.NotebookPublication) == 1


def test_compact_publication_preserves_owner_csrf_latest_revision_and_existing_request_checks(system):
    app, owner, _, reader, _ = system
    first = create(owner)
    latest = revise(owner, first, content(title="Latest revision"))
    path = f'/api/notebook/notes/{latest["id"]}/publication-requests?summary=true'
    body = publication_body(latest)
    assert TestClient(app).post(path, json=body, headers={"Origin": ORIGIN}).status_code in (401, 403)
    assert owner.client.post(path, json=body).status_code == 403
    assert reader.post(path, body).status_code == 404
    assert owner.post(path, publication_body(first)).status_code == 409
    assert owner.post(path, {**body, "content_sha256": "0" * 64}).status_code == 409
    trashed = create(owner, content(title="Deleted private draft"))
    assert owner.post(f'/api/notebook/notes/{trashed["id"]}/delete', publication_body(trashed)).status_code == 200
    assert owner.post(f'/api/notebook/notes/{trashed["id"]}/publication-requests?summary=true',
        publication_body(trashed)).status_code == 410
    assert count(app, notebook.NotebookPublication) == 0
    assert owner.post(path, body).status_code == 200
    assert owner.post(path, {**body, "idempotency_key": str(uuid4())}).status_code == 409
    assert owner.post(path, {**body, "revision": first["revision"],
        "content_sha256": first["content_sha256"]}).status_code == 409
    assert count(app, notebook.NotebookPublication) == 1


def test_compact_publication_retry_is_bound_to_requested_revision_and_current_decision(system):
    app, owner, reviewer, _, _ = system
    note = create(owner)
    path = f'/api/notebook/notes/{note["id"]}/publication-requests?summary=true'
    body = publication_body(note)
    original = owner.post(path, body).json()["publication_request"]
    approve(reviewer, note)
    assert owner.post(path, body).json()["publication_request"] == {**original, "status": "approved"}
    withdrawn = reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-withdrawals',
        publication_body(note, reason="Synthetic private material must be withdrawn."))
    assert withdrawn.status_code == 200
    latest = revise(owner, note, content(title="Newer private revision"))
    replay = owner.post(path, body)
    assert replay.status_code == 200
    assert replay.json()["publication_request"] == {**original, "status": "withdrawn"}
    assert latest["revision"] != replay.json()["publication_request"]["revision"]
    assert count(app, notebook.NotebookPublication) == 1


def test_self_review_requires_development_flag_and_never_becomes_independent_approval(system):
    app, owner, reviewer, reader, now = system
    note = create(reviewer)
    request_publication(reviewer, note)
    path = f'/api/notebook/notes/{note["id"]}/publication-reviews'
    body = publication_body(note, decision="approved", reason="Development publication workflow test.")
    assert reviewer.post(path, body).status_code == 403
    queued = reviewer.client.get("/api/notebook/review-queue").json()["notes"]
    assert [row["id"] for row in queued] == [note["id"]]
    assert queued[0]["can_review"] is False
    dev = create_app(replace(app.state.settings, allow_self_review=True), clock=lambda: now[0])
    try:
        dev_reviewer = Actor(dev, reviewer.identifier)
        response = dev_reviewer.post(path, body)
        assert response.status_code == 200, response.text
        assert response.json()["note"]["revisions"][0]["publication"]["test_only_self_review"] is True
        assert Actor(dev, reader.identifier).client.get(f'/api/notebook/notes/{note["id"]}').status_code == 200
        assert reader.client.get(f'/api/notebook/notes/{note["id"]}').status_code == 404
        assert reviewer.client.get(f'/api/notebook/notes/{note["id"]}').json()["note"]["shared_revision"] is None
    finally:
        dev.state.db.engine.dispose()


def test_import_preserves_full_history_metadata_and_timestamp_provenance_without_authority(system):
    app, owner, reviewer, reader, now = system
    value = workspace(revisions=2)
    value["notes"][0]["revisions"][0].update(assessment="reviewed", review_status="approved", verification={"checked": True})
    value["notes"][0]["revisions"][1]["extensions"] = {"domain": ["rings"]}
    value["views"] = [{"id": "legacy-view", "kind": "argument", "title": "Legacy view", "question": "What follows?", "scope": "Illustrative only.",
        "bindings": [{"id": "legacy-binding", "noteId": "local-note", "noteRevision": 1,
            "excerpt": "A quoted claim.", "scope": "Illustrative example.", "assessment": "reviewed", "assessmentReason": "A simulated label.",
            "from": "Premise", "to": "Conclusion", "relation": "supports"}]}]
    key = str(uuid4())
    response = import_workspace(owner, value, key)
    assert response.status_code == 200, response.text
    imported = response.json()
    note_id = imported["id_map"]["local-note"]
    assert note_id != "local-note"
    note = owner.client.get(f"/api/notebook/notes/{note_id}").json()["note"]
    assert [r["publication_status"] for r in note["revisions"]] == ["private", "private"]
    assert all(r["created_at"] == now[0] for r in note["revisions"])
    assert note["revisions"][0]["imported_created_at"] == 1001
    assert "assessment" not in note["revisions"][0]["content"]
    assert reviewer.client.get(f"/api/notebook/notes/{note_id}").status_code == 404
    exported = owner.client.get("/api/notebook/export").json()
    assert exported["notes"][0]["revisions"] == value["notes"][0]["revisions"]
    assert exported["views"][0]["bindings"][0]["noteId"] == note_id
    original_path = "/api/notebook/imports/" + imported["import_id"]
    assert owner.client.get(original_path).json()["workspace"] == value
    assert reader.client.get(original_path).status_code == 404
    assert import_workspace(owner, value, key).json()["replayed"] is True
    assert count(app, notebook.NotebookImport) == 1
    assert import_workspace(owner, exported).json()["added_revisions"] == 0


def test_import_append_is_atomic_and_cannot_rewrite_or_shorten_history(system):
    app, owner, *_ = system
    value = workspace()
    first = import_workspace(owner, value).json()
    appended = workspace(revisions=2)
    assert import_workspace(owner, appended).json()["added_revisions"] == 1
    damaged = deepcopy(appended)
    damaged["notes"][0]["revisions"][0]["body"] = "Rewritten past."
    damaged["notes"].insert(0, workspace("must-roll-back")["notes"][0])
    assert import_workspace(owner, damaged).status_code == 409
    assert count(app, notebook.NotebookNote) == 1
    assert count(app, notebook.NotebookRevision) == 2
    assert import_workspace(owner, value).status_code == 409
    assert owner.client.get(f'/api/notebook/notes/{first["id_map"]["local-note"]}').json()["note"]["revision"] == 2


def test_import_cannot_edit_foreign_notes_but_local_ids_are_owner_scoped(system):
    _, owner, reviewer, reader, _ = system
    shared_source = workspace()
    one = import_workspace(owner, shared_source).json()["id_map"]["local-note"]
    two = import_workspace(reader, shared_source).json()["id_map"]["local-note"]
    assert one != two
    assert import_workspace(reader, workspace(one)).status_code == 409


@pytest.mark.parametrize("bad", [
    {"kind": None}, {"title": "  "}, {"tags": [4]}, {"tags": [""]},
    {"extensions": {"__proto__": {"admin": True}}}, {"extensions": {"huge": 10 ** 1000}},
    {"publication_status": "approved"}, {"references": {"bad": float("inf")}},
])
def test_invalid_content_does_not_create_notes_or_crash(system, bad):
    app, owner, *_ = system
    payload = {"content": content(**bad), "idempotency_key": str(uuid4())}
    # Raw JSON also exercises hostile Infinity, which strict JSON clients refuse.
    response = owner.client.post("/api/notebook/notes", content=json.dumps(payload),
        headers={"Origin": ORIGIN, "X-CSRF-Token": owner.csrf, "Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert count(app, notebook.NotebookNote) == 0


def test_writes_require_csrf_and_agents_cannot_borrow_notebook_authority(system):
    app, owner, *_ = system
    payload = {"content": content(), "idempotency_key": str(uuid4())}
    assert owner.client.post("/api/notebook/notes", json=payload, headers={"Origin": ORIGIN}).status_code == 403
    token = "cru_" + "a" * 43
    with app.state.db.transaction(write=True) as session:
        session.add(MachineToken(id=str(uuid4()), user_id=owner.identifier, token_hash=token_hash(token),
            label="Existing agent", scopes=list(DEFAULT_SCOPES), created_at=app.state.clock(), expires_at=app.state.clock() + 3600))
    for path in ("/api/notebook/notes", "/api/notebook/export"):
        assert owner.client.get(path, headers={"Authorization": "Bearer " + token}).status_code == 403
    assert owner.client.post("/api/notebook/notes", json=payload,
        headers={"Authorization": "Bearer " + token, "Origin": ORIGIN, "X-CSRF-Token": owner.csrf}).status_code == 403
    anonymous = TestClient(app)
    assert anonymous.get("/api/notebook/notes").status_code == 401
    assert anonymous.get("/api/notebook/export").status_code == 401


def test_beta_revocation_blocks_notebook_reads_writes_and_exports(system):
    app, owner, reviewer, reader, now = system
    note = create(owner)
    with app.state.db.transaction(write=True) as session:
        beta_access.set_membership(session, owner.identifier, "active", now[0])
    private = create_app(replace(app.state.settings, access_mode="beta"), clock=lambda: now[0])
    try:
        actor = Actor(private, owner.identifier)
        assert actor.client.get("/api/notebook/notes").status_code == 200
        with app.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, owner.identifier, "revoked", now[0])
        for path in ("/api/notebook/notes", f'/api/notebook/notes/{note["id"]}', "/api/notebook/export"):
            assert actor.client.get(path).status_code in {401, 403}
        assert actor.post("/api/notebook/notes", {"content": content(), "idempotency_key": str(uuid4())}).status_code in {401, 403}
    finally:
        private.state.db.engine.dispose()


def test_account_capacity_rejects_change_without_losing_history_and_exports_round_trip(system, monkeypatch):
    app, owner, *_ = system
    first = create(owner)
    before = owner.client.get("/api/notebook/export").json()
    monkeypatch.setattr(notebook, "MAX_WORKSPACE_BYTES", len(notebook._export_bytes(before)) + 100)
    response = owner.post(f'/api/notebook/notes/{first["id"]}/revisions', revision_body(first, content(body="a" * 500)))
    assert response.status_code == 409
    assert "capacity" in response.json()["detail"]
    assert count(app, notebook.NotebookRevision) == 1
    assert owner.client.get("/api/notebook/export").json() == before
    assert import_workspace(owner, before).status_code == 200


def test_import_archive_quota_retries_are_free_and_failed_batch_is_rolled_back(system, monkeypatch):
    app, owner, *_ = system
    value = workspace()
    monkeypatch.setattr(notebook, "MAX_IMPORT_ARCHIVE_BYTES", len(notebook._bytes(value)))
    key = str(uuid4())
    assert import_workspace(owner, value, key).status_code == 200
    assert import_workspace(owner, value, key).status_code == 200
    assert import_workspace(owner, workspace("new-note")).status_code == 409
    assert count(app, notebook.NotebookNote) == 1
    assert count(app, notebook.NotebookImport) == 1


def test_notebook_migration_preserves_existing_accounts_and_sessions(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"m" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "open")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "0010")
    db = Database(url)
    try:
        with db.transaction(write=True) as session:
            user = User(id=str(uuid4()), handle="preserved", password_hash="unchanged-fixture", created_at=1.0)
            session.add(user)
            session.flush()
            session.add(LoginSession(token_hash="a" * 64, user_id=user.id, expires_at=100.0))
        def existing():
            with db.engine.connect() as connection:
                return {model.__tablename__: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
                        for model in (User, LoginSession)}
        before = existing()
        command.upgrade(config, "head")
        command.check(config)
        assert existing() == before
        assert "notebook_notes" in inspect(db.engine).get_table_names()
        with db.transaction() as session:
            assert session.scalar(select(func.count()).select_from(notebook.NotebookNote)) == 0
        command.downgrade(config, "0010")
        assert existing() == before
    finally:
        db.engine.dispose()
