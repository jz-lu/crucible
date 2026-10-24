"""Explicit operator-reviewed builds of an unchanged scheme release.

An AdapterVersion is the original immutable release anchor. This additive
ledger allows another *reviewed* source/artifact pair without rewriting that
anchor or any challenge/receipt. Merely observing a new pair never trusts it.

The exact JSON manifest bytes are retained. They bind the release, original
pins, candidate pins, platform, build recipe and review evidence. This validates
provenance bindings, not mathematical equivalence or the quality of a review.
Only a trusted host operator may register/revoke builds; there is no HTTP API.
Run ``python -m crucible.implementation_builds --help`` for the operator CLI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from sqlalchemy import Float, ForeignKeyConstraint, String, Text, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column

from .db import AdapterVersion, Base, Database

FORMAT = "crucible-implementation-build-v1"
ADMISSION_FORMAT = "crucible-platform-admission-v1"
MAX_MANIFEST_BYTES = 256 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class ImplementationBuild(Base):
    __tablename__ = "implementation_builds"
    __table_args__ = (ForeignKeyConstraint(
        ["scheme_id", "version"], ["adapter_versions.scheme_id", "adapter_versions.version"],
        name="fk_implementation_build_release"),)
    # The non-null identity digest also makes pairs with a null artifact unique
    # consistently on SQLite and PostgreSQL (unlike a nullable UNIQUE tuple).
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scheme_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(32))
    source_sha256: Mapped[str] = mapped_column(String(64))
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline_source_sha256: Mapped[str] = mapped_column(String(64))
    baseline_artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    manifest_text: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(String(200))
    review_basis: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[float] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


def _text(value, label, maximum):
    if type(value) is not str or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"Invalid {label}.")
    return value


def _digest(value, label):
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError(f"Invalid {label}; use a lowercase SHA-256 digest.")
    return value


def _identifier(value, label, maximum):
    _text(value, label, maximum)
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}.")
    return value


def _fields(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError(f"Invalid {label} fields.")


def _pair(value, label):
    _fields(value, {"source_sha256", "artifact_sha256"}, label)
    source = _digest(value["source_sha256"], label + " source")
    artifact = value["artifact_sha256"]
    if artifact is not None:
        _digest(artifact, label + " artifact")
    return source, artifact


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not allowed.")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Nonfinite JSON constants are not allowed.")


def parse_manifest(raw: bytes, expected_sha256: str) -> dict:
    """Validate exact bytes supplied for review, without executing any content."""
    _digest(expected_sha256, "manifest digest")
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise ValueError("Build manifest must contain 1..262144 UTF-8 bytes.")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Build manifest digest does not match the reviewed bytes.")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("Invalid UTF-8 JSON build manifest.") from None
    _fields(manifest, {"format", "scheme_id", "scheme_version", "baseline", "implementation",
                       "platform", "build", "evidence"}, "build manifest")
    if manifest["format"] != FORMAT:
        raise ValueError("Unsupported build manifest format.")
    _identifier(manifest["scheme_id"], "scheme ID", 100)
    _identifier(manifest["scheme_version"], "scheme version", 32)
    _pair(manifest["baseline"], "baseline implementation")
    _pair(manifest["implementation"], "candidate implementation")
    _fields(manifest["platform"], {"system", "machine"}, "platform")
    for name in ("system", "machine"):
        _text(manifest["platform"][name], "platform " + name, 100)
    _fields(manifest["build"], {"recipe_sha256", "toolchain"}, "build")
    _digest(manifest["build"]["recipe_sha256"], "build recipe digest")
    _text(manifest["build"]["toolchain"], "build toolchain", 4096)
    evidence = manifest["evidence"]
    if type(evidence) is not list or not 1 <= len(evidence) <= 32:
        raise ValueError("Build review requires 1..32 evidence identities.")
    names = set()
    for item in evidence:
        _fields(item, {"name", "sha256"}, "evidence")
        name = _text(item["name"], "evidence name", 200)
        if name in names:
            raise ValueError("Build evidence names must be distinct.")
        names.add(name)
        _digest(item["sha256"], "evidence digest")
    return manifest


def build_identity(scheme_id, version, source_sha256, artifact_sha256):
    """Stable identity for one release-specific source/artifact pair."""
    _identifier(scheme_id, "scheme ID", 100)
    _identifier(version, "scheme version", 32)
    _pair({"source_sha256": source_sha256, "artifact_sha256": artifact_sha256}, "implementation")
    return hashlib.sha256(json.dumps([scheme_id, version, source_sha256, artifact_sha256],
                                     separators=(",", ":")).encode()).hexdigest()


def additional_platform_admission(manifest):
    """Validate the runtime-only marker; absence retains original pin policy.

    This marker cannot grant admission. It requires an existing original pin
    and a separately reviewed build on the named platform. Unknown or malformed
    declarations must not fall back to original-build initialization.
    """
    if type(manifest) is not dict or type(manifest.get("implementation")) is not dict:
        raise ValueError("Invalid runtime implementation.")
    implementation = manifest["implementation"]
    if "admission" not in implementation:
        return None
    admission = implementation["admission"]
    _fields(admission, {"format", "kind", "platform"}, "platform admission")
    if (admission["format"] != ADMISSION_FORMAT
            or admission["kind"] != "reviewed-additional-platform"):
        raise ValueError("Unsupported platform admission policy.")
    platform = admission["platform"]
    _fields(platform, {"system", "machine"}, "admission platform")
    for name in ("system", "machine"):
        _identifier(platform[name], "admission platform " + name, 100)
    return dict(platform)


def _timestamp(now):
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError("Invalid review timestamp.")
    return float(now)


def register_build(session, raw: bytes, *, expected_sha256: str, reviewed_by: str,
                   review_basis: str, now: float) -> ImplementationBuild:
    """Append an explicit trusted-operator review in the caller's transaction.

    The caller must hold database-operator authority. No participant identity or
    submitted file can cause this function to run through a web request. The
    operator's review must establish unchanged algorithms, distributions,
    parameters, encodings, correctness semantics and KDF; hashes cannot do so.
    An existing baseline is mandatory. Re-registering exact bytes is idempotent
    and retains the original review; changed evidence cannot overwrite it.
    """
    manifest = parse_manifest(raw, expected_sha256)
    _text(reviewed_by, "reviewer identity", 200)
    _text(review_basis, "review basis", 10000)
    now = _timestamp(now)
    identity = manifest["scheme_id"], manifest["scheme_version"]
    # Serialize approvals for a release on PostgreSQL; the CLI also uses the
    # existing BEGIN IMMEDIATE transaction convention on SQLite.
    baseline = session.get(AdapterVersion, identity, with_for_update=True)
    if baseline is None:
        raise ValueError("Registering a build requires an existing release baseline; no baseline was created.")
    original = baseline.source_sha256, baseline.artifact_sha256
    if original != _pair(manifest["baseline"], "baseline implementation"):
        raise ValueError("Build review is anchored to a different original release implementation.")
    candidate = _pair(manifest["implementation"], "candidate implementation")
    if candidate == original:
        raise ValueError("The original release implementation does not need another build registration.")
    identifier = build_identity(*identity, *candidate)
    existing = session.get(ImplementationBuild, identifier)
    if existing is not None:
        if existing.revoked_at is not None:
            raise ValueError("This build registration was revoked and cannot be reactivated.")
        if existing.manifest_sha256 != expected_sha256 or existing.manifest_text.encode() != raw:
            raise ValueError("This implementation already has a different retained review manifest.")
        return existing
    result = ImplementationBuild(id=identifier, scheme_id=identity[0], version=identity[1],
        source_sha256=candidate[0], artifact_sha256=candidate[1],
        baseline_source_sha256=original[0], baseline_artifact_sha256=original[1],
        manifest_sha256=expected_sha256, manifest_text=raw.decode("utf-8"),
        reviewed_by=reviewed_by, review_basis=review_basis, reviewed_at=now,
        revoked_at=None, revocation_reason=None)
    session.add(result)
    session.flush()
    return result


def revoke_build(session, identifier: str, *, reason: str, now: float) -> ImplementationBuild:
    """Pause future use, retaining the approval and all historical evidence."""
    _digest(identifier, "build identity")
    _text(reason, "revocation reason", 10000)
    now = _timestamp(now)
    result = session.get(ImplementationBuild, identifier, with_for_update=True)
    if result is None:
        raise ValueError("Reviewed build not found.")
    if result.revoked_at is None:
        result.revoked_at, result.revocation_reason = now, reason
        session.flush()
    return result


def reviewed_build_for(session, manifest: dict, baseline: AdapterVersion | None = None):
    """Return a valid active additional build for the exact runtime manifest.

    Missing, malformed, different, or revoked identities never match. Artifact
    verification still belongs to the native adapter; this is only the release
    pin compatibility check and must not replace verification of actual files.
    """
    try:
        platform = additional_platform_admission(manifest)
        identity = manifest["scheme_id"], manifest["version"]
        candidate = manifest["implementation"]["source_sha256"], manifest["implementation"].get("artifact_sha256")
        identifier = build_identity(*identity, *candidate)
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
    baseline = baseline if baseline is not None else session.get(AdapterVersion, identity)
    if baseline is None or (baseline.scheme_id, baseline.version) != identity:
        return None
    result = session.get(ImplementationBuild, identifier)
    if result is None or result.revoked_at is not None:
        return None
    original = baseline.source_sha256, baseline.artifact_sha256
    if ((result.scheme_id, result.version) != identity
            or (result.source_sha256, result.artifact_sha256) != candidate
            or (result.baseline_source_sha256, result.baseline_artifact_sha256) != original):
        return None
    try:
        reviewed = parse_manifest(result.manifest_text.encode("utf-8"), result.manifest_sha256)
    except (ValueError, AttributeError, UnicodeError):
        return None
    if ((reviewed["scheme_id"], reviewed["scheme_version"]) != identity
            or _pair(reviewed["baseline"], "baseline") != original
            or _pair(reviewed["implementation"], "candidate") != candidate
            or (platform is not None and reviewed["platform"] != platform)):
        return None
    return result


def implementation_is_compatible(session, manifest: dict, baseline: AdapterVersion | None = None) -> bool:
    """Require explicit review for marked platforms, even for an original pair."""
    try:
        platform = additional_platform_admission(manifest)
        identity = manifest["scheme_id"], manifest["version"]
        candidate = manifest["implementation"]["source_sha256"], manifest["implementation"].get("artifact_sha256")
        build_identity(*identity, *candidate)
    except (KeyError, TypeError, AttributeError, ValueError):
        return False
    baseline = baseline if baseline is not None else session.get(AdapterVersion, identity)
    if baseline is None or (baseline.scheme_id, baseline.version) != identity:
        return False
    if platform is None and candidate == (baseline.source_sha256, baseline.artifact_sha256):
        return True
    return reviewed_build_for(session, manifest, baseline) is not None


def _summary(row):
    return {"build_id": row.id, "scheme_id": row.scheme_id, "scheme_version": row.version,
            "manifest_sha256": row.manifest_sha256, "status": "revoked" if row.revoked_at is not None else "reviewed",
            "reviewed_by": row.reviewed_by, "reviewed_at": row.reviewed_at}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("validate", "register"):
        command = commands.add_parser(name)
        command.add_argument("manifest", type=Path)
        command.add_argument("--manifest-sha256", required=True,
                             help="Digest of the exact manifest bytes actually reviewed")
        if name == "register":
            command.add_argument("--reviewed-by", required=True, help="Trusted operator/reviewer identity for the audit record")
            command.add_argument("--review-basis", required=True, help="Why the build preserves this exact scheme release")
    revoke = commands.add_parser("revoke")
    revoke.add_argument("build_id")
    revoke.add_argument("--reason", required=True)
    commands.add_parser("list")
    args = parser.parse_args(argv)
    db = None
    try:
        if args.action in {"validate", "register"}:
            with args.manifest.open("rb") as stream:
                raw = stream.read(MAX_MANIFEST_BYTES + 1)
            manifest = parse_manifest(raw, args.manifest_sha256)
            if args.action == "validate":
                print(json.dumps({"format": FORMAT, "manifest_sha256": args.manifest_sha256,
                                  "scheme_id": manifest["scheme_id"], "scheme_version": manifest["scheme_version"],
                                  "structure_valid": True, "build_approved": False}))
                return 0
        # Never fall back to the development database or create a secret/schema.
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise ValueError("Set DATABASE_URL explicitly for the trusted operator command.")
        db = Database(url)
        with db.transaction(write=args.action != "list") as session:
            if args.action == "register":
                result = _summary(register_build(session, raw, expected_sha256=args.manifest_sha256,
                    reviewed_by=args.reviewed_by, review_basis=args.review_basis, now=time.time()))
            elif args.action == "revoke":
                result = _summary(revoke_build(session, args.build_id, reason=args.reason, now=time.time()))
            else:
                result = [_summary(row) for row in session.scalars(select(ImplementationBuild).order_by(
                    ImplementationBuild.scheme_id, ImplementationBuild.version, ImplementationBuild.reviewed_at))]
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as error:
        # Validation messages include no manifest contents, database URLs or
        # credential-bearing driver error strings. File paths need not be logged.
        print(str(error) if isinstance(error, ValueError) else "Unable to read the build manifest.", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Build ledger database operation failed; check connection, schema and operator permissions.", file=sys.stderr)
        return 1
    finally:
        if db is not None:
            db.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
