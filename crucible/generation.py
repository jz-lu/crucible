"""Bound generation work and retain a denominator without retaining secrets."""
from contextlib import contextmanager
import threading
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class GenerationAttempt(Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (Index("ix_generation_profile", "scheme_id", "scheme_version", "profile_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scheme_id: Mapped[str] = mapped_column(String(100))
    scheme_version: Mapped[str] = mapped_column(String(32))
    profile_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    round_id: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[float] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), default="started")
    disposition: Mapped[str] = mapped_column(String(40), default="pending")
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)


class GenerationBusy(RuntimeError):
    pass


# Two in-flight native workers per service process. Production must additionally
# bound worker/process counts at the deployment layer; this is not a global
# distributed quota. Saturation does not draw randomness or create an attempt.
_slots = threading.BoundedSemaphore(2)
_instances = set()
_instance_lock = threading.Lock()


@contextmanager
def instance_reservation(key):
    """Avoid duplicate work in one app process, through the durable commit.

    Callers include their database/app identity in the key. This is not a
    distributed lock: final database checks still arbitrate across processes.
    Duplicates never wait while occupying a request thread or native slot.
    """
    with _instance_lock:
        if key in _instances:
            raise GenerationBusy("This instance is already being generated")
        _instances.add(key)
    try:
        yield
    finally:
        with _instance_lock:
            _instances.remove(key)


@contextmanager
def generation_slot():
    if not _slots.acquire(blocking=False):
        raise GenerationBusy("Generation capacity is busy")
    try:
        yield
    finally:
        _slots.release()


def start_attempt(db, manifest, user_id, round_id, now):
    identifier = str(uuid4())
    with db.transaction(write=True) as session:
        session.add(GenerationAttempt(id=identifier, scheme_id=manifest["scheme_id"],
            scheme_version=manifest["version"], profile_id=manifest["id"], user_id=user_id,
            round_id=round_id, started_at=now,
            artifact_sha256=manifest.get("implementation", {}).get("artifact_sha256")))
    return identifier


def finish_attempt(db, identifier, now, outcome, disposition):
    with db.transaction(write=True) as session:
        row = session.get(GenerationAttempt, identifier)
        row.finished_at, row.outcome, row.disposition = now, outcome, disposition
