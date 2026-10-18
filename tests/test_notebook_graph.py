"""Exact revision graphs, visibility, import identity and additive migration."""
from copy import deepcopy
import base64
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text

from crucible import notebook, notebook_graph
from test_notebook import (system, content, create, revise, request_publication,
                           approve, publication_body, import_workspace)


def reference(note, relation="builds_on", **changes):
    return {"title": "Authored citation title", "scope": "The claim in section 2, under its stated assumptions.",
            "url": f'/#notebook?note={note["id"]}&revision={note["revision"]}&sha256={note["content_sha256"]}',
            "relation": relation, **changes}


def graph(actor, note, direction="outgoing", **extra):
    return actor.client.get("/api/notebook/graph", params={"note_id": note["id"], "revision": note["revision"],
        "content_sha256": note["content_sha256"], "direction": direction, **extra})


def publish(owner, reviewer, note):
    request_publication(owner, note)
    approve(reviewer, note)


def test_exact_revision_chain_can_revisit_a_note_without_a_cycle(system):
    _, owner, reviewer, reader, _ = system
    first = create(owner, content(title="Construction, version one"))
    publish(owner, reviewer, first)
    critique = create(owner, content(title="A counterexample", references=[reference(first, "challenges")]))
    corrected = revise(owner, first, content(title="Construction, corrected", references=[reference(critique, "responds_to")]))
    for note in (critique, corrected):
        publish(owner, reviewer, note)
    assert graph(reader, first).json()["links"] == []
    edge = graph(reader, critique).json()["links"][0]
    assert edge["target"]["revision"] == 1
    assert edge["target"]["title"] == "Construction, version one"
    assert edge["relation"] == "challenges"
    assert graph(reader, corrected).json()["links"][0]["target"]["note_id"] == critique["id"]
    incoming = graph(reader, first, "incoming").json()["links"]
    assert len(incoming) == 1 and incoming[0]["source"]["note_id"] == critique["id"]
    assert graph(reader, corrected, "incoming").json()["links"] == []
    assert graph(reader, first, content_sha256="f" * 64).status_code == 404


def test_graph_discloses_neither_private_targets_nor_private_backlinks(system):
    app, owner, reviewer, reader, _ = system
    hidden = create(owner, content(title="PRIVATE source"))
    shared = create(owner, content(title="Public observation", references=[reference(hidden)]))
    publish(owner, reviewer, shared)
    followup = create(owner, content(title="PRIVATE follow-up", references=[reference(shared, "supports")]))
    for actor in (reader, reviewer):
        outgoing = graph(actor, shared).json()
        incoming = graph(actor, shared, "incoming").json()
        assert outgoing["links"] == incoming["links"] == []
        assert outgoing["next_after"] is incoming["next_after"] is None
        assert "PRIVATE" not in str(outgoing) + str(incoming)
        assert graph(actor, hidden).status_code == 404
    assert len(graph(owner, shared).json()["links"]) == 1
    assert len(graph(owner, shared, "incoming").json()["links"]) == 1
    request_publication(owner, followup)
    # A reviewer may inspect a submitted root, but cannot discover private
    # incoming work via another note's graph, even with a forged cursor.
    assert graph(reviewer, followup).status_code == 200
    assert graph(reviewer, shared, "incoming").json()["links"] == []
    assert TestClient(app).get("/api/notebook/graph", params={"note_id": shared["id"], "revision": 1,
        "content_sha256": shared["content_sha256"]}).status_code in (401, 403)


def test_withdrawal_removes_graph_edges_and_does_not_replace_exact_pins(system):
    _, owner, reviewer, reader, _ = system
    first = create(owner)
    response = create(owner, content(title="Response", references=[reference(first)]))
    publish(owner, reviewer, first)
    publish(owner, reviewer, response)
    later = revise(owner, response, content(title="PRIVATE later version"))
    assert graph(reader, first, "incoming").json()["links"][0]["source"]["revision"] == 1
    withdrawn = reviewer.post(f'/api/notebook/notes/{response["id"]}/publication-withdrawals',
        publication_body(response, reason="Restricted moderation reason"))
    assert withdrawn.status_code == 200
    assert graph(reader, first, "incoming").json()["links"] == []
    assert graph(reader, response).status_code == 404
    assert graph(reader, later).status_code == 404
    assert len(graph(owner, first, "incoming").json()["links"]) == 1
    # Withdraw the target too: no outgoing placeholder or private reason leaks.
    assert reviewer.post(f'/api/notebook/notes/{first["id"]}/publication-withdrawals',
        publication_body(first, reason="Another restricted reason")).status_code == 200
    assert graph(owner, response).json()["links"]  # Restricted owner history.


def test_legacy_external_and_wrong_hash_citations_cannot_fabricate_graph_edges(system):
    _, owner, *_ = system
    target = create(owner)
    correct = reference(target, relation="an older custom relation")
    values = ["Legacy prose", {"url": "javascript:alert(1)"},
        reference(target, url="https://unrelated.example/" + reference(target)["url"]),
        reference(target, url=reference(target)["url"] + "&note=other"),
        reference(target, url=reference(target)["url"].replace(target["content_sha256"], "a" * 64)),
        reference(target, url=f'/#notebook?note={target["id"]}&revision=1'), correct]
    source = create(owner, content(references=values))
    links = graph(owner, source).json()["links"]
    assert len(links) == 1 and links[0]["relation"] == "cites"
    assert links[0]["target"]["title"] == target["revisions"][0]["content"]["title"]
    assert source["revisions"][0]["content"]["references"] == values


