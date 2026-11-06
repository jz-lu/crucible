#!/usr/bin/env python3
"""Read-only fit check of all current scheme notes against optional statement blocks.

Produces review candidates, not adopted notes. Preserves exact source text,
configuration identities and recorded claim strength. Missing use metadata is
reported for authors; validation does not establish mathematical correctness.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_report():
    from crucible import registry
    from crucible.attack_models import catalogue
    from crucible.statement_workflow import Statement, Argument

    source_path = ROOT / "crucible/data/scheme_note_presentations.json"
    source = json.loads(source_path.read_text())
    report = {"format": "crucible-statement-fit-v1", "read_only": True,
        "scientific_approval": False, "ready_for_adoption": False,
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(), "releases": {}}
    for release, row in source["releases"].items():
        scheme_id, version = release.split("@", 1)
        adapter = registry.adapter_for(scheme_id, version)
        descriptor = registry.descriptor(adapter)
        configurations = {profile["id"]: {"stage": profile["stage_number"],
            "manifest": adapter.get_manifest(profile["id"])} for profile in descriptor["profiles"]}
        assert set(configurations) == set(row["profile_ids"]), release
        for config in configurations.values():
            config["manifest_sha256"] = catalogue.digest(config["manifest"])
        ram = catalogue.for_release(scheme_id, version,
            expected_manifests=[config["manifest"] for config in configurations.values()])
        assert ram is not None and ram["analysis_sha256"] == row["ram_analysis_sha256"], release
        sections = {section["id"]: section for section in row["sections"]}
        premises = {premise["id"]: premise for premise in row["assumptions"]}
        statements, declaration_scope = {}, {}
        gaps = []

        for identifier, premise in premises.items():
            section = sections[premise["section_id"]]
            text = premise.get("statement") or section["body"]
            # Scope is part of interpretation, not inferred from a familiar name.
            if premise.get("scope"):
                text += "\n\nDeclared scope: " + premise["scope"]
            candidate = {"id": identifier, "label": premise["label"], "title": section["title"], "definition": text}
            Statement.model_validate(candidate)
            statements[identifier] = candidate
            ids = premise.get("profile_ids")
            stages = premise.get("stages", [])
            if ids is None:
                ids = [key for key, value in configurations.items() if value["stage"] in stages]
            assert set(ids) <= set(configurations), (release, identifier)
            # Registered off-progression configurations have no stage number;
            # their native IDs must remain in coverage rather than disappear.
            assert set(stages) == {configurations[key]["stage"] for key in ids
                                   if configurations[key]["stage"] is not None}, (release, identifier)
            declaration_scope[identifier] = {"configuration_ids": list(ids), "stages": list(stages),
                "source_section": premise["section_id"], "source_kind": "formal-statement" if premise.get("statement") else "exact-readable-section",
                "definition_sha256": catalogue.digest({"definition": text, "definitions": []})}
            gaps.append({"statement_id": identifier, "field": "definitions",
                "reason": "Pin normative samplers/algorithm definitions to actual note revisions before publication. This read-only source adapter has no created note identity."})

        def use(identifier, category, active_ids=None):
            premise = premises[identifier]
            coverage = declaration_scope[identifier]["configuration_ids"]
            if active_ids is not None:
                coverage = [key for key in coverage if key in active_ids]
            # Full source premise is preserved as a sufficient assumption. The
            # adapter explicitly does not infer a weaker minimal requirement.
            candidate = {"statement": {"statement_id": identifier}, "configuration_ids": coverage,
                "use": "Use the complete referenced proposition as written. Exact release " + release + ". Use the unmodified registered manifests for the configuration IDs above. " +
                    premise.get("scope", "The exact source section and per-profile attack account retain the probability space and applicability.") +
                    " The authored " + category + " argument cites this premise. Preserve the exact source section " +
                    premise["section_id"] + " and its full reasoning; the fit adapter does not supply a new mathematical implication."}
            gaps.append({"argument": category, "statement_id": identifier, "field": "mapping",
                "reason": "Native parameters and manifest identities are preserved, but the variable-by-variable substitution and sampler reference still need an author's explicit pin."})
            if not identifier.startswith("ram-"):
                gaps.append({"argument": category, "statement_id": identifier, "field": "required",
                    "reason": "The entire authored premise is retained. Isolate the actual required strength; do not assume it equals a scheme-wide correctness target or a conventional named assumption."})
            return candidate

        arguments = []
        for category in ("security", "correctness"):
            authored = row["scheme_arguments"][category]
            identifiers = row["scheme_assumptions"][category]
            assert set(identifiers) <= set(premises), release
            candidate = {"id": "scheme-" + category, "title": sections[category]["title"],
                "conclusion": authored["scope"], "scope": authored["scope"], "status": authored["status"],
                "section_id": category, "dependencies": [use(identifier, category) for identifier in identifiers]}
            Argument.model_validate(candidate)
            arguments.append({"kind": category, "candidate": candidate,
                "source_section": deepcopy(sections[category]), "authored_status": authored["status"],
                "configuration_ids": list(row["profile_ids"]), "scientific_approval": False})

        models = {model["id"]: model for model in ram["models"]}
        for identifier, graph in row["graph_models"].items():
            if graph["metric"] != "ram":
                continue
            points = [point for point in ram["estimates"] if point["model_id"] == identifier]
            assert points, (release, identifier)
            active_ids = {point["profile_id"] for point in points}
            declared = set(graph["assumption_ids"])
            actual = {"ram-" + premise["id"] for point in points for premise in point["assumptions"]}
            assert actual <= declared, (release, identifier, actual, declared)
            dependencies = []
            for premise_id in graph["assumption_ids"]:
                uses = {point["profile_id"] for point in points
                        if any("ram-" + item["id"] == premise_id for item in point["assumptions"])}
                if not uses:
                    continue
                dependency = use(premise_id, identifier, uses)
                assert set(dependency["configuration_ids"]) == uses, (release, identifier, premise_id)
                dependencies.append(dependency)
            statuses = {point["success_status"] for point in points}
            if dependencies:
                status = "conditional"
            elif statuses <= {"proved-for-honest-inputs", "analytical-bound"}:
                # This is copied from explicit authored success metadata, never
                # inferred merely because a dependency list happens to be empty.
                status = "unconditional"
            else:
                status = "incomplete"
            model = models[identifier]
            candidate = {"id": identifier, "title": model["label"],
                "conclusion": model["scope"] + " This is an explicit attack-work upper bound, not a lower bound on security or a best-known-attack claim.",
                "scope": "Exact release " + release + "; selected registered configurations and the pinned bit-RAM account. " + model["scope"],
                "status": status, "section_id": graph["section_id"], "dependencies": dependencies}
            Argument.model_validate(candidate)
            arguments.append({"kind": "ram-attack", "candidate": candidate,
                "configuration_ids": [key for key in row["profile_ids"] if key in active_ids],
                "source_section": deepcopy(sections[graph["section_id"]]),
                "computation_model_id": model["computation_model_id"], "analysis_sha256": ram["analysis_sha256"],
                "authored_success_statuses": sorted(statuses), "scientific_approval": False,
                # Preserve every numerical and probability qualifier in the exact
                # per-profile source account; no string-to-number simplification.
                "points": deepcopy(points)})
        historical = [{"id": identifier, **deepcopy(graph)} for identifier, graph in row["graph_models"].items()
                      if graph["metric"] != "ram"]
        gaps.append({"field": "reviewed_adoption", "reason": "Schema compatibility is not a scientific assessment. No real scheme presentation or current adoption was changed."})
        if historical:
            gaps.append({"field": "historical_non_ram_models", "reason": "Historical/qualitative/quantum references are retained as their existing model types, not silently converted to comparable bit-RAM estimates."})
        report["releases"][release] = {"configuration_count": len(configurations),
            "configuration_ids": list(row["profile_ids"]), "configurations": configurations,
            "statements": list(statements.values()), "statement_coverage": declaration_scope,
            "arguments": arguments, "authoring_gaps": gaps, "historical_models": historical,
            "source_note_provenance": deepcopy(row["source_note"]),
            "ready_for_adoption": False}
    counts = Counter(argument["kind"] for row in report["releases"].values() for argument in row["arguments"])
    report["summary"] = {"releases": len(report["releases"]),
        "configurations": sum(row["configuration_count"] for row in report["releases"].values()),
        "statement_blocks": sum(len(row["statements"]) for row in report["releases"].values()),
        "arguments": dict(counts), "authoring_gaps": sum(len(row["authoring_gaps"]) for row in report["releases"].values())}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optionally save the full read-only fit report; does not publish notes")
    options = parser.parse_args()
    report = build_report()
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"read_only": True, "ready_for_adoption": False, **report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
