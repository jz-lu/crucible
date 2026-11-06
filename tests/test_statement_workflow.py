"""Optional canonical note blocks and scoped local adoption: no mathematical oracle."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import notebook, statement_workflow as workflow
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import User
from test_notebook import Actor, ORIGIN, create, revise, content, publication_body, system

ROOT = "/api/notebook/statement-workflow"


@pytest.fixture
def trial(tmp_path):
    identifiers = [str(uuid4()) for _ in range(3)]
    settings = Settings(f"sqlite:///{tmp_path / 'statement-trial.db'}", b"s" * 32,
        origins=(ORIGIN,), reviewer_ids=(identifiers[1],), rate_limits=False, local_note_corpus_only=True)
    app = create_app(settings, clock=lambda: 3_600_600.0, initialize=True)
    with app.state.db.transaction(write=True) as session:
        for identifier, handle in zip(identifiers, ("statement-author", "statement-reviewer", "statement-reader")):
            session.add(User(id=identifier, handle=handle, password_hash="unused-fixture", created_at=3_600_600.0))
    yield app, *(Actor(app, identifier) for identifier in identifiers)
    app.state.db.engine.dispose()


def pin(note):
    return {"note_id": note["id"], "revision": note["revision"], "content_sha256": note["content_sha256"]}


def publish(owner, reviewer, note):
    response = owner.post(f"/api/notebook/notes/{note['id']}/publication-requests", publication_body(note))
    assert response.status_code == 200, response.text
    response = reviewer.post(f"/api/notebook/notes/{note['id']}/publication-reviews", {
        **publication_body(note), "decision": "approved", "reason": "Publish this illustrative fixture, not a scientific endorsement."})
    assert response.status_code == 200, response.text


def block():
    return {"id": "rare-failure", "label": "A-1", "title": "An explicit finite probability bound",
            "definition": "Under distribution D, Pr[F] <= 1/8."}


def use(note=None):
    return {"statement": {"statement_id": "rare-failure", **(pin(note) if note else {})},
        "configuration_ids": ["toy-32"], "required": "Pr[F] <= 1/8", "mapping": "D is uniform on 32 sample points.",
        "justification": "The attack requires successful decoding with probability at least 7/8."}


def argument(note=None, **updates):
    return {"id": "attack-success", "title": "Success conditioned on a decoding bound",
        "conclusion": "Success probability is at least 7/8.", "scope": "Toy distribution D at configuration toy-32.",
        "status": "conditional", "dependencies": [use(note)], **updates}


def structured(**updates):
    return content(**{"statements": [block()], "arguments": [argument()], **updates})


def context(note, kind="statement", identifier="rare-failure"):
    return ROOT + f"/context?note_id={note['id']}&revision={note['revision']}&sha256={note['content_sha256']}&{kind}={identifier}"


def proposal(note, *, expected=None, target="toy-attack", **updates):
    return {"target": {"id": target, "title": "Toy attack success", "kind": "attack", "scope": "Only toy-32.",
                       "configuration_ids": ["toy-32"]},
        "argument": {**pin(note), "argument_id": "attack-success"}, "evidence": [pin(note)],
        "expected_adoption_id": expected, "reason": "Select this exact argument for the local workshop.",
        "idempotency_key": str(uuid4()), **updates}


def propose(owner, body):
    response = owner.post(ROOT + "/updates", body)
    assert response.status_code == 200, response.text
    return response.json()["update"]


def review_body(record, **updates):
    return {"packet_sha256": record["packet_sha256"], "decision": "approve",
        "reason": "Checked the finite example and its exact scope.", "idempotency_key": str(uuid4()), **updates}


def test_free_form_note_unchanged_and_blocks_optional(trial):
    _, owner, _, _ = trial
    value = content()
    note = create(owner, value)
    assert note["content_sha256"] == notebook._hash(value)
    assert "statements" not in note["revisions"][0]["content"]
    assert "arguments" not in note["revisions"][0]["content"]
    only_full_text = content(statements=[block()])
    authored = create(owner, only_full_text)
    assert "sections" not in authored["revisions"][0]["content"]
    assert owner.client.get(context(authored)).json()["statement"]["definition"] == block()["definition"]


@pytest.mark.parametrize("explanation", [None, "Use the entire probability bound for the stated sampler."])
def test_simple_premise_use_preserves_exact_authored_content(trial, explanation):
    _, owner, reviewer, _ = trial
    dependency = {"statement": {"statement_id": "rare-failure"}, "configuration_ids": ["toy-32"]}
    if explanation is not None:
        dependency["use"] = explanation
    value = structured(arguments=[argument(dependencies=[dependency])])
    note = create(owner, value)
    assert note["content_sha256"] == notebook._hash(value)
    assert note["revisions"][0]["content"]["arguments"][0]["dependencies"] == [dependency]
    resolved = owner.client.get(context(note, "argument", "attack-success")).json()["argument"]
    assert resolved["dependencies"][0]["resolved_statement"]["definition"] == block()["definition"]
    assert resolved["status"] == "conditional"
    # A simpler authoring format does not bypass publication or review.
    record = propose(owner, proposal(note))
    assert reviewer.post(ROOT + f"/updates/{record['id']}/review", review_body(record)).status_code == 404
    assert owner.client.get(ROOT + "/targets").json()["targets"] == []


@pytest.mark.parametrize("changes", [{"required": "Only one historical field"},
    {"use": "New explanation", "required": "Bound", "mapping": "Map", "justification": "Reason"},
    {"use": "   "}])
def test_ambiguous_premise_application_is_rejected(trial, changes):
    _, owner, _, _ = trial
    dependency = {"statement": {"statement_id": "rare-failure"}, **changes}
    response = owner.post("/api/notebook/notes", {"content": structured(arguments=[argument(dependencies=[dependency])]), "idempotency_key": str(uuid4())})
    assert response.status_code == 422


def test_definition_identity_ignores_explanation_but_pins_normative_text(trial):
    _, owner, _, _ = trial
    first = create(owner, structured())
    original = owner.client.get(context(first)).json()["statement"]
    changed = structured(); changed["statements"][0]["title"] = "A clearer title"
    changed["body"] += " Additional explanation."
    second = revise(owner, first, changed)
    assert owner.client.get(context(second)).json()["statement"]["definition_sha256"] == original["definition_sha256"]
    changed["statements"][0]["definition"] = "Under distribution D, Pr[F] <= 1/16."
    third = revise(owner, second, changed)
    assert owner.client.get(context(third)).json()["statement"]["definition_sha256"] != original["definition_sha256"]
    assert owner.client.get(context(first)).json()["statement"] == original


@pytest.mark.parametrize("mutation", [
    lambda value: value["statements"].append(block()),
    lambda value: value["arguments"].append(argument()),
    lambda value: value["arguments"][0].update(status="unconditional"),
    lambda value: value["arguments"][0].update(dependencies=[]),
    lambda value: value["arguments"][0].update(section_id="missing"),
    lambda value: value["arguments"][0]["dependencies"][0]["statement"].update(statement_id="unknown"),
    lambda value: value["arguments"][0]["dependencies"][0]["statement"].update(note_id="partial-pin"),
    lambda value: value["statements"][0].update(id="constructor"),
    lambda value: value["arguments"][0].update(id="prototype"),
])
def test_invalid_structure_is_rejected_not_implicitly_repaired(trial, mutation):
    _, owner, _, _ = trial
    value = structured(); mutation(value)
    response = owner.post("/api/notebook/notes", {"content": value, "idempotency_key": str(uuid4())})
    assert response.status_code == 422, response.text


def test_exact_selection_and_no_private_reverse_link_disclosure(trial):
    _, owner, reviewer, reader = trial
    premise = create(owner, content(statements=[block()]))
    publish(owner, reviewer, premise)
    downstream = create(owner, content(arguments=[argument(premise)]))
    assert reader.client.get(context(premise)).json()["related_uses"] == []
    publish(owner, reviewer, downstream)
    use_record = reader.client.get(context(premise)).json()["related_uses"][0]
    assert use_record["source"] == pin(downstream)
    selected = f"/api/notebook/notes/{premise['id']}?revision=1&sha256={premise['content_sha256']}&statement=rare-failure"
    assert reader.client.get(selected).json()["note"]["selected_statement"]["id"] == "rare-failure"
    assert reader.client.get(selected + "&argument=other").status_code == 422
    assert reader.client.get(context(premise).replace(premise["content_sha256"], "0" * 64)).status_code == 409
    assert reader.client.get(context(premise) + "&statement=other").status_code == 422


def test_adoption_requires_publication_independence_and_frozen_packet(trial):
    app, owner, reviewer, reader = trial
    note = create(owner, structured())
    record = propose(owner, proposal(note))
    assert reader.client.get(ROOT + "/updates/" + record["id"]).status_code == 404
    assert owner.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record)).status_code == 403
    assert reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record)).status_code == 404
    publish(owner, reviewer, note)
    path = ROOT + "/updates/" + record["id"] + "/review"
    assert reviewer.post(path, review_body(record, packet_sha256="0" * 64)).status_code == 409
    decision = review_body(record)
    response = reviewer.post(path, decision)
    assert response.status_code == 200, response.text
    assert response.json()["update"]["status"] == "approved"
    assert response.json()["update"]["stale"] is False
    assert reviewer.post(path, decision).json() == response.json()
    selected = reader.client.get(ROOT + "/targets/toy-attack").json()
    assert "scheme_context" not in selected
    assert selected["current"]["argument"] == {**pin(note), "argument_id": "attack-success", "section_id": None}
    assert selected["current"]["resolved_argument"]["status"] == "conditional"
    assert len(selected["history"]) == 1
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 1


def test_external_dependency_and_normative_definition_must_be_published(trial):
    _, owner, reviewer, reader = trial
    definition = create(owner, content(title="Private sampler definition"))
    premise_value = content(statements=[{**block(), "definitions": [pin(definition)]}])
    premise = create(owner, premise_value)
    attack = create(owner, content(arguments=[argument(premise)]))
    publish(owner, reviewer, premise); publish(owner, reviewer, attack)
    record = propose(owner, proposal(attack))
    response = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record))
    assert response.status_code == 404
    assert reader.client.get(context(premise)).status_code == 404
    publish(owner, reviewer, definition)
    response = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record))
    assert response.status_code == 200, response.text


def test_stale_replacement_cannot_overwrite_new_adoption_even_after_revert(trial):
    _, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    initial = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + initial["id"] + "/review", review_body(initial)).raise_for_status()
    event1 = reader.client.get(ROOT + "/targets/toy-attack").json()["current"]["id"]
    pending = propose(owner, proposal(note, expected=event1))
    competing = propose(owner, proposal(note, expected=event1))
    reviewer.post(ROOT + "/updates/" + competing["id"] + "/review", review_body(competing)).raise_for_status()
    assert owner.client.get(ROOT + "/updates/" + pending["id"]).json()["update"]["stale"] is True
    response = reviewer.post(ROOT + "/updates/" + pending["id"] + "/review", review_body(pending))
    assert response.status_code == 409
    # Identical argument content has a new event identity; stale reviews stay stale.
    assert reader.client.get(ROOT + "/targets/toy-attack").json()["current"]["id"] != event1


def test_alternative_arguments_remain_distinct_and_empty_is_not_unconditional(trial):
    _, owner, reviewer, reader = trial
    value = structured()
    value["arguments"].append(argument(id="independent-count", status="unconditional", dependencies=[],
        title="Independent finite enumeration", scope="Enumeration of the exact finite sample space only."))
    value["arguments"].append(argument(id="missing-proof", status="incomplete", dependencies=[]))
    note = create(owner, value); publish(owner, reviewer, note)
    one = reader.client.get(context(note, "argument", "attack-success")).json()["argument"]
    two = reader.client.get(context(note, "argument", "independent-count")).json()["argument"]
    unknown = reader.client.get(context(note, "argument", "missing-proof")).json()["argument"]
    assert one["status"] == "conditional" and len(one["dependencies"]) == 1
    assert two["status"] == "unconditional" and two["assessment"] == "authored"
    assert unknown["status"] == "incomplete"
    record = propose(owner, proposal(note, argument={**pin(note), "argument_id": "missing-proof"}))
    response = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record))
    assert response.status_code == 409


def test_reviewer_cannot_review_own_evidence_even_if_another_author_submits(trial):
    _, owner, reviewer, reader = trial
    note = create(reviewer, structured())
    # Configure the owner as a second publication reviewer solely for this test.
    # Scientific adoption still uses its independent-author checks.
    app = owner.app
    # The route closure uses the original settings, so publish explicitly via
    # the ordinary publication records after an author request; fixture-only.
    reviewer.post(f"/api/notebook/notes/{note['id']}/publication-requests", publication_body(note)).raise_for_status()
    with app.state.db.transaction(write=True) as session:
        revision = session.scalar(select(notebook.NotebookRevision).where(notebook.NotebookRevision.note_id == note["id"]))
        publication = session.scalar(select(notebook.NotebookPublication).where(notebook.NotebookPublication.revision_id == revision.id))
        publication.decision = "approved"; publication.reviewer_id = owner.identifier
        publication.reviewed_at = app.state.clock(); publication.reason = "Test-only independent publication."
        publication.test_only_self_review = False
    record = propose(owner, proposal(note))
    response = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record))
    assert response.status_code == 403


def test_shared_statement_uses_keep_distinct_distribution_and_threshold(trial):
    _, owner, reviewer, reader = trial
    premise = create(owner, content(statements=[block()]))
    publish(owner, reviewer, premise)
    attack = argument(premise)
    second = argument(premise, id="correctness-bound", title="A separate key-agreement event")
    second["dependencies"][0].update(configuration_ids=["native-q3329-n256"],
        required="Pr[K != K'] <= 2^-128", mapping="The native seeded sampler and its key disagreement event.")
    note = create(owner, content(arguments=[attack, second])); publish(owner, reviewer, note)
    context_value = reader.client.get(context(premise)).json()
    rows = {row["argument_id"]: row for row in context_value["related_uses"]}
    assert rows["attack-success"]["uses"][0]["required"] == "Pr[F] <= 1/8"
    assert rows["correctness-bound"]["uses"][0]["configuration_ids"] == ["native-q3329-n256"]
    assert context_value["targets"] == []  # Authorship/citation is not adoption.


def test_withdrawal_hides_current_presentation_without_erasing_history(trial):
    app, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    record = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record)).raise_for_status()
    response = reviewer.post(f"/api/notebook/notes/{note['id']}/publication-withdrawals", {
        **publication_body(note), "reason": "Withdraw the illustrative source."})
    assert response.status_code == 200, response.text
    assert reader.client.get(ROOT + "/targets/toy-attack").status_code == 404
    assert reader.client.get(ROOT + "/targets").json()["targets"] == []
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 1


def test_related_research_links_exact_statement_not_merely_note(trial):
    _, owner, reviewer, reader = trial
    note = create(owner, content(statements=[block()]))
    publish(owner, reviewer, note)
    url = f"/#notebook?note={note['id']}&revision=1&sha256={note['content_sha256']}&statement=rare-failure"
    evidence = create(owner, content(references=[{"url": url, "relation": "challenges", "scope": "Only toy-32."}]))
    assert reader.client.get(context(note)).json()["related_research"] == []
    publish(owner, reviewer, evidence)
    relation = reader.client.get(context(note)).json()["related_research"]
    assert len(relation) == 1 and relation[0]["source"] == pin(evidence)


def test_workflow_routes_absent_outside_local_corpus(system):
    _, owner, _, _, _ = system
    assert owner.client.get(ROOT + "/targets").status_code == 404


def test_scoped_withdrawal_preserves_history_and_allows_reviewed_replacement(trial):
    app, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    first = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + first["id"] + "/review", review_body(first)).raise_for_status()
    event1 = reader.client.get(ROOT + "/targets/toy-attack").json()["current"]["id"]
    pending = propose(owner, proposal(note, expected=event1))
    withdrawal = propose(owner, proposal(note, expected=event1, operation="withdraw_argument",
        reason="The scoped evidence undermines this justification; it does not show the conclusion false."))
    decision = review_body(withdrawal)
    response = reviewer.post(ROOT + "/updates/" + withdrawal["id"] + "/review", decision)
    assert response.status_code == 200, response.text
    assert reviewer.post(ROOT + "/updates/" + withdrawal["id"] + "/review", decision).json() == response.json()
    target = reader.client.get(ROOT + "/targets/toy-attack").json()
    assert target["current"]["status"] == "withdrawn"
    assert target["current"]["resolved_argument"] is None
    assert target["current"]["withdrawn_argument"]["id"] == "attack-success"
    assert len(target["history"]) == 2
    # Even the statement remains available, with the selected argument withdrawn.
    assert reader.client.get(context(note)).json()["targets"][0]["current"]["status"] == "withdrawn"
    assert reviewer.post(ROOT + "/updates/" + pending["id"] + "/review", review_body(pending)).status_code == 409
    event2 = target["current"]["id"]
    repeated = owner.post(ROOT + "/updates", proposal(note, expected=event2, operation="withdraw_argument"))
    assert repeated.status_code == 409
    # A new supported argument is an explicit new review, never automatic fallback.
    changed = structured(); changed["arguments"][0].update(status="unconditional", dependencies=[])
    revised = revise(owner, note, changed); publish(owner, reviewer, revised)
    replacement = propose(owner, proposal(revised, expected=event2))
    reviewer.post(ROOT + "/updates/" + replacement["id"] + "/review", review_body(replacement)).raise_for_status()
    target = reader.client.get(ROOT + "/targets/toy-attack").json()
    assert target["current"]["status"] == "selected"
    assert target["current"]["argument"]["revision"] == revised["revision"]
    assert len(target["history"]) == 3
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 3


def test_withdrawal_cannot_change_the_current_target_scope_or_argument(trial):
    _, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    first = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + first["id"] + "/review", review_body(first)).raise_for_status()
    current = reader.client.get(ROOT + "/targets/toy-attack").json()["current"]
    bad = proposal(note, expected=current["id"], operation="withdraw_argument")
    bad["target"]["scope"] = "An unrelated set of configurations."
    assert owner.post(ROOT + "/updates", bad).status_code == 409
    bad = proposal(note, operation="withdraw_argument", target="unadopted-target")
    assert owner.post(ROOT + "/updates", bad).status_code == 409


def test_agent_scopes_do_not_inherit_shared_reads_or_human_review(trial):
    from test_notebook_agents import agent
    app, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    pending = propose(owner, proposal(note))
    private = create(owner, structured())
    own = create(reviewer, structured())
    workspace_only = agent(app, reviewer, ["notebook:read"])
    assert workspace_only.get(context(note)).status_code == 404
    assert workspace_only.get(context(own)).status_code == 200
    shared = agent(app, reviewer, ["notebook:read", "notes:read"])
    assert shared.get(context(note)).status_code == 200
    assert shared.get(context(note)).json()["can_propose"] is False
    assert shared.get(context(private)).status_code == 404
    assert shared.get(ROOT + "/updates/" + pending["id"]).status_code == 404
    assert shared.get(ROOT + "/updates").json()["updates"] == []
    assert shared.post(ROOT + "/updates/" + pending["id"] + "/review", json=review_body(pending)).status_code == 403
    owner_agent = agent(app, owner, ["notebook:read", "notebook:write"])
    assert owner_agent.get(context(note)).json()["can_propose"] is True
    result = owner_agent.post(ROOT + "/updates", json=proposal(note, target="agent-proposed"))
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("authored_part", ["premise", "normative-definition"])
def test_independence_includes_premises_and_normative_definitions(trial, monkeypatch, authored_part):
    from crucible import protocol_proposals
    _, owner, reviewer, reader = trial
    # A second configured human can publish the first reviewer's research; this
    # does not make that first reviewer independent of its later scientific use.
    monkeypatch.setattr(protocol_proposals, "is_reviewer", lambda settings, user: user.id in {owner.identifier, reviewer.identifier})
    definition = create(reviewer if authored_part == "normative-definition" else owner, content(title="Exact sampler specification"))
    publish(reviewer, owner, definition) if authored_part == "normative-definition" else publish(owner, reviewer, definition)
    premise = create(reviewer if authored_part == "premise" else owner,
        content(statements=[{**block(), "definitions": [pin(definition)]}]))
    publish(reviewer, owner, premise) if authored_part == "premise" else publish(owner, reviewer, premise)
    attack = create(owner, content(arguments=[argument(premise)])); publish(owner, reviewer, attack)
    record = propose(owner, proposal(attack))
    preview = reviewer.client.get(ROOT + "/updates/" + record["id"]).json()["update"]
    assert preview["can_review"] is False and preview["can_apply"] is False
    assert reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record)).status_code == 403


def test_review_retry_after_normative_source_withdrawal_returns_safe_receipt(trial):
    from test_notebook_agents import agent
    app, owner, reviewer, reader = trial
    definition = create(owner, content(title="Sensitive sampler title", body="WITHDRAWN-NORMATIVE-CONTENT"))
    publish(owner, reviewer, definition)
    premise = create(owner, content(statements=[{**block(), "definitions": [pin(definition)]}]))
    publish(owner, reviewer, premise)
    attack = create(owner, content(arguments=[argument(premise)])); publish(owner, reviewer, attack)
    record = propose(owner, proposal(attack))
    decision = review_body(record, reason="WITHDRAWN-REVIEW-QUOTATION")
    review_path = ROOT + "/updates/" + record["id"] + "/review"
    reviewer.post(review_path, decision).raise_for_status()
    reviewer.post(f"/api/notebook/notes/{definition['id']}/publication-withdrawals", {
        **publication_body(definition), "reason": "Remove this normative source from visibility."}).raise_for_status()
    retried = reviewer.post(review_path, decision)
    assert retried.status_code == 200, retried.text
    receipt = retried.json()["update"]
    assert receipt["status"] == "approved" and receipt["source_unavailable"] is True
    assert "reason" not in receipt["assessment"] and "argument" not in receipt
    assert "WITHDRAWN-REVIEW-QUOTATION" not in retried.text and "Sensitive sampler title" not in retried.text
    assert owner.client.get(ROOT + "/updates/" + record["id"]).json()["update"]["source_unavailable"] is True
    assert reader.client.get(ROOT + "/updates/" + record["id"]).status_code == 404
    reviewer_agent = agent(app, reviewer, ["notebook:read", "notes:read"])
    assert reviewer_agent.get(ROOT + "/updates/" + record["id"]).status_code == 404
    assert reader.client.get(ROOT + "/targets/toy-attack").status_code == 404
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 1


@pytest.mark.parametrize("change", [
    {"title": "Success probability is at least 255/256."},
    {"scope": "A different distribution and claim."},
    {"kind": "correctness"},
    {"configuration_ids": ["different-native-configuration"]},
])
def test_argument_replacement_cannot_redefine_target_identity(trial, change):
    _, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    first = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + first["id"] + "/review", review_body(first)).raise_for_status()
    current = reader.client.get(ROOT + "/targets/toy-attack").json()["current"]
    changed = proposal(note, expected=current["id"])
    changed["target"].update(change)
    assert owner.post(ROOT + "/updates", changed).status_code == 409
    unchanged = proposal(note, expected=current["id"])
    assert owner.post(ROOT + "/updates", unchanged).status_code == 200


def test_bounded_related_scan_and_history_are_explicit(trial, monkeypatch):
    _, owner, reviewer, reader = trial
    note = create(owner, structured()); publish(owner, reviewer, note)
    first = propose(owner, proposal(note))
    reviewer.post(ROOT + "/updates/" + first["id"] + "/review", review_body(first)).raise_for_status()
    current = reader.client.get(ROOT + "/targets/toy-attack").json()["current"]
    second = propose(owner, proposal(note, expected=current["id"]))
    reviewer.post(ROOT + "/updates/" + second["id"] + "/review", review_body(second)).raise_for_status()
    monkeypatch.setattr(workflow, "MAX_HISTORY_EVENTS", 1)
    record = reader.client.get(ROOT + "/targets/toy-attack")
    assert record.status_code == 409
    assert "reconstruction bound" in record.json()["detail"]
    # Truncating event reconstruction could revive a withdrawn argument. The
    # local roster therefore fails closed instead of supplying a partial state.
    assert reader.client.get(ROOT + "/targets").json()["targets"] == []
    monkeypatch.setattr(workflow, "MAX_RELATED_BYTES", 1)
    assert reader.client.get(context(note)).json()["related_uses_complete"] is False


def test_unstructured_reference_metadata_cannot_break_context_browsing(trial):
    _, owner, reviewer, reader = trial
    note = create(owner, content(statements=[block()])); publish(owner, reviewer, note)
    for metadata in (7, True, {"citation": "A free-form bibliography"}, [
            {"url": "/#notebook?note=x", "relation": {"not": "a relation"}},
            {"url": "http://[invalid/#notebook?note=x", "relation": "supports"},
            {"url": "/#notebook?" + "&".join(f"x{i}=y" for i in range(12)), "relation": "supports"}]):
        other = create(owner, content(references=metadata)); publish(owner, reviewer, other)
    response = reader.client.get(context(note))
    assert response.status_code == 200, response.text
    assert response.json()["related_research"] == []
