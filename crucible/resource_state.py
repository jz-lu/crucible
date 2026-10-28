"""Append-only desired resource bindings and authenticated lifecycle evidence.

A source commit is provenance. Semantic fingerprints select compatible reports;
new activations distinguish changes, removals, re-additions and explicit rollbacks.
"""
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import BigInteger, Float, Integer, JSON, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

SHA = re.compile(r"[0-9a-f]{64}\Z")
PHASES = {"queued", "failed", "cancelled", "timed_out"}
REASONS = {"scheduled", "measurement_failed", "measurement_timeout", "measurement_cancelled",
           "measurement_incomplete", "artifact_unavailable", "archive_audit_failed", "archive_capacity", "publication_failed"}
MAX_CONTROL = 2 * 1024 * 1024


class ResourceActivation(Base):
    __tablename__ = "resource_activations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scheme_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64))
    producer_sha256: Mapped[str] = mapped_column(String(64))
    binding: Mapped[dict] = mapped_column(JSON)
    source: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float)


class ResourceEvent(Base):
    __tablename__ = "resource_events"
    __table_args__ = (UniqueConstraint("repository_id", "run_id", "run_attempt", "kind", "scheme_id", "version",
                                      name="uq_resource_event_run"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository_id: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    scheme_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(80))
    activation_id: Mapped[str] = mapped_column(String(64), index=True)
    received_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def require(ok, message="Invalid resource state document"):
    if not ok:
        raise ValueError(message)


def order(source):
    return int(source["run_id"]), int(source["run_attempt"])


def lock(session):
    # One narrowly scoped transaction lock serializes full-catalogue transitions.
    # SQLite's existing BEGIN IMMEDIATE supplies the equivalent write boundary.
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(1129469266, 20261028)"))


def latest_snapshot(session):
    return session.scalar(select(ResourceEvent).where(ResourceEvent.kind == "desired")
                          .order_by(ResourceEvent.run_id.desc(), ResourceEvent.run_attempt.desc()).limit(1))


def desired(session, scheme, version):
    snapshot = latest_snapshot(session)
    entry = next((x for x in snapshot.payload["bindings"] if x["scheme_id"] == scheme and x["version"] == version), None) if snapshot else None
    return session.get(ResourceActivation, entry["activation_id"]) if entry else None


def expected_document(activation):
    return {"activation_id": activation.id, "fingerprint": activation.fingerprint,
            "producer_sha256": activation.producer_sha256}


def validate_expected(expected):
    require(type(expected) is dict and set(expected) == {"activation_id", "fingerprint", "producer_sha256"})
    require(all(type(x) is str and SHA.fullmatch(x) for x in expected.values()))


def check_activation(session, expected, scheme, version, source, binding=None):
    validate_expected(expected)
    activation = session.get(ResourceActivation, expected["activation_id"])
    require(activation is not None and expected_document(activation) == expected
            and activation.scheme_id == scheme and activation.version == version
            and order(source) >= order(activation.source), "Unknown or incompatible resource activation")
    if binding is not None:
        require(activation.binding == binding, "Publication differs from its activated binding")
    return activation


def event_query(identity, kind, scheme="", version=""):
    return select(ResourceEvent).where(ResourceEvent.repository_id == identity["repository_id"],
        ResourceEvent.run_id == int(identity["run_id"]), ResourceEvent.run_attempt == int(identity["run_attempt"]),
        ResourceEvent.kind == kind, ResourceEvent.scheme_id == scheme, ResourceEvent.version == version)


def receipt(session, row):
    if row.kind == "desired":
        current = latest_snapshot(session)
        return {"snapshot_id": row.id, "current": current.id == row.id, "bindings": row.payload["bindings"]}
    active = desired(session, row.scheme_id, row.version)
    return {"event_id": row.id, "current": bool(active and active.id == row.activation_id), "phase": row.kind}


def ingest_control(db, document, identity, now, validate_binding):
    try:
        require(type(document) is dict and document.get("source") == identity)
        kind = document.get("format")
        if kind == "crucible-resource-desired-v1":
            require(set(document) == {"format", "source", "bindings"})
            entries = document["bindings"]
            require(type(entries) is list and len(entries) <= 100)
            seen = set()
            for entry in entries:
                require(type(entry) is dict and set(entry) == {"binding", "fingerprint", "producer_sha256"})
                validate_binding(entry["binding"])
                require(type(entry["producer_sha256"]) is str and SHA.fullmatch(entry["producer_sha256"]))
                require(entry["fingerprint"] == digest({"binding": entry["binding"], "producer": entry["producer_sha256"]}))
                key = entry["binding"]["scheme_id"], entry["binding"]["version"]
                require(key not in seen); seen.add(key)
            event_kind, scheme, version = "desired", "", ""
        else:
            require(kind == "crucible-resource-event-v1" and set(document) ==
                    {"format", "source", "scheme_id", "version", "expected", "phase", "reason"})
            require(document["phase"] in PHASES)
            require(document["reason"] in REASONS and
                    (document["phase"] == "queued") == (document["reason"] == "scheduled"))
            validate_expected(document["expected"])
            event_kind, scheme, version = document["phase"], document["scheme_id"], document["version"]
            require(type(scheme) is str and 0 < len(scheme) <= 100 and type(version) is str and 0 < len(version) <= 80)
        identifier = digest(document)
        with db.transaction(write=True) as session:
            lock(session)
            previous = session.scalar(event_query(identity, event_kind, scheme, version))
            if previous:
                if previous.id != identifier:
                    raise HTTPException(409, "This workflow already declared different resource state.")
                return receipt(session, previous)
            if event_kind == "desired":
                old = latest_snapshot(session)
                if old and order(identity) <= (old.run_id, old.run_attempt):
                    raise HTTPException(409, "A newer desired resource snapshot is already recorded.")
                active = {(x["scheme_id"], x["version"]): x for x in old.payload["bindings"]} if old else {}
                bindings = []
                for entry in entries:
                    binding = entry["binding"]; key = binding["scheme_id"], binding["version"]
                    prior = active.get(key)
                    if prior and prior["fingerprint"] == entry["fingerprint"]:
                        activation = session.get(ResourceActivation, prior["activation_id"])
                    else:
                        aid = digest({"format": "crucible-resource-activation-v1", "snapshot_id": identifier,
                                      "scheme_id": key[0], "version": key[1], "fingerprint": entry["fingerprint"]})
                        activation = ResourceActivation(id=aid, scheme_id=key[0], version=key[1], fingerprint=entry["fingerprint"],
                            producer_sha256=entry["producer_sha256"], binding=binding, source=identity, created_at=now)
                        session.add(activation)
                    bindings.append({"scheme_id": key[0], "version": key[1], **expected_document(activation)})
                payload = {"document": document, "bindings": bindings}
                activation_id = ""
            else:
                activation = check_activation(session, document["expected"], scheme, version, identity)
                terminal = session.scalar(select(ResourceEvent).where(ResourceEvent.repository_id == identity["repository_id"],
                    ResourceEvent.run_id == int(identity["run_id"]), ResourceEvent.run_attempt == int(identity["run_attempt"]),
                    ResourceEvent.scheme_id == scheme, ResourceEvent.version == version,
                    ResourceEvent.kind.in_(PHASES - {"queued"})))
                if terminal:
                    raise HTTPException(409, "This resource attempt already has a terminal lifecycle event.")
                payload = document; activation_id = activation.id
            row = ResourceEvent(id=identifier, repository_id=identity["repository_id"], run_id=int(identity["run_id"]),
                run_attempt=int(identity["run_attempt"]), kind=event_kind, scheme_id=scheme, version=version,
                activation_id=activation_id, received_at=now, payload=payload)
            session.add(row); session.flush()
            return receipt(session, row)
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise HTTPException(422, "Resource state failed its identity or binding checks.") from None


def latest_lifecycle(session, activation):
    if activation is None:
        return None
    rows = session.scalars(select(ResourceEvent).where(ResourceEvent.activation_id == activation.id,
        ResourceEvent.kind.in_(PHASES)).order_by(ResourceEvent.run_id.desc(), ResourceEvent.run_attempt.desc(),
                                               ResourceEvent.received_at.desc()).limit(4)).all()
    if not rows:
        return None
    current = [row for row in rows if (row.run_id, row.run_attempt) == (rows[0].run_id, rows[0].run_attempt)]
    row = next((r for r in current if r.kind != "queued"), current[0])
    return {"phase": row.kind, "reason": row.payload["reason"], "source": row.payload["source"], "received_at": row.received_at}
