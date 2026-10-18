"""Exact curated-note provenance for the site's evidence displays.

These are editorial bindings, not mathematical or admission approvals. Building
new evidence appends a note revision; existing revisions and user notes survive.
The corresponding source-snapshot check is part of the publication tests.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode


PATH = Path(__file__).resolve().parent / "data" / "display_note_bindings.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def _bindings():
    if not PATH.exists():
        return {"releases": []}  # Allows the offline builder's first run.
    value = json.loads(PATH.read_text())
    if value.get("format") != "crucible-display-note-bindings-v1":
        raise ValueError("Unsupported display-note bindings")
    return value


def for_release(scheme_id, version):
    rows = [row for row in _bindings()["releases"]
            if (row["scheme_id"], row["version"]) == (scheme_id, str(version))]
    if len(rows) > 1:
        raise ValueError("Ambiguous display-note binding")
    if not rows:
        return None
    note = deepcopy(rows[0]["note"])
    note["api_url"] = "/api/protocol/evidence-note?" + urlencode({
        "scheme_id": scheme_id, "version": version, "revision": note["revision"],
        "sha256": note["content_sha256"]})
    return note


def for_report(scheme_id, version, report):
    for row in _bindings()["releases"]:
        if (row["scheme_id"], row["version"]) == (scheme_id, str(version)):
            return deepcopy(row["note"]) if report is not None and row.get("timing_report_sha256") == digest(report) else None
    return None


def proposal_guide():
    return deepcopy(_bindings().get("proposal_guide", {}).get("note"))


@lru_cache(maxsize=1)
def _collection():
    return json.loads((PATH.parents[2] / "web" / "map" / "notes.json").read_text())


def document(binding, *, revision=None, sha256=None):
    """Resolve only a curator-selected note, retaining exact historical revisions."""
    if binding is None:
        return None
    number = binding["revision"] if revision is None else revision
    expected = sha256 if sha256 is not None else binding["content_sha256"] if number == binding["revision"] else None
    source = next((note for note in _collection()["notes"] if "curated:" + note["id"] == binding["id"]), None)
    selected = next((item for item in source["revisions"] if item["revision"] == number), None) if source else None
    if selected is None:
        return None
    fields = {"title", "claim", "conditions", "body", "tags", "kind", "references", "artifacts", "extensions"}
    content = {key: value for key, value in selected.items() if key in fields}
    checksum = digest(content)
    if expected is not None and checksum != expected:
        return None
    return {"id": binding["id"], "revision": number, "content_sha256": checksum,
            "origin": "curated-research", "review_status": "editorial-inclusion-not-certification",
            "content": deepcopy(content)}


@lru_cache(maxsize=16)
def _artifact(checksum):
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        return None
    path = PATH.parent / "display_evidence" / (checksum + ".json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        return None
    value = json.loads(path.read_text())
    return value if digest(value) == checksum else None


def evidence(binding, *, evidence_sha256=None, revision=None, sha256=None):
    if binding is None:
        return None
    if evidence_sha256 is not None:
        # An artifact is reachable only through this release's bound note history.
        # A hash in a URL is not permission to read an arbitrary local file.
        source = next((note for note in _collection()["notes"] if "curated:" + note["id"] == binding["id"]), None)
        match = next((item for item in source["revisions"]
                      if item.get("extensions", {}).get("display_evidence", {}).get("evidence_sha256") == evidence_sha256
                      and (revision is None or item["revision"] == revision)), None) if source else None
        if match is None:
            return None
        revision = match["revision"]
    selected = document(binding, revision=revision, sha256=sha256)
    if selected is None:
        return None
    checksum = selected["content"].get("extensions", {}).get("display_evidence", {}).get("evidence_sha256")
    snapshot = _artifact(checksum) if isinstance(checksum, str) else None
    if snapshot is None:
        return None
    return {"evidence_sha256": checksum, "evidence": deepcopy(snapshot),
            "supporting_note": {key: selected[key] for key in ("id", "revision", "content_sha256")}}