def test_curated_revisions_are_exact_graph_targets(system):
    _, owner, *_ = system
    bundled = notebook.curated_collection()[0]
    selected = {"id": bundled["id"], **bundled["revisions"][0]}
    source = create(owner, content(references=[reference(selected, "reproduces")]))
    links = graph(owner, source).json()["links"]
    assert links[0]["target"]["note_id"] == selected["id"]
    assert links[0]["target"]["content_sha256"] == selected["content_sha256"]
    assert any(link["source"]["note_id"] == source["id"] for link in graph(owner, selected, "incoming").json()["links"])


def test_import_forward_references_keep_original_bytes_and_resolve_saved_ids(system):
    app, owner, *_ = system
    target_content = content(title="Imported target")
    target = {"id": "local-target", "revision": 1, "content_sha256": notebook._hash(target_content)}
    source_content = content(title="Imported follow-up", references=[reference(target)])
    workspace = {"format": notebook.FORMAT, "views": [], "notes": [
        {"id": "local-source", "revisions": [{**source_content, "revision": 1, "createdAt": 1000}]},
        {"id": "local-target", "revisions": [{**target_content, "revision": 1, "createdAt": 1000}]}]}
    original = deepcopy(workspace)
    result = import_workspace(owner, workspace)
    assert result.status_code == 200, result.text
    mapping = result.json()["id_map"]
    source = owner.client.get("/api/notebook/notes/" + mapping["local-source"]).json()["note"]
    assert graph(owner, source).json()["links"][0]["target"]["note_id"] == mapping["local-target"]
    assert source["revisions"][0]["content_sha256"] == notebook._hash(source_content)
    assert source["revisions"][0]["content"] == source_content
    exported = owner.client.get("/api/notebook/export").json()
    assert next(item for item in exported["notes"] if item["id"] == source["id"])["revisions"] == original["notes"][0]["revisions"]
    # Editing an imported note still resolves its inherited local references.
    changed = revise(owner, source, {**source_content, "title": "Edited follow-up"})
    assert graph(owner, changed).json()["links"][0]["target"]["note_id"] == mapping["local-target"]
    with app.state.db.transaction() as session:
        assert len(session.scalars(select(notebook_graph.NotebookReference)).all()) == 2


def test_graph_pages_visible_backlinks_without_loading_unrelated_note_bodies(system):
    app, owner, reviewer, reader, _ = system
    target = create(owner)
    publish(owner, reviewer, target)
    sources = []
    for number in range(8):
        value = create(owner, content(title=f"Source {number}", references=[reference(target)]))
        if number % 2 == 0:
            publish(owner, reviewer, value)
            sources.append(value["id"])
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        statements.append(statement)
    event.listen(app.state.db.engine, "before_cursor_execute", record)
    try:
        seen, after = [], None
        while True:
            response = graph(reader, target, "incoming", limit=1, **({"after": after} if after else {}))
            assert response.status_code == 200, response.text
            page = response.json()
            seen.extend(item["source"]["note_id"] for item in page["links"])
            after = page["next_after"]
            if not after:
                break
        assert seen == sorted(sources)
        backlink_queries = [sql for sql in statements if "JOIN notebook_references" in sql]
        assert backlink_queries and all("JSON_EXTRACT" in sql and "LIMIT" in sql for sql in backlink_queries)
        assert all(" notebook_revisions.content," not in sql for sql in backlink_queries)
    finally:
        event.remove(app.state.db.engine, "before_cursor_execute", record)
    assert graph(reader, target, after="not a cursor").status_code == 422


def test_migration_backfills_references_without_touching_original_records(system, monkeypatch, tmp_path):
    app, owner, *_ = system
    first = create(owner)
    second = create(owner, content(references=[reference(first, "challenges")]))
    root = Path(__file__).resolve().parents[1]
    for name in tuple(os.environ):
        if name == "DATABASE_URL" or name.startswith("CRUCIBLE_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABASE_URL", app.state.settings.database_url)
    monkeypatch.setenv("CRUCIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CRUCIBLE_SECRET", base64.b64encode(b"g" * 32).decode())
    monkeypatch.setenv("CRUCIBLE_ENV", "development")
    monkeypatch.setenv("CRUCIBLE_ACCESS_MODE", "open")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    with app.state.db.engine.begin() as connection:
        tables = [name for name in inspect(connection).get_table_names() if name != "notebook_references"]
        before = {name: connection.execute(text('SELECT * FROM "' + name + '"')).all() for name in tables}
        notebook_graph.NotebookReference.__table__.drop(connection)
    command.stamp(config, "0017")
    command.upgrade(config, "0018")
    with app.state.db.engine.connect() as connection:
        after = {name: connection.execute(text('SELECT * FROM "' + name + '"')).all() for name in tables}
    assert after == before
    assert graph(owner, second).json()["links"][0]["target"]["note_id"] == first["id"]
    command.upgrade(config, "0018")
    assert len(graph(owner, first, "incoming").json()["links"]) == 1
