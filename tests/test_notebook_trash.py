"""Private draft deletion preserves evidence, ownership and retry semantics."""
from uuid import uuid4

import pytest

from crucible import machine_auth, notebook, notebook_trash
from test_notebook import (system, content, create, revise, revision_body,
                           publication_body, request_publication, approve,
                           workspace, import_workspace, count)
from test_notebook_agents import agent
from test_notebook_graph import graph, reference
from test_proposal_notes import scheme_content, submit as submit_scheme
from test_research_reviews import submit as submit_research, request_body


def delete(actor, note, body=None):
    return actor.post(f'/api/notebook/notes/{note["id"]}/delete', body or publication_body(note))


def restore(actor, deletion, body=None):
    return actor.post(f'/api/notebook/notes/{deletion["note_id"]}/restore', body or {
        "deletion_id": deletion["id"], "idempotency_key": str(uuid4())})


def test_trash_hides_all_revisions_and_graph_edges_but_restores_exact_history(system):
    app, owner, _, _, _ = system
    first = create(owner, content(title="Disposable draft"))
    second = revise(owner, first, content(title="Disposable revised draft"))
    citing = create(owner, content(references=[reference(first)]))
    path = "/api/notebook/notes/" + first["id"]
    before = owner.client.get(path).json()["note"]
    timeline_before = owner.client.get("/api/notebook/timeline").json()["total"]
    assert graph(owner, citing).json()["links"]
    assert delete(owner, first).status_code == 409
    result = delete(owner, second)
    assert result.status_code == 200, result.text
    deletion = result.json()["deletion"]
    assert result.json()["deleted"] is True
    assert owner.client.get(path).status_code == 410
    for query in ("", "?scope=mine", "?q=Disposable"):
        assert first["id"] not in owner.client.get("/api/notebook/notes" + query).text
    assert first["id"] not in {n["id"] for n in owner.client.get("/api/notebook/export").json()["notes"]}
    assert owner.client.get("/api/notebook/export", params={"note_id": first["id"]}).status_code == 404
    assert owner.client.get("/api/notebook/timeline").json()["total"] == timeline_before - 1
    assert graph(owner, first).status_code == 404
    assert graph(owner, citing).json()["links"] == []
    trash = owner.client.get("/api/notebook/notes?scope=trash").json()["notes"]
    assert [n["id"] for n in trash] == [first["id"]]
    assert not trash[0]["can_edit"] and trash[0]["can_restore"]
    saved = owner.client.get(path + "?include_deleted=true").json()["note"]
    assert saved["revisions"] == before["revisions"]
    assert saved["deletion"] == deletion and not saved["can_delete"]
    assert restore(owner, deletion).json()["deleted"] is False
    restored = owner.client.get(path).json()["note"]
    assert restored["revisions"] == before["revisions"]
    assert restored["can_delete"] and not restored["can_restore"]
    assert owner.client.get("/api/notebook/notes?scope=trash").json()["notes"] == []
    assert graph(owner, citing).json()["links"]
    assert count(app, notebook.NotebookNote) == 2
    assert count(app, notebook.NotebookRevision) == 3


def test_retries_cannot_reverse_later_delete_or_restore_operations(system):
    app, owner, *_ = system
    note = create(owner)
    payload = publication_body(note)
    first = delete(owner, note, payload).json()["deletion"]
    assert delete(owner, note, payload).json()["deletion"] == first
    assert delete(owner, note).status_code == 409
    restore_body = {"deletion_id": first["id"], "idempotency_key": str(uuid4())}
    assert restore(owner, first, restore_body).json()["deleted"] is False
    # A delayed delete retry only retrieves its receipt; it cannot re-delete.
    assert delete(owner, note, payload).json()["deleted"] is False
    assert restore(owner, first, restore_body).json()["deleted"] is False
    second = delete(owner, note).json()["deletion"]
    assert second["id"] != first["id"]
    # Likewise an old restore retry cannot restore a newer deletion.
    assert restore(owner, first, restore_body).json()["deleted"] is True
    assert restore(owner, first).status_code == 409
    assert restore(owner, second, {**restore_body, "deletion_id": second["id"]}).status_code == 409
    assert delete(owner, note, {**payload, "content_sha256": "f" * 64}).status_code == 409
    assert restore(owner, second).json()["deleted"] is False
    assert count(app, notebook_trash.NotebookDeletion) == 2


