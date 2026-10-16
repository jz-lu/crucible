"""Bound generation work and retain a denominator without retaining secrets."""
from contextlib import contextmanager
import threading
import time
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


class GenerationLimiter:
    """Non-queueing admission for one app's expensive generation work.

    The account limit covers different schemes/profiles too. Neither rejection
    draws randomness nor opens a database transaction. This is process-local:
    deployments must also bound the number of app processes. Account identifiers
    are retained only while their slots are active and never enter snapshots.
    """

    def __init__(self, max_inflight=2, *, max_per_account=1):
        if type(max_inflight) is not int or max_inflight < 1:
            raise ValueError("Generation capacity must be a positive integer")
        if type(max_per_account) is not int or not 1 <= max_per_account <= max_inflight:
            raise ValueError("Per-account capacity must be between one and total capacity")
        self.max_inflight = max_inflight
        self.max_per_account = max_per_account
        self._lock = threading.Lock()
        self._accounts = {}
        self._inflight = self._peak = self._started = self._completed = self._failed = 0
        self._busy_capacity = self._busy_account = 0
        self._duration_seconds = self._max_duration_seconds = 0.0

    @contextmanager
    def slot(self, account_id):
        """Acquire immediately or raise GenerationBusy; release on every exit.

        Hold this only around generation, outside DB transactions. Timing covers
        the admitted block, not a request's authentication or commit. A completed
        block is not a claim of cryptographic correctness or successful issuance.
        """
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("Generation requires an account identifier")
        with self._lock:
            account_active = self._accounts.get(account_id, 0)
            if account_active >= self.max_per_account:
                self._busy_account += 1
                raise GenerationBusy("This account already has generation in progress")
            if self._inflight >= self.max_inflight:
                self._busy_capacity += 1
                raise GenerationBusy("Generation capacity is busy")
            started = time.monotonic()
            self._accounts[account_id] = account_active + 1
            self._inflight += 1
            self._started += 1
            self._peak = max(self._peak, self._inflight)
        failed = True
        try:
            yield
            failed = False
        finally:
            elapsed = max(0.0, time.monotonic() - started)
            with self._lock:
                remaining = self._accounts[account_id] - 1
                if remaining:
                    self._accounts[account_id] = remaining
                else:
                    del self._accounts[account_id]
                self._inflight -= 1
                self._completed += 1
                self._failed += int(failed)
                self._duration_seconds += elapsed
                self._max_duration_seconds = max(self._max_duration_seconds, elapsed)

    def snapshot(self):
        """Aggregate process-local counters since startup; no user data.

        `completed` includes blocks that raised, recorded separately as `failed`.
        Durations include completed blocks only; counters reset on app restart.
        """
        with self._lock:
            return {
                "max_inflight": self.max_inflight,
                "max_per_account": self.max_per_account,
                "inflight": self._inflight,
                "peak_inflight": self._peak,
                "started": self._started,
                "completed": self._completed,
                "failed": self._failed,
                "busy_capacity": self._busy_capacity,
                "busy_account": self._busy_account,
                "duration_seconds": self._duration_seconds,
                "max_duration_seconds": self._max_duration_seconds,
            }


# Compatibility for callers of the original helper. The app uses its own
# GenerationLimiter so separate app instances do not share this legacy quota.
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
