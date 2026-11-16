#!/usr/bin/env python3
"""Validate launch-guide drafts; optionally save them in the loopback fixture.

This authoring helper cannot spend allowance or contact a hosted server.
Optional publication uses explicitly synthetic local fixture personas.
Repeated saves preserve note identities and immutable revisions.
Independent edits are never overwritten. The receipt contains no credentials.
"""
from __future__ import annotations

import argparse
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DIRECTORY = ROOT / "docs/launch-note-candidates"
WORKSPACE = ROOT / ".local/claims-preview"
RECEIPT = WORKSPACE / "launch-guide-receipt.json"
FORMAT = "crucible-launch-guide-drafts-v1"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prepared_guides():
    from crucible.notebook import _content, _hash

    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    if manifest.get("format") != FORMAT:
        raise RuntimeError("Unrecognized launch-guide manifest.")
    for name, expected in manifest["sources"].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or digest(path.read_bytes()) != expected:
            raise RuntimeError("Review the changed guide source before saving: " + name)
    prepared = []
    for item in manifest["notes"]:
        path = (DIRECTORY / item["file"]).resolve()
        if path.parent != DIRECTORY:
            raise RuntimeError("A guide must be stored in its authoring directory.")
        content = _content({
            "title": item["title"], "claim": item["claim"],
            "conditions": item["conditions"], "body": path.read_text(),
            "sections": item["sections"], "tags": item["tags"], "kind": "Guide",
            "references": item["references"],
            "extensions": {
                "authorship": {"method": "AI-assisted draft", "date": manifest["date"]},
                "launch_guide": {
                    "id": item["id"], "status": "draft", "audience": "agents",
                    "source_commit": manifest["source_commit"],
                    "sources": [{"path": name, "sha256": manifest["sources"][name]}
                                for name in item["sources"]],
                    "review_questions": item["review_questions"],
                },
            },
        })
        prepared.append({"id": item["id"], "content": content, "sha256": _hash(content)})
    if len({item["id"] for item in prepared}) != len(prepared):
        raise RuntimeError("Guide identities must be distinct.")
    return prepared


def save_receipt(value):
    temporary = RECEIPT.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(RECEIPT)


