"""Immutable measured-resource releases, linked to existing scheme profiles.

Only the pinned main-branch GitHub workflow can publish. Uploaded files are data:
we validate their inventory and identities and never import or run their code.
The clean producer job performs the numeric receipt audit. Authentication of that
job is provenance, not independent scientific reproduction or scheme approval.
"""
import asyncio
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import re
import stat
import zipfile

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import BigInteger, Float, Integer, JSON, LargeBinary, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

from . import registry, resource_identity
from .db import Base
from .public_json import catalogue_response

MAX_UPLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 128 * 1024 * 1024
PROFILE_FIELDS = ("parameters", "parameter_bits", "distribution", "algorithms", "encoding", "kdf")
SHA = re.compile(r"[0-9a-f]{64}\Z")


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


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _name(value):
    require(isinstance(value, str) and len(value) <= 300 and "\\" not in value, "Invalid archive path")
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value and not any(x in {".", ".."} for x in p.parts), "Invalid archive path")
    return value


def validate_bundle(raw, identity):
    require(0 < len(raw) <= MAX_UPLOAD, "Publication exceeds the upload limit")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        require(0 < len(items) <= 15000 and sum(x.file_size for x in items) <= MAX_EXPANDED, "Expanded publication is too large")
        names = [_name(x.filename) for x in items]
        require(len(set(names)) == len(names), "Duplicate archive member")
        for item in items:
            mode = item.external_attr >> 16
            require(not item.is_dir() and not stat.S_ISLNK(mode) and (not stat.S_IFMT(mode) or stat.S_ISREG(mode))
                    and not item.flag_bits & 1 and item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "Unsupported archive member")
        files = {item.filename: archive.read(item) for item in items}
    def document(name):
        require(name in files and len(files[name]) <= 16 * 1024 * 1024, "Missing or oversized publication document")
        value = resource_identity.strict_json(files[name])
        require(type(value) is dict, "Publication document must be an object")
        return value
    publication = document("publication.json")
    require(set(publication) == {"format", "binding", "source", "evaluation_manifest_sha256", "resources_sha256"}
            and publication["format"] == "crucible-website-publication-v1", "Unsupported publication format")
    require(publication["source"] == identity, "Publication source differs from the authenticated workflow")
    manifest = document("bundle/manifest.json")
    require(manifest["format"] == "crucible-evaluation-bundle-v1", "Unsupported evaluation bundle")
    require(hashlib.sha256(files["bundle/manifest.json"]).hexdigest() == publication["evaluation_manifest_sha256"], "Evaluation inventory hash differs")
    expected = {"publication.json", "bundle/manifest.json"}
    for row in manifest["files"]:
        require(set(row) == {"path", "sha256", "bytes"}, "Invalid inventory entry")
        path = "bundle/" + _name(row["path"])
        require(path not in expected and path in files and type(row["bytes"]) is int and len(files[path]) == row["bytes"]
                and hashlib.sha256(files[path]).hexdigest() == row["sha256"], "Publication inventory differs")
        expected.add(path)
    require(set(files) == expected, "Unlisted publication files")
    require(hashlib.sha256(files["bundle/resources.json"]).hexdigest() == publication["resources_sha256"], "Resource projection hash differs")
    binding = publication["binding"]
    require(set(binding) == {"scheme_id", "version", "scheme", "release", "definition_sha256", "specification_sha256", "implementation", "profiles"}, "Invalid scheme binding")
    require(binding["scheme_id"] == binding["scheme"] and binding["version"] == binding["release"], "Construction identity differs from the website release")
    adapter = registry.adapter_for(binding["scheme_id"], binding["version"])
    profiles = {x["profile_id"]: x for x in registry.presentation_manifests(adapter)}
    mappings = binding["profiles"]
    require(isinstance(mappings, list) and 0 < len(mappings) <= 500, "Invalid publication profile count")
    require(len({x["profile_id"] for x in mappings}) == len(mappings) == len({x["configuration"] for x in mappings}), "Duplicate profile mapping")
    require({x["profile_id"] for x in mappings} == set(profiles), "Main publication must cover every registered website profile")
    for row in mappings:
        require(set(row) == {"profile_id", "configuration", "protocol_sha256"} and row["profile_id"] in profiles, "Unknown website profile")
        require(SHA.fullmatch(row["configuration"]) is not None and row["protocol_sha256"] == digest({k: profiles[row["profile_id"]].get(k) for k in PROFILE_FIELDS}), "Website algorithms, parameters or encoding differ")
    plan, outcomes = document("bundle/plan.json"), document("bundle/workflow.json")
    definition, resources = document("bundle/declaration/definition.json"), document("bundle/resources.json")
    require(definition["scheme"] == binding["scheme"] and definition["release"] == binding["release"]
            and digest(definition) == binding["definition_sha256"] == plan["definition_sha256"]
            and definition["specification_sha256"] == binding["specification_sha256"], "Construction identity differs")
    require(plan["scheme"] == binding["scheme"] and plan["release"] == binding["release"]
            and plan["implementation"]["id"] == binding["implementation"]["id"]
            and digest(plan["implementation"]) == binding["implementation"]["sha256"], "Main implementation differs")
    require(plan["sha256"] == digest({k: v for k, v in plan.items() if k != "sha256"})
            == manifest["plan_sha256"] == outcomes["plan_sha256"], "Measurement plan differs")
    expected_ids = [x["configuration"] for x in mappings]
    require([x["id"] for x in plan["configurations"]] == expected_ids
            == [x["configuration"] for x in outcomes["outcomes"]]
            == [x["configuration"] for x in resources["configurations"]], "Measured route differs from the approved website mapping")
    require(outcomes["status"] == "complete" and all(x[k]["status"] == "measured" for x in outcomes["outcomes"] for k in ("native", "rss", "stack", "massif")), "Incomplete resource evaluation cannot replace the current report")
    for row, declared in zip(resources["configurations"], plan["configurations"]):
        require(row["scheme"] == binding["scheme"] and row["release"] == binding["release"]
                and row["parameters"] == declared["parameters"] and row["sizes_bytes"] == declared["sizes"], "Configuration resource identity differs")
        require(row["configuration"] == digest({"format": "crucible-configuration-v1", "scheme": binding["scheme"],
                "release": binding["release"], "specification": binding["specification_sha256"], "parameters": row["parameters"]}), "Parameter hash differs")
        for field in ("native_evidence", "stack_evidence", "memory_evidence"):
            require(len(row[field]) == 1, "Publication must identify one run per instrument and configuration")
            evidence = row[field][0]
            require(evidence["implementation"] == binding["implementation"] and SHA.fullmatch(evidence["comparison_profile"]), "Measured implementation or comparison profile differs")
            if field == "native_evidence":
                for op in ("keygen", "encapsulate", "decapsulate"):
                    v = evidence["computation"][op]
                    require(v["complete"] is True and v["duration_ns"]["n"] == v["recorded"] == v["planned"] == 100, "Incomplete standard timing schedule")
                    rss = evidence["memory_components"][op + ":rss"]
                    require(rss["complete"] is True and rss["values"]["n"] == 5, "Incomplete process memory schedule")
            else:
                n = 5 if field == "stack_evidence" else 2
                require(all(evidence["operations"][op]["measured"] == evidence["operations"][op]["planned"] == n for op in ("keygen", "encapsulate", "decapsulate")), "Incomplete memory schedule")
    return publication, resources


