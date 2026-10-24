#!/usr/bin/env python3
"""Append immutable, substantive notes supporting the current display evidence.

No network, benchmark, challenge generation, database or submitted code is run.
Run from the checkout: python scripts/build_display_notes.py [--check].
The profile projection retains explicit parameter and graph records. Dense
evidence is stored separately, so notebook revisions remain small and portable.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from crucible import assumption_sheets, benchmark_reports, notebook, reference_policy, registry, security_references

GENERATOR = "crucible-display-notes-v1"
FORMAT = "crucible-display-evidence-v1"
CONTENT_FIELDS = notebook.CONTENT_FIELDS
PROFILE_FIELDS = (
    "profile_kind", "profile_order", "parameter_bits",
    "parameter_label", "stage_number", "stage_position", "stage_context",
    "native_parameter_label", "progression_id", "parameters", "encoding", "shared_secret_bytes",
    "initial_security_estimate", "correctness", "security_claim", "assumption_context", "distribution",
    "algorithms", "kdf", "implementation", "references", "record_inheritance",
)
DESCRIPTOR_FIELDS = (
    "id", "version", "name", "summary", "overview", "protocol", "stage_range",
    "ladder_design", "size_parameter", "profile_metrics_basis", "initial_security_model",
    "release_kind", "review_status", "review_note", "failure_policy", "record_inheritance",
)
OVERLAYS = (
    "Live recovery markers and participant totals are not frozen observations in this note. "
    "They derive from the site's accepted submission receipts and account records at request time. "
    "A highest-record marker identifies the largest recorded configuration; shading below it is a "
    "navigation/progress convention, not evidence that each smaller configuration was independently "
    "attacked or a proof of a security level. Receipt details, exact release, configuration and any "
    "registered inheritance rule remain authoritative. Participants count distinct accounts that "
    "attempted the scheme, not independently verified unique humans. Neither no recoveries nor a "
    "large participant count proves security."
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def content_hash(revision):
    return digest({key: value for key, value in revision.items() if key in CONTENT_FIELDS})


def profile_projection(profiles, metrics):
    return [{"id": profile["id"],
             **{field: deepcopy(profile[field]) for field in PROFILE_FIELDS if field in profile},
             "metrics": deepcopy(metrics[profile["id"]])} for profile in profiles]


def expand_profiles(projection):
    """Return selected manifest fields without the separately derived byte metrics."""
    return [{key: deepcopy(value) for key, value in profile.items() if key != "metrics"}
            for profile in projection]


def artifact_records(value):
    paths = set()
    def visit(item):
        if isinstance(item, str):
            paths.update(re.findall(r"(?:docs|crucible|scripts)/[A-Za-z0-9_./-]+\.(?:json|txt|py|sage|c|h)\b", item))
        elif isinstance(item, dict):
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    result = []
    for name in sorted(paths):
        path = (ROOT / name).resolve()
        if path.is_relative_to(ROOT) and path.is_file():
            result.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "scope": "Pinned local evidence or implementation identified by this release; a hash is provenance, not verification."})
    return result


def portable_numbers(value):
    """Preserve exact large parameter integers without JavaScript rounding."""
    if type(value) is int and abs(value) > 9007199254740991:
        return {"integer_decimal": str(value)}
    if isinstance(value, dict): return {key: portable_numbers(item) for key, item in value.items()}
    if isinstance(value, list): return [portable_numbers(item) for item in value]
    return value


def source_snapshot(adapter):
    """Deterministic graph/reading sources, explicitly excluding evidence-note links."""
    descriptor = registry.descriptor(adapter)
    report = benchmark_reports.report_for(adapter)
    publication = benchmark_reports._publication(adapter.SCHEME_ID, str(adapter.SCHEME_VERSION)) if report else None
    snapshot = {
        "format": FORMAT, "scheme_id": adapter.SCHEME_ID, "version": str(adapter.SCHEME_VERSION),
        "descriptor": {key: deepcopy(descriptor[key]) for key in DESCRIPTOR_FIELDS if key in descriptor},
        "profiles": profile_projection(descriptor["profiles"], descriptor["profile_metrics"]),
        "assumption_sheet": assumption_sheets.sheet_for(adapter.SCHEME_ID, str(adapter.SCHEME_VERSION)),
        "security_references": security_references.for_release(adapter.SCHEME_ID, str(adapter.SCHEME_VERSION)),
        "timing_report": report,
        "benchmark_publication": {key: publication[key] for key in ("file", "sha256", "status")} if publication else None,
        "reference_method": deepcopy(reference_policy.METHOD),
        "live_overlays": OVERLAYS,
    }
    snapshot["local_artifacts"] = artifact_records(snapshot)
    return portable_numbers(snapshot)


def guide_snapshot():
    path = "docs/proposal-guide.txt"
    raw = (ROOT / path).read_bytes()
    return {"format": "crucible-proposal-guide-evidence-v1", "path": path,
            "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8"),
            "scope": "Procedural advice summarizing current proposal policy; not approval, mathematical evidence, or execution authority."}


def source_records(value):
    found = {}
    def visit(item):
        if isinstance(item, dict):
            if isinstance(item.get("url"), str) and item["url"].startswith("https://") and isinstance(item.get("title"), str):
                found.setdefault(item["url"], {"title": item["title"], "url": item["url"]})
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    return list(found.values())


def references_for(snapshot, catalogue):
    by_url = {source["url"]: source for source in catalogue["sources"]}
    by_id = {source["id"]: source for source in catalogue["sources"]}
    result = []
    for source in source_records(snapshot):
        if source["url"] not in by_url:
            identifier = "display-source-" + hashlib.sha256(source["url"].encode()).hexdigest()[:16]
            if identifier in by_id:
                raise ValueError("Source identifier collision: " + identifier)
            record = {"id": identifier, **source, "scope": "Primary source context for the exact release's named model or argument. Applicability, numerical evidence and limitations are stated in the pinned evidence note; citation is not certification."}
            catalogue["sources"].append(record)
            by_url[source["url"]] = by_id[identifier] = record
        result.append(deepcopy(by_url[source["url"]]))
    return result


def prose(value):
    if isinstance(value, str): return value
    if isinstance(value, list): return "\n".join("- " + prose(item) for item in value)
    if isinstance(value, dict): return "\n\n".join(str(key).replace("_", " ").capitalize() + ": " + prose(item) for key, item in value.items())
    return str(value)


def release_body(snapshot):
    d, sheet = snapshot["descriptor"], snapshot["assumption_sheet"]
    lines = [f"EXACT RELEASE: {snapshot['scheme_id']}@{snapshot['version']}", d["summary"],
             "This is a traceable compilation of the display's current evidence, not an independent proof or review. The structured snapshot retains exact configuration data and source identities.",
             "SCHEME OVERVIEW", prose(d.get("overview", {})), "CURATED MATHEMATICAL OVERVIEW", sheet.get("lead", "")]
    presentation = sheet.get("presentation") or {}
    if presentation.get("overview"): lines.append(prose(presentation["overview"]))
    elif sheet.get("overview"): lines.append(prose(sheet["overview"]))
    lines += ["ENCODED SIZE AND CONFIGURATION TABLE", prose(d["profile_metrics_basis"]),
              "Each row is an exact registered configuration. Stage is the default progression position, not a universal security level. PK and CT are canonical binary bytes; a dash is missing evidence, never zero. Full parameter tuples, encoding rules and implementation hashes are retained in the structured snapshot.",
              "Configuration | Stage | Native setting | PK bytes | CT bytes | Frozen classical reference"]
    profiles = expand_profiles(snapshot["profiles"])
    for p, row in zip(profiles, snapshot["profiles"]):
        metrics = row["metrics"]
        estimate = p.get("initial_security_estimate") or {}
        bit = estimate.get("bits")
        native = p.get("native_parameter_label") or (str(p.get("parameter_bits", "")) + " " + p.get("parameter_label", ""))
        lines.append(f"{p['id']} | {p.get('stage_number', '—')} | {native} | {metrics.get('public_key_bytes', '—')} | {metrics.get('ciphertext_bytes', '—')} | {bit if bit is not None else '—'}")
    lines += ["SECURITY REFERENCE: SCOPE AND UNITS",
              "Frozen references are unchanged by successful recoveries. Values are logarithms of the named model's work quantity, not proven security lower bounds, measured attack times or a common unit across schemes. Use a supplemental point only for its exact profile and only where the frozen metric has no value. Do not interpolate unsupported regions, halve classical values to manufacture a quantum curve, or interpret an empty graph as zero work.",
              prose(d.get("initial_security_model") or "No frozen numerical classical model is published for this release."),
              prose(snapshot.get("security_references") or "No supplemental reference is published.")]
    report = snapshot["timing_report"]
    lines.append("REFERENCE OPERATION TIMINGS")
    if report:
        statuses = {}
        for row in report["results"]: statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        lines += [f"Report {report['id']}; measured {report['measured_at']}; status {report['status']}. Operation rows by status: {prose(statuses)}.",
                  "Implementation: " + prose(report["implementation"]), "Environment: " + prose(report["environment"]),
                  "Actual sampling: " + prose(report["sampling"]),
                  "Every exact operation result, median, quartiles, sample count and failure count is retained in timing_report.results in the linked immutable structured snapshot. The raw publication is pinned by file name and SHA-256, so these summaries can be audited without treating display labels as measurements.",
                  "Report notes: " + prose(report.get("notes", []))]
    else:
        lines.append("No currently validated timing publication supports this release. Missing timings are gaps, not estimates.")
    method = snapshot["reference_method"]
    for key in ("workload", "includes", "excludes", "statistics", "limits", "interpretation"):
        lines.append(key.capitalize() + ": " + prose(method[key]))
    lines += ["EXACT MATHEMATICAL ITEMS", "The entries below have different jobs: hardness questions, modeling premises, conditional calculations, proof obligations and implementation obligations. Reading links and supports labels identify relevance, not verified logical implication or mathematical truth."]
    for claim in sheet["claims"]:
        lines.append(f"{claim['title']} [{claim['id']}; {claim['role']}]")
        for key in ("summary", "purpose", "precise_statement", "scope", "depends_on", "status", "challenge", "limitations"):
            if key in claim: lines.append(key.replace("_", " ").capitalize() + ": " + prose(claim[key]))
        lines.append("Source context: " + "; ".join(source["title"] + " — " + source["url"] for source in claim["sources"]))
    lines += ["READING BOUNDARIES", prose(sheet.get("limitations", [])), "LIVE OVERLAYS AND PARTICIPANTS", OVERLAYS,
              "PROVENANCE", "Exact source/artifact hashes and local certificate paths are retained in local_artifacts and the per-profile implementation fields of the snapshot. Primary references below provide context; the locally calculated bounds and measured timings are attributed to their local artifacts, not to a standard that never asserted those research results."]
    return "\n\n".join(lines)


def note_content(snapshot, references, *, guide=False):
    sid = snapshot.get("scheme_id")
    checksum = digest(snapshot)
    query = {"scheme_id": sid, "version": snapshot["version"]} if sid else {"note": "true"}
    query.update(evidence_sha256=checksum, data="true")
    data_url = ("/api/protocol/evidence-note?" if sid else "/api/protocol/proposal-guide?") + urlencode(query)
    return {
        "title": "Proposal advice — requirements, evidence and review" if guide else snapshot["descriptor"]["name"] + " — graphs, premises and evidence",
        "claim": ("A Crucible Proposal requests a concrete change supported by pinned note evidence: pipeline or process changes, content or visualizations, a new KEM or improved parameters, or challenge-scoring enrollment. The KEM path additionally requires a precise specification, scoped correctness and security claims, justified configurations and manual review. Authors should explicitly retract requests they now consider misguided or superseded, preserving the record and reason. This guidance is procedural; a recommendation does not itself publish, activate or award anything."
                  if guide else f"The displayed sizes, model-based security references, reference timings and mathematical reading for {sid}@{snapshot['version']} are grounded in the exact evidence compiled here. Their stated scopes differ; none by itself certifies full KEM security."),
        "conditions": (snapshot["scope"] if guide else "Applies only to this exact registered release and the configurations in the snapshot. Preserves model assumptions, missing points, conditional correctness, implementation-specific timings and outstanding proof obligations. Curated compilation is not independent scientific review. Live user records are separately evidenced by accepted receipts."),
        "body": snapshot["text"] if guide else release_body(snapshot),
        "tags": ["proposal-guidance", "procedure"] if guide else ["display-evidence", "scheme-evidence", "kem", sid],
        "kind": "Procedural guidance" if guide else "Release evidence compilation",
        "references": references,
        "artifacts": [{"path": "crucible/data/display_evidence/" + checksum + ".json", "sha256": checksum,
                       "url": data_url, "scope": "Immutable canonical JSON source snapshot; exact parameters, graph rows, claims and provenance."}],
        "extensions": {
            "research_note": {"schema_version": 1, "role": "procedural-guidance" if guide else "release-evidence",
                              "origin": "crucible-compilation", "scheme_ids": [] if guide else [sid],
                              "prepared_by": "Codex · curated evidence compilation",
                              "evidence_scope": "Version-pinned source compilation; not independent proof, approval or scientific certification."},
            "display_evidence": {"format": FORMAT, "generator": GENERATOR, "kind": "proposal-guide" if guide else "release",
                                 "evidence_sha256": checksum, "snapshot_file": checksum + ".json", "data_url": data_url},
        },
    }


def append_revision(workspace, identifier, content, *, now=None):
    """Idempotent append; never adopt unrelated content or change old revisions."""
    notebook._content(content)
    existing = next((note for note in workspace["notes"] if note["id"] == identifier), None)
    if existing:
        for revision in existing["revisions"]:
            metadata = revision.get("extensions", {}).get("display_evidence", {})
            if metadata.get("generator") != GENERATOR:
                raise ValueError("Refusing existing note ID collision: " + identifier)
        if content_hash(existing["revisions"][-1]) == content_hash(content):
            return existing["revisions"][-1]
    else:
        existing = {"id": identifier, "revisions": []}
        workspace["notes"].append(existing)
    revision = {"revision": len(existing["revisions"]) + 1,
                "createdAt": int(time.time() * 1000) if now is None else now, **deepcopy(content)}
    existing["revisions"].append(revision)
    return revision


def binding(identifier, revision, snapshot):
    note_id = "curated:" + identifier
    note = {"id": note_id, "revision": revision["revision"], "content_sha256": content_hash(revision)}
    note["url"] = "#notebook?" + urlencode({"note": note_id, "revision": note["revision"], "sha256": note["content_sha256"]})
    result = {"note": note, "evidence_sha256": digest(snapshot), "snapshot_file": digest(snapshot) + ".json"}
    if snapshot.get("scheme_id"):
        report = snapshot["timing_report"]
        result.update(scheme_id=snapshot["scheme_id"], version=snapshot["version"],
                      timing_report_id=report["id"] if report else None,
                      timing_report_sha256=digest(report) if report else None)
    return result


def generate(workspace, catalogue, *, now=None):
    """Return new copies, preserving every existing record and revision."""
    workspace, catalogue = deepcopy(workspace), deepcopy(catalogue)
    bindings = {"format": "crucible-display-note-bindings-v1", "releases": []}
    for sid, version in registry.CURRENT.items():
        snapshot = source_snapshot(registry.ADAPTERS[sid, version])
        identifier = "display-evidence-" + sid + "-" + re.sub(r"[^a-z0-9-]", "-", version.lower())
        content = note_content(snapshot, references_for(snapshot, catalogue))
        revision = append_revision(workspace, identifier, content, now=now)
        bindings["releases"].append(binding(identifier, revision, snapshot))
    snapshot = guide_snapshot()
    revision = append_revision(workspace, "proposal-advice", note_content(snapshot, [], guide=True), now=now)
    bindings["proposal_guide"] = binding("proposal-advice", revision, snapshot)
    notebook._workspace(workspace)
    return workspace, catalogue, bindings


def build(*, check=False):
    map_root = ROOT / "web/map"
    spec = importlib.util.spec_from_file_location("display_note_map_build", map_root / "build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    workspace = json.loads((map_root / "notes.json").read_text())
    catalogue = json.loads((map_root / "catalogue.json").read_text())
    updated, catalogue, bindings = generate(workspace, catalogue)
    projection = builder.project(catalogue, updated)
    hashes = {n["id"]: {str(r["revision"]): content_hash(r) for r in n["revisions"]} for n in updated["notes"]}
    pretty = lambda value: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    compact = lambda value: json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    outputs = {
        map_root / "notes.json": pretty(updated), map_root / "catalogue.json": pretty(catalogue),
        map_root / "notes-data.js": "// Generated by build.py; edit notes.json.\nwindow.CRUCIBLE_NOTES = " + compact({**updated, "revision_hashes": hashes}) + ";\n",
        map_root / "catalogue.js": "// Generated projection; edit notes.json and catalogue.json.\nwindow.CRUCIBLE_LANDSCAPE = " + compact(projection) + ";\n",
        ROOT / "crucible/data/display_note_bindings.json": pretty(bindings),
    }
    snapshots = [source_snapshot(registry.ADAPTERS[sid, version]) for sid, version in registry.CURRENT.items()] + [guide_snapshot()]
    for snapshot in snapshots:
        path = ROOT / "crucible/data/display_evidence" / (digest(snapshot) + ".json")
        text = canonical(snapshot).decode("utf-8")
        if path.exists() and path.read_text() != text:
            raise ValueError("Immutable evidence artifact collision: " + str(path))
        outputs[path] = text
    for note in updated["notes"]:
        for revision in note["revisions"]:
            metadata = revision.get("extensions", {}).get("display_evidence", {})
            if metadata.get("generator") == GENERATOR:
                path = ROOT / "crucible/data/display_evidence" / metadata["snapshot_file"]
                raw = outputs.get(path, path.read_text() if path.exists() else None)
                if raw is None or digest(json.loads(raw)) != metadata["evidence_sha256"]:
                    raise ValueError("Missing or changed historical evidence artifact: " + str(path))
    changed = [str(path.relative_to(ROOT)) for path, text in outputs.items() if not path.exists() or path.read_text() != text]
    if check and changed:
        raise ValueError("Display-note outputs are stale: " + ", ".join(changed))
    if not check:
        for path, text in outputs.items():
            if str(path.relative_to(ROOT)) in changed:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    print(f"{'Checked' if check else 'Built'} {len(bindings['releases'])} release notes and proposal advice; {len(updated['notes'])} total notes; {len(outputs[map_root / 'notes.json'].encode())} workspace bytes.")
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate current evidence and generated payloads without writing.")
    args = parser.parse_args()
    build(check=args.check)