def save_guides(prepared, port, *, publish=False):
    from scripts.prepare_note_integration import AUTHOR, REVIEWER

    if not 1024 <= port <= 65535:
        raise RuntimeError("Choose an unprivileged loopback port.")
    if (ROOT / ".local").is_symlink() or WORKSPACE.is_symlink() or any(
            path.is_symlink() for path in WORKSPACE.rglob("*")):
        raise RuntimeError("The preview workspace must not contain symlinks.")
    marker = WORKSPACE / "preview-workspace.json"
    if not marker.is_file() or json.loads(marker.read_text()) != {"format": "crucible-claims-preview-v1"}:
        raise RuntimeError("Prepare the isolated claims preview first.")
    origin = f"http://127.0.0.1:{port}"
    class LocalRedirects(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, url):
            if not url.startswith(origin + "/"):
                raise RuntimeError("The preview must not redirect away from loopback.")
            return super().redirect_request(request, fp, code, msg, headers, url)

    opener = build_opener(ProxyHandler({}), LocalRedirects(), HTTPCookieProcessor(CookieJar()))
    csrf = None

    def call(path, payload=None):
        headers = {"Origin": origin}
        body = None
        if payload is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": csrf})
            body = json.dumps(payload).encode()
        request = Request(origin + path, data=body, headers=headers)
        try:
            with opener.open(request, timeout=30) as response:
                if not response.url.startswith(origin + "/"):
                    raise RuntimeError("The preview redirected away from loopback.")
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"Preview request {path}: HTTP {error.code}. No new retry identity was issued.") from None

    def sign_in(persona, expected):
        nonlocal csrf
        with opener.open(Request(origin + "/preview?persona=" + persona,
                                 headers={"Origin": origin}), timeout=30):
            pass
        session = call("/api/session")
        if (session.get("user", {}).get("id") != expected
                or session.get("capabilities", {}).get("statement_workflow_trial") is not True):
            raise RuntimeError("The connected account is not the requested isolated preview persona.")
        csrf = session["csrf_token"]

    sign_in("author", AUTHOR)
    receipt = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {
        "format": FORMAT, "author_id": AUTHOR, "notes": {},
    }
    if receipt.get("format") != FORMAT or receipt.get("author_id") != AUTHOR:
        raise RuntimeError("The existing receipt belongs to a different fixture.")

    # Check the entire batch before writing any revised note.
    current = {}
    for item in prepared:
        saved = receipt["notes"].get(item["id"])
        if saved is None:
            continue
        note = call("/api/notebook/notes/" + saved["note_id"])["note"]
        if note["owner"]["id"] != AUTHOR or note["publication_status"] not in {"private", "approved", "submitted"}:
            raise RuntimeError("A draft's ownership or publication status changed; preserve it for manual review.")
        if note["content_sha256"] not in {saved["content_sha256"], item["sha256"]}:
            raise RuntimeError("An independent edit will not be overwritten: " + item["id"])
        if note["publication_status"] == "submitted" and note["content_sha256"] != item["sha256"]:
            raise RuntimeError("Finish the existing publication request before revising: " + item["id"])
        current[item["id"]] = note

    for item in prepared:
        note = current.get(item["id"])
        if note is None:
            key = str(uuid5(NAMESPACE_URL, "crucible-launch-guide-draft-20261116/" + item["id"]))
            note = call("/api/notebook/notes", {"content": item["content"], "idempotency_key": key})["note"]
        elif note["content_sha256"] != item["sha256"]:
            key = str(uuid5(NAMESPACE_URL, "crucible-launch-guide-revision/" + note["id"] + "/" + item["sha256"]))
            note = call("/api/notebook/notes/" + note["id"] + "/revisions", {
                "content": item["content"], "idempotency_key": key,
                "expected_revision": note["revision"], "content_sha256": note["content_sha256"],
            })["note"]
        if note["content_sha256"] != item["sha256"]:
            raise RuntimeError("The saved draft does not match the candidate.")
        receipt["notes"][item["id"]] = {
            "note_id": note["id"], "revision": note["revision"],
            "content_sha256": note["content_sha256"], "title": item["content"]["title"],
            "publication_status": note["publication_status"],
        }
        save_receipt(receipt)
        if publish and note["publication_status"] != "approved":
            frozen = {"revision": note["revision"], "content_sha256": note["content_sha256"]}
            identity = note["id"] + "/" + note["content_sha256"]
            call("/api/notebook/notes/" + note["id"] + "/publication-requests", {
                **frozen, "idempotency_key": str(uuid5(NAMESPACE_URL, "launch-guide-publication/" + identity)),
            })
            sign_in("reviewer", REVIEWER)
            note = call("/api/notebook/notes/" + note["id"] + "/publication-reviews", {
                **frozen, "idempotency_key": str(uuid5(NAMESPACE_URL, "launch-guide-local-review/" + identity)),
                "decision": "approved", "reason": "Local launch review fixture, displayed at the editorial owner's request. This records local publication only, not independent scientific review or hosted publication.",
            })["note"]
            sign_in("author", AUTHOR)
            if note["publication_status"] != "approved":
                raise RuntimeError("The local publication did not complete.")
            receipt["notes"][item["id"]]["local_publication"] = "approved"
            receipt["notes"][item["id"]]["publication_status"] = "approved"
            save_receipt(receipt)
        print(json.dumps({"title": item["content"]["title"], "status": note["publication_status"],
                          "revision": note["revision"], "url": origin + "/#notebook?note=" + note["id"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Save private drafts in the isolated loopback preview")
    parser.add_argument("--publish", action="store_true", help="With --apply, publish in the synthetic local fixture only")
    parser.add_argument("--port", type=int, default=8041)
    arguments = parser.parse_args()
    if arguments.publish and not arguments.apply:
        parser.error("--publish requires --apply and applies only to the local fixture.")
    prepared = prepared_guides()
    if arguments.apply:
        save_guides(prepared, arguments.port, publish=arguments.publish)
    else:
        print(json.dumps({"status": "validated; no writes", "notes": [
            {"title": item["content"]["title"], "body_characters": len(item["content"]["body"])}
            for item in prepared]}))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, URLError) as error:
        raise SystemExit(str(error)) from None
