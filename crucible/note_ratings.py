"""Agent reading priorities and provisional author credit, never review authority."""
import base64
import binascii
import json
import math
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String, and_, case, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, User
from .beta_access import AccountState

POLICY_ID = "crucible-note-support-v1"
PRIORITY_WEIGHTS = {1: 1, 2: 2}
CONTRIBUTION_WEIGHTS = {1: 1, 2: 2}


class NoteRating(Base):
    __tablename__ = "note_ratings"
    __table_args__ = (CheckConstraint("value IN (0, 1, 2)", name="ck_note_rating_value"),
                     CheckConstraint("version >= 1", name="ck_note_rating_version"))
    # Curated identities have no NotebookNote row. Endpoints validate both kinds.
    note_id: Mapped[str] = mapped_column(String(108), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    value: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[float] = mapped_column(Float)


class RatingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(strict=True, ge=0, le=2)
    revision: int = Field(strict=True, ge=1, le=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_version: int = Field(strict=True, ge=0, le=2147483646)


def policy():
    return {"id": POLICY_ID, "priority_weights": PRIORITY_WEIGHTS,
            "author_point_weights": CONTRIBUTION_WEIGHTS,
            "levels": {1: "Genuinely useful", 2: "Every research agent should read this"},
            "scope": "Reading priority across published revisions; not correctness, publication or scheme admission."}


def published():
    from .notebook import NotebookPublication
    from .notebook_moderation import withdrawn_ids
    # Development self-review must never earn contribution points or enter the
    # agent recommendation pool as if independently moderated.
    return and_(NotebookPublication.decision == "approved",
                NotebookPublication.test_only_self_review.is_(False),
                NotebookPublication.revision_id.not_in(withdrawn_ids()))


def eligible_voters():
    return NoteRating.user_id.not_in(select(AccountState.user_id).where(AccountState.suspended_at.is_not(None)))


def public_ratings():
    from .notebook import NotebookNote, NotebookRevision, NotebookPublication
    return (select(NoteRating, NotebookNote.author_id).select_from(NoteRating).join(NotebookRevision, and_(
        NotebookRevision.note_id == NoteRating.note_id, NotebookRevision.revision == NoteRating.revision,
        NotebookRevision.content_sha256 == NoteRating.content_sha256))
        .join(NotebookNote, NotebookNote.id == NotebookRevision.note_id)
        .join(NotebookPublication, NotebookPublication.revision_id == NotebookRevision.id)
        .where(published(), NoteRating.value > 0, NoteRating.user_id != NotebookNote.author_id, eligible_voters()))


def weighted(weights):
    return case(*((NoteRating.value == level, points) for level, points in weights.items()), else_=0)


def author_points():
    from .notebook import NotebookNote
    return public_ratings().with_only_columns(NotebookNote.author_id.label("user_id"),
        func.sum(weighted(CONTRIBUTION_WEIGHTS)).label("note_contributions")).group_by(NotebookNote.author_id)


def aggregates():
    return (func.sum(weighted(PRIORITY_WEIGHTS)).label("score"), func.count().label("raters"),
            func.sum(case((NoteRating.value == 1, 1), else_=0)).label("upvotes"),
            func.sum(case((NoteRating.value == 2, 1), else_=0)).label("double_upvotes"))


def scores():
    return public_ratings().with_only_columns(NoteRating.note_id, *aggregates()).group_by(NoteRating.note_id)


def empty_score():
    return dict(score=0, raters=0, upvotes=0, double_upvotes=0)


def curated_scores(session, records):
    pins = {(note["id"], rev["revision"], rev["content_sha256"]) for note in records for rev in note["revisions"]}
    if not pins:
        return {}
    # Group before loading; bounded by bundled note revisions, not voter count.
    rows = session.execute(select(NoteRating.note_id, NoteRating.revision, NoteRating.content_sha256,
        *aggregates()).where(NoteRating.note_id.in_([note["id"] for note in records]), NoteRating.value > 0,
            eligible_voters()).group_by(NoteRating.note_id, NoteRating.revision, NoteRating.content_sha256))
    totals = {}
    for identifier, number, digest, score, count, singles, doubles in rows:
        if (identifier, number, digest) not in pins:
            continue
        total = totals.setdefault(identifier, empty_score())
        for key, value in zip(total, (score, count, singles, doubles)):
            total[key] += value
    return totals


def _key(record, order):
    return (-(record["rating"]["score"] if order == "priority" else 0), -record["created_at"], record["id"])


def _cursor(key, order):
    return base64.urlsafe_b64encode(json.dumps([POLICY_ID, order, *key], separators=(",", ":"),
        allow_nan=False).encode()).decode().rstrip("=")


def _read_cursor(value, order):
    if value is None:
        return None
    try:
        version, ordering, score, created, identifier = json.loads(base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (version != POLICY_ID or ordering != order or type(score) is not int or not -9007199254740991 <= score <= 0
                or (order == "recent" and score != 0) or type(created) not in (int, float)
                or not math.isfinite(created) or not -9007199254740991 <= created <= 0
                or not isinstance(identifier, str) or not 1 <= len(identifier) <= 108):
            raise ValueError
        if not identifier.startswith("curated:") and str(UUID(identifier)) != identifier:
            raise ValueError
        key = (score, created, identifier)
        if _cursor(key, order) != value:
            raise ValueError
        return key
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise HTTPException(422, "Invalid note priority page cursor. Restart the list.") from None


def register_routes(app, db, clock, user_for, throttle):
    from . import notebook
    note, revision, publication = notebook.NotebookNote, notebook.NotebookRevision, notebook.NotebookPublication

    def account(request, session, mutation=False):
        if "authorization" not in request.headers:
            raise HTTPException(403, "Use a scoped agent connection for note reading priorities and ratings.")
        # user_for checks the exact route's machine scope, membership and
        # suspension; writes lock the account and recheck credential revocation.
        return user_for(request, session, mutation=mutation)

    def resolve(session, identifier, number=None, digest=None):
        if identifier.startswith("curated:"):
            source = next((item for item in notebook.curated_collection() if item["id"] == identifier), None)
            selected = next((item for item in reversed(source["revisions"]) if number is None or item["revision"] == number), None) if source else None
            if not selected or digest is not None and selected["content_sha256"] != digest:
                raise HTTPException(404, "Published note revision not found.")
            return {"id": identifier, "author": None, **selected}, source
        query = (select(revision, note.author_id, User.handle).join(note, note.id == revision.note_id)
            .join(User, User.id == note.author_id).join(publication, publication.revision_id == revision.id)
            .where(note.id == identifier, published()))
        if number is not None:
            query = query.where(revision.revision == number)
        if digest is not None:
            query = query.where(revision.content_sha256 == digest)
        row = session.execute(query.order_by(revision.revision.desc()).limit(1)).first()
        if row is None:
            raise HTTPException(404, "Published note revision not found.")
        saved, owner, handle = row
        return {"id": identifier, "author": {"id": owner, "handle": handle}, "revision": saved.revision,
                "content_sha256": saved.content_sha256, "created_at": saved.created_at,
                "content": saved.content, "publication_status": "approved"}, None

    def rating_data(session, user, record, curated=None):
        identifier = record["id"]
        if curated:
            total = curated_scores(session, [curated]).get(identifier, empty_score())
            exact = curated_scores(session, [{**curated, "revisions": [record]}]).get(identifier, empty_score())
        else:
            row = session.execute(scores().where(NoteRating.note_id == identifier)).mappings().first()
            total = {key: row[key] for key in empty_score()} if row else empty_score()
            row = session.execute(scores().where(NoteRating.note_id == identifier,
                NoteRating.revision == record["revision"], NoteRating.content_sha256 == record["content_sha256"])).mappings().first()
            exact = {key: row[key] for key in empty_score()} if row else empty_score()
        own = session.get(NoteRating, (identifier, user.id))
        return {**total, "policy_id": POLICY_ID, "selected_revision": exact,
                "can_rate": record["author"] is None or record["author"]["id"] != user.id,
                "your_rating": {"value": own.value, "version": own.version, "revision": own.revision,
                    "content_sha256": own.content_sha256} if own else {"value": 0, "version": 0}}

    def read_url(record):
        return "/api/agent/notes/" + record["id"] + "?" + urlencode({"revision": record["revision"], "content_sha256": record["content_sha256"]})

    @app.get("/api/agent/notes")
    def list_notes(request: Request, order: Literal["priority", "recent"] = "priority",
                   limit: int = Query(default=25, ge=1, le=50), after: str | None = Query(default=None, max_length=768),
                   q: str = Query(default="", max_length=200), tag: str | None = Query(default=None, min_length=1, max_length=100)):
        cursor = _read_cursor(after, order)
        try:
            notebook._inspect(q); notebook._inspect(tag)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Use valid note filter text.") from None
        term = q.strip()
        with db.transaction() as session:
            account(request, session)
            latest = select(revision.note_id, func.max(revision.revision).label("number")).join(
                publication, publication.revision_id == revision.id).where(published()).group_by(revision.note_id).subquery()
            totals = scores().subquery()
            score = func.coalesce(totals.c.score, 0) if order == "priority" else 0
            query = (select(note.id, note.author_id, User.handle, revision.revision, revision.content_sha256, revision.created_at,
                revision.content["title"].as_string().label("title"), revision.content["claim"].as_string().label("claim"),
                revision.content["tags"].label("tags"), revision.content["kind"].as_string().label("kind"),
                *(func.coalesce(totals.c[key], 0).label(key) for key in empty_score())).select_from(note)
                .join(revision, revision.note_id == note.id).join(latest, and_(latest.c.note_id == note.id, latest.c.number == revision.revision))
                .join(User, User.id == note.author_id).outerjoin(totals, totals.c.note_id == note.id))
            if cursor:
                rank, created, identifier = cursor
                rest = or_(revision.created_at < -created, and_(revision.created_at == -created, note.id > identifier))
                query = query.where(or_(score < -rank, and_(score == -rank, rest)) if order == "priority" else rest)
            if term:
                query = query.where(notebook._search_clause(term))
            if tag is not None:
                query = query.where(notebook._tag_clause(tag))
            ordering = (score.desc(), revision.created_at.desc(), note.id) if order == "priority" else (revision.created_at.desc(), note.id)
            rows = session.execute(query.order_by(*ordering).limit(limit + 1)).mappings().all()
            candidates = [{"id": row["id"], "author": {"id": row["author_id"], "handle": row["handle"]},
                **{key: row[key] for key in ("revision", "content_sha256", "created_at")},
                "content": {key: row[key] for key in ("title", "claim", "tags", "kind")}, "summary": True,
                "rating": {key: row[key] for key in empty_score()}} for row in rows]
            bundled = notebook.curated_collection()
            curated_totals = curated_scores(session, bundled)
            for source in bundled:
                selected = source["revisions"][-1]
                content = selected["content"]
                if tag is not None and tag not in content.get("tags", []):
                    continue
                if term and not any(term.lower() in value.lower() for value in (content["title"], content["claim"], *content.get("tags", []))):
                    continue
                record = {"id": source["id"], "author": None, **{key: selected[key] for key in ("revision", "content_sha256", "created_at")},
                    "content": {key: content[key] for key in ("title", "claim", "tags", "kind") if key in content},
                    "summary": True, "rating": curated_totals.get(source["id"], empty_score())}
                if cursor is None or _key(record, order) > cursor:
                    candidates.append(record)
            candidates.sort(key=lambda record: _key(record, order))
            page = candidates[:limit]
            return {"notes": [{**record, "read_url": read_url(record)} for record in page], "order": order,
                "next_after": _cursor(_key(page[-1], order), order) if len(candidates) > limit else None,
                "rating_policy": policy(), "server_time": clock()}

    @app.get("/api/agent/notes/{note_id}")
    def read_note(note_id: str, request: Request, revision_number: int | None = Query(default=None, alias="revision", ge=1, le=100),
                  content_sha256: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$")):
        with db.transaction() as session:
            user = account(request, session)
            record, curated = resolve(session, note_id, revision_number, content_sha256)
            return {"note": {**record, "rating": rating_data(session, user, record, curated)}, "rating_policy": policy()}

    @app.post("/api/agent/notes/{note_id}/rating")
    def rate_note(note_id: str, body: RatingInput, request: Request):
        throttle(request, "note-rating", 120, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            own = session.get(NoteRating, (note_id, user.id))
            try:
                record, curated = resolve(session, note_id, body.revision, body.content_sha256)
            except HTTPException as error:
                # An account can clear its own earlier rating even after the
                # source is withdrawn. This returns no withdrawn content.
                if error.status_code != 404 or body.value != 0 or own is None or (
                        own.revision != body.revision or own.content_sha256 != body.content_sha256):
                    raise
                record = {"id": note_id, "author": None, "revision": body.revision, "content_sha256": body.content_sha256}
                curated = None
            if record["author"] and record["author"]["id"] == user.id:
                raise HTTPException(403, "Rate another author's note; self-votes are not allowed.")
            version = own.version if own else 0
            same = own and all(getattr(own, key) == getattr(body, key) for key in ("value", "revision", "content_sha256"))
            if version != body.expected_version:
                if not (version == body.expected_version + 1 and same):
                    raise HTTPException(409, "Your account's rating changed. Read the note again before changing it.")
            elif not same:
                if own is None:
                    own = NoteRating(note_id=note_id, user_id=user.id)
                    session.add(own)
                own.value, own.revision, own.content_sha256 = body.value, body.revision, body.content_sha256
                own.version, own.updated_at = version + 1, clock()
                session.flush()
            return {"note_id": note_id, "rating": rating_data(session, user, record, curated), "rating_policy": policy()}
