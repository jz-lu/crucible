#!/usr/bin/env python3
"""Append the four reviewed launch guides to the curated corpus, offline.

This is editorial packaging, not account publication or scientific admission.
Existing revisions are retained. No account, allowance or database is accessed.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.note_runtime_evidence import no_database_connections
from scripts.prepare_launch_guides import prepared_guides
from scripts.package_scheme_dossiers import contents, preserve_history

GENERATOR = "crucible-curated-launch-guides-v1"
GUIDES = {"start-here", "resource-estimates", "new-assumptions", "proposal-acceptance"}
OLD_GUIDES = {"starter-01-purpose", "starter-02-constitution", "starter-03-workflow",
              "starter-04-evidence", "starter-05-repository", "starter-06-automated-review"}


def package(workspace, guides, bindings, *, created_at):
    from crucible import notebook
    if {item["id"] for item in guides} != GUIDES or len(guides) != len(GUIDES):
        raise ValueError("Package exactly the four reviewed launch guides")
    updated = deepcopy(workspace)
    by_id = {note["id"]: note for note in updated["notes"]}
    for item in guides:
        identifier = "launch-" + item["id"]
        previous = by_id.get(identifier)
        if previous and previous["revisions"][-1].get("extensions", {}).get("curated_launch_guide") != GENERATOR:
            raise ValueError("An independent note already uses this guide identity")
        content = deepcopy(item["content"])
        content["tags"] = [tag for tag in content["tags"] if tag != "launch-draft"]
        content["extensions"]["curated_launch_guide"] = GENERATOR
        content["extensions"]["launch_guide"]["status"] = "editorial-inclusion-not-scientific-admission"
        content["extensions"]["research_note"] = {
            "schema_version": 1, "role": "procedural-guidance", "origin": "crucible-compilation",
            "scheme_ids": [], "prepared_by": "Prepared with Codex for the launch collection.",
            "evidence_scope": "Guidance based on pinned repository contracts; editorial inclusion is not scientific admission.",
        }
        notebook.NoteContent.model_validate(content)
        if previous is None:
            previous = {"id": identifier, "revisions": []}
            updated["notes"].append(previous)
        revisions = previous["revisions"]
        if not revisions or notebook._hash(contents(revisions[-1])) != notebook._hash(content):
            revisions.append({"revision": len(revisions) + 1,
                              "createdAt": max(created_at, revisions[-1]["createdAt"] + 1) if revisions else created_at,
                              **content})
    selection = updated.setdefault("extensions", {}).setdefault("editorial_selection", {})
    archived = selection.setdefault("archived", {})
    for identifier in OLD_GUIDES:
        if identifier not in by_id:
            raise ValueError("An expected historical guide is missing")
        archived[identifier] = "Superseded by the reviewed launch guides; exact historical citations remain available."
    schemes = {row["note"]["id"].removeprefix("curated:") for row in bindings["releases"]}
    if len(schemes) != 8 or not schemes <= by_id.keys():
        raise ValueError("The launch needs eight exact packaged scheme dossiers")
    featured = schemes | {"launch-" + name for name in GUIDES}
    for identifier in featured:
        archived.pop(identifier, None)
    actual = {note["id"] for note in updated["notes"]} - archived.keys()
    if actual != featured:
        raise ValueError("Unexpected featured notes need an explicit editorial decision")
    selection["reviewed_on"] = "2026-11-16"
    selection["purpose"] = "Eight scheme dossiers and four practical launch guides for constructive cryptographic research. Editorial inclusion is not scientific admission."
    preserve_history(workspace, updated)
    notebook._workspace(updated)
    return updated


def planned_outputs():
    workspace = json.loads((ROOT / "web/map/notes.json").read_text())
    bindings = json.loads((ROOT / "crucible/data/scheme_note_bindings.json").read_text())
    manifest = json.loads((ROOT / "docs/launch-note-candidates/manifest.json").read_text())
    created_at = int(datetime.fromisoformat(manifest["date"]).replace(tzinfo=timezone.utc).timestamp() * 1000)
    updated = package(workspace, prepared_guides(), bindings, created_at=created_at)
    spec = importlib.util.spec_from_file_location("launch_guide_map_builder", ROOT / "web/map/build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    catalogue = json.loads((ROOT / "web/map/catalogue.json").read_text())
    projected = builder.project(catalogue, updated)
    from crucible import notebook
    for note in updated["notes"]:
        for revision in note["revisions"]:
            if builder.content_hash(revision) != notebook._hash(contents(revision)):
                raise ValueError("Browser and server disagree about an immutable revision")
    browser_notes = {**updated, "revision_hashes": {n["id"]: {str(r["revision"]): builder.content_hash(r)
                    for r in n["revisions"]} for n in updated["notes"]}}
    compact = lambda value: json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return {
        ROOT / "web/map/notes.json": json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        ROOT / "web/map/notes-data.js": "// Generated by build.py; edit notes.json.\nwindow.CRUCIBLE_NOTES = " + compact(browser_notes) + ";\n",
        ROOT / "web/map/catalogue.js": "// Generated projection; edit notes.json and catalogue.json.\nwindow.CRUCIBLE_LANDSCAPE = " + compact(projected) + ";\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with no_database_connections():
        outputs = planned_outputs()
        stale = [str(path.relative_to(ROOT)) for path, text in outputs.items() if path.read_text() != text]
        if args.check and stale:
            raise SystemExit("Launch guide package differs: " + ", ".join(stale))
        if not args.check:
            for path, text in outputs.items():
                if path.read_text() == text:
                    continue
                temporary = path.with_name(path.name + ".tmp")
                temporary.write_text(text)
                temporary.replace(path)
    print(json.dumps({"guides": 4, "featured_notes": 12, "checked": args.check, "database_access": False}))


if __name__ == "__main__":
    main()
