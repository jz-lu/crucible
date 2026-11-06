"""Readable notebook partitions and local, exact-source display bindings.

Sections are authored content, not scientific approval. The optional local
binding manifest selects already-readable revisions and cannot publish a note,
change a curve, grant access, or adopt an assumption.
"""
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select


SECTION_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"


class ReadableSection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=SECTION_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=40000)

    @field_validator("id", "title", "body")
    @classmethod
    def text(cls, value):
        if not value.strip() or value in {"constructor", "prototype", "__proto__"}:
            raise ValueError("Use nonempty readable text and a stable section identifier.")
        return value


def _readable_section(content, identifier):
    sections = content.get("sections", [])
    if not isinstance(sections, list):
        raise HTTPException(404, "This exact note section is unavailable.")
    matches = [item for item in sections
               if isinstance(item, dict) and item.get("id") == identifier]
    if len(matches) != 1:
        raise HTTPException(404, "This exact note section is unavailable.")
    return matches[0]


def section_record(content, identifier):
    from . import notebook
    return {"id": identifier, "section_sha256": notebook._hash(_readable_section(content, identifier))}


class NotePin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, le=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
    label: str = Field(min_length=1, max_length=80)
    version: int = Field(default=1, ge=1, le=1000000)
    section_id: str = Field(pattern=SECTION_PATTERN)
    stages: list[int] = Field(max_length=1000)
    profile_ids: list[str] | None = Field(default=None, max_length=1000)
    status: Literal["unproved", "incomplete", "model-condition"]
    summary: str | None = Field(default=None, min_length=1, max_length=600)
    kind: Literal["hardness", "model", "distribution-bound"] | None = None
    formal_statement: str | None = Field(default=None, min_length=1, max_length=4000)

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Give a nonempty plain-language summary of the premise.")
        return value

    @field_validator("formal_statement")
    @classmethod
    def nonempty_statement(cls, value):
        if value is not None and not value.strip():
            raise ValueError("State the mathematical experiment and claim explicitly.")
        return value

    @model_validator(mode="after")
    def formal_kind_and_statement(self):
        if (self.kind is None) != (self.formal_statement is None):
            raise ValueError("A formal assumption needs both its kind and statement.")
        return self


class SchemeAssumptions(BaseModel):
    """Uses of premises, authored in the same exact note as their statements."""
    model_config = ConfigDict(extra="forbid", strict=True)
    security: list[str] = Field(max_length=64)
    correctness: list[str] = Field(max_length=64)


class SchemeArgument(BaseModel):
    """An authored claim about an exact argument, not independent approval."""
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["conditional", "unconditional", "incomplete"]
    scope: str = Field(min_length=1, max_length=1600)
    summary: str | None = Field(default=None, min_length=1, max_length=600)

    @field_validator("scope")
    @classmethod
    def nonempty_scope(cls, value):
        if not value.strip():
            raise ValueError("State the exact experiment and scope of the argument.")
        return value

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Give a nonempty plain-language summary of the argument's scope.")
        return value


class SchemeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    security: SchemeArgument
    correctness: SchemeArgument


class SchemeSecurityGap(BaseModel):
    """A missing argument remains separate from any numbered premise."""
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")
    section_id: str = Field(pattern=SECTION_PATTERN)
    statement: str = Field(min_length=1, max_length=4000)
    scope: str = Field(min_length=1, max_length=4000)
    status: Literal["incomplete"]
    stages: list[int] = Field(max_length=1000)
    profile_ids: list[str] | None = Field(default=None, max_length=1000)


class GraphBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    section_id: str = Field(pattern=SECTION_PATTERN)
    metric: Literal["classical", "quantum", "ram"]
    kind: str = Field(min_length=1, max_length=80)
    assumption_ids: list[str] = Field(default_factory=list, max_length=64)
    argument_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,79}$")


class ReleaseBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scheme_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=32)
    note: NotePin
    sections: dict[str, str] = Field(default_factory=dict, max_length=2)
    graph_models: dict[str, GraphBinding] = Field(default_factory=dict, max_length=32)
    assumptions: list[Assumption] | None = Field(default=None, max_length=64)
    arguments: dict[str, str] | None = Field(default=None, max_length=2)


class BindingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["crucible-local-note-bindings-v1"]
    releases: list[ReleaseBinding] = Field(max_length=100)


