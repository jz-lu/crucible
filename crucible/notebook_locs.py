"""Permanent passage locs, allocated on citation, never on an ordinary read.

A loc names one immutable note/revision/hash/range. It is not a mathematical
classification or cross-revision identity, and it confers no reading rights.
Reservations survive deletion; they hold no authored text or author metadata.
"""
import re
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Identity, Integer, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .note_sections import PASSAGE_PATTERN, resolve_passage

LOC_PATTERN = r"^P\.[1-9][0-9]{0,9}$"
PIN_FIELDS = ("note_id", "revision", "content_sha256", "passage")


class NotebookLoc(Base):
    __tablename__ = "notebook_locs"
    __table_args__ = (
        UniqueConstraint(*PIN_FIELDS, name="uq_notebook_loc_source"),
        {"sqlite_autoincrement": True},
    )
    number: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(108), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    passage: Mapped[str] = mapped_column(String(90), nullable=False)


class PassagePin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str = Field(min_length=1, max_length=108)
    revision: int = Field(strict=True, ge=1, le=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passage: str = Field(pattern=PASSAGE_PATTERN)


def number(value):
    if not isinstance(value, str) or not re.fullmatch(LOC_PATTERN, value):
        return None
    result = int(value[2:])
    return result if result <= 2_147_483_647 else None


def reference(row):
    return {key: getattr(row, key) for key in PIN_FIELDS}


def lookup(connection, loc):
    """Internal index lookup only. Callers must independently authorize sources."""
    from . import notebook_section_refs
    if notebook_section_refs.parse(loc):
        return notebook_section_refs.lookup(connection, loc)
    key = number(loc)
    if key is None:
        return None
    return connection.execute(select(*(getattr(NotebookLoc, field) for field in PIN_FIELDS))
        .where(NotebookLoc.number == key)).mappings().first()


def checked_link(fields, connection):
    """Loc URLs carry their exact source, so imports cannot retarget collisions.

Return normalized query fields only if this installation knows that same pin.
Bare locs remain useful for explicit API lookups, never portable citations.
"""
    if 'loc' not in fields or len(fields) != 5 or any(len(value) != 1 for value in fields.values()):
        return None
    pin = lookup(connection, fields["loc"][0])
    if pin is None:
        return None
    expected = {key: [str(value)] for key, value in route(pin).items()}
    return expected if set(fields) == {'loc', *expected} and all(fields[key] == value for key, value in expected.items()) else None


def route(pin):
    anchor = next(({wire: pin[key]} for key, wire in [('passage', 'passage'), ('statement_id', 'statement'),
                  ('section_id', 'note_section')] if key in pin), {})
    return {'note': pin['note_id'], 'revision': pin['revision'], 'sha256': pin['content_sha256'], **anchor}


def metadata(session, note_id, revision, digest):
    """Caller has already authorized this exact revision. Read only."""
    cache = session.info.setdefault("passage_loc_metadata", {})
    key = (note_id, revision, digest)
    if key not in cache:
        cache[key] = {row.passage: "P." + str(row.number) for row in session.scalars(
        select(NotebookLoc).where(NotebookLoc.note_id == note_id,
            NotebookLoc.revision == revision, NotebookLoc.content_sha256 == digest))}
    return cache[key]


def collection_metadata(session, notes):
    allowed = {(note["id"], rev["revision"], rev["content_sha256"]) for note in notes for rev in note["revisions"]}
    result = {}
    for row in session.scalars(select(NotebookLoc).where(NotebookLoc.note_id.in_({pin[0] for pin in allowed}))):
        if (row.note_id, row.revision, row.content_sha256) in allowed:
            result.setdefault(row.note_id, {}).setdefault(str(row.revision), {})[row.passage] = "P." + str(row.number)
    return result


def ensure(session, ref):
    values = ref.model_dump()
    table = NotebookLoc.__table__
    query = select(NotebookLoc).where(*(getattr(NotebookLoc, key) == value for key, value in values.items()))
    row = session.scalar(query)
    if row is None:
        connection = session.connection()
        insert = {"sqlite": sqlite_insert, "postgresql": pg_insert}[connection.dialect.name]
        connection.execute(insert(table).values(values).on_conflict_do_nothing(index_elements=list(PIN_FIELDS)))
        row = session.scalar(query)
    session.info.pop("passage_loc_metadata", None)
    return row


def record(row, content):
    pin = reference(row)
    loc = "P." + str(row.number)
    route = {"note": row.note_id, "revision": row.revision, "sha256": row.content_sha256, "passage": row.passage}
    return {"loc": loc, "reference": pin,
            "url": "/#notebook?" + urlencode({"loc": loc, **route}),
            "exact_url": "/#notebook?" + urlencode(route),
            "passage": {**resolve_passage(content, row.passage), "label": loc, "loc": loc}}


def register_routes(app, db, settings, clock, user_for, visible, throttle):
    from . import machine_auth, notebook, notebook_moderation, notebook_trash

    def source(request, session, user, ref):
        scopes = None
        if "authorization" in request.headers:
            scopes = machine_auth.request_identity(request, session, settings, clock,
                mutation=request.method == "POST").scopes
        if ref.note_id.startswith("curated:"):
            if settings.local_note_corpus_only or scopes is not None and "notes:read" not in scopes:
                raise HTTPException(404, "Passage not found.")
            note = next((item for item in notebook.curated_collection(session) if item["id"] == ref.note_id), None)
            rev = next((item for item in note["revisions"] if item["revision"] == ref.revision), None) if note else None
            content = rev["content"] if rev else None
        else:
            note = session.get(notebook.NotebookNote, ref.note_id)
            rev = session.scalar(visible(session, user, ref.note_id, review=scopes is None).where(
                notebook.NotebookRevision.revision == ref.revision))
            if (note is None or rev is None or notebook_trash.active(session, ref.note_id)
                    or session.get(notebook_moderation.PublicationWithdrawal, rev.id) is not None):
                raise HTTPException(404, "Passage not found.")
            if scopes is not None:
                own = note.author_id == user.id and bool({"notebook:read", "notebook:write"} & set(scopes))
                published = "notes:read" in scopes and session.scalar(select(notebook.NotebookPublication.id).where(
                    notebook.NotebookPublication.revision_id == rev.id, notebook.publication_screening.approved_clause()))
                if not own and not published:
                    raise HTTPException(404, "Passage not found.")
            content = rev.content
        if content is None:
            raise HTTPException(404, "Passage not found.")
        if notebook._hash(content) != ref.content_sha256:
            raise HTTPException(409, "This loc does not match the saved source revision.")
        return content

    @app.post("/api/notebook/locs")
    def create_loc(body: PassagePin, request: Request):
        throttle(request, "notebook-loc", 120, 3600)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            content = source(request, session, user, body)
            try:
                resolve_passage(content, body.passage)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            return record(ensure(session, body), content)

    @app.get("/api/notebook/locs/{loc}")
    def get_loc(loc: str, request: Request):
        with db.transaction() as session:
            user = user_for(request, session)
            from . import notebook_section_refs
            if notebook_section_refs.parse(loc):
                pin = lookup(session, loc)
                if pin is None:
                    raise HTTPException(404, "Section not found.")
                content = source(request, session, user, SimpleNamespace(**pin))
                if request.query_params:
                    fields = {key: request.query_params.getlist(key) for key in request.query_params}
                    if 'loc' in fields or checked_link({'loc': [loc], **fields}, session) is None:
                        raise HTTPException(404, "This reference does not name the cited source on this installation.")
                parsed = notebook_section_refs.parse(loc)
                row = next(item for item in notebook_section_refs.outline(content) if item['number'] == parsed[1])
                target = route(pin)
                return {'loc': loc, 'reference': pin, 'label': loc.split('@')[0], 'title': row['title'], 'kind': row['kind'],
                        'url': '/#notebook?' + urlencode({'loc': loc, **target}), 'exact_url': '/#notebook?' + urlencode(target)}
            row = session.get(NotebookLoc, number(loc)) if number(loc) is not None else None
            if row is None:
                raise HTTPException(404, "Passage not found.")
            content = source(request, session, user, PassagePin(**reference(row)))
            if request.query_params:
                fields = {key: request.query_params.getlist(key) for key in request.query_params}
                if "loc" in fields or checked_link({"loc": [loc], **fields}, session) is None:
                    raise HTTPException(404, "This loc does not name the cited source on this installation.")
            return record(row, content)
