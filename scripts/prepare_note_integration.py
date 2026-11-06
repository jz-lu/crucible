#!/usr/bin/env python3
"""Prepare a fresh loopback notebook through the existing create/review APIs.

This is an explicit local fixture builder, not a production import or review.
It refuses an occupied notebook unless --revise-fixture explicitly selects its
own exact local fixture. Revisions use the normal optimistic-write/review APIs;
old citations and all history survive. Source files and every existing pin are
checked before writes. --check validates the planned operation without writing.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AUTHOR = str(uuid5(NAMESPACE_URL, "crucible-readable-note-local-author-v1"))
REVIEWER = str(uuid5(NAMESPACE_URL, "crucible-readable-note-local-reviewer-v1"))

OWNED_EXTENSIONS = {"scheme_context", "assumptions", "assumption_statements", "scheme_assumptions",
    "scheme_arguments", "scheme_security_gaps", "authorship", "source_evidence_note", "source_refs",
    "presentation_sha256", "ram_analysis_sha256", "local_fixture", "canonical_conversion"}


def numbered_premises(content):
    """Read both earlier premise metadata and canonical blocks, excluding claims."""
    conclusion_ids = {argument["conclusion_statement"]["statement_id"]
        for argument in content.get("arguments", [])
        if argument.get("conclusion_statement")
        and not argument["conclusion_statement"].get("note_id")}
    return [premise for premise in [*content.get("extensions", {}).get("assumptions", []),
                                   *content.get("statements", [])]
            if premise["id"] not in conclusion_ids]


def preserve_unrelated_content(previous, generated):
    """Carry unrelated metadata/citations through an explicit fixture revision."""
    result = dict(generated)
    old_extensions = previous.get("extensions") or {}
    if not isinstance(old_extensions, dict):
        raise RuntimeError("An existing fixture has non-object extension data; revise it explicitly.")
    result["extensions"] = {**{key: value for key, value in old_extensions.items() if key not in OWNED_EXTENSIONS},
                            **generated["extensions"]}
    references = []
    seen = set()
    for values in (previous.get("references") or [], generated.get("references") or []):
        if not isinstance(values, list):
            raise RuntimeError("An existing fixture has non-list citations; revise it explicitly.")
        for value in values:
            identity = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if identity not in seen:
                seen.add(identity)
                references.append(value)
    result["references"] = references
    if "artifacts" in previous and "artifacts" not in result:
        result["artifacts"] = previous["artifacts"]
    return result


def prepare(data_dir: Path, *, revise_fixture=False, check=False):
    from fastapi.testclient import TestClient
    from sqlalchemy import select, func
    from crucible import notebook, display_notes, registry
    from crucible.app import create_app
    from crucible.config import load_settings
    from crucible.db import User, LoginSession
    from crucible.security import token_hash, mac

    data_dir = data_dir.resolve()
    if os.getenv("DATABASE_URL") or os.getenv("CRUCIBLE_ENV", "development") != "development":
        raise SystemExit("Use an isolated local environment, without DATABASE_URL or production settings.")
    os.environ["CRUCIBLE_DATA_DIR"] = str(data_dir)
    os.environ["CRUCIBLE_LOCAL_NOTE_CORPUS_ONLY"] = "true"
    settings = replace(load_settings(), origins=("http://127.0.0.1:8000",),
                       rate_limits=False, reviewer_ids=(REVIEWER,),
                       publication_screening_mode="disabled")
    if settings.production or not settings.database_url.startswith("sqlite:///"):
        raise SystemExit("This fixture builder only operates on a local SQLite development database.")
    if data_dir.is_relative_to(ROOT / ".local"):
        # Match this isolated preview's history allowance. This process-only
        # override retains earlier full revisions; production limits are untouched.
        notebook.MAX_WORKSPACE_BYTES = max(notebook.MAX_WORKSPACE_BYTES, 8 * 1024 * 1024)
    source_path = ROOT / "crucible/data/scheme_note_presentations.json"
    source = json.loads(source_path.read_text())
    if source.get("format") != "crucible-scheme-note-presentations-v1":
        raise SystemExit("Unsupported note presentation format")
    fingerprint = hashlib.sha256(source_path.read_bytes()).hexdigest()
    receipt_path = data_dir / "note-integration-receipt.json"
    manifest_path = data_dir / "scheme-note-bindings.json"
    app = create_app(settings)
    origin = settings.origins[0]

    class Actor:
        def __init__(self, user_id):
            self.client = TestClient(app)
            raw = str(uuid4()) + str(uuid4())
            with app.state.db.transaction(write=True) as session:
                session.add(LoginSession(token_hash=token_hash(raw), user_id=user_id,
                                         expires_at=time.time() + 3600))
            self.client.cookies.set(settings.cookie_name, raw)
            self.csrf = mac(settings.secret, "csrf-v1", raw.encode("ascii"))

        def post(self, path, payload):
            response = self.client.post(path, json=payload,
                headers={"Origin": origin, "X-CSRF-Token": self.csrf, "Host": "127.0.0.1:8000"})
            if response.status_code != 200:
                raise RuntimeError(f"Local API {path}: {response.status_code}: {response.text[:1000]}")
            return response.json()["note"]

    with app.state.db.transaction() as session:
        count = session.scalar(select(func.count()).select_from(notebook.NotebookNote))
    if revise_fixture and not count:
        raise SystemExit("No existing local fixture to revise; no notes were created.")
    existing, previous_labels, used_labels = {}, {}, set()
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if count:
        if not revise_fixture and receipt.get("presentation_sha256") != fingerprint:
            raise SystemExit("Notebook is occupied. Use --revise-fixture for this builder's exact local fixture, or select a fresh directory.")
        if receipt.get("author_id") != AUTHOR or receipt.get("reviewer_id") != REVIEWER or not manifest_path.exists():
            raise SystemExit("Existing notebook is not this builder's identified local fixture.")
        from crucible.note_sections import _manifest
        old_manifest = _manifest(manifest_path)
        # Other authors/rehearsals may add ordinary notes alongside the scheme
        # fixture. Only this manifest's exact identities are ours to revise.
        # Below we still reject independent edits of any managed source.
        if (len({r.note.id for r in old_manifest.releases}) != len(old_manifest.releases)
                or {f"{r.scheme_id}@{r.version}" for r in old_manifest.releases} != set(source["releases"])):
            raise SystemExit("Fixture membership changed; existing notes were not touched.")
        with app.state.db.transaction() as session:
            for binding in old_manifest.releases:
                release = f"{binding.scheme_id}@{binding.version}"
                note = session.get(notebook.NotebookNote, binding.note.id)
                history = session.scalars(select(notebook.NotebookRevision).where(
                    notebook.NotebookRevision.note_id == binding.note.id).order_by(notebook.NotebookRevision.revision)).all()
                base = next((r for r in history if r.revision == binding.note.revision), None)
                if (note is None or note.author_id != AUTHOR or base is None
                        or base.content_sha256 != binding.note.content_sha256
                        or notebook._hash(base.content) != base.content_sha256
                        or history[-1].revision != note.latest_revision
                        or base.content.get("extensions", {}).get("local_fixture") is not True
                        or base.content["extensions"].get("scheme_context") != {"scheme_id": binding.scheme_id, "version": binding.version}
                        or base.content["extensions"].get("source_evidence_note") != source["releases"][release]["source_note"]):
                    raise SystemExit("An existing fixture identity or source pin changed: " + release)
                latest = history[-1]
                existing[release] = {"pin": binding.note.model_dump(), "latest_revision": latest.revision,
                    "latest_sha256": latest.content_sha256, "latest_key": latest.idempotency_key,
                    "latest_content": latest.content}
                for revision in history:
                    # Conclusion identities use exact statement pins, not the
                    # historical A-number allocation reserved for premises.
                    for premise in numbered_premises(revision.content):
                        label = premise.get("label", "")
                        if not re.fullmatch(r"A-[1-9][0-9]*", label):
                            raise SystemExit("Invalid historical fixture assumption label")
                        identity = (release, premise["id"])
                        if identity in previous_labels and previous_labels[identity] != label:
                            raise SystemExit("Historical assumption identity changed labels")
                        previous_labels[identity] = label
                        used_labels.add(int(label[2:]))

    # Validate the complete source set before creating any notes.
    prepared = []
    next_label = max(used_labels, default=0) + 1
    for release, row in source["releases"].items():
        scheme_id, version = release.split("@", 1)
        registry.adapter_for(scheme_id, version)
        for ref in row["source_refs"]:
            if "path" not in ref:
                continue
            path = (ROOT / ref["path"]).resolve()
            if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != ref["sha256"]:
                raise RuntimeError("Source has changed: " + ref["path"])
        binding = display_notes.for_release(scheme_id, version)
        original = display_notes.document(binding)
        if not original:
            raise RuntimeError("Original exact evidence note unavailable: " + release)
        if row.get("source_note") != {k: original[k] for k in ("id", "revision", "content_sha256")}:
            raise RuntimeError("Authored presentation pins a different source revision: " + release)
        if not all(key in row for key in ("statements", "arguments", "readable_sections", "argument_bindings", "canonical_conversion")):
            raise RuntimeError("Generate the canonical note blocks before preparing local revisions: " + release)
        labels = {}
        for premise in row["assumptions"]:
            if premise["status"] not in {"model-premise", "unproved"}:
                raise RuntimeError("Only actual premises belong in assumptions: " + premise["id"])
            label = previous_labels.get((release, premise["id"]))
            if label is None:
                label = f"A-{next_label}"
                next_label += 1
            labels[premise["label"]] = label
        def relabel(text):
            return re.sub(r"\bA-[1-9][0-9]*\b", lambda m: labels.get(m[0], m[0]), text)
        def relabel_blocks(value):
            if isinstance(value, str):
                return relabel(value)
            if isinstance(value, list):
                return [relabel_blocks(item) for item in value]
            if isinstance(value, dict):
                return {key: relabel_blocks(item) for key, item in value.items()}
            return value
        sections = [{key: relabel(section[key]) if key in {"title", "body"} else section[key]
                     for key in ("id", "title", "body")} for section in row["readable_sections"]]
        source_body = original["content"]["body"]
        if row.get("full_text_argument_appendix"):
            # The current, complete derivations replace duplicate snapshots in
            # this revision. Earlier immutable revisions retain that source text.
            # Keep an exact archival reference, not a dynamically changing link
            # to the previous revision (which would break idempotent preparation).
            body = relabel(row["full_text_argument_appendix"])
            companion = "\n\n" + relabel(row.get("full_text_appendix", ""))
            companion += ("\n\n## Earlier source and provenance\n\n"
                "These current derivations are locally authored research arguments, not independent scientific admission. "
                "They supersede the older narrative only within their stated scope. "
                "Earlier immutable local revisions retain the older discussion; no history has been deleted. "
                "The original bundled evidence is also pinned below and remains reproducible from this source checkout.\n\n"
                f"Source note: `{original['id']}`, revision `{original['revision']}`, "
                f"content SHA256 `{original['content_sha256']}`.\n\n"
                "```python\nfrom crucible import display_notes\n"
                f"record = display_notes.document(display_notes.for_release({scheme_id!r}, {version!r}))\n"
                f"assert record['content_sha256'] == {original['content_sha256']!r}\n"
                "print(record['content']['body'])\n```\n")
        elif row.get("full_text_appendix"):
            # These display tables repeat the registered catalogue and resource
            # reports. Keep the mathematical arguments, qualifications and
            # provenance intact; don't duplicate unrelated tables in a proof.
            for start, end in (
                ("ENCODED SIZE AND CONFIGURATION TABLE", "SECURITY REFERENCE: SCOPE AND UNITS"),
                ("REFERENCE OPERATION TIMINGS", "EXACT MATHEMATICAL ITEMS"),
            ):
                before, found, rest = source_body.partition("\n\n" + start + "\n\n")
                if not found or "\n\n" + end + "\n\n" not in rest:
                    raise RuntimeError("Source section boundaries changed: " + release)
                _, _, after = rest.partition("\n\n" + end + "\n\n")
                source_body = before + "\n\n" + end + "\n\n" + after
            body = ("## Retained source arguments\n\n"
                "This excerpt preserves the earlier mathematical arguments, qualifications and provenance. "
                "Repeated encoded-size/configuration and operation-timing display tables are omitted; "
                "their complete source remains pinned in this note's source_evidence_note record, "
                "and existing note revisions are preserved. The readable sections give the current "
                "qualifications. The bit-RAM appendix below is a separate, explicitly bounded attack analysis.\n\n"
                + source_body)
            companion = "\n\n" + relabel(row["full_text_appendix"])
        else:
            body = ("## Retained source snapshot\n\n"
                "The original evidence record below is retained verbatim for reproducibility. "
                "The readable companion and updated qualifications at the end identify later "
                "results and narrower scopes; do not treat a superseded source claim as a new guarantee.\n\n"
                + source_body)
            companion = "\n\n## Readable companion and updated qualifications\n\n" + "\n\n".join(
                "### " + item["title"] + "\n\n" + item["body"] for item in sections)
        if len(body + companion) > 40000:
            raise RuntimeError("Full source plus companion exceeds note limit: " + release)
        references = [{"title": ref["title"], "url": ref["url"]}
                      for ref in row["source_refs"] if ref.get("url", "").startswith("https://")]
        content = {
            "title": row["title"], "claim": row["summary"],
            "conditions": "Exact research release " + release + ". " + " ".join(row["caveats"]),
            "body": body + companion, "sections": sections,
            "statements": relabel_blocks(row["statements"]), "arguments": relabel_blocks(row["arguments"]),
            "tags": ["scheme evidence", "security", "correctness", scheme_id],
            "kind": "research-note", "references": references,
            "extensions": {"scheme_context": {"scheme_id": scheme_id, "version": version},
                "canonical_conversion": row["canonical_conversion"],
                "scheme_security_gaps": row.get("scheme_security_gaps", []),
                "authorship": {"kind": "model-assisted", "description": "Authored and checked in the local Crucible integration pass; not human-written or independently scientifically reviewed."},
                "source_evidence_note": {k: original[k] for k in ("id", "revision", "content_sha256")},
                "source_refs": row["source_refs"], "presentation_sha256": fingerprint,
                **({"ram_analysis_sha256": row["ram_analysis_sha256"]} if row.get("ram_analysis_sha256") else {}),
                "local_fixture": True},
        }
        content = preserve_unrelated_content(existing.get(release, {}).get("latest_content", {}), content)
        notebook.NoteContent.model_validate(content)
        prepared.append((scheme_id, version, row, content))

    def action_key(action, release):
        return str(uuid5(NAMESPACE_URL, f"crucible-readable-fixture:{fingerprint}:{release}:{action}"))

    # A partially completed identical run may have saved its one deterministic
    # new revision. Any unrelated edit, even by the fixture owner, stops the run.
    for scheme_id, version, _, content in prepared:
        release = f"{scheme_id}@{version}"
        old = existing.get(release)
        if not old:
            continue
        base = old["pin"]
        planned_hash = notebook._hash(notebook._content(content))
        unchanged = old["latest_revision"] == base["revision"] and old["latest_sha256"] == base["content_sha256"]
        resumed = (old["latest_revision"] == base["revision"] + 1
            and old["latest_sha256"] == planned_hash
            and notebook._hash(old["latest_content"]) == planned_hash
            and old["latest_key"] == action_key("revise", release))
        if not (unchanged or resumed):
            raise SystemExit("A fixture note has an independent edit; no notes were changed: " + release)
    if check:
        print(f"Validated {len(prepared)} local note {'revisions' if existing else 'creations'}; no writes performed.")
        return
    if existing and receipt.get("presentation_sha256") == fingerprint:
        print("Existing exact local fixture retained; no notes duplicated or revised.")
        return

    with app.state.db.transaction(write=True) as session:
        for identifier, handle in [(AUTHOR, "research-preview"), (REVIEWER, "local-publication-reviewer")]:
            if session.get(User, identifier) is None:
                session.add(User(id=identifier, handle=handle, password_hash="local-fixture-no-password-login",
                                 created_at=time.time()))
    owner, reviewer = Actor(AUTHOR), Actor(REVIEWER)
    releases = []
    for scheme_id, version, row, content in prepared:
        release = f"{scheme_id}@{version}"
        old = existing.get(release)
        if old:
            base = old["pin"]
            if base["content_sha256"] == notebook._hash(notebook._content(content)):
                # Recover a crash after the manifest was replaced but before
                # its receipt was written; do not append an identical revision.
                note = base
            else:
                note = owner.post(f'/api/notebook/notes/{base["id"]}/revisions', {
                    "content": content, "expected_revision": base["revision"], "content_sha256": base["content_sha256"],
                    "idempotency_key": action_key("revise", release)})
        else:
            note = owner.post("/api/notebook/notes", {"content": content, "idempotency_key": action_key("create", release)})
        pin = {key: note[key] for key in ("id", "revision", "content_sha256")}
        payload = {key: pin[key] for key in ("revision", "content_sha256")}
        owner.post(f'/api/notebook/notes/{note["id"]}/publication-requests', {**payload, "idempotency_key": action_key("request", release)})
        reviewer.post(f'/api/notebook/notes/{note["id"]}/publication-reviews', {
            **payload, "idempotency_key": action_key("review", release), "decision": "approved",
            "reason": "Local integration fixture: suitable to display as a sourced research draft. This is a fixture publication action, not independent scientific review, admission, or proof of correctness/security.",
        })
        releases.append({"scheme_id": scheme_id, "version": version, "note": pin,
            "sections": {"security": "security", "correctness": "correctness"},
            "arguments": row["argument_bindings"],
            "graph_models": row["graph_models"]})
        print("Revised local readable note:" if old else "Created local readable note:", scheme_id, version)
    manifest = {"format": "crucible-local-note-bindings-v1", "releases": releases}
    def save_json(path, value):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2) + "\n")
        temporary.replace(path)
    # The original manifest stays usable until every new exact revision passes
    # the local publication fixture. Never repoint it to an unfinished revision.
    save_json(manifest_path, manifest)
    save_json(receipt_path, {"presentation_sha256": fingerprint,
        "notes": len(releases), "assumptions": sum(len(item[2]["assumptions"]) for item in prepared),
        "statements": sum(len(item[3]["statements"]) for item in prepared),
        "arguments": sum(len(item[3]["arguments"]) for item in prepared),
        "last_assumption_label": next_label - 1, "author_id": AUTHOR,
        "reviewer_id": REVIEWER, "publication": "local-fixture-not-scientific-review",
        "existing_notes_deleted": 0})
    print(f"Prepared {len(releases)} notes; no existing research/accounts were removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--revise-fixture", action="store_true", help="Revise only this builder's exact existing fixture through normal APIs")
    parser.add_argument("--check", action="store_true", help="Validate identities, sources and content without writes")
    args = parser.parse_args()
    prepare(args.data_dir, revise_fixture=args.revise_fixture, check=args.check)
