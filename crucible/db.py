"""Relational constraints remain authoritative across processes and retries."""
from contextlib import contextmanager
from sqlalchemy import (Boolean, Float, ForeignKey, Index, Integer, JSON, String, Text,
                        UniqueConstraint, create_engine, event, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    handle: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class Method(Base):
    __tablename__ = "methods"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    pseudocode: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (UniqueConstraint("user_id", "profile_id", "scheme_version", "round_id", name="uq_challenge_round"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scheme_id: Mapped[str] = mapped_column(String(100))
    scheme_version: Mapped[str] = mapped_column(String(32))
    profile_id: Mapped[str] = mapped_column(String(100))
    round_id: Mapped[int] = mapped_column(Integer)
    issued_at: Mapped[float] = mapped_column(Float)
    starts_at: Mapped[int] = mapped_column(Integer)
    ends_at: Mapped[int] = mapped_column(Integer)
    public_data: Mapped[dict] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    target_mac: Mapped[str] = mapped_column(String(64))
    correctness_passed: Mapped[bool] = mapped_column(Boolean)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("challenge_id", name="uq_submission_challenge"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_submission_idempotency"),
        Index("ix_result_profile_time", "profile_id", "scheme_version", "correct", "received_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    challenge_id: Mapped[str] = mapped_column(ForeignKey("challenges.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    method_id: Mapped[str] = mapped_column(ForeignKey("methods.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100))
    scheme_version: Mapped[str] = mapped_column(String(32))
    round_id: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    payload_mac: Mapped[str] = mapped_column(String(64))
    correct: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[float] = mapped_column(Float)
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    compute: Mapped[dict] = mapped_column(JSON)
    ai_model: Mapped[str | None] = mapped_column(String(300), nullable=True)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    bucket: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class ProfileIncident(Base):
    __tablename__ = "profile_incidents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    scheme_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float)
    diagnostics: Mapped[dict] = mapped_column(JSON)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class AdapterVersion(Base):
    __tablename__ = "adapter_versions"
    scheme_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class Database:
    def __init__(self, url):
        self.sqlite = url.startswith("sqlite")
        options = {"connect_args": {"check_same_thread": False, "timeout": 15}} if self.sqlite else {"pool_pre_ping": True}
        self.engine = create_engine(url, **options)
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=15000")
                cursor.close()
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self, *, write=False):
        with self.sessions() as session:
            # Avoid SQLite read-to-write upgrade races. PostgreSQL uses row locks
            # and unique constraints at the individual mutation sites instead.
            if write and self.sqlite:
                session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
