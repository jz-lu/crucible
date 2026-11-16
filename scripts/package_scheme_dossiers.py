#!/usr/bin/env python3
"""Package reviewed Linux evidence as immutable curated dossiers, without a DB.

The receipt is provenance from a separately reviewed CI run, not a signature or
scientific approval. This command independently checks its frozen inputs and
unchanged accounts. Live Linux endpoint qualification remains a separate gate.
"""
from copy import deepcopy
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from deploy.note_runtime_evidence import no_database_connections, require_unchanged_accounts
from scripts.scheme_note_content import build_content, numbered_premises, preserve_unrelated_content

GENERATOR = "crucible-curated-scheme-dossiers-v1"
NATIVE_FILES = ("ram_attack_curves.json", "scheme_note_presentations.json")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Evidence input exceeds its bound")
    return json.loads(raw, object_pairs_hook=unique)


def git_file(root, revision, relative):
    return subprocess.check_output(["git", "-C", str(root), "show", revision + ":" + relative], timeout=30)


def relative_file(root, relative):
    path = root / relative
    if not isinstance(relative, str) or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source evidence must stay in the checkout")
    return path


def verify_evidence(root, evidence_directory, receipt_path, *, expected_revision=None):
    """Verify the receipt against Git, current sources and actual artifact bytes."""
    from crucible.attack_models import catalogue

    raw_receipt = receipt_path.read_bytes()
    receipt = read_json(raw_receipt)
    revision = receipt.get("source_revision", "")
    if (receipt.get("format") != "crucible-note-runtime-evidence-v1"
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or expected_revision is not None and revision != expected_revision):
        raise ValueError("Unexpected evidence receipt source identity")
    subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", revision, "HEAD"], check=True, timeout=30)
    original_contract = read_json(git_file(root, revision, "deploy/release-runtime.json"))
    current_contract = read_json((root / "deploy/release-runtime.json").read_bytes())
    for key in ("app_base", "app_base_config", "python_version", "releases"):
        if current_contract[key] != original_contract[key]:
            raise ValueError("The reviewed runtime contract changed")
    expected = {"runtime_image": current_contract["app_base"], "runtime_config": current_contract["app_base_config"],
        "python": current_contract["python_version"], "registered_releases": 32, "registered_profiles": 1466,
        "analytical_releases": 8, "analytical_accounts": 379, "numerical_evidence_unchanged": True,
        "database_access": False, "deployment_performed": False}
    if any(type(receipt.get(key)) is not type(value) or receipt[key] != value for key, value in expected.items()):
        raise ValueError("The receipt does not describe the reviewed no-publication runtime run")
    builder = "scripts/build_ram_attack_notes.py"
    if receipt["builder_sha256"] != sha(git_file(root, revision, builder)) or sha((root / builder).read_bytes()) != receipt["builder_sha256"]:
        raise ValueError("The analytical builder source changed")
    if set(receipt["files"]) != set(NATIVE_FILES):
        raise ValueError("Unexpected evidence artifact set")
    files = {name: (evidence_directory / name).read_bytes() for name in NATIVE_FILES}
    if any(sha(raw) != receipt["files"][name] for name, raw in files.items()):
        raise ValueError("The downloaded artifact bytes do not match their receipt")
    original = {name: read_json(git_file(root, revision, "crucible/data/" + name)) for name in NATIVE_FILES}
    regenerated = {name: read_json(raw) for name, raw in files.items()}
    before, after = original[NATIVE_FILES[0]], regenerated[NATIVE_FILES[0]]
    if require_unchanged_accounts(before, after) != receipt["runtime_pin_changes"]:
        raise ValueError("The receipt's runtime-pin change list is inconsistent")

    # The companion may change only its analysis digest and that exact digest
    # inside canonical attack statements. Prose, definitions, IDs and layouts
    # otherwise remain the reviewed source, rather than newly authored results.
    presentations = regenerated[NATIVE_FILES[1]]
    normalized = deepcopy(presentations)
    for identity, row in normalized["releases"].items():
        old_digest, new_digest = before["releases"][identity]["analysis_sha256"], after["releases"][identity]["analysis_sha256"]
        if row["ram_analysis_sha256"] != new_digest:
            raise ValueError("A dossier does not pin its exact analytical evidence")
        row["ram_analysis_sha256"] = old_digest
        for statement in row["statements"]:
            statement["definition"] = statement["definition"].replace(new_digest, old_digest)
        for reference in row["source_refs"]:
            if "path" in reference:
                raw = relative_file(root, reference["path"]).read_bytes()
                if sha(raw) != reference["sha256"] or raw != git_file(root, revision, reference["path"]):
                    raise ValueError("A pinned dossier source changed: " + reference["path"])
        scheme, version = identity.split("@", 1)
        checked = catalogue.for_release(scheme, version, path=evidence_directory / NATIVE_FILES[0])
        if not checked or {p["profile_id"] for p in checked["estimates"]} != set(row["profile_ids"]):
            raise ValueError("Analytical evidence does not exactly cover the dossier configurations")
    if normalized != original[NATIVE_FILES[1]]:
        raise ValueError("The runtime refresh changed reviewed dossier content")
    if len(after["releases"]) != 8 or sum(len(row["estimates"]) for row in after["releases"].values()) != 379:
        raise ValueError("Unexpected analytical release coverage")
    return receipt, raw_receipt, files, presentations


