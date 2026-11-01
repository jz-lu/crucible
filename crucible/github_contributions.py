"""Owner-requested, inert export of a verified challenge contribution.

This endpoint has no GitHub credential and performs no remote write. Source
files remain behind their existing methods:read endpoint; submission readers do
not gain new permission to download code or private challenge inputs.
"""
import hashlib
import json
import re
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .db import Challenge, Method, Submission
from .github_auth import GitHubIdentity
from .method_sources import source_descriptor
from .resource_identity import REPOSITORY

FORMAT = "crucible-github-contribution-v1"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def component(value):
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", value)
            or value in {".", ".."}):
        raise HTTPException(503, "This release cannot be exported as a contribution.")
    return value


def export(session, user, identifier):
    row = session.execute(select(Submission, Challenge, Method)
        .join(Challenge, Challenge.id == Submission.challenge_id)
        .join(Method, Method.id == Submission.method_id)
        .where(Submission.id == str(identifier), Submission.user_id == user.id,
               Submission.user_id == Challenge.user_id, Submission.user_id == Method.user_id,
               Submission.profile_id == Challenge.profile_id,
               Submission.scheme_version == Challenge.scheme_version,
               Submission.round_id == Challenge.round_id)).first()
    if row is None:
        raise HTTPException(404, "Your submission was not found.")
    result, challenge, method = row
    if (not result.correct or not challenge.correctness_passed or
            not challenge.issued_at <= result.received_at < challenge.ends_at):
        raise HTTPException(409, "Only an on-time, verified recovery can be exported as a contribution.")
    scheme, release = component(challenge.scheme_id), component(challenge.scheme_version)
    identity = session.scalar(select(GitHubIdentity).where(GitHubIdentity.user_id == user.id))
    source = source_descriptor(session, method.id)
    manifest = challenge.manifest if isinstance(challenge.manifest, dict) else {}
    record = {
        "format": "crucible-recovery-evidence-v1", "submission_id": result.id, "challenge_id": challenge.id,
        "receipt_path": f"/api/submissions/{result.id}", "crucible_handle": user.handle,
        "scheme_id": scheme, "scheme_version": release, "profile_id": challenge.profile_id,
        "manifest_sha256": digest(manifest), "received_at": result.received_at,
        "elapsed_seconds": result.elapsed_seconds, "status": "correct",
        "verification": {"key_recovery": "server-verified", "method": "contributor-reported",
                         "compute": "contributor-reported"},
        "method": {"id": method.id, "name": method.name, "pseudocode": method.pseudocode,
                   "source_sha256": source["sha256"] if source else None},
        "compute": {key: result.compute[key] for key in ("hardware", "workers", "wall_seconds",
                    "precomputation", "peak_memory", "notes") if isinstance(result.compute, dict) and key in result.compute},
        "ai_model": result.ai_model or "",
    }
    readme = (f"# Verified Crucible challenge recovery\n\n"
              f"- Scheme: `{scheme}`\n- Release: `{release}`\n"
              f"- Submission: `{result.id}`\n\n"
              "The server verified a complete recovered key for this exact instance before its deadline.\n"
              "This is not verification of the submitted attack code, compute report, or broader security claims.\n"
              "See receipt.json for exact configuration, method, source commitment, and provenance.\n"
              "The recovered key, authentication material, and private server state are omitted.\n"
              "Source files, when included, are inert evidence and must be reviewed before execution.\n")
    files = [{"path": "README.md", "content": readme},
             {"path": "receipt.json", "content": json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"}]
    value = {"format": FORMAT, "submission_id": result.id, "owner": {"handle": user.handle},
             "github_identity": {"id": identity.github_user_id, "login": identity.github_login} if identity else None,
             "target": {"repository": REPOSITORY, "base": "main",
                        "directory": f"contributions/{scheme}/{release}/{result.id}"},
             "challenge": {"id": challenge.id, "download_url": f"/api/challenges/{challenge.id}/download",
                           "public_sha256": digest(challenge.public_data), "manifest_sha256": digest(manifest)},
             "source": source, "files": files}
    return {**value, "sha256": digest(value)}


def register_routes(app, db, user_for, throttle):
    @app.get("/api/submissions/{submission_id}/github-export")
    def contribution(submission_id: UUID, request: Request):
        throttle(request, "github-export-ip", 300, 3600)
        with db.transaction() as session:
            user = user_for(request, session)
            value = export(session, user, submission_id)
        return JSONResponse(value, headers={"Cache-Control": "no-store"})
