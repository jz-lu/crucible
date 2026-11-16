"""Pure scheme-dossier rendering shared by local fixtures and curated packaging."""
import hashlib
import json
import re

OWNED_EXTENSIONS = {"scheme_context", "assumptions", "assumption_statements", "scheme_assumptions",
    "scheme_arguments", "scheme_security_gaps", "authorship", "source_evidence_note", "source_refs",
    "presentation_sha256", "ram_analysis_sha256", "local_fixture", "canonical_conversion"}


def readable_fixture_sections(row):
    """Apply authored placements without changing the generated scientific data.

    The generator owns readable_sections. Its optional presentation overlay pins
    exact paragraph boundaries, so an upstream prose change requires a deliberate
    layout revision instead of silently moving a statement to unrelated context.
    """
    from crucible.note_sections import ReadableSection, validate_statement_placements

    sections = [{key: section[key] for key in ("id", "title", "body")}
                for section in row["readable_sections"]]
    layout = row.get("readable_statement_layout")
    if layout is None:
        return sections
    if not isinstance(layout, list) or len(layout) > 64:
        raise ValueError("Use at most 64 authored statement placement boundaries.")
    by_id = {section["id"]: section for section in sections}
    insertions = {}
    for entry in layout:
        if set(entry) != {"section_id", "after_paragraph", "paragraph_sha256", "content"}:
            raise ValueError("Invalid authored statement placement boundary.")
        section = by_id.get(entry["section_id"])
        paragraphs = section["body"].split("\n\n") if section else []
        index = entry["after_paragraph"]
        if type(index) is not int or not 0 <= index < len(paragraphs):
            raise ValueError("The statement placement paragraph is unavailable.")
        if hashlib.sha256(paragraphs[index].encode()).hexdigest() != entry["paragraph_sha256"]:
            raise ValueError("Readable prose changed; review its authored statement placement.")
        if not isinstance(entry["content"], str) or not entry["content"].strip() or len(entry["content"]) > 40000:
            raise ValueError("A placement needs bounded readable content.")
        key = (section["id"], index)
        if key in insertions:
            raise ValueError("A paragraph has two conflicting placement instructions.")
        insertions[key] = entry["content"]
    for section in sections:
        paragraphs = []
        for index, paragraph in enumerate(section["body"].split("\n\n")):
            paragraphs.append(paragraph)
            if extra := insertions.get((section["id"], index)):
                paragraphs.append(extra)
        section["body"] = "\n\n".join(paragraphs)
    statement_ids = {statement["id"] for statement in row["statements"]}
    placed = validate_statement_placements([ReadableSection.model_validate(section) for section in sections], statement_ids)
    if placed != statement_ids:
        raise ValueError("The fixture layout must place each canonical statement exactly once.")
    return sections


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



def build_content(release, row, original, fingerprint, labels, *, local_fixture=True):
    """Build authored content only: no accounts, publication or I/O."""
    scheme_id, version = release.split("@", 1)
    history_kind = "local" if local_fixture else "curated"
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
                 for key in ("id", "title", "body")} for section in readable_fixture_sections(row)]
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
            f"Earlier immutable {history_kind} revisions retain the older discussion; no history has been deleted. "
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
            "authorship": {"kind": "model-assisted", "description": (
                "Authored and checked in the local Crucible integration pass; not human-written or independently scientifically reviewed."
                if local_fixture else "Model-assisted research dossier compiled from pinned Crucible source evidence; editorial inclusion is not independent scientific review.")},
            "source_evidence_note": {k: original[k] for k in ("id", "revision", "content_sha256")},
            "source_refs": row["source_refs"], "presentation_sha256": fingerprint,
            **({"ram_analysis_sha256": row["ram_analysis_sha256"]} if row.get("ram_analysis_sha256") else {}),
            **({"local_fixture": True} if local_fixture else {})},
    }
    return content