def latest(session, scheme, version):
    return session.scalar(select(ResourcePublication).where(ResourcePublication.scheme_id == scheme, ResourcePublication.version == version)
                          .order_by(ResourcePublication.run_id.desc(), ResourcePublication.run_attempt.desc()).limit(1))


def ingest(db, raw, identity, now):
    try:
        publication, resources = validate_bundle(raw, identity)
    except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, OverflowError):
        raise HTTPException(422, "Publication failed its inventory, scheme mapping or completeness checks.") from None
    binding = publication["binding"]
    identifier = hashlib.sha256(raw).hexdigest()
    def existing(session):
        return session.scalar(select(ResourcePublication).where(ResourcePublication.repository_id == identity["repository_id"],
            ResourcePublication.run_id == int(identity["run_id"]), ResourcePublication.run_attempt == int(identity["run_attempt"]),
            ResourcePublication.scheme_id == binding["scheme_id"], ResourcePublication.version == binding["version"]))
    def receipt(session, row):
        if row.id != identifier:
            raise HTTPException(409, "This workflow run already published different evidence for this release.")
        return {"publication_id": row.id, "current": latest(session, row.scheme_id, row.version).id == row.id,
                "scheme_id": row.scheme_id, "version": row.version}
    try:
        with db.transaction(write=True) as session:
            row = existing(session)
            if row is None:
                row = ResourcePublication(id=identifier, scheme_id=binding["scheme_id"], version=binding["version"],
                    repository_id=identity["repository_id"], run_id=int(identity["run_id"]), run_attempt=int(identity["run_attempt"]),
                    created_at=now, publication=publication, resources=resources, bundle=raw)
                session.add(row); session.flush()
            return receipt(session, row)
    except IntegrityError:
        with db.transaction() as session:
            row = existing(session)
            if row is None:
                raise
            return receipt(session, row)


