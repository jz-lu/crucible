"""Indexed, exact-revision references. Edges never confer reading permission."""
import base64
import json
import re
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException, Query, Request
from sqlalchemy import ForeignKey, Index, Integer, String, Text, select, tuple_, update
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

RELATIONS = {"cites", "builds_on", "supports", "challenges", "reproduces", "responds_to"}


class NotebookReference(Base):
    __tablename__ = "notebook_references"
    __table_args__ = (Index("ix_notebook_reference_target", "target_note_id", "target_revision", "target_sha256"),)
    source_revision_id: Mapped[str] = mapped_column(ForeignKey("notebook_revisions.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_note_id: Mapped[str] = mapped_column(String(100))
    target_revision: Mapped[int] = mapped_column(Integer)
    target_sha256: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(Text)


def references(content):
    """Legacy/external citations stay intact; only unambiguous local pins are edges.

    Absolute URLs are intentionally not inferred to belong to this installation.
    The editor canonicalizes same-origin links to /#notebook?... before saving.
    """
    values = content.get("references")
    if not isinstance(values, list):
        values = [] if values is None else [values]
    for position, value in enumerate(values):
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            continue
        try:
            url = urlsplit(value["url"])
            if url.scheme or url.netloc or url.path not in ("", "/") or url.query or not url.fragment.startswith("notebook?"):
                continue
            fields = parse_qs(url.fragment[len("notebook?"):], strict_parsing=True, keep_blank_values=True, max_num_fields=4)
            if (set(fields) not in ({"note", "revision", "sha256"}, {"note", "revision", "sha256", "location"})
                    or any(len(items) != 1 for items in fields.values())):
                continue
            location = fields.get("location", [None])[0]
            if location is not None and (not re.fullmatch(r"L[1-9][0-9]{0,6}", location) or int(location[1:]) > 1_000_000):
                continue
            identifier, number, digest = (fields[key][0] for key in ("note", "revision", "sha256"))
            if not 1 <= len(identifier) <= 100 or identifier != identifier.strip() or any(ord(c) < 32 for c in identifier):
                continue
            if not re.fullmatch(r"[1-9][0-9]?|100", number) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
        except ValueError:
            continue
        relation = value.get("relation")
        # A passage is an authored locator within this immutable revision.
        # The existing graph stays at note-revision granularity; the original
        # URL is retained in source content. Neither grants target access nor
        # establishes that the passage exists or supports the claimed relation.
        yield {"position": position, "target_note_id": identifier, "target_revision": int(number),
               "target_sha256": digest, "relation": relation if isinstance(relation, str) and relation in RELATIONS else "cites",
               "scope": value.get("scope", "")[:10000] if isinstance(value.get("scope", ""), str) else ""}


def index_revision(connection, revision):
    """Called on every ORM insertion, including proposal-created revisions."""
    from .notebook import NotebookNote

    rows = [{"source_revision_id": revision.id, **item} for item in references(revision.content)]
    if rows:
        author = select(NotebookNote.author_id).where(NotebookNote.id == revision.note_id).scalar_subquery()
        mapping = dict(connection.execute(select(NotebookNote.source_id, NotebookNote.id).where(
            NotebookNote.author_id == author, NotebookNote.source_id.in_([row["target_note_id"] for row in rows]))).all())
        for row in rows:
            row["target_note_id"] = mapping.get(row["target_note_id"], row["target_note_id"])
        connection.execute(NotebookReference.__table__.insert(), rows)


def remap_import(connection, revision_ids, mapping):
    # Resolve this import's local IDs in the derived index only. The original
    # authored references, bytes, hashes and export receipts remain untouched.
    if not revision_ids:
        return
    for original, saved in mapping.items():
        if original != saved:
            connection.execute(update(NotebookReference).where(
                NotebookReference.source_revision_id.in_(revision_ids),
                NotebookReference.target_note_id == original).values(target_note_id=saved))


def _cursor(key):
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode().rstrip("=")


def _read_cursor(value):
    if value is None:
        return None
    try:
        key = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(key, list) or len(key) != 3 or not isinstance(key[0], str) or not 1 <= len(key[0]) <= 100
                or type(key[1]) is not int or not 1 <= key[1] <= 100 or type(key[2]) is not int or not 0 <= key[2] < 1000):
            raise ValueError
        return tuple(key)
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(422, "Invalid note graph cursor.") from None


def register_routes(app, db, settings, account, visible):
    from . import notebook

    revision, link = notebook.NotebookRevision, NotebookReference

    def node(identifier, number, digest, title):
        return {"note_id": identifier, "revision": number, "content_sha256": digest, "title": title}

    @app.get("/api/notebook/graph")
    def graph(request: Request, note_id: str = Query(min_length=1, max_length=100),
              revision_number: int = Query(alias="revision", ge=1, le=100),
              content_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
              direction: str = Query(default="outgoing", pattern=r"^(incoming|outgoing)$"),
              limit: int = Query(default=25, ge=1, le=50), after: str | None = Query(default=None, max_length=512)):
        cursor = _read_cursor(after)
        pin = (note_id, revision_number, content_sha256)
        with db.transaction() as session:
            user = account(request, session)
            readable = visible(session, user).with_only_columns(revision.id)
            curated = notebook.curated_collection()
            bundled = {(item["id"], rev["revision"], rev["content_sha256"]): rev
                       for item in curated for rev in item["revisions"]}
            root_revision = None
            if pin in bundled:
                root = node(*pin, bundled[pin]["content"]["title"])
            else:
                # Reviewers can open a submitted root, as in the note reader;
                # discovery of surrounding notes uses ordinary visibility.
                root_revision = session.scalar(visible(session, user, note_id, review=True).where(
                    revision.revision == revision_number, revision.content_sha256 == content_sha256))
                if root_revision is None:
                    raise HTTPException(404, "Note revision not found.")
                root = node(*pin, root_revision.content["title"])

            candidates = []
            if direction == "incoming":
                query = select(revision.note_id, revision.revision, revision.content_sha256,
                    revision.content["title"].as_string(), link.position, link.relation, link.scope).join(
                        link, link.source_revision_id == revision.id).where(
                            revision.id.in_(readable), link.target_note_id == note_id,
                            link.target_revision == revision_number, link.target_sha256 == content_sha256)
                if cursor:
                    query = query.where(tuple_(revision.note_id, revision.revision, link.position) > cursor)
                for identifier, number, digest, title, position, relation, scope in session.execute(
                        query.order_by(revision.note_id, revision.revision, link.position).limit(limit + 1)):
                    candidates.append(((identifier, number, position),
                        {"source": node(identifier, number, digest, title), "target": root, "relation": relation, "scope": scope}))
                for source_pin, source in bundled.items():
                    for item in references(source["content"]):
                        key = (source_pin[0], source_pin[1], item["position"])
                        if (item["target_note_id"], item["target_revision"], item["target_sha256"]) == pin and (cursor is None or key > cursor):
                            candidates.append((key, {"source": node(*source_pin, source["content"]["title"]),
                                "target": root, "relation": item["relation"], "scope": item["scope"]}))
            else:
                # Outgoing pins are bounded by the authored references limit.
                # Resolve targets in one query, never one query per citation.
                items = list(references(bundled[pin]["content"])) if root_revision is None else [
                    {key: getattr(row, key) for key in ("position", "target_note_id", "target_revision", "target_sha256", "relation", "scope")}
                    for row in session.scalars(select(link).where(link.source_revision_id == root_revision.id)
                        .order_by(link.position))]
                items = [item for item in items if cursor is None or (note_id, revision_number, item["position"]) > cursor]
                pins = {(item["target_note_id"], item["target_revision"], item["target_sha256"]) for item in items}
                targets = {target: node(*target, value["content"]["title"]) for target, value in bundled.items() if target in pins}
                account_pins = pins - targets.keys()
                # Stay below SQLite's parameter limit for large legacy arrays.
                values = list(account_pins)
                for start in range(0, len(values), 200):
                    rows = session.execute(select(revision.note_id, revision.revision, revision.content_sha256,
                        revision.content["title"].as_string()).where(revision.id.in_(readable),
                            tuple_(revision.note_id, revision.revision, revision.content_sha256).in_(values[start:start + 200])))
                    for identifier, number, digest, title in rows:
                        targets[(identifier, number, digest)] = node(identifier, number, digest, title)
                for item in items:
                    target = targets.get((item["target_note_id"], item["target_revision"], item["target_sha256"]))
                    if target:
                        candidates.append(((note_id, revision_number, item["position"]),
                            {"source": root, "target": target, "relation": item["relation"], "scope": item["scope"]}))

            # Invalid, unavailable or mismatched pins never create placeholder
            # nodes, counts, titles or backlinks that could disclose private work.
            candidates = [item for item in candidates if item[1]["source"] != item[1]["target"]]
            candidates.sort(key=lambda item: item[0])
            page = candidates[:limit]
            return {"format": "crucible-note-graph-v1", "root": root, "direction": direction,
                    "links": [item[1] for item in page],
                    "next_after": _cursor(page[-1][0]) if len(candidates) > limit else None}
