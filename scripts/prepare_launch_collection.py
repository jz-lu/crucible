#!/usr/bin/env python3
"""Archive this preview's six superseded rehearsal notes from discovery.

Only a release-owned editorial file is written. Every database record, exact
citation and historical review is retained. Run before restarting the preview.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def prepare(*, apply=False):
    from scripts.serve_claims_preview import workspace

    directory = workspace()
    receipt_path = directory / "statement-trial-receipt.json"
    if not receipt_path.exists():
        print("No earlier rehearsal collection is present; no selection change needed.")
        return
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("fixture") != "crucible-statement-sampler-audit-v1":
        raise RuntimeError("Unrecognized rehearsal receipt; no selection changed.")
    expected = {}
    for pin in receipt["notes"].values():
        if pin["revision"] > expected.get(pin["id"], {}).get("revision", 0):
            expected[pin["id"]] = pin
    if len(expected) != 6:
        raise RuntimeError("The rehearsal membership changed; inspect it before curation.")
    source = ROOT / "web/notebook/editorial-selection.json"
    selection = json.loads(source.read_text())
    path = directory / "editorial-selection.json"
    if path.exists():
        selection = json.loads(path.read_text())
    archives = {(item["note_id"], item["revision"], item["content_sha256"]): item
                for item in selection["archived_publications"]}
    titles = []
    with sqlite3.connect((directory / "crucible.db").as_uri() + "?mode=ro", uri=True) as database:
        for identifier, pin in expected.items():
            row = database.execute("SELECT r.revision, r.content_sha256, r.content FROM notebook_notes n "
                "JOIN notebook_revisions r ON r.note_id=n.id AND r.revision=n.latest_revision "
                "WHERE n.id=?", (identifier,)).fetchone()
            if row is None or row[:2] != (pin["revision"], pin["content_sha256"]):
                raise RuntimeError("A rehearsal note has an independent edit; nothing was archived.")
            content = json.loads(row[2])
            if content.get("extensions", {}).get("statement_trial") != receipt["fixture"]:
                raise RuntimeError("A rehearsal identity changed; nothing was archived.")
            titles.append(content["title"])
            archives[(identifier, row[0], row[1])] = {
                "note_id": identifier, "revision": row[0], "content_sha256": row[1],
                "reason": "Superseded demonstration, removed from the launch collection at the editorial owner's request.",
            }
    selection["archived_publications"] = list(archives.values())
    if apply:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(selection, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(path)
    print(json.dumps({"status": "selection saved; restart preview" if apply else "validated; no writes",
                      "archived_demonstrations": titles, "database_changes": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        prepare(apply=arguments.apply)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
