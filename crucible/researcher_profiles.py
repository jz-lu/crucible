"""Optional, self-declared member profiles; never an identity or score authority."""
import hashlib
import ipaddress
import json
import re
import unicodedata
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import CheckConstraint, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .schemas import Input

FIELDS = ("display_name", "affiliation", "position", "website")


class ResearcherProfile(Base):
    __tablename__ = "researcher_profiles"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_researcher_profile_revision"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    affiliation: Mapped[str] = mapped_column(String(200))
    position: Mapped[str] = mapped_column(String(150))
    website: Mapped[str] = mapped_column(String(2048))
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[float] = mapped_column(Float)
    last_idempotency_key: Mapped[str] = mapped_column(String(36))
    last_request_sha256: Mapped[str] = mapped_column(String(64))


class ProfileUpdate(Input):
    display_name: str = Field(max_length=100, strict=True)
    affiliation: str = Field(max_length=200, strict=True)
    position: str = Field(max_length=150, strict=True)
    website: str = Field(max_length=2048, strict=True)
    expected_revision: int = Field(ge=0, le=2_147_483_646, strict=True)
    idempotency_key: UUID

    @field_validator(*FIELDS)
    @classmethod
    def plain_text(cls, value):
        if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in value):
            raise ValueError("Use a single line of plain text.")
        return value.strip()

    @field_validator("website")
    @classmethod
    def http_website(cls, value):
        if not value:
            return value
        if "\\" in value or any(char.isspace() for char in value):
            raise ValueError("Use an HTTP or HTTPS website without credentials.")
        try:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.netloc or not url.hostname or url.username is not None or url.password is not None:
                raise ValueError
            _ = url.port  # Reject invalid or out-of-range ports.
            if ":" in url.hostname:
                ipaddress.IPv6Address(url.hostname)
            else:
                host = url.hostname.encode("idna").decode("ascii").rstrip(".")
                if len(host) > 253 or any(not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                          for label in host.split(".")):
                    raise ValueError
        except (ValueError, UnicodeError):
            raise ValueError("Use an HTTP or HTTPS website without credentials.") from None
        return value


def profile_payload(record):
    return {**{name: getattr(record, name) if record else "" for name in FIELDS},
            "revision": record.revision if record else 0,
            "updated_at": record.updated_at if record else None}


def profile_for(session, user_id):
    return profile_payload(session.get(ResearcherProfile, user_id))


def _conflict(code):
    message = ("This profile changed. Reload it before saving your draft."
               if code == "profile_revision_conflict" else
               "This save key was already used for a different profile update.")
    return JSONResponse({"detail": message, "code": code}, status_code=409)


def register_routes(app, db, clock, user_for, envelope, throttle):
    def browser_only(request):
        if "authorization" in request.headers:
            raise HTTPException(403, "Agent credentials cannot manage browser profiles.")

    @app.get("/api/me/profile")
    def own_profile(request: Request):
        browser_only(request)
        with db.transaction() as session:
            user = user_for(request, session)
            profile = profile_for(session, user.id)
        return envelope(profile=profile)

    @app.put("/api/me/profile")
    def update_profile(body: ProfileUpdate, request: Request):
        browser_only(request)
        throttle(request, "researcher-profile-write", 60, 3600)
        values = {name: getattr(body, name) for name in FIELDS}
        digest = hashlib.sha256(json.dumps({**values, "expected_revision": body.expected_revision},
                                          ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = str(body.idempotency_key)
        with db.transaction(write=True) as session:
            # user_for locks User, then rechecks the browser credential, CSRF,
            # suspension and membership. Concurrent profile saves and access
            # revocation share that account lock; SQLite uses BEGIN IMMEDIATE.
            user = user_for(request, session, mutation=True)
            record = session.get(ResearcherProfile, user.id)
            if record and record.last_idempotency_key == key:
                if record.last_request_sha256 != digest:
                    return _conflict("profile_idempotency_conflict")
                return envelope(profile=profile_payload(record))
            if body.expected_revision != (record.revision if record else 0):
                return _conflict("profile_revision_conflict")
            if record is None:
                record = ResearcherProfile(user_id=user.id)
                session.add(record)
            for name, value in values.items():
                setattr(record, name, value)
            record.revision = body.expected_revision + 1
            record.updated_at = clock()
            record.last_idempotency_key = key
            record.last_request_sha256 = digest
            profile = profile_payload(record)
        return envelope(profile=profile)
