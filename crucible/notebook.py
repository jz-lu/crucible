"""Account-owned research notes, immutable revisions and scoped publication.

Authored text and imported metadata are evidence supplied by their author. They
cannot grant review authority, publish a revision, or change a live challenge.
"""
import base64
import binascii
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, and_, case, cast, event, func, or_, select, tuple_
from sqlalchemy.orm import Mapped, aliased, load_only, mapped_column

from .db import Base, User
from . import protocol_proposals, proposal_notes, proposal_retractions, notebook_sharing, notebook_moderation, notebook_graph, note_ratings, machine_auth, notebook_trash


FORMAT = "crucible-theory-local-v1"
MAX_WORKSPACE_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_CONTENT_BYTES = 384 * 1024
MAX_NOTES = 200
MAX_REVISIONS = 100
SUMMARY_INDEX_MAX_NOTES = 500
SUMMARY_INDEX_MAX_BYTES = 512 * 1024
CONTENT_FIELDS = {"title", "claim", "conditions", "body", "tags", "kind", "references", "artifacts", "extensions"}
FORBIDDEN_KEYS = {"__proto__", "prototype", "constructor"}
CURATED_NOTES_PATH = Path(__file__).resolve().parents[1] / "web/map/notes.json"


class NotebookNote(Base):
    __tablename__ = "notebook_notes"
    __table_args__ = (
        UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_create_key"),
        UniqueConstraint("author_id", "source_id", name="uq_notebook_import_identity"),
        Index("ix_notebook_author_created", "author_id", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    latest_revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class NotebookRevision(Base):
    __tablename__ = "notebook_revisions"
    __table_args__ = (
        UniqueConstraint("note_id", "revision", name="uq_notebook_revision"),
        UniqueConstraint("note_id", "idempotency_key", name="uq_notebook_revision_key"),
        Index("ix_notebook_revision_created", "created_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    note_id: Mapped[str] = mapped_column(ForeignKey("notebook_notes.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    content: Mapped[dict] = mapped_column(JSON)
    content_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))
    # Imported timestamps/extra labels are retained verbatim as provenance only.
    source_revision: Mapped[dict | None] = mapped_column(JSON, nullable=True)


@event.listens_for(NotebookRevision, "after_insert")
def _index_references(mapper, connection, revision):
    notebook_graph.index_revision(connection, revision)


class NotebookPublication(Base):
    __tablename__ = "notebook_publications"
    __table_args__ = (
        UniqueConstraint("revision_id", name="uq_notebook_publication_revision"),
        UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_publication_key"),
        UniqueConstraint("reviewer_id", "review_key", name="uq_notebook_publication_review_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("notebook_revisions.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(10000), nullable=True)
    review_key: Mapped[str | None] = mapped_column(String(36), nullable=True)
    review_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_only_self_review: Mapped[bool] = mapped_column(Boolean, default=False)


class NotebookImport(Base):
    __tablename__ = "notebook_imports"
    __table_args__ = (UniqueConstraint("author_id", "idempotency_key", name="uq_notebook_import_key"),
                     UniqueConstraint("author_id", "sequence", name="uq_notebook_import_sequence"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    sequence: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))
    workspace: Mapped[dict] = mapped_column(JSON)
    id_map: Mapped[dict] = mapped_column(JSON)
    added_notes: Mapped[int] = mapped_column(Integer)
    added_revisions: Mapped[int] = mapped_column(Integer)


def _inspect(value, depth=0):
    if depth > 16:
        raise ValueError("The document is nested too deeply.")
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        if abs(value) > 9007199254740991 or (type(value) is float and not math.isfinite(value)):
            raise ValueError("Use finite JSON numbers within the exact integer range.")
        return
    if type(value) is str:
        if len(value) > 50000 or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise ValueError("Text is too long or contains unsupported controls.")
        value.encode("utf-8")
        return
    if type(value) is list:
        if len(value) > 1000:
            raise ValueError("Too many array items.")
        for item in value:
            _inspect(item, depth + 1)
        return
    if type(value) is dict and len(value) <= 100:
        for key, item in value.items():
            if type(key) is not str or key in FORBIDDEN_KEYS or len(key) > 100:
                raise ValueError("Unsupported object field.")
            _inspect(key, depth + 1)
            _inspect(item, depth + 1)
        return
    raise ValueError("Use bounded plain JSON objects.")


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _export_bytes(value):
    # The browser downloads pretty-printed JSON. Bound that actual portable
    # artifact too, rather than accepting a compact snapshot it cannot reopen.
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


class NoteContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    title: str = Field(min_length=1, max_length=240)
    claim: str = Field(min_length=1, max_length=20000)
    conditions: str = Field(min_length=1, max_length=20000)
    body: str = Field(min_length=1, max_length=40000)
    tags: list[str] = Field(default_factory=list, max_length=40)
    kind: str = Field(default="", max_length=100)
    references: Any = None
    artifacts: Any = None
    extensions: Any = None

    @field_validator("title", "claim", "conditions", "body")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Use nonempty text.")
        _inspect(value)
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("Use nonempty tags of at most 100 characters.")
        _inspect(values)
        return values

    @field_validator("kind", "references", "artifacts", "extensions")
    @classmethod
    def inert_metadata(cls, value):
        _inspect(value)
        return value


class CreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    content: NoteContent
    idempotency_key: UUID


class RevisionInput(CreateInput):
    expected_revision: int = Field(ge=1, le=MAX_REVISIONS, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublicationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, le=MAX_REVISIONS, strict=True)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: UUID


class ReviewInput(PublicationInput):
    decision: Literal["approved", "changes_requested"]
    reason: str = Field(min_length=1, max_length=10000)

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value):
        if not value.strip():
            raise ValueError("Explain the publication decision.")
        _inspect(value)
        return value


class ImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    workspace: dict[str, Any]
    idempotency_key: UUID


def _content(value):
    content = value.model_dump(exclude_unset=True) if isinstance(value, NoteContent) else NoteContent.model_validate(value).model_dump(exclude_unset=True)
    content.setdefault("tags", [])
    try:
        _inspect(content)
        if len(_bytes(content)) > MAX_CONTENT_BYTES:
            raise ValueError("Note content is too large.")
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        raise HTTPException(422, "Use bounded, valid JSON note content.") from None
    return content


def _workspace(value):
    """Validate legacy notes without treating any authored labels as authority."""
    try:
        _inspect(value)
        if len(_bytes(value)) > MAX_WORKSPACE_BYTES:
            raise ValueError("The workspace is too large.")
        if value.get("format") != FORMAT:
            raise ValueError("Unknown workspace format.")
        notes = value.get("notes")
        if not isinstance(notes, list) or len(notes) > MAX_NOTES:
            raise ValueError("Use at most 200 notes per workspace.")
        views = value.get("views")
        if not isinstance(views, list) or len(views) > 50:
            raise ValueError("Use at most 50 inert legacy views.")
        identifiers = set()
        revision_counts = {}
        for note in notes:
            if not isinstance(note, dict):
                raise ValueError("Invalid note.")
            identifier = note.get("id")
            if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 100 or identifier in identifiers:
                raise ValueError("Each note needs a distinct bounded identifier.")
            identifiers.add(identifier)
            revisions = note.get("revisions")
            if not isinstance(revisions, list) or not 1 <= len(revisions) <= MAX_REVISIONS:
                raise ValueError("Use 1–100 revisions per note.")
            revision_counts[identifier] = len(revisions)
            for number, revision in enumerate(revisions, 1):
                if not isinstance(revision, dict) or type(revision.get("revision")) is not int or revision["revision"] != number:
                    raise ValueError("Revisions must be consecutive from one.")
                timestamp = revision.get("createdAt")
                if type(timestamp) is not int or not 0 <= timestamp <= 9007199254740991:
                    raise ValueError("Imported dates must be integer milliseconds.")
                if not isinstance(revision.get("tags"), list):
                    raise ValueError("Each legacy revision needs a tag array.")
                _content({key: item for key, item in revision.items() if key in CONTENT_FIELDS})
        def bounded(value, maximum, allow_empty=False):
            if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
                raise ValueError("Invalid legacy view text.")
        view_ids = set()
        for view in views:
            if not isinstance(view, dict):
                raise ValueError("Invalid legacy view.")
            bounded(view.get("id"), 100)
            if view["id"] in view_ids:
                raise ValueError("Duplicate legacy view.")
            view_ids.add(view["id"])
            for name, maximum in (("title", 240), ("question", 4000), ("scope", 10000)):
                bounded(view.get(name), maximum)
            if view.get("kind") not in {"argument", "tradeoff"}:
                raise ValueError("Unknown legacy view kind.")
            bindings = view.get("bindings")
            if not isinstance(bindings, list) or len(bindings) > 200:
                raise ValueError("Invalid legacy bindings.")
            binding_ids = set()
            for binding in bindings:
                if not isinstance(binding, dict):
                    raise ValueError("Invalid legacy binding.")
                bounded(binding.get("id"), 100)
                if binding["id"] in binding_ids:
                    raise ValueError("Duplicate binding.")
                binding_ids.add(binding["id"])
                pinned = binding.get("noteRevision")
                if (binding.get("noteId") not in revision_counts or type(pinned) is not int
                        or not 1 <= pinned <= revision_counts[binding["noteId"]]):
                    raise ValueError("A legacy binding must pin an included note revision.")
                bounded(binding.get("excerpt"), 10000)
                bounded(binding.get("scope"), 10000)
                if binding.get("assessment") not in {"unreviewed", "reviewed", "disputed"}:
                    raise ValueError("Unknown legacy illustrative assessment.")
                bounded(binding.get("assessmentReason"), 10000, binding["assessment"] == "unreviewed")
                if view["kind"] == "argument":
                    for name, maximum in (("from", 240), ("to", 240), ("relation", 100)):
                        bounded(binding.get(name), maximum)
                else:
                    bounded(binding.get("label"), 240)
                    if any(type(binding.get(name)) not in (int, float) or not -1024 <= binding[name] <= 1024
                           for name in ("timeLog2", "memoryLog2")) or binding.get("evidence") != "illustrative":
                        raise ValueError("Legacy plotted values must remain bounded illustrative data.")
    except (ValueError, UnicodeError, TypeError, OverflowError, RecursionError):
        raise HTTPException(422, "Use a valid notebook workspace with consecutive, bounded note revisions.") from None
    return value


def _legacy_revision(revision):
    if revision.source_revision is not None:
        return revision.source_revision
    return {**revision.content, "revision": revision.revision, "createdAt": int(revision.created_at * 1000)}


def check_capacity(session, user, added=1):
    count = session.scalar(select(func.count()).select_from(NotebookNote).where(NotebookNote.author_id == user.id))
    if count + added > MAX_NOTES:
        raise HTTPException(409, "This notebook currently supports 200 notes per account. Existing notes remain saved.")

def workspace_for(session, user, note_id=None, *, include_deleted=True):
    query = select(NotebookNote).where(NotebookNote.author_id == user.id)
    if not include_deleted:
        query = query.where(NotebookNote.id.not_in(notebook_trash.deleted_ids()))
    if note_id:
        query = query.where(NotebookNote.id == str(note_id))
    notes = session.scalars(query.order_by(NotebookNote.id)).all()
    if note_id and not notes:
        raise HTTPException(404, "Note not found.")
    note_ids = [note.id for note in notes]
    # Every successful mutation keeps this complete history below 2 MiB.
    # A defensive SQL preflight also protects against externally written or
    # inconsistent databases before materializing any note bodies.
    estimated = session.scalar(select(func.sum(func.length(cast(NotebookRevision.content, Text)) +
        func.coalesce(func.length(cast(NotebookRevision.source_revision, Text)), 0))).where(
            NotebookRevision.note_id.in_(note_ids))) or 0
    if estimated > 4 * MAX_WORKSPACE_BYTES:
        raise HTTPException(409, "This notebook exceeds its workspace capacity. Existing notes remain saved; contact the operator.")
    imported = session.scalar(select(NotebookImport).where(NotebookImport.author_id == user.id)
        .order_by(NotebookImport.sequence.desc()).limit(1)) if not note_id else None
    workspace = {**(imported.workspace if imported else {}), "format": FORMAT, "notes": [], "views": []}
    revisions = session.scalars(select(NotebookRevision).where(NotebookRevision.note_id.in_(note_ids))
        .order_by(NotebookRevision.note_id, NotebookRevision.revision)).all()
    history = {identifier: [] for identifier in note_ids}
    for revision in revisions:
        history[revision.note_id].append(_legacy_revision(revision))
    for note in notes:
        workspace["notes"].append({**(note.source_metadata or {}), "id": note.id, "revisions": history[note.id]})
    if imported:
        workspace["views"] = json.loads(json.dumps(imported.workspace.get("views", [])))
        for view in workspace["views"]:
            if not isinstance(view, dict):
                continue
            for binding in view.get("bindings", []) if isinstance(view.get("bindings", []), list) else []:
                if isinstance(binding, dict) and binding.get("noteId") in imported.id_map:
                    binding["noteId"] = imported.id_map[binding["noteId"]]
            if not include_deleted:
                view["bindings"] = [binding for binding in view.get("bindings", [])
                                    if binding.get("noteId") in note_ids]
    if len(_export_bytes(workspace)) > MAX_WORKSPACE_BYTES:
        raise HTTPException(409, "This change would exceed the notebook's 2 MiB capacity, including revision history. Existing notes are unchanged; your draft has not been saved.")
    return workspace




def proposal_request_clause(settings, user, *, own_only=False):
    """Whether this exact revision has a proposal the reader may discover."""
    from . import research_reviews

    link, revision = proposal_notes.ProposalNoteLink, protocol_proposals.ProtocolProposalRevision
    scheme_query = select(link.note_id, link.note_revision_id).join(revision,
        and_(revision.id == link.proposal_revision_id, revision.proposal_id == link.proposal_id))
    if own_only or not protocol_proposals.is_reviewer(settings, user):
        scheme_query = scheme_query.where(link.author_id == user.id)
    scheme = tuple_(NotebookRevision.note_id, NotebookRevision.id).in_(scheme_query)
    request = research_reviews.ResearchRequest
    research = select(request.sources[0]["note_id"].as_string(), request.sources[0]["revision"].as_integer(),
        request.sources[0]["content_sha256"].as_string()).where(request.purpose.in_(research_reviews.PROPOSAL_PURPOSES))
    if own_only or not protocol_proposals.is_reviewer(settings, user):
        research = research.where(request.author_id == user.id)
    # Uncorrelated membership sets avoid scanning/decoding every request packet
    # again for each historical note revision. Both identities remain exact.
    return or_(scheme, tuple_(NotebookRevision.note_id, NotebookRevision.revision,
        NotebookRevision.content_sha256).in_(research))


def proposal_records(records):
    from .research_reviews import PROPOSAL_PURPOSES
    return [record for record in records if record["kind"] == "scheme_admission" or record["kind"] in PROPOSAL_PURPOSES]


def _file_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=2)
def _curated_collection(path, identity):
    """Cache only validated, immutable bundled content; never account status."""
    source = Path(path)
    if _file_identity(source.stat()) != identity:
        raise ValueError("The curated collection changed while opening it.")
    with source.open("rb") as stream:
        raw = stream.read(MAX_WORKSPACE_BYTES + 1)
    if len(raw) > MAX_WORKSPACE_BYTES or _file_identity(source.stat()) != identity:
        raise ValueError("The curated collection changed or exceeds its limit.")
    workspace = _workspace(json.loads(raw))
    records = []
    for note in workspace["notes"]:
        revisions = []
        for item in note["revisions"]:
            content = {key: value for key, value in item.items() if key in CONTENT_FIELDS}
            revisions.append({"revision": item["revision"], "content_sha256": _hash(content),
                "created_at": item["createdAt"] / 1000, "content": content, "publication_status": "curated"})
        records.append({"id": "curated:" + note["id"], "source_id": note["id"],
            "source_kind": "curated-research", "owner": None, "can_edit": False,
            "can_review": False, "can_self_review": False, "created_at": revisions[0]["created_at"],
            "revision_count": len(revisions), "revisions": revisions})
    return tuple(records)


def curated_collection():
    try:
        path = CURATED_NOTES_PATH.resolve()
        return _curated_collection(str(path), _file_identity(path.stat()))
    except (OSError, ValueError, TypeError, KeyError, HTTPException):
        raise HTTPException(503, "The curated notebook is temporarily unavailable. Retry the complete list.") from None


def curated_request_summaries(session, settings, user, records):
    """One scalar query, then validate each primary pin against bundled history."""
    from .research_reviews import ResearchRequest, ResearchAssessment
    expected = {(note["id"], revision["revision"]): revision["content_sha256"]
                for note in records for revision in note["revisions"]}
    if not expected:
        return {}
    primary = (ResearchRequest.sources[0]["note_id"].as_string(),
        ResearchRequest.sources[0]["revision"].as_integer(),
        ResearchRequest.sources[0]["content_sha256"].as_string())
    withdrawal = proposal_retractions.ProposalRetraction
    query = (select(*primary, ResearchRequest.id, ResearchRequest.purpose,
        ResearchAssessment.outcome, ResearchAssessment.test_only_self_review, withdrawal.created_at)
        .select_from(ResearchRequest).outerjoin(ResearchAssessment,
            ResearchAssessment.request_id == ResearchRequest.id)
        .outerjoin(withdrawal, and_(withdrawal.target_kind == proposal_retractions.RESEARCH,
            withdrawal.target_id == ResearchRequest.id))
        .where(primary[0].in_([note["id"] for note in records])).order_by(ResearchRequest.id))
    if not protocol_proposals.is_reviewer(settings, user):
        query = query.where(ResearchRequest.author_id == user.id)
    result = {}
    for note_id, number, digest, identifier, purpose, outcome, test_only, withdrawn_at in session.execute(query):
        if expected.get((note_id, number)) != digest:
            continue
        item = {"id": identifier, "kind": purpose, "status": outcome or "submitted",
                "url": f"/#notebook?research_request={identifier}", **proposal_retractions.compact(withdrawn_at)}
        if outcome:
            item.update(test_only_self_review=bool(test_only), effective=not test_only or
                        (not settings.production and settings.allow_self_review))
        result.setdefault((note_id, number, digest), []).append(item)
    return result


def latest_proposal_reference(note_id, revisions, requests):
    return next(({"revision": revision["revision"], "content_sha256": revision["content_sha256"], "requests": found}
        for revision in reversed(revisions)
        if (found := proposal_records(requests.get((note_id, revision["revision"], revision["content_sha256"]), [])))), None)


def _tag_clause(value):
    # The portable note schema bounds tags to forty strings. Scalar JSON paths
    # work on SQLite and PostgreSQL without matching JSON syntax or substrings.
    return or_(*(NotebookRevision.content["tags"][index].as_string() == value for index in range(40)))


def _search_clause(value):
    pattern = "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    fields = [NotebookRevision.content[name].as_string() for name in ("title", "claim")]
    fields.extend(NotebookRevision.content["tags"][index].as_string() for index in range(40))
    return or_(*(field.ilike(pattern, escape="\\") for field in fields))


def _browse_cursor(created_at, identifier):
    payload = _bytes([1, created_at, identifier])
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _read_browse_cursor(value):
    """Decode a versioned, canonical boundary for the recent-first ordering."""
    try:
        payload = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        version, created_at, identifier = json.loads(payload)
        if (type(version) is not int or version != 1 or type(created_at) not in (int, float)
                or not math.isfinite(created_at) or not 0 <= created_at <= 9007199254740991
                or type(identifier) is not str or not identifier or len(identifier) > 108):
            raise ValueError
        _inspect(identifier)
        if identifier.startswith("curated:"):
            if not identifier[len("curated:"):].strip():
                raise ValueError
        elif str(UUID(identifier)) != identifier:
            raise ValueError
        if _browse_cursor(created_at, identifier) != value:
            raise ValueError
        return created_at, identifier
    except (ValueError, TypeError, OverflowError, UnicodeError, binascii.Error):
        raise HTTPException(422, "Invalid notebook page cursor. Restart the notebook list.") from None


def request_summaries(session, settings, user, revision_refs, *, own_only=False):
    """Batch trusted request metadata for exact visible note revisions.

    No authored tags/extensions, proposal bodies, or frozen research packets
    are loaded. A research packet labels only its first (primary) source and is
    visible only to its requester or configured reviewers, even on shared notes.
    """
    from . import research_reviews

    refs = list(dict.fromkeys(revision_refs))
    if not refs:
        return {}
    result = {ref: [] for ref in refs}
    review = protocol_proposals.ProtocolProposalReview
    proposal_revision = protocol_proposals.ProtocolProposalRevision
    link = proposal_notes.ProposalNoteLink
    identity = (NotebookRevision.note_id, NotebookRevision.revision, NotebookRevision.content_sha256)
    withdrawal = proposal_retractions.ProposalRetraction
    schemes = (select(*identity, link.proposal_id, proposal_revision.revision,
        review.decision, review.checks["test_only_self_review"].as_boolean(), withdrawal.created_at)
        .select_from(link)
        .join(NotebookRevision, NotebookRevision.id == link.note_revision_id)
        .join(proposal_revision, and_(proposal_revision.id == link.proposal_revision_id,
            proposal_revision.proposal_id == link.proposal_id))
        .outerjoin(review, and_(review.proposal_id == link.proposal_id,
            review.revision == proposal_revision.revision,
            review.content_sha256 == proposal_revision.content_sha256))
        .outerjoin(withdrawal, and_(withdrawal.target_kind == proposal_retractions.SCHEME,
            withdrawal.target_id == link.proposal_id))
        .where(tuple_(*identity).in_(refs), link.note_id == NotebookRevision.note_id)
        .order_by(link.created_at, link.proposal_id))
    if own_only or not protocol_proposals.is_reviewer(settings, user):
        schemes = schemes.where(link.author_id == user.id)
    for note_id, number, digest, proposal_id, proposal_number, decision, test_only, withdrawn_at in session.execute(schemes):
        item = {"id": proposal_id, "kind": "scheme_admission", "status": decision or "submitted",
                "url": f"/#propose?proposal={proposal_id}", "proposal_revision": proposal_number,
                **proposal_retractions.compact(withdrawn_at)}
        if decision:
            item.update(test_only_self_review=bool(test_only), effective=not test_only or
                        (not settings.production and settings.allow_self_review))
        result[(note_id, number, digest)].append(item)

    request, assessment = research_reviews.ResearchRequest, research_reviews.ResearchAssessment
    primary = (request.sources[0]["note_id"].as_string(), request.sources[0]["revision"].as_integer(),
               request.sources[0]["content_sha256"].as_string())
    query = (select(*primary, request.id, request.purpose, assessment.outcome, assessment.test_only_self_review,
        withdrawal.created_at)
        .select_from(request).outerjoin(assessment, assessment.request_id == request.id)
        .outerjoin(withdrawal, and_(withdrawal.target_kind == proposal_retractions.RESEARCH,
            withdrawal.target_id == request.id))
        .where(tuple_(*primary).in_(refs)).order_by(request.created_at, request.id))
    if own_only or not protocol_proposals.is_reviewer(settings, user):
        query = query.where(request.author_id == user.id)
    for note_id, number, digest, identifier, purpose, outcome, test_only, withdrawn_at in session.execute(query):
        item = {"id": identifier, "kind": purpose, "status": outcome or "submitted",
                "url": f"/#notebook?research_request={identifier}", **proposal_retractions.compact(withdrawn_at)}
        if outcome:
            item.update(test_only_self_review=bool(test_only), effective=not test_only or
                        (not settings.production and settings.allow_self_review))
        result[(note_id, number, digest)].append(item)
    return result


def register_routes(app, db, settings, clock, user_for, throttle):
    def account(request, session, mutation=False):
        if ("authorization" in request.headers
                and machine_auth.required_scope(request.method, request.url.path) not in machine_auth.NOTEBOOK_SCOPES):
            raise HTTPException(403, "This notebook action requires a browser account.")
        user = user_for(request, session, mutation=mutation)
        if mutation:
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
        return user

    def publication_map(session, revisions):
        if not revisions:
            return {}, {}
        rows = session.execute(select(NotebookPublication, User.handle)
            .outerjoin(User, User.id == NotebookPublication.reviewer_id).where(
                NotebookPublication.revision_id.in_([item.id for item in revisions]))).all()
        return ({row.revision_id: row for row, _ in rows},
                {row.revision_id: handle for row, handle in rows})

    def revision_data(revision, publication=None, withdrawal=None, reviewer_handle=None):
        data = {"revision": revision.revision, "created_at": revision.created_at,
                "content_sha256": revision.content_sha256, "content": revision.content,
                "publication_status": (publication.decision or "submitted") if publication else "private",
                "imported": revision.source_revision is not None}
        if revision.source_revision is not None:
            data["imported_created_at"] = revision.source_revision["createdAt"]
        if publication:
            data["publication"] = {"requested_at": publication.requested_at, "reviewed_at": publication.reviewed_at,
                "reason": publication.reason, "reviewer": {"handle": reviewer_handle} if reviewer_handle is not None else None,
                "test_only_self_review": publication.test_only_self_review,
                "scope": "Publication makes this exact revision readable to eligible Crucible participants. It does not certify mathematics or activate a scheme."}
        if withdrawal:
            data["publication_status"] = "withdrawn"
            data["moderation"] = notebook_moderation.audit_data(withdrawal)
        return data

    def approved_clause():
        allowed = [NotebookPublication.test_only_self_review.is_(False)]
        if not settings.production and settings.allow_self_review:
            allowed.append(NotebookPublication.test_only_self_review.is_(True))
        return and_(NotebookPublication.decision == "approved", or_(*allowed),
                    NotebookPublication.revision_id.not_in(notebook_moderation.withdrawn_ids()))

    def visible(session, user, note_id=None, *, review=False, include_deleted=False):
        query = select(NotebookRevision).join(NotebookNote, NotebookNote.id == NotebookRevision.note_id).outerjoin(
            NotebookPublication, NotebookPublication.revision_id == NotebookRevision.id)
        permitted = [NotebookNote.author_id == user.id, approved_clause()]
        if review and protocol_proposals.is_reviewer(settings, user):
            permitted.append(NotebookPublication.id.is_not(None))
            permitted.append(NotebookRevision.id.in_(select(proposal_notes.ProposalNoteLink.note_revision_id)))
        query = query.where(or_(*permitted))
        if not include_deleted:
            query = query.where(NotebookNote.id.not_in(notebook_trash.deleted_ids()))
        if note_id is not None:
            query = query.where(NotebookNote.id == note_id)
        return query

    def shared_summaries(session, note_ids):
        if not note_ids:
            return {}
        latest_shared = select(NotebookRevision.note_id, func.max(NotebookRevision.revision).label("latest")).join(
            NotebookPublication, NotebookPublication.revision_id == NotebookRevision.id).where(
                NotebookRevision.note_id.in_(note_ids), approved_clause()).group_by(NotebookRevision.note_id).subquery()
        rows = session.execute(select(NotebookRevision.note_id, NotebookRevision.revision,
            NotebookRevision.content_sha256, NotebookRevision.created_at,
            NotebookRevision.content["title"].as_string(), NotebookRevision.content["claim"].as_string(),
            NotebookRevision.content["tags"], NotebookRevision.content["kind"].as_string(),
            NotebookPublication.test_only_self_review).join(latest_shared,
                and_(NotebookRevision.note_id == latest_shared.c.note_id, NotebookRevision.revision == latest_shared.c.latest))
            .join(NotebookPublication, NotebookPublication.revision_id == NotebookRevision.id)).all()
        return {identifier: {"revision": number, "content_sha256": digest, "created_at": created,
                "content": {"title": title, "claim": claim, "tags": tags or [], **({"kind": kind} if kind is not None else {})},
                "publication_status": "approved", "publication": {"test_only_self_review": test_only}, "summary": True}
                for identifier, number, digest, created, title, claim, tags, kind, test_only in rows}

    def note_data(session, note, user, revisions, *, selected=None, summary=False, agent=False):
        publications, reviewer_handles = publication_map(session, revisions)
        withdrawals = notebook_moderation.records(session, [item.id for item in revisions])
        chosen = next((item for item in revisions if item.revision == selected), None) if selected is not None else revisions[-1]
        if chosen is None:
            raise HTTPException(404, "Note revision not found.")
        owner = session.get(User, note.author_id)
        serial = [revision_data(item, publications.get(item.id), withdrawals.get(item.id), reviewer_handles.get(item.id))
                  for item in ([chosen] if summary else revisions)]
        submissions = (proposal_notes.review_records(session, revisions, settings=settings)
            if user.id == note.author_id or protocol_proposals.is_reviewer(settings, user) else {})
        for data, item in zip(serial, ([chosen] if summary else revisions)):
            data["scheme_review"] = submissions.get(item.id)
        current = next(data for data in serial if data["revision"] == chosen.revision)
        shared = shared_summaries(session, [note.id]).get(note.id)
        refs = [(note.id, item.revision, item.content_sha256) for item in revisions]
        if shared:
            refs.append((note.id, shared["revision"], shared["content_sha256"]))
        requests = request_summaries(session, settings, user, refs, own_only=agent)
        for data in serial:
            data["requests"] = requests.get((note.id, data["revision"], data["content_sha256"]), [])
        if shared:
            shared["requests"] = requests.get((note.id, shared["revision"], shared["content_sha256"]), [])
        proposal_revision = next(({"revision": item.revision, "content_sha256": item.content_sha256,
            "requests": proposals} for item in reversed(revisions)
            if (proposals := proposal_records(requests.get((note.id, item.revision, item.content_sha256), [])))), None)
        deletion = notebook_trash.active(session, note.id)
        return {"id": note.id, "owner": {"id": owner.id, "handle": owner.handle}, "can_edit": user.id == owner.id and deletion is None,
                "can_delete": user.id == owner.id and deletion is None and not notebook_trash.has_review_history(session, note.id),
                "can_restore": user.id == owner.id and deletion is not None,
                "deletion": notebook_trash.data(deletion) if deletion else None,
                "can_review": not agent and protocol_proposals.is_reviewer(settings, user) and (user.id != owner.id or protocol_proposals.can_self_review(settings, user)),
                "can_self_review": not agent and protocol_proposals.can_self_review(settings, user),
                "can_moderate": not agent and protocol_proposals.is_reviewer(settings, user),
                "created_at": note.created_at, "revision": chosen.revision, "content_sha256": chosen.content_sha256,
                "publication_status": current["publication_status"], "revision_count": len(revisions), "revisions": serial,
                "shared_revision": shared, "proposal_revision": proposal_revision, "imported": note.source_id is not None}

    def owned(session, user, note_id, *, metadata_only=False):
        query = select(NotebookNote).where(NotebookNote.id == note_id,
            NotebookNote.author_id == user.id).with_for_update()
        if metadata_only:
            query = query.options(load_only(NotebookNote.id, NotebookNote.author_id,
                                           NotebookNote.latest_revision, raiseload=True))
        note = session.scalar(query)
        if note is None:
            raise HTTPException(404, "Note not found.")
        notebook_trash.require_active(session, note.id)
        return note

    def all_revisions(session, note):
        return session.scalars(select(NotebookRevision).where(NotebookRevision.note_id == note.id)
            .order_by(NotebookRevision.revision)).all()

    @app.get("/api/notebook/timeline")
    def timeline(request: Request):
        now = clock()
        as_of = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        # This is the current readable corpus grouped by recorded creation
        # date, not an audit of what the reader could access on past dates.
        with db.transaction() as session:
            user = account(request, session)
            readable = visible(session, user).with_only_columns(NotebookRevision.note_id)
            # IN is a semijoin: multiple visible revisions/publications cannot
            # multiply a note. No account note IDs or bodies leave SQL.
            day = case((and_(NotebookNote.created_at > 0, NotebookNote.created_at <= now),
                cast(func.floor(NotebookNote.created_at / 86400), Integer)), else_=None)
            rows = session.execute(select(day.label("day"), func.count())
                .where(NotebookNote.id.in_(readable)).group_by(day)).all()
            counts = {number: count for number, count in rows if number is not None}
            undated = sum(count for number, count in rows if number is None)
            # First bundled revision dates describe catalogue authoring, not
            # the publication dates of papers discussed in those notes.
            for note in curated_collection():
                timestamp = note["created_at"]
                if math.isfinite(timestamp) and 0 < timestamp <= now:
                    number = int(timestamp // 86400)
                    counts[number] = counts.get(number, 0) + 1
                else:
                    undated += 1
            points, total = [], 0
            for number, count in sorted(counts.items()):
                total += count
                points.append({"date": (date(1970, 1, 1) + timedelta(days=number)).isoformat(),
                    "created": count, "total": total})
            return {"format": "crucible-notebook-timeline-v1", "date_basis": "recorded_creation_utc",
                "audience": "currently_visible_notes", "as_of": as_of, "total": total + undated,
                "undated_count": undated, "points": points, "server_time": now}

    @app.get("/api/notebook/notes")
    def list_notes(request: Request, after: str | None = Query(default=None, max_length=768),
                   limit: int = Query(default=50, ge=1, le=100), proposals_only: bool = False,
                   include_curated: bool = False, q: str = Query(default="", max_length=200),
                   scope: Literal["all", "mine", "shared", "literature", "human", "trash"] = "all",
                   tag: str | None = Query(default=None, min_length=1, max_length=100),
                   projection: Literal["index"] | None = None):
        indexed = projection == "index"
        if indexed:
            # An index is complete for one ordinary visibility domain, never a
            # partial search/page presented as the full notebook. Text search
            # remains server-side so SQL case/escaping semantics do not change.
            if (scope not in {"all", "mine"} or any(name in request.query_params
                    for name in ("after", "limit", "q", "tag", "proposals_only"))):
                raise HTTPException(422, "A notebook summary index does not accept search, filters, or pagination.")
            limit = SUMMARY_INDEX_MAX_NOTES
        try:
            for value in (q, tag):
                _inspect(value)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Use valid text for notebook search and filters.") from None
        term = q.strip()
        scope_tag = {"literature": "literature", "human": "human-written"}.get(scope)
        cursor = _read_browse_cursor(after) if after is not None else None
        with db.transaction() as session:
            user = account(request, session)
            agent = "authorization" in request.headers
            if agent:
                # A notebook credential covers only the author's own records,
                # even when the account is also a configured reviewer.
                if scope not in {"all", "mine", "trash"} or include_curated:
                    raise HTTPException(422, "Agent notebook listing supports owned notes only. Use the published-note API for community notes.")
                if scope != "trash":
                    scope = "mine"
            if scope == "shared":
                raise HTTPException(410, notebook_sharing.RETIRED)
            capabilities = {"review_publication": not agent and protocol_proposals.is_reviewer(settings, user),
                            "self_review_publication": not agent and protocol_proposals.can_self_review(settings, user)}

            def index_result(records, *, overflow_reason=None):
                return {"format": "crucible-notebook-summary-index-v1", "projection": "index",
                    "complete": overflow_reason is None, "viewer_id": user.id,
                    "scope": scope, "include_curated": include_curated,
                    "notes": records, "next_after": None, "overflow_reason": overflow_reason,
                    "capabilities": capabilities, "server_time": clock()}

            # First identify the latest VISIBLE revision; search never selects
            # an old matching draft or discloses an unpublished later revision.
            newest_proposal = func.max(case((proposal_request_clause(settings, user, own_only=agent), NotebookRevision.revision), else_=None))
            grouped = visible(session, user, include_deleted=scope == "trash").with_only_columns(NotebookRevision.note_id,
                func.max(NotebookRevision.revision).label("latest"), func.count().label("revision_count"),
                newest_proposal.label("proposal_revision"))
            if scope in {"mine", "trash"}:
                grouped = grouped.where(NotebookNote.author_id == user.id)
            if scope == "trash":
                grouped = grouped.where(NotebookNote.id.in_(notebook_trash.deleted_ids()))
            if proposals_only:
                grouped = grouped.having(newest_proposal.is_not(None))
            latest_visible = grouped.group_by(NotebookRevision.note_id).subquery()
            proposed = aliased(NotebookRevision)
            withdrawal = notebook_moderation.PublicationWithdrawal
            deletion = notebook_trash.NotebookDeletion
            query = (select(NotebookNote, User.handle, NotebookRevision.id,
                NotebookRevision.revision, NotebookRevision.created_at, NotebookRevision.content_sha256,
                NotebookRevision.content["title"].as_string(), NotebookRevision.content["claim"].as_string(),
                NotebookRevision.content["tags"], NotebookRevision.content["kind"].as_string(),
                latest_visible.c.revision_count, latest_visible.c.proposal_revision,
                proposed.content_sha256, NotebookPublication.id, NotebookPublication.decision,
                withdrawal.revision_id, deletion).join(latest_visible, latest_visible.c.note_id == NotebookNote.id)
                .join(NotebookRevision, and_(NotebookRevision.note_id == NotebookNote.id,
                    NotebookRevision.revision == latest_visible.c.latest))
                .join(User, User.id == NotebookNote.author_id)
                .outerjoin(proposed, and_(proposed.note_id == NotebookNote.id,
                    proposed.revision == latest_visible.c.proposal_revision))
                # Each join has at most one row: publication/withdrawal are
                # unique per revision, and only one active deletion is allowed.
                # Keep the current summary and its status in the same read.
                .outerjoin(NotebookPublication, NotebookPublication.revision_id == NotebookRevision.id)
                .outerjoin(withdrawal, withdrawal.revision_id == NotebookRevision.id)
                .outerjoin(deletion, and_(deletion.note_id == NotebookNote.id, deletion.restored_at.is_(None)))
                .options(load_only(NotebookNote.id, NotebookNote.author_id, NotebookNote.created_at,
                                  NotebookNote.source_id, raiseload=True),
                         load_only(deletion.id, deletion.note_id, deletion.revision, deletion.content_sha256,
                                   deletion.deleted_at, deletion.restored_at, raiseload=True)))
            if cursor:
                created_at, identifier = cursor
                query = query.where(or_(NotebookRevision.created_at < created_at,
                    and_(NotebookRevision.created_at == created_at, NotebookNote.id > identifier)))
            if term:
                query = query.where(_search_clause(term))
            if tag is not None:
                query = query.where(_tag_clause(tag))
            if scope_tag:
                query = query.where(_tag_clause(scope_tag))
            rows = session.execute(query.order_by(NotebookRevision.created_at.desc(), NotebookNote.id).limit(limit + 1)).all()

            curated, curated_requests = [], {}
            if include_curated and scope not in {"mine", "trash"}:
                for note in curated_collection():
                    latest = note["revisions"][-1]
                    content = latest["content"]
                    tags = content.get("tags", [])
                    if cursor and (-latest["created_at"], note["id"]) <= (-cursor[0], cursor[1]):
                        continue
                    if ((scope_tag is not None and scope_tag not in tags)
                            or (tag is not None and tag not in tags)):
                        continue
                    if term and not any(term.lower() in value.lower() for value in
                        (content["title"], content["claim"], *tags)):
                        continue
                    curated.append(note)
                curated_requests = curated_request_summaries(session, settings, user, curated)
            curated_proposals = {note["id"]: latest_proposal_reference(note["id"], note["revisions"], curated_requests)
                                 for note in curated}
            if proposals_only:
                curated = [note for note in curated if curated_proposals[note["id"]] is not None]
            curated = sorted(curated, key=lambda note: (-note["revisions"][-1]["created_at"], note["id"]))[:limit + 1]
            # Each collection contributes at most its first limit+1 matches.
            # Merge identities before hydration so summaries are fetched only
            # for the returned account notes, with no per-note requests.
            candidates = sorted([((-row[4], row[0].id), "account", row) for row in rows] +
                                [((-note["revisions"][-1]["created_at"], note["id"]), "curated", note) for note in curated],
                                key=lambda item: item[0])
            if indexed and len(candidates) > limit:
                return index_result([], overflow_reason="count_limit")
            page = candidates[:limit]
            selected_rows = [value for _, kind, value in page if kind == "account"]
            shared = shared_summaries(session, [row[0].id for row in selected_rows])
            refs = [(row[0].id, row[3], row[5]) for row in selected_rows]
            refs.extend((identifier, item["revision"], item["content_sha256"]) for identifier, item in shared.items())
            refs.extend((row[0].id, row[11], row[12]) for row in selected_rows if row[11] is not None)
            requests = request_summaries(session, settings, user, refs, own_only=agent)
            for identifier, item in shared.items():
                item["requests"] = requests.get((identifier, item["revision"], item["content_sha256"]), [])
            records = []
            for _, source_kind, row in page:
                if source_kind == "curated":
                    latest = row["revisions"][-1]
                    content = {key: value for key, value in latest["content"].items() if key in {"title", "claim", "tags", "kind"}}
                    records.append({key: value for key, value in row.items() if key != "revisions"} | {
                        "revision": latest["revision"], "content_sha256": latest["content_sha256"],
                        "publication_status": "curated", "shared_revision": None,
                        "proposal_revision": curated_proposals[row["id"]],
                        "revisions": [{key: value for key, value in latest.items() if key != "content"} | {
                            "content": content, "summary": True,
                            "requests": curated_requests.get((row["id"], latest["revision"], latest["content_sha256"]), [])}]})
                    continue
                (note, handle, revision_id, number, created, digest, title, claim, tags, kind, count,
                 proposed_number, proposed_digest, publication_id, decision, withdrawn_id, deleted) = row
                status = "withdrawn" if withdrawn_id is not None else (decision or "submitted") if publication_id else "private"
                content = {"title": title, "claim": claim, "tags": tags or []}
                if kind is not None:
                    content["kind"] = kind
                records.append({"id": note.id, "owner": {"id": note.author_id, "handle": handle},
                    "can_edit": user.id == note.author_id and deleted is None,
                    "can_restore": user.id == note.author_id and deleted is not None,
                    "deletion": notebook_trash.data(deleted) if deleted is not None else None,
                    "can_review": not agent and protocol_proposals.is_reviewer(settings, user) and (user.id != note.author_id or protocol_proposals.can_self_review(settings, user)),
                    "can_self_review": not agent and protocol_proposals.can_self_review(settings, user),
                    "created_at": note.created_at, "revision": number, "content_sha256": digest,
                    "publication_status": status, "revision_count": count, "imported": note.source_id is not None,
                    "shared_revision": shared.get(note.id),
                    "proposal_revision": {"revision": proposed_number, "content_sha256": proposed_digest,
                        "requests": proposal_records(requests.get((note.id, proposed_number, proposed_digest), []))}
                        if proposed_number is not None else None,
                    "revisions": [{"revision": number, "content_sha256": digest, "created_at": created,
                                   "content": content, "publication_status": status, "summary": True,
                                   "requests": requests.get((note.id, number, digest), [])}]})
            if indexed:
                result = index_result(records)
                if len(_bytes(result)) > SUMMARY_INDEX_MAX_BYTES:
                    return index_result([], overflow_reason="byte_limit")
                return result
            return {"notes": records, "next_after": _browse_cursor(-page[-1][0][0], page[-1][0][1])
                    if len(candidates) > limit else None,
                    "capabilities": capabilities, "server_time": clock()}

    notebook_sharing.register_routes(app, db, settings, clock, account, throttle)
    notebook_moderation.register_routes(app, db, settings, clock, account, throttle)
    notebook_graph.register_routes(app, db, settings, account, visible)
    notebook_trash.register_routes(app, db, clock, account, throttle)
    note_ratings.register_routes(app, db, clock, user_for, throttle)

    @app.get("/api/notebook/notes/{note_id}")
    def get_note(note_id: UUID, request: Request, revision: int | None = Query(default=None, ge=1, le=MAX_REVISIONS),
                 include_deleted: bool = False):
        with db.transaction() as session:
            user = account(request, session)
            agent = "authorization" in request.headers
            note = session.get(NotebookNote, str(note_id))
            owner = note is not None and note.author_id == user.id
            if (agent or include_deleted) and not owner:
                raise HTTPException(404, "Note not found.")
            if owner and not include_deleted:
                notebook_trash.require_active(session, str(note_id))
            revisions = session.scalars(visible(session, user, str(note_id), review=True,
                include_deleted=include_deleted).order_by(NotebookRevision.revision)).all()
            # A pinned link must not fall through to another revision or expose
            # a private moderation reason. Only previously shared targets get
            # a neutral tombstone; unrelated private drafts still return 404.
            if not revisions or (revision is not None and not any(row.revision == revision for row in revisions)):
                withdrawn = select(NotebookRevision.id).where(NotebookRevision.note_id == str(note_id),
                    NotebookRevision.id.in_(notebook_moderation.withdrawn_ids()))
                if revision is not None:
                    withdrawn = withdrawn.where(NotebookRevision.revision == revision)
                if session.scalar(withdrawn.limit(1)):
                    raise HTTPException(410, notebook_moderation.NOTICE)
            if not revisions:
                raise HTTPException(404, "Note not found.")
            return {"note": note_data(session, note, user, revisions, selected=revision, agent=agent)}

    @app.post("/api/notebook/notes")
    def create_note(body: CreateInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        content = _content(body.content)
        commitment = _hash(content)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            existing = session.scalar(select(NotebookNote).where(NotebookNote.author_id == user.id,
                NotebookNote.idempotency_key == str(body.idempotency_key)))
            if existing:
                notebook_trash.require_active(session, existing.id)
                if existing.request_sha256 != commitment:
                    raise HTTPException(409, "This save identifier was already used for different content.")
                return {"note": note_data(session, existing, user, all_revisions(session, existing), selected=1, agent="authorization" in request.headers)}
            check_capacity(session, user)
            now = clock()
            note = NotebookNote(id=str(uuid4()), author_id=user.id, created_at=now, latest_revision=1,
                idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            session.add(note)
            session.flush()
            revision = NotebookRevision(id=str(uuid4()), note_id=note.id, revision=1, created_at=now,
                content=content, content_sha256=commitment, idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            session.add(revision)
            session.flush()
            workspace_for(session, user)
            return {"note": note_data(session, note, user, [revision], agent="authorization" in request.headers)}

    @app.post("/api/notebook/notes/{note_id}/revisions")
    def revise_note(note_id: UUID, body: RevisionInput, request: Request):
        throttle(request, "notebook-write", 120, 3600)
        content = _content(body.content)
        commitment = _hash({"content": content, "expected_revision": body.expected_revision, "content_sha256": body.content_sha256})
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, str(note_id))
            history = all_revisions(session, note)
            replay = next((item for item in history if item.idempotency_key == str(body.idempotency_key)), None)
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This save identifier was already used for a different revision.")
                return {"note": note_data(session, note, user, history, selected=replay.revision, agent="authorization" in request.headers)}
            if note.latest_revision != body.expected_revision or history[-1].content_sha256 != body.content_sha256:
                raise HTTPException(409, "This note changed. Reload the latest revision before saving; your draft has not been overwritten.")
            if note.latest_revision >= MAX_REVISIONS:
                raise HTTPException(409, "This note currently supports 100 revisions. Its existing history remains saved.")
            revision = NotebookRevision(id=str(uuid4()), note_id=note.id, revision=note.latest_revision + 1,
                created_at=clock(), content=content, content_sha256=_hash(content),
                idempotency_key=str(body.idempotency_key), request_sha256=commitment)
            note.latest_revision = revision.revision
            session.add(revision)
            session.flush()
            workspace_for(session, user)
            return {"note": note_data(session, note, user, [*history, revision], agent="authorization" in request.headers)}

    @app.post("/api/notebook/notes/{note_id}/proposal-submissions")
    def submit_proposal_note(note_id: UUID, body: PublicationInput, request: Request):
        throttle(request, "protocol-proposal-write", 10, 3600)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, str(note_id))
            revision = session.scalar(select(NotebookRevision).where(NotebookRevision.note_id == note.id,
                NotebookRevision.revision == body.revision))
            return proposal_notes.submit_note(session, user, note, revision, body, clock())

    @app.get("/api/notebook/export")
    def export_notebook(request: Request, note_id: UUID | None = Query(default=None)):
        with db.transaction() as session:
            user = account(request, session)
            return workspace_for(session, user, note_id, include_deleted=False)

    @app.post("/api/notebook/import")
    def import_notebook(body: ImportInput, request: Request):
        throttle(request, "notebook-import", 20, 3600)
        workspace = _workspace(body.workspace)
        commitment = _hash(workspace)
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            replay = session.scalar(select(NotebookImport).where(NotebookImport.author_id == user.id,
                NotebookImport.idempotency_key == str(body.idempotency_key)))
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This import identifier was already used for a different workspace.")
                return {"import_id": replay.id, "id_map": replay.id_map,
                        "added_notes": replay.added_notes, "added_revisions": replay.added_revisions, "replayed": True}
            byte_size = len(_bytes(workspace))
            used, previous = session.execute(select(func.coalesce(func.sum(NotebookImport.byte_size), 0),
                func.coalesce(func.max(NotebookImport.sequence), 0)).where(NotebookImport.author_id == user.id)).one()
            if used + byte_size > MAX_IMPORT_ARCHIVE_BYTES or previous >= 1000:
                raise HTTPException(409, "The private import archive has reached its current capacity. Existing notes and imports remain saved; contact the operator.")
            mapping, added_notes, added_revisions = {}, 0, 0
            imported_revision_ids = []
            for incoming in workspace["notes"]:
                source_id = incoming["id"]
                note = session.get(NotebookNote, source_id)
                if note is not None and note.author_id != user.id:
                    raise HTTPException(409, "An import cannot revise another account's note. Create your own note with a new identifier.")
                if note is None:
                    note = session.scalar(select(NotebookNote).where(NotebookNote.author_id == user.id,
                        NotebookNote.source_id == source_id))
                if note is not None:
                    notebook_trash.require_active(session, note.id)
                if note is None:
                    check_capacity(session, user)
                    note = NotebookNote(id=str(uuid4()), author_id=user.id, created_at=clock(), latest_revision=0,
                        idempotency_key=str(uuid4()), request_sha256=_hash(incoming), source_id=source_id,
                        source_metadata={key: item for key, item in incoming.items() if key not in {"id", "revisions"}})
                    session.add(note)
                    session.flush()
                    added_notes += 1
                history = all_revisions(session, note)
                if len(incoming["revisions"]) < len(history) or any(_bytes(_legacy_revision(old)) != _bytes(new)
                        for old, new in zip(history, incoming["revisions"])):
                    raise HTTPException(409, "An import must preserve each existing note's exact revision history. Append new revisions instead of rewriting or removing old ones.")
                for source in incoming["revisions"][len(history):]:
                    content = _content({key: item for key, item in source.items() if key in CONTENT_FIELDS})
                    revision = NotebookRevision(id=str(uuid4()), note_id=note.id, revision=source["revision"],
                        created_at=clock(), content=content, content_sha256=_hash(content), source_revision=source,
                        idempotency_key=str(uuid4()), request_sha256=_hash(source))
                    session.add(revision)
                    imported_revision_ids.append(revision.id)
                    note.latest_revision = revision.revision
                    added_revisions += 1
                mapping[source_id] = note.id
                session.flush()
            notebook_graph.remap_import(session.connection(), imported_revision_ids, mapping)
            record = NotebookImport(id=str(uuid4()), author_id=user.id, created_at=clock(),
                sequence=previous + 1, byte_size=byte_size,
                idempotency_key=str(body.idempotency_key), request_sha256=commitment, workspace=workspace,
                id_map=mapping, added_notes=added_notes, added_revisions=added_revisions)
            session.add(record)
            session.flush()
            workspace_for(session, user)
            return {"import_id": record.id, "id_map": mapping, "added_notes": added_notes,
                    "added_revisions": added_revisions, "replayed": False}

    @app.get("/api/notebook/imports/{import_id}")
    def original_import(import_id: UUID, request: Request):
        with db.transaction() as session:
            user = account(request, session)
            record = session.scalar(select(NotebookImport).where(NotebookImport.id == str(import_id), NotebookImport.author_id == user.id))
            if record is None:
                raise HTTPException(404, "Import not found.")
            return {"import_id": record.id, "created_at": record.created_at, "workspace": record.workspace, "id_map": record.id_map}

    @app.post("/api/notebook/notes/{note_id}/publication-requests")
    def request_publication(note_id: UUID, body: PublicationInput, request: Request, summary: bool = False):
        throttle(request, "notebook-publication", 60, 3600)
        commitment = _hash({"note_id": str(note_id), "revision": body.revision, "content_sha256": body.content_sha256})
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            note = owned(session, user, str(note_id), metadata_only=summary)
            history = None if summary else all_revisions(session, note)

            def result(publication):
                if not summary:
                    return {"note": note_data(session, note, user, history, selected=body.revision,
                                              agent="authorization" in request.headers)}
                withdrawn = session.scalar(select(notebook_moderation.PublicationWithdrawal.revision_id)
                    .where(notebook_moderation.PublicationWithdrawal.revision_id == publication.revision_id))
                return {"format": "crucible-publication-request-v1", "publication_request": {
                    "id": publication.id, "note_id": note.id, "revision": body.revision,
                    "content_sha256": body.content_sha256, "requested_at": publication.requested_at,
                    "status": "withdrawn" if withdrawn is not None else publication.decision or "submitted"}}

            replay_query = select(NotebookPublication).where(NotebookPublication.author_id == user.id,
                NotebookPublication.idempotency_key == str(body.idempotency_key))
            if summary:
                replay_query = replay_query.options(load_only(NotebookPublication.id, NotebookPublication.revision_id,
                    NotebookPublication.request_sha256, NotebookPublication.requested_at,
                    NotebookPublication.decision, raiseload=True))
            replay = session.scalar(replay_query)
            if replay:
                if replay.request_sha256 != commitment:
                    raise HTTPException(409, "This publication identifier was already used for another request.")
                return result(replay)
            target = (session.execute(select(NotebookRevision.id, NotebookRevision.revision,
                NotebookRevision.content_sha256).where(NotebookRevision.note_id == note.id)
                .order_by(NotebookRevision.revision.desc()).limit(1)).one() if summary else history[-1])
            if target.revision != body.revision or target.content_sha256 != body.content_sha256:
                raise HTTPException(409, "Request publication of the exact latest revision. Refresh before trying again.")
            existing = session.scalar(select(NotebookPublication.id).where(NotebookPublication.revision_id == target.id))
            if existing:
                raise HTTPException(409, "This revision already has a publication request. Revise the note before making a new request.")
            publication = NotebookPublication(id=str(uuid4()), revision_id=target.id, author_id=user.id,
                requested_at=clock(), idempotency_key=str(body.idempotency_key), request_sha256=commitment,
                test_only_self_review=False)
            session.add(publication)
            session.flush()
            return result(publication)

    @app.get("/api/notebook/review-queue")
    def publication_queue(request: Request, after: str | None = Query(default=None, max_length=36),
                          limit: int = Query(default=50, ge=1, le=100)):
        with db.transaction() as session:
            user = account(request, session)
            if not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, "A configured reviewer is required.")
            query = select(NotebookPublication, NotebookRevision, NotebookNote).join(
                NotebookRevision, NotebookRevision.id == NotebookPublication.revision_id).join(
                NotebookNote, NotebookNote.id == NotebookRevision.note_id).where(NotebookPublication.decision.is_(None))
            if not protocol_proposals.can_self_review(settings, user):
                query = query.where(NotebookNote.author_id != user.id)
            if after:
                anchor = session.get(NotebookPublication, after)
                if anchor is None:
                    raise HTTPException(422, "Invalid sharing review cursor.")
                query = query.where(or_(NotebookPublication.requested_at > anchor.requested_at,
                    and_(NotebookPublication.requested_at == anchor.requested_at, NotebookPublication.id > anchor.id)))
            rows = session.execute(query.order_by(NotebookPublication.requested_at, NotebookPublication.id).limit(limit + 1)).all()
            return {"notes": [note_data(session, note, user, [revision]) for _, revision, note in rows[:limit]],
                    "next_after": rows[limit - 1][0].id if len(rows) > limit else None}

    @app.post("/api/notebook/notes/{note_id}/publication-reviews")
    def review_publication(note_id: UUID, body: ReviewInput, request: Request):
        throttle(request, "notebook-review", 120, 3600)
        commitment = _hash({"note_id": str(note_id), **body.model_dump(mode="json", exclude={"idempotency_key"})})
        with db.transaction(write=True) as session:
            user = account(request, session, True)
            if not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, "A configured reviewer is required.")
            note = session.scalar(select(NotebookNote).where(NotebookNote.id == str(note_id)).with_for_update())
            target = session.scalar(select(NotebookRevision).where(NotebookRevision.note_id == str(note_id),
                NotebookRevision.revision == body.revision))
            publication = session.scalar(select(NotebookPublication).where(
                NotebookPublication.revision_id == target.id).with_for_update()) if target else None
            if note is None or target is None or publication is None:
                raise HTTPException(404, "Submitted note revision not found.")
            self_review = note.author_id == user.id
            if self_review and not protocol_proposals.can_self_review(settings, user):
                raise HTTPException(403, "Ask a different configured reviewer to approve publication.")
            replay = session.scalar(select(NotebookPublication).where(NotebookPublication.reviewer_id == user.id,
                NotebookPublication.review_key == str(body.idempotency_key)))
            if replay:
                if replay.review_sha256 != commitment:
                    raise HTTPException(409, "This review identifier was already used for another decision.")
            else:
                if target.content_sha256 != body.content_sha256:
                    raise HTTPException(409, "The review must name the submitted revision's exact content hash.")
                if publication.decision is not None:
                    raise HTTPException(409, "This revision already has a publication decision. Review a newly submitted revision instead.")
                publication.decision = body.decision
                publication.reviewer_id = user.id
                publication.reviewed_at = clock()
                publication.reason = body.reason
                publication.review_key = str(body.idempotency_key)
                publication.review_sha256 = commitment
                publication.test_only_self_review = self_review
                session.flush()
            revisions = session.scalars(visible(session, user, note.id, review=True).order_by(NotebookRevision.revision)).all()
            return {"note": note_data(session, note, user, revisions, selected=body.revision)}
