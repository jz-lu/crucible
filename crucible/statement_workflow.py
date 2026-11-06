"""Optional note statements and a deliberately local presentation-adoption trial.

Notes remain the authoritative content. This module never activates executable
code, replaces a numerical curve, changes a challenge, or awards points. The
trial tables/routes are unavailable unless local_note_corpus_only is enabled.
They need an explicit migration/compatibility review before any hosted rollout.
"""
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Float, ForeignKey, JSON, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, User

IDENTIFIER = r"^[a-z][a-z0-9-]{0,79}$"
HASH = r"^[0-9a-f]{64}$"
SCOPE = "Local presentation trial. Review selects an argument; it does not activate code, change challenge instances or award points."
MAX_RELATED_BYTES = 2 * 1024 * 1024
MAX_HISTORY_EVENTS = 50


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("id", "statement_id", "argument_id", "section_id", check_fields=False)
    @classmethod
    def safe_identifier(cls, value):
        if value in {"constructor", "prototype", "__proto__"}:
            raise ValueError("Use a nonreserved statement, argument or section identifier.")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_text(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Use nonempty text.")
        return value


class NoteSource(StrictModel):
    note_id: str = Field(min_length=1, max_length=108)
    revision: int = Field(ge=1, le=100)
    content_sha256: str = Field(pattern=HASH)
    section_id: str | None = Field(default=None, pattern=IDENTIFIER)


class Statement(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    label: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=240)
    definition: str = Field(min_length=1, max_length=12000)
    definitions: list[NoteSource] = Field(default_factory=list, max_length=16)


class StatementReference(StrictModel):
    statement_id: str = Field(pattern=IDENTIFIER)
    note_id: str | None = Field(default=None, min_length=1, max_length=108)
    revision: int | None = Field(default=None, ge=1, le=100)
    content_sha256: str | None = Field(default=None, pattern=HASH)

    @model_validator(mode="after")
    def complete_pin(self):
        present = [self.note_id is not None, self.revision is not None, self.content_sha256 is not None]
        if any(present) and not all(present):
            raise ValueError("An external statement needs its complete exact note pin.")
        return self


class StatementUse(StrictModel):
    statement: StatementReference
    configuration_ids: list[str] = Field(default_factory=list, max_length=100)
    # One optional explanation for new authoring. With no explanation, the
    # complete exact proposition is a premise in the argument's stated scope;
    # absence never asserts that an implication or parameter mapping was proved.
    use: str | None = Field(default=None, min_length=1, max_length=16000)
    # Historical revisions used three prose fields. Keep those revisions
    # readable without rewriting their content hashes or silently losing detail.
    required: str | None = Field(default=None, min_length=1, max_length=3000)
    mapping: str | None = Field(default=None, min_length=1, max_length=3000)
    justification: str | None = Field(default=None, min_length=1, max_length=10000)

    @model_validator(mode="after")
    def one_application_description(self):
        legacy = [self.required is not None, self.mapping is not None, self.justification is not None]
        if any(legacy) and (not all(legacy) or self.use is not None):
            raise ValueError("Use one premise explanation, or preserve the complete historical application fields.")
        return self

    @field_validator("configuration_ids")
    @classmethod
    def configurations(cls, values):
        if any(not item.strip() or len(item) > 150 for item in values) or len(set(values)) != len(values):
            raise ValueError("Use distinct stable configuration identifiers.")
        return values


class Argument(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    title: str = Field(min_length=1, max_length=240)
    conclusion: str | None = Field(default=None, min_length=1, max_length=6000)
    conclusion_statement: StatementReference | None = None
    scope: str = Field(min_length=1, max_length=6000)
    status: Literal["conditional", "unconditional", "incomplete"]
    section_id: str | None = Field(default=None, pattern=IDENTIFIER)
    dependencies: list[StatementUse] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def declared_status(self):
        if (self.conclusion is None) == (self.conclusion_statement is None):
            raise ValueError("Use either an exact conclusion statement or the historical prose conclusion.")
        if self.status == "unconditional" and self.dependencies:
            raise ValueError("An argument with unproved dependencies cannot declare itself unconditional.")
        if self.status == "conditional" and not self.dependencies:
            raise ValueError("A conditional argument must identify its premises; use incomplete if they are not recorded.")
        return self


class ArgumentReference(NoteSource):
    argument_id: str = Field(pattern=IDENTIFIER)


class Target(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    title: str = Field(min_length=1, max_length=240)
    kind: Literal["attack", "security", "correctness", "statement"]
    scope: str = Field(min_length=1, max_length=6000)
    configuration_ids: list[str] = Field(default_factory=list, max_length=100)
    conclusion_statement: StatementReference | None = None

    _configurations = field_validator("configuration_ids")(StatementUse.configurations.__func__)


class UpdateInput(StrictModel):
    operation: Literal["admit_argument", "select_argument", "withdraw_argument"] = "select_argument"
    target: Target
    argument: ArgumentReference
    evidence: list[NoteSource] = Field(min_length=1, max_length=8)
    expected_adoption_id: str | None = Field(pattern=r"^[0-9a-f-]{36}$")
    reason: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(pattern=r"^[0-9a-f-]{36}$")


class ReviewInput(StrictModel):
    packet_sha256: str = Field(pattern=HASH)
    decision: Literal["approve", "changes_requested", "decline"]
    reason: str = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(pattern=r"^[0-9a-f-]{36}$")


class StatementUpdate(Base):
    __tablename__ = "local_statement_updates"
    __table_args__ = (UniqueConstraint("author_id", "idempotency_key", name="uq_local_statement_update_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    packet: Mapped[dict] = mapped_column(JSON)
    packet_sha256: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    assessment: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class StatementCurrent(Base):
    __tablename__ = "local_statement_current"
    target_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    adoption_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class StatementAdoption(Base):
    __tablename__ = "local_statement_adoptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    update_id: Mapped[str] = mapped_column(ForeignKey("local_statement_updates.id"), unique=True)
    target_id: Mapped[str] = mapped_column(String(80), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[float] = mapped_column(Float)
    previous_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


def definition_hash(statement):
    from . import notebook
    data = Statement.model_validate(statement).model_dump(mode="json", exclude_none=True)
    return notebook._hash({"definition": data["definition"], "definitions": data["definitions"]})


def validate_content(content):
    """Internal links are validated without requiring or rewriting other notes."""
    statements, arguments = content.statements, content.arguments
    if len({item.id for item in statements}) != len(statements) or len({item.id for item in arguments}) != len(arguments):
        raise ValueError("Statement and argument identifiers must each be distinct within a note.")
    sections = {item.id for item in content.sections}
    statement_ids = {item.id for item in statements}
    for argument in arguments:
        if argument.section_id is not None and argument.section_id not in sections:
            raise ValueError("The argument's reasoning section must exist in this note.")
        conclusion = argument.conclusion_statement
        if conclusion and conclusion.note_id is None and conclusion.statement_id not in statement_ids:
            raise ValueError("A local conclusion must identify a statement in this note.")
        for use in argument.dependencies:
            if use.statement.note_id is None and use.statement.statement_id not in statement_ids:
                raise ValueError("A local premise must identify a statement in this note.")
    return content


def selected_record(content, kind, identifier):
    from . import notebook
    values = content.get("statements" if kind == "statement" else "arguments", [])
    matches = [value for value in values if isinstance(value, dict) and value.get("id") == identifier]
    if len(matches) != 1:
        raise HTTPException(404, "This exact note statement or argument is unavailable.")
    value = matches[0]
    digest = definition_hash(value) if kind == "statement" else notebook._hash(value)
    return {"id": identifier, "definition_sha256" if kind == "statement" else "argument_sha256": digest}


def register_routes(app, db, settings, clock, account, visible, throttle):
    from . import notebook, notebook_moderation, notebook_trash, protocol_proposals, machine_auth
    if not settings.local_note_corpus_only:
        return

    def workflow_account(request, session, mutation=False):
        user = account(request, session, mutation)
        agent = "authorization" in request.headers
        session.info["statement_agent"] = agent
        session.info["statement_shared_reads"] = not agent
        session.info["statement_can_propose"] = not agent
        if agent:
            token = machine_auth.request_identity(request, session, settings, clock, mutation=mutation)
            session.info["statement_shared_reads"] = "notes:read" in token.scopes
            session.info["statement_can_propose"] = "notebook:write" in token.scopes
        return user

    def human_reviewer(session, user):
        return not session.info.get("statement_agent") and protocol_proposals.is_reviewer(settings, user)

    def source(session, user, ref, *, published=False):
        cache = session.info.setdefault("statement_sources", {})
        cache_key = (ref.note_id, ref.revision, ref.content_sha256, ref.section_id, published)
        if cache_key in cache:
            return cache[cache_key]
        note = session.get(notebook.NotebookNote, ref.note_id)
        if (note is None or session.info.get("statement_agent") and not session.info.get("statement_shared_reads")
                and note.author_id != user.id):
            raise HTTPException(404, "An exact source is unavailable.")
        row = session.scalar(visible(session, user, ref.note_id).where(
            notebook.NotebookRevision.revision == ref.revision))
        if row is None or notebook_trash.active(session, ref.note_id):
            raise HTTPException(404, "An exact source is unavailable.")
        if row.content_sha256 != ref.content_sha256 or notebook._hash(row.content) != ref.content_sha256:
            raise HTTPException(409, "An exact source hash does not match.")
        if session.get(notebook_moderation.PublicationWithdrawal, row.id) is not None:
            raise HTTPException(404, "An exact source is unavailable.")
        if published and session.scalar(select(notebook.NotebookPublication.id).where(
                notebook.NotebookPublication.revision_id == row.id,
                notebook.publication_screening.approved_clause(allow_development=False))) is None:
            raise HTTPException(409, "Every adopted source must first be published through the note publication process.")
        if ref.section_id is not None:
            from .note_sections import section_record
            section_record(row.content, ref.section_id)
        cache[cache_key] = row
        return row

    def pin(ref):
        return {"note_id": ref.note_id, "revision": ref.revision, "content_sha256": ref.content_sha256}

    def resolve_statement(session, user, ref, base=None, *, published=False):
        src = NoteSource(**({key: getattr(ref, key) for key in ("note_id", "revision", "content_sha256")}
            if ref.note_id is not None else pin(base)))
        row = source(session, user, src, published=published)
        selected_record(row.content, "statement", ref.statement_id)
        value = next(item for item in row.content["statements"] if item["id"] == ref.statement_id)
        parsed = Statement.model_validate(value)
        for definition in parsed.definitions:
            source(session, user, definition, published=published)
        return {**parsed.model_dump(mode="json", exclude_none=True), "source": pin(src),
                "definition_sha256": definition_hash(value)}

    def resolve_argument(session, user, ref, *, published=False):
        row = source(session, user, ref, published=published)
        record = selected_record(row.content, "argument", ref.argument_id)
        value = Argument.model_validate(next(item for item in row.content["arguments"] if item["id"] == ref.argument_id))
        resolved = value.model_dump(mode="json", exclude_none=True)
        if value.conclusion_statement:
            conclusion = resolve_statement(session, user, value.conclusion_statement, ref, published=published)
            resolved["resolved_conclusion_statement"] = conclusion
            resolved["conclusion"] = conclusion["definition"]
        resolved["dependencies"] = [{**use.model_dump(mode="json", exclude_none=True),
            "resolved_statement": resolve_statement(session, user, use.statement, ref, published=published)}
            for use in value.dependencies]
        return {**resolved, "source": pin(ref), "assessment": "authored", "argument_sha256": record["argument_sha256"]}

    def resolve_packet(session, user, packet, *, published=False):
        argument = resolve_argument(session, user, ArgumentReference.model_validate(packet["argument"]), published=published)
        target_statement = packet["target"].get("conclusion_statement")
        if target_statement:
            expected = StatementReference.model_validate(target_statement)
            if not expected.note_id:
                raise HTTPException(422, "A review target requires an exact external conclusion statement pin.")
            conclusion = resolve_statement(session, user, expected, published=published)
            actual = argument.get("resolved_conclusion_statement")
            if not actual or actual["definition_sha256"] != conclusion["definition_sha256"]:
                raise HTTPException(409, "This argument must address the target's exact proposition. Different propositions need a separate target; no implication is inferred.")
            configurations = set(packet["target"]["configuration_ids"])
            if configurations and any(not use["configuration_ids"] or not set(use["configuration_ids"]) <= configurations
                                      for use in argument["dependencies"]):
                raise HTTPException(409, "Each premise use must identify exact configurations within this target's scope.")
        evidence = []
        for raw in packet["evidence"]:
            ref = NoteSource.model_validate(raw)
            row = source(session, user, ref, published=published)
            evidence.append({"source": ref.model_dump(mode="json", exclude_none=True), "title": row.content["title"]})
        return argument, evidence

    def reference_key(value):
        return notebook._hash(ArgumentReference.model_validate(value).model_dump(mode="json", exclude_none=True))

    def presentation_baselines(session, user):
        """Local manifest admissions are explicit fixtures, never invented reviews."""
        cache = session.info.setdefault("statement_baselines", {})
        if user.id in cache:
            return cache[user.id]
        values = {}
        if not settings.local_note_bindings_path:
            cache[user.id] = values
            return values
        from .note_sections import _manifest
        from . import registry
        try:
            manifest = _manifest(settings.local_note_bindings_path)
        except (OSError, ValueError):
            cache[user.id] = values
            return values
        for binding in manifest.releases:
            if not binding.arguments:
                continue
            try:
                descriptor = registry.descriptor(registry.adapter_for(binding.scheme_id, binding.version))
                configurations = [item["id"] for item in descriptor["profiles"]]
            except (KeyError, ValueError):
                continue
            entries = [(role, role, identifier) for role, identifier in binding.arguments.items()]
            entries.extend(("attack:" + model_id, "attack", graph.argument_id)
                for model_id, graph in binding.graph_models.items() if graph.argument_id)
            for slot, kind, identifier in entries:
                ref = ArgumentReference(note_id=binding.note.id, revision=binding.note.revision,
                    content_sha256=binding.note.content_sha256, argument_id=identifier)
                try:
                    argument = resolve_argument(session, user, ref, published=True)
                except (HTTPException, ValueError):
                    continue
                conclusion = argument.get("resolved_conclusion_statement")
                if not conclusion or argument["status"] == "incomplete":
                    continue  # Historical free-form notes remain readable, not silently admitted.
                selected_configurations = configurations
                if slot.startswith("attack:"):
                    from .attack_models import catalogue
                    try:
                        analysis = catalogue.for_release(binding.scheme_id, binding.version)
                        selected_configurations = [item["profile_id"] for item in (analysis or {}).get("estimates", [])
                            if item["model_id"] == slot.removeprefix("attack:")]
                    except (ValueError, OSError):
                        continue
                    if not selected_configurations:
                        continue  # No inferred coverage for qualitative historical references.
                identity = {"release": binding.scheme_id + "@" + binding.version, "slot": slot,
                    "conclusion_sha256": conclusion["definition_sha256"], "configuration_ids": sorted(selected_configurations)}
                target_id = "scheme-" + notebook._hash(identity)[:40]
                title = descriptor["name"] + " · " + ({"security": "Security claim", "correctness": "Correctness claim"}.get(kind) or argument["title"])
                target = {"id": target_id, "title": title[:240], "kind": kind,
                    "scope": "Exact research release " + identity["release"] + "; " + slot + ".",
                    "configuration_ids": selected_configurations,
                    "conclusion_statement": {**conclusion["source"], "statement_id": conclusion["id"]}}
                argument_pin = ref.model_dump(mode="json", exclude_none=False)
                event_id = str(uuid5(NAMESPACE_URL, "crucible-initial-argument-admission:" + notebook._hash({"target": target, "argument": argument_pin})))
                values[target_id] = {"id": event_id, "target": target, "argument": argument_pin,
                    "slot": slot, "scheme_id": binding.scheme_id, "version": binding.version}
        cache[user.id] = values
        return values

    def baseline_for(session, user, target_id):
        current = session.get(StatementCurrent, target_id)
        if current and current.adoption_id:
            event = session.get(StatementAdoption, current.adoption_id)
            update = session.get(StatementUpdate, event.update_id) if event else None
            if update:
                return update.packet.get("initial_admission")
        return presentation_baselines(session, user).get(target_id)

    def current_id(session, target_id, user=None):
        current = session.get(StatementCurrent, target_id)
        if current and current.adoption_id:
            return current.adoption_id
        if user:
            baseline = baseline_for(session, user, target_id)
            return baseline["id"] if baseline else None
        return None

    def target_state(session, user, target_id):
        baseline = baseline_for(session, user, target_id)
        head = current_id(session, target_id, user)
        if head is None:
            return None
        events = []
        cursor = head
        while cursor and (not baseline or cursor != baseline["id"]):
            if len(events) >= MAX_HISTORY_EVENTS:
                raise HTTPException(409, "This local roster exceeds the reconstruction bound; no earlier argument was substituted.")
            event = session.get(StatementAdoption, cursor)
            update = session.get(StatementUpdate, event.update_id) if event else None
            if not event or not update or event.target_id != target_id or not update.assessment or update.assessment.get("decision") != "approve":
                raise HTTPException(409, "The argument history is incomplete; no default argument was substituted.")
            events.append((event, update))
            cursor = event.previous_id
        if baseline and cursor != baseline["id"]:
            raise HTTPException(409, "The initial argument admission no longer matches its history.")
        roster, selected, selection_id, selection_withdrawn, selection_withdrawal = {}, None, None, False, None
        target = baseline["target"] if baseline else None
        if baseline:
            selected = reference_key(baseline["argument"])
            selection_id = baseline["id"]
            roster[selected] = {"id": baseline["id"], "argument": baseline["argument"], "status": "admitted",
                "origin": "initial_admission", "independent_review": False, "created_at": None,
                "reason": "Initial local admission authorized for this trial; not an independent scientific review."}
        for event, update in reversed(events):
            target = update.packet["target"]
            key = reference_key(update.packet["argument"])
            operation = update.packet.get("operation", "select_argument")
            if operation == "withdraw_argument":
                if key not in roster or roster[key]["status"] != "admitted":
                    raise HTTPException(409, "The history withdraws an argument that was not admitted.")
                roster[key] = {**roster[key], "status": "withdrawn", "withdrawal_id": event.id,
                    "withdrawal_reason": update.assessment["reason"]}
                if selected == key:
                    selection_withdrawn = True
                    selection_withdrawal = {"id": event.id, "created_at": event.created_at,
                        "reason": update.assessment["reason"], "reviewer": {"handle": session.get(User, event.reviewer_id).handle}}
            else:
                roster[key] = {"id": event.id, "update_id": update.id, "argument": update.packet["argument"],
                    "status": "admitted", "origin": "reviewed", "independent_review": True,
                    "created_at": event.created_at, "reviewer": {"handle": session.get(User, event.reviewer_id).handle},
                    "reason": update.assessment["reason"]}
                if operation == "select_argument":
                    selected = key
                    selection_id = event.id
                    selection_withdrawn = False
                    selection_withdrawal = None
        return {"target": target, "head": head, "selected": selected, "selection_id": selection_id,
                "selection_withdrawn": selection_withdrawn, "selection_withdrawal": selection_withdrawal, "roster": roster,
                "events": events, "baseline": baseline}

    def validate_withdrawal(session, user, packet):
        if packet.get("operation", "select_argument") != "withdraw_argument":
            return
        state = target_state(session, user, packet["target"]["id"])
        value = state["roster"].get(reference_key(packet["argument"])) if state else None
        if not value or value["status"] != "admitted":
            raise HTTPException(409, "Withdrawal must identify an admitted exact argument in this target's current roster.")

    def validate_target_identity(session, user, packet):
        state = target_state(session, user, packet["target"]["id"])
        if state is None:
            return
        old, new = state["target"], packet["target"]
        # Titles may state quantitative conclusions; identity is deliberately exact.
        if (old["title"] != new["title"] or old["kind"] != new["kind"] or old["scope"] != new["scope"]
                or old.get("conclusion_statement") != new.get("conclusion_statement")
                or set(old["configuration_ids"]) != set(new["configuration_ids"])):
            raise HTTPException(409, "A changed target title, claim or configuration scope needs a new target identity; replacing an argument cannot silently change the result.")

    def independent(session, user, record):
        if user.id == record.author_id:
            return False
        refs = [record.packet["argument"], *record.packet["evidence"]]
        # Prior premises and normative sampler definitions are scientific sources
        # too. A reviewer must not adjudicate a packet resting on their own work.
        main = ArgumentReference.model_validate(record.packet["argument"])
        revision = session.scalar(select(notebook.NotebookRevision).where(
            notebook.NotebookRevision.note_id == main.note_id, notebook.NotebookRevision.revision == main.revision))
        if revision is None:
            return False
        authored = next((item for item in revision.content.get("arguments", []) if item.get("id") == main.argument_id), None)
        if authored is None:
            return False
        parsed = Argument.model_validate(authored)
        statement_refs = [use.statement for use in parsed.dependencies]
        if parsed.conclusion_statement:
            statement_refs.append(parsed.conclusion_statement)
        if record.packet["target"].get("conclusion_statement"):
            statement_refs.append(StatementReference.model_validate(record.packet["target"]["conclusion_statement"]))
        for statement_ref in statement_refs:
            src = {key: getattr(statement_ref, key) for key in ("note_id", "revision", "content_sha256")} if statement_ref.note_id else pin(main)
            refs.append(src)
            premise_revision = session.scalar(select(notebook.NotebookRevision).where(
                notebook.NotebookRevision.note_id == src["note_id"], notebook.NotebookRevision.revision == src["revision"]))
            if premise_revision is None:
                return False
            premise = next((item for item in premise_revision.content.get("statements", []) if item.get("id") == statement_ref.statement_id), None)
            if premise is None:
                return False
            refs.extend(Statement.model_validate(premise).model_dump(mode="json")["definitions"])
        for raw in refs:
            note = session.get(notebook.NotebookNote, raw["note_id"])
            if note and note.author_id == user.id:
                return False
        return True

    def update_data(session, user, record):
        if record.author_id != user.id and not human_reviewer(session, user) and not (
                record.assessment and record.assessment.get("decision") == "approve"):
            raise HTTPException(404, "Update proposal not found.")
        assessment = record.assessment
        try:
            argument, evidence = resolve_packet(session, user, record.packet)
        except HTTPException:
            if not assessment or not (user.id == record.author_id or (
                    user.id == assessment["reviewer_id"] and not session.info.get("statement_agent"))):
                raise
            # A retry can arrive after withdrawal of a source. Preserve the
            # committed decision receipt without disclosing the withdrawn text,
            # author-written target title, or potentially quoting review reason.
            return {"id": record.id, "target": {"id": record.packet["target"]["id"], "title": "Update source unavailable"},
                "status": {"approve": "approved", "decline": "declined"}.get(assessment["decision"], assessment["decision"]),
                "assessment": {key: assessment[key] for key in ("decision", "created_at", "adoption_id", "reviewer")},
                "source_unavailable": True, "can_review": False, "can_apply": False, "prototype": True,
                "reason": "The decision is preserved, but an exact source is no longer available.", "scope": SCOPE}
        stale = not assessment and current_id(session, record.packet["target"]["id"], user) != record.packet["expected_adoption_id"]
        applied = assessment.get("adoption_id") if assessment else None
        can_review = not assessment and human_reviewer(session, user) and independent(session, user, record)
        publication_ready = True
        try:
            resolve_packet(session, user, record.packet, published=True)
        except HTTPException:
            publication_ready = False
        return {"id": record.id, **record.packet, "packet_sha256": record.packet_sha256,
            "created_at": record.created_at, "author": {"handle": session.get(User, record.author_id).handle},
            "status": {"approve": "approved", "decline": "declined"}.get(assessment["decision"], assessment["decision"]) if assessment else "pending", "assessment": assessment,
            "resolved_argument": argument, "resolved_evidence": evidence,
            "can_review": can_review, "can_apply": can_review and not stale and publication_ready and (
                record.packet.get("operation") == "withdraw_argument" or argument["status"] != "incomplete"),
            "publication_ready": publication_ready,
            "stale": stale, "is_current": bool(applied and current_id(session, record.packet["target"]["id"], user) == applied),
            "prototype": True, "scope": SCOPE}

    def adoption_data(session, user, event):
        update = session.get(StatementUpdate, event.update_id)
        argument, _ = resolve_packet(session, user, update.packet, published=True)
        operation = update.packet.get("operation", "select_argument")
        withdrawn = operation == "withdraw_argument"
        return {"id": event.id, "update_id": event.update_id, "argument": update.packet["argument"],
            "operation": operation, "status": "withdrawn" if withdrawn else "admitted" if operation == "admit_argument" else "selected",
            "resolved_argument": None if withdrawn else argument, "withdrawn_argument": argument if withdrawn else None,
            "target": update.packet["target"], "created_at": event.created_at,
            "origin": "reviewed", "independent_review": True,
            "reviewer": {"handle": session.get(User, event.reviewer_id).handle},
            "reason": update.assessment["reason"], "previous_id": event.previous_id}

    def target_data(session, user, identifier, *, include_history=True):
        state = target_state(session, user, identifier)
        if state is None:
            raise HTTPException(404, "No argument has been adopted for this target.")
        target = state["target"]
        if target.get("conclusion_statement"):
            conclusion = resolve_statement(session, user, StatementReference.model_validate(target["conclusion_statement"]), published=True)
        else:
            conclusion = None
        rows, current, total_bytes = [], None, 0
        for key, entry in state["roster"].items():
            try:
                resolved = resolve_argument(session, user, ArgumentReference.model_validate(entry["argument"]), published=True)
            except HTTPException:
                if key == state["selected"]:
                    raise  # A hidden selected source cannot silently reveal an older alternative.
                continue
            total_bytes += len(notebook._bytes(resolved))
            if total_bytes > MAX_RELATED_BYTES:
                raise HTTPException(409, "This local argument roster exceeds its response bound.")
            is_current = key == state["selected"] and not state["selection_withdrawn"] and entry["status"] == "admitted"
            row = {**entry, "resolved_argument": resolved, "is_current": is_current}
            rows.append(row)
            if key == state["selected"]:
                current = {**entry, "id": state["head"], "selection_id": state["selection_id"], "target": target,
                    "status": "selected" if is_current else "withdrawn", "previous_id": None,
                    "resolved_argument": resolved if is_current else None,
                    "withdrawn_argument": None if is_current else resolved}
                if state["selection_withdrawal"]:
                    withdrawal = state["selection_withdrawal"]
                    current.update(withdrawal=withdrawal, reason=withdrawal["reason"], reviewer=withdrawal["reviewer"],
                                   origin="reviewed", independent_review=True)
        if current is None:
            current = {"id": state["head"], "selection_id": None, "status": "unselected", "argument": None,
                "resolved_argument": None, "withdrawn_argument": None, "target": target,
                "origin": "reviewed", "independent_review": False}
        history = []
        if include_history:
            for event, _ in state["events"]:
                try:
                    item = adoption_data(session, user, event)
                except HTTPException:
                    continue
                total_bytes += len(notebook._bytes(item))
                if total_bytes > MAX_RELATED_BYTES:
                    raise HTTPException(409, "This local argument history exceeds its response bound.")
                history.append(item)
            if state["baseline"]:
                base = state["baseline"]
                try:
                    resolved = resolve_argument(session, user, ArgumentReference.model_validate(base["argument"]), published=True)
                    history.append({**base, "status": "initial_admission", "origin": "initial_admission",
                        "independent_review": False, "resolved_argument": resolved, "created_at": None,
                        "reason": "Initial local admission authorized for this trial; not an independent scientific review."})
                except HTTPException:
                    pass
        return {"target": target, "current": current, "roster": rows, "history": history,
                "resolved_conclusion_statement": conclusion, "history_limit_reached": False,
                **({"scheme_context": {key: state["baseline"][key] for key in ("scheme_id", "version")}}
                   if state["baseline"] else {}),
                "prototype": True, "scope": SCOPE}

    def target_ids(session, user):
        return list(dict.fromkeys([*presentation_baselines(session, user), *session.scalars(
            select(StatementCurrent.target_id).where(StatementCurrent.adoption_id.is_not(None)).limit(100))]))[:150]

    # Shared local projection used by the scheme page; no model, runtime or curve mutation.
    app.state.statement_workflow_target = target_data
    app.state.statement_workflow_baselines = presentation_baselines

    @app.get("/api/notebook/statement-workflow/context")
    def context(request: Request, note_id: str = Query(min_length=1, max_length=108), revision: int = Query(ge=1, le=100),
                sha256: str = Query(pattern=HASH), statement: str | None = Query(default=None, pattern=IDENTIFIER),
                argument: str | None = Query(default=None, pattern=IDENTIFIER)):
        if (statement is None) == (argument is None) or any(len(request.query_params.getlist(key)) != 1
                for key in ("note_id", "revision", "sha256")) or any(len(request.query_params.getlist(key)) > 1 for key in ("statement", "argument")):
            raise HTTPException(422, "Choose one statement or argument in an exact note revision.")
        with db.transaction() as session:
            user = workflow_account(request, session)
            ref = NoteSource(note_id=note_id, revision=revision, content_sha256=sha256)
            row = source(session, user, ref)
            result = {"source": pin(ref), "prototype": True, "can_propose": session.info["statement_can_propose"], "related_uses": [], "related_arguments": [], "related_research": [], "targets": []}
            if statement:
                result["statement"] = resolve_statement(session, user, StatementReference(statement_id=statement), ref)
            else:
                result["argument"] = resolve_argument(session, user, ArgumentReference(**pin(ref), argument_id=argument))
            # Bounded local research fixture scan; no bodies or private counts returned.
            related = visible(session, user)
            if session.info.get("statement_agent") and not session.info.get("statement_shared_reads"):
                related = related.where(notebook.NotebookNote.author_id == user.id)
            rows = session.scalars(related.order_by(notebook.NotebookRevision.created_at.desc()).limit(301)).yield_per(10)
            result["related_uses_complete"] = True
            related_bytes = 0
            for index, candidate in enumerate(rows):
                related_bytes += len(notebook._bytes(candidate.content))
                if index >= 300 or related_bytes > MAX_RELATED_BYTES:
                    result["related_uses_complete"] = False
                    break
                if session.get(notebook_moderation.PublicationWithdrawal, candidate.id) is not None:
                    continue
                candidate_ref = NoteSource(note_id=candidate.note_id, revision=candidate.revision, content_sha256=candidate.content_sha256)
                references = candidate.content.get("references")
                for reference in references if isinstance(references, list) else []:
                    if (not isinstance(reference, dict) or not isinstance(reference.get("relation"), str)
                            or reference["relation"] not in {"supports", "challenges", "reproduces", "responds_to"}):
                        continue
                    raw = reference.get("url")
                    if not isinstance(raw, str):
                        continue
                    try:
                        parsed_url = urlsplit(raw)
                    except ValueError:
                        continue
                    # The normal reference pipeline recognizes local exact note routes.
                    # An arbitrary external site's matching query is not a dependency.
                    if parsed_url.scheme or parsed_url.netloc or parsed_url.path not in {"", "/"} or not parsed_url.fragment.startswith("notebook?"):
                        continue
                    try:
                        fields = parse_qs(parsed_url.fragment.partition("?")[2], max_num_fields=8)
                    except ValueError:
                        continue
                    key, location = ("statement", statement) if statement else ("argument", argument)
                    wanted = {"note": note_id, "revision": str(revision), "sha256": sha256, key: location}
                    if set(fields) != set(wanted) or any(fields[k] != [v] for k, v in wanted.items()):
                        continue
                    candidate_note = session.get(notebook.NotebookNote, candidate.note_id)
                    candidate_author = session.get(User, candidate_note.author_id)
                    result["related_research"].append({"source": pin(candidate_ref), "title": candidate.content["title"],
                        "relation": reference["relation"], "scope": reference.get("scope") if isinstance(reference.get("scope"), str) else "",
                        "author": {"handle": candidate_author.handle}})
                for raw in candidate.content.get("arguments", []):
                    parsed = Argument.model_validate(raw)
                    matches = []
                    if statement:
                        conclusion = parsed.conclusion_statement
                        if conclusion:
                            conclusion_pin = pin(candidate_ref) if conclusion.note_id is None else {key: getattr(conclusion, key) for key in ("note_id", "revision", "content_sha256")}
                            if conclusion.statement_id == statement and conclusion_pin == pin(ref):
                                result["related_arguments"].append({"source": pin(candidate_ref), "argument_id": parsed.id, "title": parsed.title})
                        for use in parsed.dependencies:
                            target = use.statement
                            target_pin = pin(candidate_ref) if target.note_id is None else {key: getattr(target, key) for key in ("note_id", "revision", "content_sha256")}
                            if target.statement_id == statement and target_pin == pin(ref):
                                matches.append(use.model_dump(mode="json", exclude_none=True))
                    if matches:
                        result["related_uses"].append({"source": pin(candidate_ref), "argument_id": parsed.id,
                            "title": parsed.title, "uses": matches})
            for identifier in target_ids(session, user):
                try:
                    target = target_data(session, user, identifier, include_history=False)
                except HTTPException:
                    continue
                chosen = target["current"]["resolved_argument"] or target["current"]["withdrawn_argument"]
                direct = argument and any(entry["resolved_argument"]["id"] == argument and entry["resolved_argument"]["source"] == pin(ref) for entry in target["roster"])
                used = statement and chosen and any(use["resolved_statement"]["id"] == statement and use["resolved_statement"]["source"] == pin(ref)
                                         for use in chosen["dependencies"])
                conclusion = target.get("resolved_conclusion_statement")
                exact_conclusion = statement and conclusion and conclusion["id"] == statement and conclusion["source"] == pin(ref)
                # Both statements have already passed exact-source visibility
                # checks. This is byte-identical normative definition identity,
                # including all pinned definitions, never inferred implication.
                asserted = statement and conclusion and result["statement"]["definition_sha256"] == conclusion["definition_sha256"]
                if direct or used or asserted:
                    if asserted:
                        target["conclusion_match"] = "exact_pin" if exact_conclusion else "exact_definition"
                    result["targets"].append(target)
            return result

    @app.get("/api/notebook/statement-workflow/targets")
    def targets(request: Request):
        with db.transaction() as session:
            user = workflow_account(request, session)
            values = []
            target_bytes = 0
            payload_limit_reached = False
            for identifier in target_ids(session, user):
                try:
                    data = target_data(session, user, identifier, include_history=False)
                except HTTPException:
                    continue
                target_bytes += len(notebook._bytes(data))
                if target_bytes > MAX_RELATED_BYTES:
                    payload_limit_reached = True
                    break
                values.append(data)
            return {"targets": values, "payload_limit_reached": payload_limit_reached, "prototype": True}

    @app.get("/api/notebook/statement-workflow/targets/{target_id}")
    def target(target_id: str, request: Request):
        with db.transaction() as session:
            return target_data(session, workflow_account(request, session), target_id)

    @app.post("/api/notebook/statement-workflow/updates")
    def propose(body: UpdateInput, request: Request):
        throttle(request, "statement-update", 30, 3600)
        packet = body.model_dump(mode="json", exclude={"idempotency_key"}, exclude_none=False)
        request_digest = notebook._hash(packet)
        with db.transaction(write=True) as session:
            user = workflow_account(request, session, True)
            previous = session.scalar(select(StatementUpdate).where(StatementUpdate.author_id == user.id,
                StatementUpdate.idempotency_key == body.idempotency_key))
            if previous:
                if notebook._hash({key: value for key, value in previous.packet.items() if key != "initial_admission"}) != request_digest:
                    raise HTTPException(409, "This save identifier already belongs to a different update.")
                return {"update": update_data(session, user, previous)}
            count = session.scalar(select(func.count()).select_from(StatementUpdate).where(StatementUpdate.author_id == user.id))
            if count >= 200:
                raise HTTPException(409, "Local update capacity reached; existing research is preserved.")
            resolve_packet(session, user, packet)
            if current_id(session, body.target.id, user) != body.expected_adoption_id:
                raise HTTPException(409, "The currently adopted argument changed. Refresh the comparison before proposing.")
            validate_target_identity(session, user, packet)
            validate_withdrawal(session, user, packet)
            baseline = baseline_for(session, user, body.target.id)
            if baseline:
                packet["initial_admission"] = baseline
            digest = notebook._hash(packet)
            row = StatementUpdate(id=str(uuid4()), author_id=user.id, created_at=clock(), packet=packet,
                packet_sha256=digest, idempotency_key=body.idempotency_key)
            session.add(row)
            session.flush()
            return {"update": update_data(session, user, row)}

    @app.get("/api/notebook/statement-workflow/updates")
    def updates(request: Request, limit: int = Query(default=30, ge=1, le=100)):
        with db.transaction() as session:
            user = workflow_account(request, session)
            query = select(StatementUpdate).order_by(StatementUpdate.created_at.desc(), StatementUpdate.id).limit(limit)
            if not human_reviewer(session, user):
                query = query.where(StatementUpdate.author_id == user.id)
            values = []
            for row in session.scalars(query):
                try:
                    values.append(update_data(session, user, row))
                except HTTPException:
                    continue
            return {"updates": values, "prototype": True}

    @app.get("/api/notebook/statement-workflow/updates/{update_id}")
    def update(update_id: UUID, request: Request):
        with db.transaction() as session:
            user = workflow_account(request, session)
            row = session.get(StatementUpdate, str(update_id))
            if row is None:
                raise HTTPException(404, "Update proposal not found.")
            return {"update": update_data(session, user, row)}

    @app.post("/api/notebook/statement-workflow/updates/{update_id}/review")
    def review(update_id: UUID, body: ReviewInput, request: Request):
        if "authorization" in request.headers:
            raise HTTPException(403, "Adoption needs a configured human reviewer.")
        throttle(request, "statement-review", 60, 3600)
        commitment = notebook._hash({"update_id": str(update_id), **body.model_dump(exclude={"idempotency_key"})})
        with db.transaction(write=True) as session:
            user = workflow_account(request, session, True)
            row = session.scalar(select(StatementUpdate).where(StatementUpdate.id == str(update_id)).with_for_update())
            if row is None:
                raise HTTPException(404, "Update proposal not found.")
            if not protocol_proposals.is_reviewer(settings, user):
                raise HTTPException(403, "An independent configured reviewer is required.")
            if row.assessment:
                if row.assessment["idempotency_key"] == body.idempotency_key and row.assessment["request_sha256"] == commitment and row.assessment["reviewer_id"] == user.id:
                    return {"update": update_data(session, user, row)}
                raise HTTPException(409, "This proposal already has a review decision.")
            if not independent(session, user, row):
                raise HTTPException(403, "An independent configured reviewer is required.")
            reused_key = session.scalar(select(StatementUpdate.id).where(
                StatementUpdate.assessment["reviewer_id"].as_string() == user.id,
                StatementUpdate.assessment["idempotency_key"].as_string() == body.idempotency_key).limit(1))
            if reused_key is not None:
                raise HTTPException(409, "This review identifier already belongs to another decision.")
            if row.packet_sha256 != body.packet_sha256:
                raise HTTPException(409, "The review does not match the frozen update packet.")
            resolved, _ = resolve_packet(session, user, row.packet, published=body.decision == "approve")
            adoption_id = None
            if body.decision == "approve":
                if row.packet.get("operation", "select_argument") != "withdraw_argument" and resolved["status"] == "incomplete":
                    raise HTTPException(409, "An incomplete argument cannot be adopted as a justification. Request a completed argument with explicit premises.")
                target_id = row.packet["target"]["id"]
                # Establish a lockable target without an absent-row race. Both
                # reviewers then serialize on the same current-event record.
                if db.sqlite:
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    from sqlalchemy.dialects.postgresql import insert
                session.execute(insert(StatementCurrent).values(target_id=target_id, adoption_id=None)
                    .on_conflict_do_nothing(index_elements=["target_id"]))
                current = session.scalar(select(StatementCurrent).where(StatementCurrent.target_id == target_id).with_for_update())
                head = current.adoption_id or (row.packet.get("initial_admission") or {}).get("id")
                if head != row.packet["expected_adoption_id"]:
                    raise HTTPException(409, "The currently adopted argument changed. Refresh this proposal before approval.")
                validate_target_identity(session, user, row.packet)
                validate_withdrawal(session, user, row.packet)
                adoption_id = str(uuid4())
                session.add(StatementAdoption(id=adoption_id, update_id=row.id, target_id=target_id,
                    reviewer_id=user.id, created_at=clock(), previous_id=head))
                current.adoption_id = adoption_id
            row.assessment = {"decision": body.decision, "reason": body.reason, "created_at": clock(),
                "reviewer_id": user.id, "reviewer": {"handle": user.handle}, "adoption_id": adoption_id,
                "idempotency_key": body.idempotency_key, "request_sha256": commitment}
            session.flush()
            return {"update": update_data(session, user, row)}