def test_only_owner_with_write_authority_can_delete_or_restore(system):
    app, owner, reviewer, reader, _ = system
    note = create(owner)
    path = "/api/notebook/notes/" + note["id"]
    payload = publication_body(note)
    for actor in (reviewer, reader):
        assert delete(actor, note).status_code == 404
    assert owner.client.post(path + "/delete", json=payload).status_code == 403  # Browser CSRF
    for scopes in (machine_auth.DEFAULT_SCOPES, ("notebook:read",), ("notebook:publish",)):
        client = agent(app, owner, scopes)
        assert client.post(path + "/delete", json=payload).status_code == 403
    writer = agent(app, owner, ("notebook:write",))
    result = writer.post(path + "/delete", json=payload)
    assert result.status_code == 200, result.text
    deletion = result.json()["deletion"]
    for actor in (reviewer, reader):
        assert restore(actor, deletion).status_code == 404
        assert actor.client.get(path + "?include_deleted=true").status_code == 404
        assert actor.client.get("/api/notebook/notes?scope=trash").json()["notes"] == []
    read_agent = agent(app, owner, ("notebook:read",))
    assert read_agent.get(path).status_code == 410
    assert read_agent.get(path + "?include_deleted=true").json()["note"]["deletion"] == deletion
    assert read_agent.get("/api/notebook/notes?scope=trash").json()["notes"][0]["id"] == note["id"]
    restore_body = {"deletion_id": deletion["id"], "idempotency_key": str(uuid4())}
    assert read_agent.post(path + "/restore", json=restore_body).status_code == 403
    assert writer.post(path + "/restore", json=restore_body).json()["deleted"] is False


@pytest.mark.parametrize("history", ["requested", "approved", "changes_requested", "scheme", "research"])
def test_submitted_evidence_cannot_be_deleted_even_after_a_new_private_revision(system, history):
    _, owner, reviewer, *_ = system
    note = create(owner, scheme_content() if history == "scheme" else content())
    if history == "scheme":
        assert submit_scheme(owner, note).status_code == 200
    elif history == "research":
        submit_research(owner, note, purpose="procedure")
    else:
        request_publication(owner, note)
        if history == "approved":
            approve(reviewer, note)
        elif history == "changes_requested":
            assert reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews',
                publication_body(note, decision="changes_requested", reason="Needs clearer attribution.")).status_code == 200
    revised = revise(owner, note)
    assert revised["publication_status"] == "private" and not revised["can_delete"]
    result = delete(owner, revised)
    assert result.status_code == 409 and "evidence" in result.text
    assert owner.client.get("/api/notebook/notes/" + note["id"]).status_code == 200


def test_deleted_note_cannot_be_revised_submitted_or_resurrected_by_import(system):
    app, owner, *_ = system
    value = workspace()
    value["views"] = [{"id": "view", "kind": "argument", "title": "Legacy view",
        "question": "What follows?", "scope": "Illustrative only.", "bindings": [{
            "id": "binding", "noteId": "local-note", "noteRevision": 1,
            "excerpt": "A quoted claim.", "scope": "Illustrative example.",
            "assessment": "unreviewed", "assessmentReason": "",
            "from": "Premise", "to": "Conclusion", "relation": "supports"}]}]
    imported = import_workspace(owner, value)
    assert imported.status_code == 200, imported.text
    note_id = imported.json()["id_map"]["local-note"]
    path = "/api/notebook/notes/" + note_id
    note = owner.client.get(path).json()["note"]
    assert delete(owner, note).status_code == 200
    for suffix, body in (("/revisions", revision_body(note)),
                         ("/publication-requests", publication_body(note)),
                         ("/proposal-submissions", publication_body(note))):
        assert owner.post(path + suffix, body).status_code == 410
    assert owner.post("/api/notebook/research-requests", request_body(note, purpose="procedure")).status_code == 404
    assert import_workspace(owner, value).status_code == 410
    assert owner.client.get("/api/notebook/imports/" + imported.json()["import_id"]).json()["workspace"] == value
    exported = owner.client.get("/api/notebook/export").json()
    assert exported["notes"] == [] and exported["views"][0]["bindings"] == []
    assert count(app, notebook.NotebookRevision) == 1


def test_trash_counts_toward_quota_and_old_create_retry_does_not_resurrect(system, monkeypatch):
    app, owner, *_ = system
    key = str(uuid4())
    note = create(owner, key=key)
    assert delete(owner, note).status_code == 200
    payload = {"content": content(), "idempotency_key": key}
    assert owner.post("/api/notebook/notes", payload).status_code == 410
    monkeypatch.setattr(notebook, "MAX_NOTES", 1)
    assert owner.post("/api/notebook/notes", {**payload, "idempotency_key": str(uuid4())}).status_code == 409
    assert count(app, notebook.NotebookNote) == 1
