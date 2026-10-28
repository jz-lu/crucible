"""Schema-only preservation bridge for a resource-feature rollback.

These definitions match migrations 0026/0027. They register metadata only; this
candidate has no resource publisher, receiver, lifecycle handler or graph routes.
Stored measurements, original ZIPs and lifecycle evidence remain untouched.
"""
from sqlalchemy import BigInteger, Float, Integer, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class ResourcePublication(Base):
    __tablename__ = "resource_publications"
    __table_args__ = (UniqueConstraint("repository_id", "run_id", "run_attempt", "scheme_id", "version",
                                      name="uq_resource_publication_run"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scheme_id: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(80))
    repository_id: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[float] = mapped_column(Float)
    publication: Mapped[dict] = mapped_column(JSON)
    resources: Mapped[dict] = mapped_column(JSON)
    bundle: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)



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