def _manifest(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate binding field")
            result[key] = value
        return result
    with Path(path).open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Binding manifest exceeds limit")
    manifest = BindingManifest.model_validate(json.loads(raw, object_pairs_hook=unique))
    identities = [(row.scheme_id, row.version) for row in manifest.releases]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate release binding")
    return manifest


class _ArgumentReader:
    """One exact-source resolver for canonical blocks and legacy read adapters.

    This returns authored research, never an adoption or mathematical verdict.
    Historical payloads and their hashes are not rewritten into the new format.
    """
    def __init__(self, session, user, visible, profiles):
        self.session, self.user, self.visible = session, user, visible
        self.profiles = {row["id"]: row for row in profiles}
        self.cache = {}

    def read(self, source):
        from . import notebook, notebook_moderation
        identifier = source.get("note_id", source.get("id"))
        key = (identifier, source["revision"], source["content_sha256"])
        if key not in self.cache:
            row = self.session.scalar(self.visible(self.session, self.user, identifier).where(
                notebook.NotebookRevision.revision == source["revision"],
                notebook.NotebookRevision.content_sha256 == source["content_sha256"]))
            if (row is None or notebook._hash(row.content) != source["content_sha256"]
                    or self.session.get(notebook_moderation.PublicationWithdrawal, row.id) is not None):
                raise ValueError("An exact argument source is unavailable")
            self.cache[key] = row.content
        content = self.cache[key]
        if source.get("section_id"):
            section_record(content, source["section_id"])
        return content

    def pin(self, source, **location):
        return {"id": source.get("note_id", source.get("id")), "revision": source["revision"],
                "content_sha256": source["content_sha256"], **location}

    def section(self, source, identifier):
        content = self.read(source)
        selected = section_record(content, identifier)
        return self.pin(source, note_section=identifier, section_sha256=selected["section_sha256"])

    def stages(self, configuration_ids):
        if not set(configuration_ids) <= set(self.profiles):
            raise ValueError("Argument coverage includes a configuration outside this release")
        return sorted({self.profiles[identifier]["stage_number"] for identifier in configuration_ids
                       if type(self.profiles[identifier].get("stage_number")) is int})

    def statement(self, base, reference):
        from . import statement_workflow
        source = reference.model_dump(exclude_none=True) if reference.note_id else base
        content = self.read(source)
        statement_workflow.selected_record(content, "statement", reference.statement_id)
        value = statement_workflow.Statement.model_validate(next(item for item in content["statements"]
            if item["id"] == reference.statement_id))
        for definition in value.definitions:
            self.read(definition.model_dump(exclude_none=True))
        return {**value.model_dump(exclude_none=True), "source": self.pin(source, statement=value.id),
                "definition_sha256": statement_workflow.definition_hash(value.model_dump(exclude_none=True))}

    def argument(self, source, identifier):
        from . import statement_workflow
        content = self.read(source)
        selection = statement_workflow.selected_record(content, "argument", identifier)
        value = statement_workflow.Argument.model_validate(next(item for item in content["arguments"] if item["id"] == identifier))
        conclusion = self.statement(source, value.conclusion_statement) if value.conclusion_statement else None
        dependencies = []
        for use in value.dependencies:
            if not use.configuration_ids:
                raise ValueError("A bound scheme argument must declare the configurations of each premise use")
            item = use.model_dump(exclude_none=True)
            item["statement"] = self.statement(source, use.statement)
            item["stages"] = self.stages(use.configuration_ids)
            dependencies.append(item)
        return {"id": value.id, "title": value.title, "status": value.status,
                "conclusion": conclusion["definition"] if conclusion else value.conclusion,
                **({"conclusion_statement": value.conclusion_statement.model_dump(exclude_none=True),
                    "resolved_conclusion_statement": conclusion} if conclusion else {}),
                "scope": value.scope, "summary": None, "source": self.pin(source, argument=value.id),
                "reasoning_source": self.section(source, value.section_id) if value.section_id else None,
                "dependencies": dependencies, "origin": "canonical", "scientific_adoption": False,
                "argument_sha256": selection["argument_sha256"]}

    def legacy_argument(self, source, section_id, role, group):
        content = self.read(source)
        section = _readable_section(content, section_id)
        dependencies = []
        for premise in (group or {}).get("assumptions", []):
            configurations = premise.get("profile_ids")
            if configurations is None:
                configurations = [identifier for identifier, profile in self.profiles.items()
                                  if profile.get("stage_number") in premise.get("stages", [])]
            definition = premise.get("formal_statement")
            if definition is None:
                definition = _readable_section(content, premise["source"]["note_section"])["body"]
            dependencies.append({"statement": {"id": premise["id"], "label": premise["label"],
                "title": premise["title"], "definition": definition, "source": premise["source"],
                "definition_origin": "legacy-formal-field" if premise.get("formal_statement") else "legacy-readable-section"},
                "configuration_ids": configurations, "stages": self.stages(configurations),
                "required": None, "mapping": None, "justification": None})
        return {"id": "legacy-" + role, "title": section["title"],
                "status": "incomplete" if (group or {}).get("incomplete") else (group or {}).get("argument_status"),
                "conclusion": None, "scope": (group or {}).get("scope"), "summary": (group or {}).get("summary"),
                "source": self.section(source, section_id), "reasoning_source": self.section(source, section_id),
                "dependencies": dependencies, "origin": "legacy", "scientific_adoption": False}


def _premise_cards(argument):
    """Compatibility projection only: definitions remain canonical note blocks."""
    return [{"id": use["statement"]["id"], "label": use["statement"]["label"],
             "title": use["statement"]["title"], "formal_statement": use["statement"]["definition"],
             "source": use["statement"]["source"], "status": "assumed", "stages": use["stages"],
             "profile_ids": use["configuration_ids"], "definition_sha256": use["statement"].get("definition_sha256")}
            for use in argument["dependencies"]]


def _canonical_binding(binding, reader, source, allowed_models, ram_evidence, attack_catalogue):
    if set(binding.arguments or {}) != {"security", "correctness"}:
        raise ValueError("Canonical scheme bindings require both argument roles")
    if binding.assumptions is not None:
        raise ValueError("The manifest cannot override canonical statements with copied assumptions")
    groups = {role: reader.argument(source, identifier) for role, identifier in binding.arguments.items()}
    graph_models = {}
    all_cards = []
    for group in groups.values():
        all_cards.extend(_premise_cards(group))
    for identifier, graph in binding.graph_models.items():
        if identifier not in allowed_models[graph.metric] or not graph.argument_id:
            raise ValueError("Every bound canonical graph model needs its exact argument")
        argument = reader.argument(source, graph.argument_id)
        expected_reasoning = reader.section(source, graph.section_id)
        if argument["reasoning_source"] is not None and argument["reasoning_source"] != expected_reasoning:
            raise ValueError("Graph reasoning and its canonical argument disagree")
        cards = _premise_cards(argument)
        graph_models[identifier] = {**graph.model_dump(exclude={"assumption_ids"}, exclude_none=True),
            "source": argument["source"], "reasoning_source": expected_reasoning,
            "argument": argument, "assumptions": cards}
        all_cards.extend(cards)
    if ram_evidence:
        for model in ram_evidence["models"]:
            selected = graph_models.get(model["id"])
            if selected is None or selected["metric"] != "ram" or selected["section_id"] != model.get("section_id"):
                raise ValueError("Every RAM model needs its exact canonical argument")
            expected = {}
            for point in ram_evidence["estimates"]:
                if point["model_id"] == model["id"]:
                    for premise in point["assumptions"]:
                        expected.setdefault("ram-" + premise["id"], set()).add(point["profile_id"])
            actual = {}
            for use in selected["argument"]["dependencies"]:
                actual.setdefault(use["statement"]["id"], set()).update(use["configuration_ids"])
            if actual != expected:
                raise ValueError("Canonical RAM dependencies differ from the exact attack account")
    cards = {}
    for item in all_cards:
        key = (item["source"]["id"], item["source"]["revision"], item["source"]["content_sha256"], item["id"])
        if key not in cards:
            cards[key] = {**item, "stages": list(item["stages"]), "profile_ids": list(item["profile_ids"])}
        else:
            cards[key]["stages"] = sorted(set(cards[key]["stages"]) | set(item["stages"]))
            cards[key]["profile_ids"] = sorted(set(cards[key]["profile_ids"]) | set(item["profile_ids"]))
    return {"format": "crucible-note-justifications-v1", "prototype": True, "scientific_adoption": False,
        "scheme_id": binding.scheme_id, "version": binding.version, "note": source,
        "sections": {role: reader.section(source, binding.sections[role]) if role in binding.sections else
                     argument["reasoning_source"] or argument["source"] for role, argument in groups.items()},
        "scheme_arguments": groups,
        "scheme_assumptions": {role: {"source": argument["source"], "assumptions": _premise_cards(argument),
            "incomplete": argument["status"] == "incomplete", "argument_status": argument["status"],
            "scope": argument["scope"], "summary": argument["summary"]} for role, argument in groups.items()},
        "assumptions": list(cards.values()), "graph_models": graph_models,
        **({"attack_comparison": attack_catalogue.presentation(ram_evidence)} if ram_evidence else {})}


def _project_argument_rosters(app, session, user, packet, reader):
    """Display reviewed choices without changing the pinned numerical account."""
    baselines = getattr(app.state, "statement_workflow_baselines", None)
    resolve = getattr(app.state, "statement_workflow_target", None)
    if baselines is None or resolve is None:
        return packet
    targets = {value["slot"]: identifier for identifier, value in baselines(session, user).items()
               if value["scheme_id"] == packet["scheme_id"] and value["version"] == packet["version"]}

    def project(argument, slot):
        identifier = targets.get(slot)
        if not identifier:
            return argument
        state = resolve(session, user, identifier, include_history=False)
        current = state["current"]
        conclusion = state.get("resolved_conclusion_statement")
        metadata = {"target_id": identifier, "head_id": current["id"], "selection_id": current.get("selection_id"),
            "origin": current["origin"], "independent_review": current["independent_review"],
            "current_status": current["status"], "roster_count": sum(row["status"] == "admitted" for row in state["roster"]),
            "conclusion_source": reader.pin(conclusion["source"], statement=conclusion["id"]) if conclusion else None,
            "numeric_curve_unchanged": True, "prototype": True}
        if current["status"] != "selected":
            return {"id": argument["id"], "title": argument["title"], "status": current["status"],
                "conclusion": conclusion["definition"] if conclusion else argument["conclusion"],
                "scope": state["target"]["scope"], "summary": None, "source": None, "reasoning_source": None,
                "dependencies": [], "origin": "canonical", "scientific_adoption": False,
                "resolved_conclusion_statement": {**conclusion, "source": metadata["conclusion_source"]} if conclusion else None,
                "presentation": metadata}
        selected = current["argument"]
        return {**reader.argument(selected, selected["argument_id"]), "presentation": metadata}

    packet["scheme_arguments"] = {role: project(argument, role) for role, argument in packet["scheme_arguments"].items()}
    for role, argument in packet["scheme_arguments"].items():
        packet["sections"][role] = argument["reasoning_source"] or argument["source"]
        packet["scheme_assumptions"][role] = {"source": argument["source"], "assumptions": _premise_cards(argument),
            "incomplete": argument["status"] in {"incomplete", "withdrawn", "unselected"},
            "argument_status": argument["status"], "scope": argument["scope"], "summary": argument["summary"]}
    for identifier, model in packet["graph_models"].items():
        argument = project(model["argument"], "attack:" + identifier)
        model.update(argument=argument, source=argument["source"], reasoning_source=argument["reasoning_source"],
                     assumptions=_premise_cards(argument))
        if "presentation" in argument:
            model["presentation"] = argument["presentation"]
    packet["assumptions"] = list({(item["source"]["id"], item["source"]["revision"], item["id"]): item
        for argument in [*packet["scheme_arguments"].values(), *(model["argument"] for model in packet["graph_models"].values())]
        for item in _premise_cards(argument)}.values())
    return packet


def register_routes(app, db, settings, account, visible):
    from . import notebook, registry

    @app.get("/api/protocol/note-justifications")
    def note_justifications(request: Request,
                            scheme_id: str = Query(min_length=1, max_length=100),
                            version: str = Query(min_length=1, max_length=32)):
        if not settings.local_note_corpus_only or not settings.local_note_bindings_path:
            raise HTTPException(404, "No local note justification is available.")
        with db.transaction() as session:
            user = account(request, session)
            try:
                if any(len(request.query_params.getlist(key)) != 1 for key in ("scheme_id", "version")):
                    raise ValueError("Ambiguous release")
                adapter = registry.adapter_for(scheme_id, version)
                manifest = _manifest(settings.local_note_bindings_path)
                binding = next(row for row in manifest.releases
                               if row.scheme_id == scheme_id and row.version == version)
                source = binding.note
                revision = session.scalar(visible(session, user, source.id).where(
                    notebook.NotebookRevision.revision == source.revision,
                    notebook.NotebookRevision.content_sha256 == source.content_sha256))
                if revision is None or notebook._hash(revision.content) != source.content_sha256:
                    raise ValueError("Exact source unavailable")
                if (binding.arguments is None and set(binding.sections) != {"security", "correctness"}
                        or binding.arguments is not None and not set(binding.sections) <= {"security", "correctness"}):
                    raise ValueError("Both scheme justifications are required")

                def pin(identifier):
                    selected = section_record(revision.content, identifier)
                    return {**source.model_dump(), "note_section": identifier,
                            "section_sha256": selected["section_sha256"]}

                descriptor = registry.descriptor(adapter)
                allowed_models = {"classical": set(), "quantum": set(), "ram": set()}
                initial = descriptor.get("initial_security_model") or {}
                if initial.get("id"):
                    allowed_models["classical"].add(initial["id"])
                for metric, value in (descriptor.get("security_references") or {}).get("metrics", {}).items():
                    if metric in allowed_models:
                        allowed_models[metric].update(model["id"] for model in value.get("models", []))
                stages = {row.get("stage_number") for row in descriptor["profiles"]
                          if type(row.get("stage_number")) is int}
                profiles = {row["id"] for row in descriptor["profiles"]}
                extensions = revision.content.get("extensions")
                if not isinstance(extensions, dict) or extensions.get("scheme_context") != {
                        "scheme_id": scheme_id, "version": version}:
                    raise ValueError("The source is bound to another exact release")
                reader = _ArgumentReader(session, user, visible, descriptor["profiles"])
                ram_evidence = None
                if any(graph.metric == "ram" for graph in binding.graph_models.values()):
                    from .attack_models import catalogue as attack_catalogue
                    ram_evidence = attack_catalogue.for_release(scheme_id, version,
                        expected_manifests=[adapter.get_manifest(identifier) for identifier in profiles])
                    if (not ram_evidence or extensions.get("ram_analysis_sha256") != ram_evidence["analysis_sha256"]
                            or {p["profile_id"] for p in ram_evidence["estimates"]} != profiles):
                        raise ValueError("Exact attack analysis is not pinned by this note")
                    allowed_models["ram"].update(model["id"] for model in ram_evidence["models"])
                if binding.arguments is not None:
                    packet = _canonical_binding(binding, reader, source.model_dump(), allowed_models,
                                                ram_evidence, attack_catalogue if ram_evidence else None)
                    return _project_argument_rosters(app, session, user, packet, reader)
                declared = extensions.get("assumptions", []) if isinstance(extensions, dict) else []
                if not isinstance(declared, list) or len(declared) > 64:
                    raise ValueError("Bounded assumption list required")
                assumptions = [Assumption.model_validate(item) for item in declared]
                if binding.assumptions is not None and binding.assumptions != assumptions:
                    raise ValueError("Manifest cannot override authored assumptions")
                by_id = {}
                for item in assumptions:
                    if (item.id in by_id or len(set(item.stages)) != len(item.stages)
                            or any(type(stage) is not int or stage not in stages for stage in item.stages)
                            or item.profile_ids is not None and (
                                len(set(item.profile_ids)) != len(item.profile_ids)
                                or not set(item.profile_ids) <= profiles)):
                        raise ValueError("Invalid assumption scope")
                    section = ReadableSection.model_validate(_readable_section(revision.content, item.section_id))
                    by_id[item.id] = {**item.model_dump(exclude={"section_id"}, exclude_none=True),
                                     "title": section.title, "source": pin(item.section_id)}
                scheme_assumptions = None
                argument_claims = (SchemeArguments.model_validate(extensions["scheme_arguments"])
                                   if "scheme_arguments" in extensions else None)
                if argument_claims is not None and "scheme_assumptions" not in extensions:
                    raise ValueError("Argument claims require explicit premise classifications")
                if "scheme_assumptions" in extensions:
                    # Missing metadata means uncategorized, not an empty proof
                    # dependency list. Membership is never guessed from a title
                    # or inferred by subtracting the graph's attack premises.
                    groups = SchemeAssumptions.model_validate(extensions["scheme_assumptions"])
                    raw_gaps = extensions.get("scheme_security_gaps", [])
                    if not isinstance(raw_gaps, list) or len(raw_gaps) > 64:
                        raise ValueError("Bounded security gap list required")
                    gaps = [SchemeSecurityGap.model_validate(item) for item in raw_gaps]
                    seen_gaps = set()
                    for gap in gaps:
                        if (gap.id in seen_gaps or gap.id in by_id or not gap.statement.strip() or not gap.scope.strip()
                                or len(set(gap.stages)) != len(gap.stages)
                                or any(stage not in stages for stage in gap.stages)
                                or gap.profile_ids is not None and (
                                    len(set(gap.profile_ids)) != len(gap.profile_ids)
                                    or not set(gap.profile_ids) <= profiles)):
                            raise ValueError("Invalid security gap scope")
                        pin(gap.section_id)
                        seen_gaps.add(gap.id)
                    scheme_assumptions = {}
                    for role, identifiers in groups.model_dump().items():
                        if (len(set(identifiers)) != len(identifiers)
                                or any(identifier not in by_id or by_id[identifier]["status"] == "incomplete"
                                       or not (by_id[identifier]["stages"] or by_id[identifier].get("profile_ids"))
                                       for identifier in identifiers)):
                            raise ValueError("Scheme categories must identify authored unproved premises")
                        claim = getattr(argument_claims, role) if argument_claims else None
                        if claim and ((claim.status == "unconditional" and identifiers)
                                      or (claim.status == "conditional" and not identifiers)
                                      or (role == "security" and gaps and claim.status != "incomplete")):
                            raise ValueError("The argument claim conflicts with its premises or missing steps")
                        scheme_assumptions[role] = {
                            "source": pin(binding.sections[role]),
                            "assumptions": [by_id[identifier] for identifier in identifiers],
                            "incomplete": (role == "security" and bool(gaps)) or bool(claim and claim.status == "incomplete"),
                            "argument_status": claim.status if claim else None,
                            "scope": claim.scope if claim else None,
                            "summary": claim.summary if claim else None,
                        }
                graph_models = {}
                for identifier, graph in binding.graph_models.items():
                    if (identifier not in allowed_models[graph.metric]
                            or len(set(graph.assumption_ids)) != len(graph.assumption_ids)
                            or any(item not in by_id or by_id[item]["status"] == "incomplete"
                                   for item in graph.assumption_ids)):
                        raise ValueError("Unknown model or assumption binding")
                    graph_models[identifier] = {**graph.model_dump(exclude={"assumption_ids"}, exclude_none=True),
                        "source": pin(graph.section_id),
                        "assumptions": [by_id[item] for item in graph.assumption_ids]}
                if ram_evidence:
                    required_scope = {}
                    for point in ram_evidence["estimates"]:
                        for premise in point["assumptions"]:
                            required_scope.setdefault("ram-" + premise["id"], set()).add(point["profile_id"])
                    for model in ram_evidence["models"]:
                        graph = binding.graph_models.get(model["id"])
                        required = {"ram-" + premise["id"] for point in ram_evidence["estimates"]
                                    if point["model_id"] == model["id"] for premise in point["assumptions"]}
                        if (graph is None or graph.metric != "ram" or graph.section_id != model.get("section_id")
                                or set(graph.assumption_ids) != required):
                            raise ValueError("Every RAM attack and required premise needs an exact note location")
                    for identity, required in required_scope.items():
                        premise = by_id.get(identity)
                        required_stages = {p["stage_number"] for p in descriptor["profiles"] if p["id"] in required
                                           and type(p.get("stage_number")) is int}
                        if (premise is None or set(premise.get("profile_ids", [])) != required
                                or set(premise.get("stages", [])) != required_stages):
                            raise ValueError("RAM premise scope differs from the actual analysis")
                return {"format": "crucible-note-justifications-v1", "prototype": True,
                    "scientific_adoption": False, "scheme_id": scheme_id, "version": version,
                    "note": source.model_dump(),
                    "sections": {role: pin(identifier) for role, identifier in binding.sections.items()},
                    "scheme_arguments": {role: reader.legacy_argument(source.model_dump(), identifier, role,
                        (scheme_assumptions or {}).get(role)) for role, identifier in binding.sections.items()},
                    "scheme_assumptions": scheme_assumptions,
                    "assumptions": list(by_id.values()), "graph_models": graph_models,
                    **({"attack_comparison": attack_catalogue.presentation(ram_evidence)} if ram_evidence else {})}
            except (OSError, ValueError, TypeError, KeyError, StopIteration, HTTPException):
                raise HTTPException(404, "No matching readable note justification is available.") from None
