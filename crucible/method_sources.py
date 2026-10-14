"""Bounded, inert source snapshots attached to immutable attack methods.

The service stores text and serves a download. It never imports, extracts, or
executes participant code. A snapshot must be attached before any final answer.
Visibility follows the existing method policy: owned drafts, then published
evidence when a consistent, verified recovery uses the method.
"""
import hashlib
import io
import json
import re
from typing import Literal
from uuid import UUID
import zipfile

from fastapi import HTTPException, Request, Response
from pydantic import Field, StrictStr, field_validator, model_validator
from sqlalchemy import Float, ForeignKey, Integer, JSON, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, Challenge, Method, Submission, User
from .schemas import Input

MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 1024 * 1024


class SourceArtifact(Base):
    __tablename__ = "source_artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle: Mapped[dict] = mapped_column(JSON)
    total_bytes: Mapped[int] = mapped_column(Integer)
    file_count: Mapped[int] = mapped_column(Integer)


class MethodSource(Base):
    __tablename__ = "method_sources"
    method_id: Mapped[str] = mapped_column(ForeignKey("methods.id"), primary_key=True)
    sha256: Mapped[str] = mapped_column(ForeignKey("source_artifacts.sha256"), index=True)
    created_at: Mapped[float] = mapped_column(Float)


class SourceFile(Input):
    path: StrictStr = Field(min_length=1, max_length=240)
    content: StrictStr = Field(max_length=MAX_FILE_BYTES)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        # Portable POSIX paths. Reject hidden files, Windows alternate streams,
        # device names, traversal, and ambiguous names before creating a ZIP.
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./ -]*", value):
            raise ValueError("Use a portable relative source path.")
        parts = value.split("/")
        if len(parts) > 12 or any(
            not part or part.startswith(".") or part.endswith((".", " "))
            or len(part) > 100
            or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
            for part in parts
        ):
            raise ValueError("Use unambiguous nonhidden source paths.")
        return value

    @field_validator("content")
    @classmethod
    def bounded_text(cls, value):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Source must be valid UTF-8 text.") from None
        if "\x00" in value or size > MAX_FILE_BYTES:
            raise ValueError("Each text file must be at most 256 KiB, without NUL bytes.")
        return value


class SourceBundle(Input):
    format: Literal["crucible-source-v1"]
    files: list[SourceFile] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_unique_files(self):
        paths = [item.path.casefold() for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Source paths must be unique, ignoring letter case.")
        # Also reject a file that is another file's parent directory.
        names = set(paths)
        if any("/".join(path.split("/")[:i]) in names
               for path in paths for i in range(1, len(path.split("/")))):
            raise ValueError("A source file cannot also be a directory.")
        if sum(len(item.content.encode("utf-8")) for item in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("The complete source snapshot must be at most 1 MiB.")
        return self


def canonical_bundle(body):
    bundle = {"format": body.format,
              "files": [item.model_dump() for item in sorted(body.files, key=lambda item: item.path)]}
    encoded = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return bundle, hashlib.sha256(encoded).hexdigest()


def source_descriptor(session, method_id):
    row = session.execute(select(MethodSource.sha256, SourceArtifact.file_count, SourceArtifact.total_bytes)
                          .join(SourceArtifact, SourceArtifact.sha256 == MethodSource.sha256)
                          .where(MethodSource.method_id == method_id)).first()
    if row is None:
        return None
    url = f"/api/methods/{method_id}/source"
    return {"sha256": row.sha256, "files": row.file_count,
            "total_bytes": row.total_bytes, "url": url, "download_url": url + "/download"}


def _is_published(session, method):
    return session.scalar(select(Submission.id).join(Challenge, Challenge.id == Submission.challenge_id).where(
        Submission.method_id == method.id, Submission.user_id == method.user_id,
        Submission.user_id == Challenge.user_id, Submission.profile_id == Challenge.profile_id,
        Submission.scheme_version == Challenge.scheme_version,
        Submission.round_id == Challenge.round_id, Challenge.correctness_passed.is_(True),
        Submission.received_at >= Challenge.issued_at, Submission.received_at < Challenge.ends_at,
        Submission.correct.is_(True)).limit(1)) is not None


def register_routes(app, db, user_for, clock, throttle):
    def readable(session, method_id, request):
        method = session.get(Method, str(method_id))
        if method is None:
            raise HTTPException(404, "Source snapshot not found.")
        if not _is_published(session, method):
            try:
                user = user_for(request, session)
            except HTTPException as error:
                if error.status_code != 401:
                    raise
                raise HTTPException(404, "Source snapshot not found.") from None
            if user.id != method.user_id:
                raise HTTPException(404, "Source snapshot not found.")
        link = session.get(MethodSource, method.id)
        if link is None:
            raise HTTPException(404, "Source snapshot not found.")
        return session.get(SourceArtifact, link.sha256)

    @app.post("/api/methods/{method_id}/source")
    def attach(method_id: UUID, body: SourceBundle, request: Request):
        throttle(request, "method-source", 30, 3600)
        bundle, digest = canonical_bundle(body)
        with db.transaction(write=True) as session:
            user = user_for(request, session, mutation=True)
            # Same first row lock as final answers: adding evidence cannot race a
            # submission and retroactively change what accompanied that answer.
            session.execute(select(User.id).where(User.id == user.id).with_for_update())
            method = session.scalar(select(Method).where(Method.id == str(method_id)).with_for_update())
            if method is None or method.user_id != user.id:
                raise HTTPException(404, "Method not found.")
            previous = session.get(MethodSource, method.id)
            if previous is not None:
                if previous.sha256 != digest:
                    raise HTTPException(409, "This method already has a different immutable source snapshot. Create a new method.")
                return {"source": source_descriptor(session, method.id)}
            if session.scalar(select(Submission.id).where(Submission.method_id == method.id).limit(1)):
                raise HTTPException(409, "Attach source before submitting a final answer. Create a new method for later work.")
            if session.get(SourceArtifact, digest) is None:
                try:
                    with session.begin_nested():
                        session.add(SourceArtifact(sha256=digest, bundle=bundle, file_count=len(body.files),
                                                   total_bytes=sum(len(item.content.encode("utf-8")) for item in body.files)))
                        session.flush()
                except IntegrityError:
                    # Another account can attach identical content concurrently.
                    if session.get(SourceArtifact, digest) is None:
                        raise
            session.add(MethodSource(method_id=method.id, sha256=digest, created_at=clock()))
            session.flush()
            return {"source": source_descriptor(session, method.id)}

    @app.get("/api/methods/{method_id}/source")
    def source(method_id: UUID, request: Request):
        with db.transaction() as session:
            return readable(session, method_id, request).bundle

    @app.get("/api/methods/{method_id}/source/download")
    def download(method_id: UUID, request: Request):
        with db.transaction() as session:
            artifact = readable(session, method_id, request)
            bundle, digest = artifact.bundle, artifact.sha256
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in bundle["files"]:
                info = zipfile.ZipInfo(item["path"])
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, item["content"].encode("utf-8"))
        return Response(output.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="crucible-source-{digest[:16]}.zip"',
            "Cache-Control": "no-store", "X-Source-SHA256": digest,
        })