def contents(revision):
    from crucible.notebook import CONTENT_FIELDS
    return {key: value for key, value in revision.items() if key in CONTENT_FIELDS}


def preserve_history(original, updated):
    """Every pre-existing record/revision is retained byte-for-value and by hash."""
    from crucible import notebook
    notes = {note["id"]: note for note in updated["notes"]}
    for old in original["notes"]:
        current = notes.get(old["id"])
        if current is None or current["revisions"][:len(old["revisions"])] != old["revisions"]:
            raise ValueError("An existing curated revision changed or disappeared")
        for before, after in zip(old["revisions"], current["revisions"]):
            if notebook._hash(contents(before)) != notebook._hash(contents(after)):
                raise ValueError("An existing curated revision hash changed")


def package_notes(workspace, presentations, fingerprint, receipt, receipt_reference, *, created_at):
    """Return a new corpus and exact bindings; no input or publication is mutated."""
    from crucible import notebook, note_sections
    from scripts.build_display_notes import append_revision

    updated = deepcopy(workspace)
    previous_labels, used_labels = {}, set()
    releases = presentations["releases"]
    by_id = {"curated:" + note["id"]: note for note in updated["notes"]}
    for release, row in releases.items():
        note = by_id.get(row["source_note"]["id"])
        if not note:
            raise ValueError("A pinned source note is missing")
        for revision in note["revisions"]:
            metadata = revision.get("extensions", {}).get("scheme_dossier")
            if not metadata:
                continue
            if metadata.get("generator") != GENERATOR:
                raise ValueError("Another dossier generator owns this note history")
            for premise in numbered_premises(revision):
                if not re.fullmatch(r"A-[1-9][0-9]*", premise["label"]):
                    raise ValueError("Invalid historical dossier premise label")
                key = (release, premise["id"])
                label = premise["label"]
                if (key in previous_labels and previous_labels[key] != label
                        or any(other != key and value == label for other, value in previous_labels.items())):
                    raise ValueError("Historical dossier premise labels are ambiguous")
                previous_labels[key] = label
                used_labels.add(int(label[2:]))
    next_label = max(used_labels, default=0) + 1
    bindings = []
    for release in sorted(releases):
        row = releases[release]
        scheme, version = release.split("@", 1)
        pin = row["source_note"]
        note = by_id[pin["id"]]
        source = next((r for r in note["revisions"] if r["revision"] == pin["revision"]), None)
        if source is None or notebook._hash(contents(source)) != pin["content_sha256"]:
            raise ValueError("The exact original evidence note changed")
        previous = contents(note["revisions"][-1])
        if (notebook._hash(previous) != pin["content_sha256"] and
                previous.get("extensions", {}).get("scheme_dossier", {}).get("generator") != GENERATOR):
            raise ValueError("An independent later revision needs editorial review before packaging")
        original = {**pin, "content": contents(source)}
        for key in ("research_note", "display_evidence"):
            if previous.get("extensions", {}).get(key) != original["content"]["extensions"][key]:
                raise ValueError("The original display evidence provenance changed")
        labels = {}
        for premise in row["assumptions"]:
            if premise["status"] not in {"model-premise", "unproved"}:
                raise ValueError("Only authored premises receive assumption labels")
            label = previous_labels.get((release, premise["id"]))
            if label is None:
                label = f"A-{next_label}"
                next_label += 1
            labels[premise["label"]] = label
        content = build_content(release, row, original, fingerprint, labels, local_fixture=False)
        content = preserve_unrelated_content(previous, content)
        content["extensions"]["scheme_dossier"] = {"generator": GENERATOR,
            "source_revision": receipt["source_revision"], "runtime_evidence": receipt_reference,
            "status": "editorial-inclusion-not-scientific-admission"}
        notebook.NoteContent.model_validate(content)
        if "local_fixture" in content["extensions"]:
            raise ValueError("Local fixture content cannot be packaged as a hosted dossier")
        revision = append_revision(updated, note["id"], content,
            now=max(created_at, source["createdAt"] + 1))
        selected = {"id": pin["id"], "revision": revision["revision"], "content_sha256": notebook._hash(content)}
        bindings.append({"scheme_id": scheme, "version": version, "note": selected,
            "sections": {"security": "security", "correctness": "correctness"},
            "arguments": row["argument_bindings"], "graph_models": row["graph_models"]})
    manifest = {"format": "crucible-note-presentation-bindings-v1", "releases": bindings}
    note_sections.BindingManifest.model_validate(manifest)
    notebook._workspace(updated)
    preserve_history(workspace, updated)
    return updated, manifest