class ResourceIngestBoundary:
    """Authenticate before buffering a bounded binary upload, outside browser CSRF.

    Only this exact POST bypasses the browser/agent credential boundary. Read
    routes remain subject to the normal membership policy. Nothing runs here.
    """
    def __init__(self, app, db, settings, clock):
        self.app, self.db, self.settings, self.clock = app, db, settings, clock
        flag = os.getenv("CRUCIBLE_RESOURCE_PUBLISHING", "false")
        if flag not in {"true", "false"}:
            raise RuntimeError("CRUCIBLE_RESOURCE_PUBLISHING must be true or false")
        self.enabled = flag == "true"
        self.upload_slot = asyncio.Semaphore(1)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != resource_identity.UPLOAD_PATH:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        acquired = False
        try:
            if not self.enabled:
                raise HTTPException(404, "Resource publication is not enabled.")
            if self.upload_slot.locked():
                raise HTTPException(429, "Another resource publication is being processed. Retry shortly.")
            await self.upload_slot.acquire()
            acquired = True
            auth = [v.decode("latin1") for k, v in scope["headers"] if k.lower() == b"authorization"]
            if len(auth) != 1 or b"origin" in headers or b"cookie" in headers:
                raise HTTPException(401, "A resource publisher credential is required.")
            identity = await run_in_threadpool(resource_identity.verify, auth[0], self.settings.origins[0] + resource_identity.UPLOAD_PATH, self.clock())
            if headers.get(b"content-type", b"") != b"application/zip":
                raise HTTPException(415, "Send an application/zip resource publication.")
            if b"content-encoding" in headers or int(headers.get(b"content-length", b"0")) > MAX_UPLOAD:
                raise HTTPException(413, "Resource publication is too large.")
            body = bytearray()
            async with asyncio.timeout(60):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_UPLOAD:
                        raise HTTPException(413, "Resource publication is too large.")
                    if not message.get("more_body", False):
                        break
            result = await run_in_threadpool(ingest, self.db, bytes(body), identity, self.clock())
            response = JSONResponse(result, 200)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, exc.status_code)
        except (ValueError, TimeoutError):
            response = JSONResponse({"detail": "Invalid or incomplete publication upload."}, 400)
        finally:
            if acquired:
                self.upload_slot.release()
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
        if response.status_code in {429, 503}:
            response.headers["Retry-After"] = "10"
        if self.settings.production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        await response(scope, receive, send)


def register_routes(app, db):
    @app.get("/api/protocol/resources")
    def resources(request: Request, scheme_id: str, version: str):
        try:
            registry.adapter_for(scheme_id, version)
        except (ValueError, KeyError):
            raise HTTPException(404, "Unknown scheme release.") from None
        with db.transaction() as session:
            row = latest(session, scheme_id, version)
            if row is None:
                return {"publication": None, "scheme_id": scheme_id, "version": version}
            projection = deepcopy(row.resources)
            # This channel has no authority over the site's correctness/security.
            for config in projection["configurations"]:
                config.pop("security", None); config.pop("correctness", None)
            document = {"publication": {**row.publication, "id": row.id, "received_at": row.created_at,
                        "evidence_url": "/api/resource-publications/" + row.id + "/download"},
                    "resources": projection, "scheme_id": scheme_id, "version": version}
            return catalogue_response(document, request.headers.get("accept-encoding"))

    @app.get("/api/resource-publications/{publication_id}/download")
    def download(publication_id: str):
        if not SHA.fullmatch(publication_id):
            raise HTTPException(404, "Resource publication not found.")
        with db.transaction() as session:
            row = session.get(ResourcePublication, publication_id)
            if row is None:
                raise HTTPException(404, "Resource publication not found.")
            raw = row.bundle
        return Response(raw, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="resources-{publication_id[:16]}.zip"',
                         "X-Content-SHA256": publication_id})
