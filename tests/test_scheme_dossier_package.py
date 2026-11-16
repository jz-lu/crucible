"""Offline dossier packaging preserves source science, history and provenance."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from crucible import notebook, note_sections
from scripts import package_scheme_dossiers as package
from scripts.scheme_note_content import numbered_premises
from deploy.note_runtime_evidence import no_database_connections

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "docs/evidence/note-runtime-a62f5e58edd57e205556591988f02f56fec7cec4.json"


@pytest.fixture
def inputs():
    workspace = json.loads((ROOT / "web/map/notes.json").read_text())
    for note in workspace["notes"]:
        note["revisions"] = [r for r in note["revisions"]
            if r.get("extensions", {}).get("scheme_dossier", {}).get("generator") != package.GENERATOR]
    source = ROOT / "crucible/data/scheme_note_presentations.json"
    presentations = json.loads(source.read_text())
    receipt = json.loads(RECEIPT.read_text())
    reference = {"path": str(RECEIPT.relative_to(ROOT)), "sha256": package.sha(RECEIPT.read_bytes())}
    return workspace, presentations, package.sha(source.read_bytes()), receipt, reference


def build(inputs, *, created_at=1):
    with no_database_connections():
        return package.package_notes(*inputs, created_at=created_at)


def test_eight_dossiers_append_without_changing_any_original_revision_or_artifact(inputs):
    original = deepcopy(inputs[0])
    updated, manifest = build(inputs)
    assert inputs[0] == original
    package.preserve_history(original, updated)
    assert len(updated["notes"]) == len(original["notes"])
    assert sum(len(n["revisions"]) for n in updated["notes"]) == sum(len(n["revisions"]) for n in original["notes"]) + 8
    notes = {"curated:" + n["id"]: n for n in updated["notes"]}
    statements, arguments, labels = 0, 0, []
    for binding in manifest["releases"]:
        pin = binding["note"]
        note = notes[pin["id"]]
        current, previous = note["revisions"][-1], note["revisions"][-2]
        assert current["revision"] == pin["revision"]
        assert notebook._hash(package.contents(current)) == pin["content_sha256"]
        for key in ("display_evidence", "research_note"):
            assert current["extensions"][key] == previous["extensions"][key]
        assert current["artifacts"] == previous["artifacts"]
        assert all(reference in current["references"] for reference in previous["references"])
        assert "local_fixture" not in current["extensions"]
        assert current["extensions"]["authorship"]["kind"] == "model-assisted"
        assert current["extensions"]["scheme_dossier"]["status"] == "editorial-inclusion-not-scientific-admission"
        assert "Earlier immutable curated revisions" in current["body"]
        placed = [identifier for section in current["sections"] for identifier in note_sections.statement_markers(section["body"])]
        assert len(placed) == len(set(placed))
        assert set(placed) == {s["id"] for s in current["statements"]}
        statements += len(current["statements"])
        arguments += len(current["arguments"])
        labels.extend(p["label"] for p in numbered_premises(current))
    assert (len(manifest["releases"]), statements, arguments) == (8, 73, 39)
    assert len(labels) == len(set(labels)) == 34


def test_package_is_deterministic_and_reuses_exact_revisions_with_stable_labels(inputs):
    first, manifest = build(inputs)
    repeated, same = build((first, *inputs[1:]), created_at=9999999999999)
    assert repeated == first and same == manifest
    # Object ordering cannot reassign a new global label or alter a dossier.
    reordered = deepcopy(inputs[1])
    reordered["releases"] = dict(reversed(list(reordered["releases"].items())))
    assert build((inputs[0], reordered, *inputs[2:])) == (first, manifest)


def test_unrelated_records_and_editorial_selection_are_not_owned_by_the_packager(inputs):
    workspace = inputs[0]
    workspace["extensions"] = {"editorial_selection": {"archived": {workspace["notes"][0]["id"]: "An editorial decision."}}}
    updated, _ = build(inputs)
    assert updated["extensions"] == workspace["extensions"]
    assert updated["notes"][0] == workspace["notes"][0]


@pytest.mark.parametrize("mutation", ["source-hash", "independent-edit", "display-artifact"])
def test_changed_sources_or_unreviewed_later_content_fail_before_any_mutation(inputs, mutation):
    workspace, presentations = inputs[:2]
    row = next(iter(presentations["releases"].values()))
    note = next(n for n in workspace["notes"] if "curated:" + n["id"] == row["source_note"]["id"])
    if mutation == "source-hash":
        row["source_note"]["content_sha256"] = "0" * 64
    else:
        later = deepcopy(note["revisions"][-1]); later["revision"] += 1
        if mutation == "independent-edit":
            later["body"] += " Unreviewed independent edit."
        else:
            later["extensions"]["scheme_dossier"] = {"generator": package.GENERATOR}
            later["extensions"]["display_evidence"]["evidence_sha256"] = "0" * 64
        note["revisions"].append(later)
    before = deepcopy(workspace)
    with pytest.raises(ValueError):
        build(inputs)
    assert workspace == before


def test_history_comparison_detects_erasure_and_rewrites(inputs):
    workspace = inputs[0]
    for mutation in (lambda data: data["notes"].pop(),
                     lambda data: data["notes"][0]["revisions"][0].update(body="Rewritten historical text.")):
        changed = deepcopy(workspace); mutation(changed)
        with pytest.raises(ValueError, match="changed or disappeared"):
            package.preserve_history(workspace, changed)


def test_packaged_native_files_retain_the_exact_reviewed_receipt_bytes():
    receipt = json.loads(RECEIPT.read_text())
    for name, expected in receipt["files"].items():
        assert package.sha((ROOT / "crucible/data" / name).read_bytes()) == expected
    assert receipt["python"] == "3.13.17" and receipt["analytical_accounts"] == 379
    assert len(receipt["runtime_pin_changes"]) == 229
    assert receipt["numerical_evidence_unchanged"] and not receipt["database_access"]


def test_map_hashes_match_server_for_both_legacy_and_canonical_revisions(inputs):
    spec = importlib.util.spec_from_file_location("package_map_builder", ROOT / "web/map/build.py")
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    updated, _ = build(inputs)
    for note in updated["notes"]:
        for revision in note["revisions"]:
            assert builder.content_hash(revision) == notebook._hash(package.contents(revision))
    sources = {"known": {"id": "known", "title": "Registered", "url": "https://example.org"}}
    builder.validate_citation(sources["known"], sources)
    builder.validate_citation({"title": "Independent citation", "url": "https://example.org/paper"}, sources)
    for bad in ({"id": "known", "title": "Changed", "url": "https://example.org"},
                {"id": "unknown", "title": "Unknown", "url": "https://example.org"},
                {"title": "Unsafe", "url": "javascript:alert(1)"},
                {"title": "Credentials", "url": "https://user:password@example.org"},
                {"title": "Whitespace", "url": "https://example.org/a b"}):
        with pytest.raises(ValueError):
            builder.validate_citation(bad, sources)
