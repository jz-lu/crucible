"""Curated guide refreshes preserve historical citations and editorial ownership."""
from copy import deepcopy
import importlib.util
import json

import pytest

from scripts import package_launch_guides as package
from scripts.prepare_launch_guides import prepared_guides
from scripts.package_scheme_dossiers import preserve_history


def inputs():
    workspace = json.loads((package.ROOT / "web/map/notes.json").read_text())
    bindings = json.loads((package.ROOT / "crucible/data/scheme_note_bindings.json").read_text())
    return workspace, prepared_guides(), bindings


def test_repeated_packaging_is_identical_and_a_guide_revision_retains_all_citations():
    before, guides, bindings = inputs()
    with package.no_database_connections():
        current = package.package(before, guides, bindings, created_at=0)
        assert current == before
        changed = deepcopy(guides)
        changed[0]["content"]["body"] += "\n\nAn additional reviewed operational clarification."
        revised = package.package(current, changed, bindings, created_at=0)
    preserve_history(before, revised)
    target = "launch-" + changed[0]["id"]
    for old, new in zip(before["notes"], revised["notes"]):
        assert old["id"] == new["id"]
        assert len(new["revisions"]) == len(old["revisions"]) + (old["id"] == target)
    archive = revised["extensions"]["editorial_selection"]["archived"]
    featured = {n["id"] for n in revised["notes"]} - archive.keys()
    assert len(featured) == 12 and package.OLD_GUIDES <= archive.keys()
    assert {"launch-" + name for name in package.GUIDES} <= featured


def test_independently_owned_guide_is_not_adopted_or_overwritten():
    before, guides, bindings = inputs()
    note = next(n for n in before["notes"] if n["id"] == "launch-start-here")
    del note["revisions"][-1]["extensions"]["curated_launch_guide"]
    snapshot = deepcopy(before)
    with pytest.raises(ValueError, match="independent note"):
        package.package(before, guides, bindings, created_at=0)
    assert before == snapshot


@pytest.mark.parametrize("url,allowed", [
    ("/api/agent/pipeline", True),
    ("/api/agent/guidance/note-format.txt", True),
    ("//other.example/api/agent/pipeline", False),
    ("/api/agent/guidance/../private", False),
    ("/api/agent/guidance/%2e%2e%2fprivate", False),
    ("javascript:alert(1)", False),
])
def test_guide_citations_only_accept_explicit_local_reading_routes(url, allowed):
    spec = importlib.util.spec_from_file_location("launch_map_test", package.ROOT / "web/map/build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reference = {"title": "Reading contract", "url": url, "scope": "Current guidance", "relation": "cites"}
    if allowed:
        module.validate_citation(reference, {})
    else:
        with pytest.raises(ValueError):
            module.validate_citation(reference, {})
