"""Immutable curated-note generation, separate from HTTP provenance readers."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import build_display_notes as builder
from crucible import notebook, registry

ROOT = Path(__file__).resolve().parents[1]


def content(body="An explicit local claim."):
    return {
        "title": "A test note", "claim": "The test records a precise change.",
        "conditions": "Isolated fixture; no scientific authority.", "body": body,
        "tags": [], "kind": "test", "references": [], "artifacts": [],
        "extensions": {"display_evidence": {"generator": builder.GENERATOR}},
    }


def test_append_is_idempotent_and_preserves_earlier_revision():
    workspace = {"format": "crucible-theory-local-v1", "notes": [], "views": []}
    first = builder.append_revision(workspace, "fixture", content(), now=10)
    original = deepcopy(first)
    assert builder.append_revision(workspace, "fixture", content(), now=20) is first
    second = builder.append_revision(workspace, "fixture", content("A revised explicit claim."), now=30)
    assert workspace["notes"][0]["revisions"][0] == original
    assert second["revision"] == 2 and second["createdAt"] == 30
    assert builder.content_hash(first) != builder.content_hash(second)
    copied = {**first, "revision": 99, "createdAt": 999}
    assert builder.content_hash(copied) == builder.content_hash(first)


def test_existing_unrelated_identity_is_never_adopted():
    old = {"id": "fixture", "revisions": [{"revision": 1, "createdAt": 1, **content()}]}
    old["revisions"][0]["extensions"] = {"authored": True}
    workspace = {"notes": [old]}
    before = deepcopy(workspace)
    with pytest.raises(ValueError, match="collision"):
        builder.append_revision(workspace, "fixture", content(), now=10)
    assert workspace == before


def test_profile_projection_preserves_absence_null_and_exact_large_integers():
    profiles = [{"id": "a", "parameters": {"p": 2**90}, "stage_context": None},
                {"id": "b", "parameters": {"p": 2**90}}]
    projected = builder.profile_projection(profiles, {"a": {}, "b": {}})
    assert builder.expand_profiles(projected) == profiles
    portable = builder.portable_numbers(projected)
    restored = builder.expand_profiles(portable)
    assert restored[0]["parameters"]["p"] == {"integer_decimal": str(2**90)}
    assert "stage_context" not in restored[1]


def test_current_generated_sources_are_fresh_and_existing_map_bindings_survive():
    workspace = json.loads((ROOT / "web/map/notes.json").read_text())
    catalogue = json.loads((ROOT / "web/map/catalogue.json").read_text())
    generated, copied_catalogue, bindings = builder.generate(workspace, catalogue, now=1)
    assert generated == workspace  # No timestamp-induced revisions on a repeat.
    assert copied_catalogue == catalogue
    assert len(bindings["releases"]) == len(registry.CURRENT) == 8
    notes = {"curated:" + note["id"]: note for note in generated["notes"]}
    for binding in bindings["releases"]:
        revision = notes[binding["note"]["id"]]["revisions"][binding["note"]["revision"] - 1]
        snapshot_path = ROOT / "crucible/data/display_evidence" / binding["snapshot_file"]
        raw = snapshot_path.read_bytes()
        assert builder.digest(json.loads(raw)) == binding["evidence_sha256"]
        assert builder.canonical(json.loads(raw)) == raw
        assert "snapshot" not in revision["extensions"]["display_evidence"]
        assert len(revision["body"]) < 40000
        snapshot = json.loads(raw)
        for claim in snapshot["assumption_sheet"]["claims"]:
            assert claim["precise_statement"] in revision["body"]
            assert claim["scope"] in revision["body"]
        assert binding["timing_report_sha256"] == builder.digest(snapshot["timing_report"])
    assert len((json.dumps(generated, ensure_ascii=False, indent=2) + "\n").encode()) < notebook.MAX_WORKSPACE_BYTES


def test_check_mode_does_not_write(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("--check attempted a file mutation")
    monkeypatch.setattr(Path, "write_text", forbidden)
    builder.build(check=True)


def test_proposal_advice_covers_process_and_content_changes_with_a_distinct_kem_path():
    snapshot = builder.guide_snapshot()
    note = builder.note_content(snapshot, [], guide=True)
    assert note["body"] == (ROOT / "docs/proposal-guide.txt").read_text()
    for label in ("Pipeline or process change", "Content or visualization change",
                  "Scheme proposal", "Enable challenge scoring"):
        assert label in note["body"]
    assert "KEM scheme-proposal path" in note["body"]
    assert "correctness.justification" in note["body"]
    assert "tier_design.parameter_selection" in note["body"]
    assert "not points merely for writing a note" in note["body"]
    assert "pipeline or process changes" in note["claim"]
    assert "recommendation does not itself publish, activate or award" in note["claim"]
    assert "Reconsidering and retracting proposals" in note["body"]
    assert "explicitly retract" in note["claim"]
    assert "Criticism cannot automatically retract" in note["body"]
    # Publication applies to the reviewed revision; new drafts stay private.
    assert "need separate publication approval" in note["body"]
    assert "Private participant sharing is retired" in note["body"]
    assert "standalone technical_review request is retired" in note["body"]
    assert "grant notebook bearer access" in note["body"]
