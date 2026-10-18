"""Trusted links between immutable notebook and scheme-review revisions.

Authored tags/extensions never grant review or publication authority. The exact
specification is carried in a note; this table records an explicit submission.
"""
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import Float, ForeignKey, String, UniqueConstraint, and_, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from . import proposal_retractions

SPEC_FIELD = "kem_specification"


class ProposalNoteLink(Base):
    __tablename__ = "proposal_note_links"
    __table_args__ = (UniqueConstraint("note_revision_id", name="uq_proposal_note_revision"),
                     UniqueConstraint("author_id", "idempotency_key", name="uq_proposal_note_request"))
    proposal_revision_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposal_revisions.id"), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("protocol_proposals.id"), index=True)
    note_revision_id: Mapped[str] = mapped_column(ForeignKey("notebook_revisions.id"))
    note_id: Mapped[str] = mapped_column(ForeignKey("notebook_notes.id"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_sha256: Mapped[str] = mapped_column(String(64))


def content_from_spec(spec):
    """A readable, explicitly unreviewed note; keep the exact spec attached."""
    from . import notebook
    correctness, security, parameters = spec["correctness"], spec["security"], spec["parameters"]
    body = []
    for title, text in [("Motivation", spec["summary"]),
                        ("Construction", "\n\n".join(name.title() + ": " + str(text) for name, text in spec["algorithms"].items())),
                        ("Configurations and progression", parameters["mapping"]),
                        ("Correctness argument", correctness["argument"]),
                        ("Security claim and open questions", security["claim"])]:
        body.append("## " + title + "\n" + str(text))
    text = "\n\n".join(body)
    # Full details always remain in the attachment, even for long submissions.
    if len(text) > 38000:
        text = text[:37000] + "\n\nThe complete specification is attached below."
    return notebook._content({"title": spec["name"], "claim": spec["summary"],
        "conditions": ("Unreviewed scheme proposal. " + str(correctness["scope"]) + "\n\nSecurity model: " + str(security["model"]))[:19000],
        "body": text, "tags": ["scheme-description", "KEM", "proposal"], "kind": "Unreviewed construction",
        "references": spec.get("references", []), "extensions": {SPEC_FIELD: spec}})


def link_revision(session, record, proposal_revision, note, revision, now, *, origin, key=None, commitment=None):
    from . import notebook
    link = ProposalNoteLink(proposal_revision_id=proposal_revision.id, proposal_id=record.id,
        note_revision_id=revision.id, note_id=note.id, author_id=note.author_id, created_at=now,
        origin=origin, idempotency_key=key or str(uuid4()),
        request_sha256=commitment or notebook._hash({"note_id": note.id, "revision": revision.revision,
                                                    "content_sha256": revision.content_sha256}))
    session.add(link)
    session.flush()
    return link


def capture_submission(session, user, record, proposal_revision, now, *, content=None, origin="specification"):
    """Existing API/CLI submissions become notes in the same transaction.

    Never overwrite an unsubmitted notebook draft. API revisions regenerate a
    readable note from their specification; previous prose remains in history.
    """
    from . import notebook
    prior = session.scalar(select(ProposalNoteLink).where(ProposalNoteLink.proposal_id == record.id)
        .join(notebook.NotebookRevision, notebook.NotebookRevision.id == ProposalNoteLink.note_revision_id)
        .order_by(notebook.NotebookRevision.revision.desc()).limit(1))
    value = notebook._content(content) if content is not None else content_from_spec(proposal_revision.specification)
    if (value.get("extensions") or {}).get(SPEC_FIELD) != proposal_revision.specification:
        raise HTTPException(422, "The note must carry this exact submitted specification.")
    digest = notebook._hash(value)
    key = str(uuid4())
    if prior:
        note = session.scalar(select(notebook.NotebookNote).where(notebook.NotebookNote.id == prior.note_id).with_for_update())
        previous = session.get(notebook.NotebookRevision, prior.note_revision_id)
        if note.latest_revision != previous.revision:
            raise HTTPException(409, "This proposal has an unsubmitted notebook revision. Review and submit that note before revising through the proposal API.")
        if note.latest_revision >= notebook.MAX_REVISIONS:
            raise HTTPException(409, "This note has reached its revision capacity. Existing history is unchanged.")
        number = note.latest_revision + 1
        note.latest_revision = number
    else:
        notebook.check_capacity(session, user)
        note = notebook.NotebookNote(id=str(uuid4()), author_id=user.id, created_at=now, latest_revision=1,
            idempotency_key=key, request_sha256=digest, source_id="protocol-proposal:" + record.id,
            source_metadata={"extensions": {"proposal_origin": {"proposal_id": record.id, "conversion": origin}}})
        session.add(note)
        session.flush()
        number = 1
    revision = notebook.NotebookRevision(id=str(uuid4()), note_id=note.id, revision=number,
        created_at=now, content=value, content_sha256=digest, idempotency_key=key, request_sha256=digest)
    session.add(revision)
    session.flush()
    notebook.workspace_for(session, user)
    link_revision(session, record, proposal_revision, note, revision, now, origin=origin)
    return note


def source_record(session, proposal_id, number):
    from . import notebook, protocol_proposals as proposals
    row = session.execute(select(ProposalNoteLink, notebook.NotebookRevision)
        .join(proposals.ProtocolProposalRevision, proposals.ProtocolProposalRevision.id == ProposalNoteLink.proposal_revision_id)
        .join(notebook.NotebookRevision, notebook.NotebookRevision.id == ProposalNoteLink.note_revision_id)
        .where(ProposalNoteLink.proposal_id == proposal_id, proposals.ProtocolProposalRevision.revision == number)).first()
    if not row:
        return None
    link, revision = row
    return {"id": link.note_id, "revision": revision.revision, "content_sha256": revision.content_sha256,
            "url": f"/#notebook?note={link.note_id}&revision={revision.revision}"}


def review_records(session, revisions, *, settings):
    from . import protocol_proposals as proposals
    if not revisions:
        return {}
    withdrawal = proposal_retractions.ProposalRetraction
    rows = session.execute(select(ProposalNoteLink, proposals.ProtocolProposalRevision, proposals.ProtocolProposal,
                                   proposals.ProtocolProposalReview.decision,
                                   proposals.ProtocolProposalReview.checks["test_only_self_review"].as_boolean(),
                                   withdrawal.created_at)
        .join(proposals.ProtocolProposalRevision, proposals.ProtocolProposalRevision.id == ProposalNoteLink.proposal_revision_id)
        .join(proposals.ProtocolProposal, proposals.ProtocolProposal.id == ProposalNoteLink.proposal_id)
        .outerjoin(proposals.ProtocolProposalReview, (proposals.ProtocolProposalReview.proposal_id == ProposalNoteLink.proposal_id) &
                   (proposals.ProtocolProposalReview.revision == proposals.ProtocolProposalRevision.revision))
        .outerjoin(withdrawal, and_(withdrawal.target_kind == proposal_retractions.SCHEME,
            withdrawal.target_id == ProposalNoteLink.proposal_id))
        .where(ProposalNoteLink.note_revision_id.in_([revision.id for revision in revisions]))).all()
    return {link.note_revision_id: {"id": record.id, "revision": rev.revision,
        "content_sha256": rev.content_sha256, "status": decision or "submitted",
        "url": f"/#propose?proposal={record.id}", "is_current": rev.revision == record.revision,
        **({"test_only_self_review": bool(test_only),
            "effective": not test_only or (not settings.production and settings.allow_self_review)} if decision else {}),
        **proposal_retractions.compact(withdrawn_at)}
        for link, rev, record, decision, test_only, withdrawn_at in rows}


def submit_note(session, user, note, revision, body, now):
    from . import notebook, protocol_proposals as proposals
    commitment = notebook._hash({"note_id": note.id, "revision": body.revision, "content_sha256": body.content_sha256})
    replay = session.scalar(select(ProposalNoteLink).where(ProposalNoteLink.author_id == user.id,
        ProposalNoteLink.idempotency_key == str(body.idempotency_key)))
    if replay:
        if replay.request_sha256 != commitment or replay.origin != "notebook":
            raise HTTPException(409, "This submission identifier was already used for a different request.")
        record = session.get(proposals.ProtocolProposal, replay.proposal_id)
        frozen = session.get(proposals.ProtocolProposalRevision, replay.proposal_revision_id)
        return {"proposal": proposals._historical_proposal(session, record, frozen, user), "validation": proposals._validation()}
    if revision is None or revision.content_sha256 != body.content_sha256:
        raise HTTPException(409, "The note changed. Read its latest saved revision before submitting for scheme review.")
    existing = session.scalar(select(ProposalNoteLink).where(ProposalNoteLink.note_revision_id == revision.id))
    if existing:
        record = session.get(proposals.ProtocolProposal, existing.proposal_id)
        frozen = session.get(proposals.ProtocolProposalRevision, existing.proposal_revision_id)
        return {"proposal": proposals._historical_proposal(session, record, frozen, user), "validation": proposals._validation()}
    if note.latest_revision != body.revision:
        raise HTTPException(409, "The note changed. Read its latest saved revision before submitting for scheme review.")
    ext = revision.content.get("extensions")
    specification = ext.get(SPEC_FIELD) if isinstance(ext, dict) else None
    if not isinstance(specification, dict):
        raise HTTPException(422, "Attach a KEM specification to this note before requesting scheme review.")
    result = proposals._validated(specification)
    if isinstance(result, JSONResponse):
        return result
    specification, digest = result
    prior = session.scalar(select(ProposalNoteLink).where(ProposalNoteLink.note_id == note.id).limit(1))
    if prior:
        record = session.scalar(select(proposals.ProtocolProposal).where(proposals.ProtocolProposal.id == prior.proposal_id).with_for_update())
        proposal_retractions.ensure_active(session, proposal_retractions.SCHEME, record.id)
        number = record.revision + 1
        record.revision, record.status = number, "submitted"
        record.specification, record.content_sha256 = specification, digest
    else:
        number = 1
        record = proposals.ProtocolProposal(id=str(uuid4()), author_id=user.id, created_at=now,
            idempotency_key=str(uuid4()), revision=1, status="submitted", content_sha256=digest, specification=specification)
        session.add(record)
        session.flush()
    frozen = proposals.ProtocolProposalRevision(id=str(uuid4()), proposal_id=record.id, revision=number,
        created_at=now, idempotency_key=str(uuid4()), request_sha256=commitment,
        content_sha256=digest, specification=specification)
    session.add(frozen)
    session.flush()
    link_revision(session, record, frozen, note, revision, now, origin="notebook", key=str(body.idempotency_key), commitment=commitment)
    return {"proposal": proposals._proposal_data(record, session, user), "validation": proposals._validation()}