def plan(root, evidence_directory, receipt_path, *, expected_revision=None):
    from crucible import notebook, display_notes

    receipt, raw_receipt, native_files, presentations = verify_evidence(root, evidence_directory, receipt_path,
                                                                       expected_revision=expected_revision)
    source_revision = receipt["source_revision"]
    baseline = read_json(git_file(root, source_revision, "web/map/notes.json"))
    current = read_json((root / "web/map/notes.json").read_bytes())
    preserve_history(baseline, current)
    reference = {"path": "docs/evidence/note-runtime-" + source_revision + ".json", "sha256": sha(raw_receipt)}
    receipt_destination = root / reference["path"]
    if receipt_destination.exists() and receipt_destination.read_bytes() != raw_receipt:
        raise ValueError("Immutable runtime receipt collision")
    # Check original display artifacts without replacing their timings or pins.
    for row in presentations["releases"].values():
        original = row["source_note"]
        note = next(n for n in current["notes"] if "curated:" + n["id"] == original["id"])
        revision = next(r for r in note["revisions"] if r["revision"] == original["revision"])
        provenance = revision["extensions"]["display_evidence"]
        checksum = provenance["evidence_sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", checksum) or provenance["snapshot_file"] != checksum + ".json":
            raise ValueError("Invalid original display evidence identity")
        raw = (root / "crucible/data/display_evidence" / provenance["snapshot_file"]).read_bytes()
        if display_notes.digest(read_json(raw)) != checksum:
            raise ValueError("The original display artifact changed")
    created_at = int(subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%ct", source_revision], timeout=30)) * 1000
    updated, manifest = package_notes(current, presentations, sha(native_files[NATIVE_FILES[1]]), receipt, reference, created_at=created_at)
    preserve_history(baseline, updated)
    spec = importlib.util.spec_from_file_location("scheme_dossier_map_builder", root / "web/map/build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    catalogue = read_json((root / "web/map/catalogue.json").read_bytes())
    projection = builder.project(catalogue, updated)
    for note in updated["notes"]:
        for revision in note["revisions"]:
            if builder.content_hash(revision) != notebook._hash(contents(revision)):
                raise ValueError("Browser and server content hashes disagree")
    pretty = lambda value: (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    compact = lambda value: json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    browser_notes = {**updated, "revision_hashes": {n["id"]: {str(r["revision"]): builder.content_hash(r)
                      for r in n["revisions"]} for n in updated["notes"]}}
    outputs = {root / "crucible/data" / name: raw for name, raw in native_files.items()}
    outputs.update({receipt_destination: raw_receipt, root / "web/map/notes.json": pretty(updated),
        root / "crucible/data/scheme_note_bindings.json": pretty(manifest),
        root / "web/map/notes-data.js": ("// Generated by build.py; edit notes.json.\nwindow.CRUCIBLE_NOTES = " + compact(browser_notes) + ";\n").encode(),
        root / "web/map/catalogue.js": ("// Generated projection; edit notes.json and catalogue.json.\nwindow.CRUCIBLE_LANDSCAPE = " + compact(projection) + ";\n").encode()})
    return outputs, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "crucible/data")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with no_database_connections():
        outputs, manifest = plan(ROOT, args.evidence_dir, args.receipt, expected_revision=args.source_revision)
        stale = [str(path.relative_to(ROOT)) for path, raw in outputs.items() if not path.exists() or path.read_bytes() != raw]
        if args.check and stale:
            raise SystemExit("Dossier package differs: " + ", ".join(stale))
        if not args.check:
            for path, raw in outputs.items():
                if path.exists() and path.read_bytes() == raw:
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + ".tmp")
                temporary.write_bytes(raw)
                temporary.replace(path)
    print(json.dumps({"schemes": len(manifest["releases"]), "checked": args.check,
                      "database_access": False, "scientific_admission": False, "deployment_performed": False}))


if __name__ == "__main__":
    main()
