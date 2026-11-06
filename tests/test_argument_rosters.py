"""Exact propositions, local initial admissions, and reviewed argument rosters."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from crucible import notebook, registry, statement_workflow as workflow
from test_notebook import create, revise, content
from test_note_sections import local_system, publish, binding_url
from test_statement_workflow import trial, context, pin, ROOT, propose, review_body


def claim():
    return {"id": "correctness-claim", "label": "Correctness claim", "title": "Honest keys agree", "definition": "For every declared valid input, both honest parties return the same key."}


def canonical_argument(*, source=None, identifier="correctness-proof", status="unconditional"):
    return {"id": identifier, "title": "Direct algebraic argument", "conclusion_statement": {
        "statement_id": "correctness-claim", **(pin(source) if source else {})},
        "scope": "Exact RSA research configurations after successful key generation.", "status": status,
        "dependencies": []}


def prepare_baseline(system):
    app, owner, reviewer, reader, path = system
    value = content(statements=[claim()], arguments=[canonical_argument()],
        extensions={"scheme_context": {"scheme_id": "rsa-kem", "version": registry.CURRENT["rsa-kem"]}})
    note = create(owner, value); publish(owner, reviewer, note)
    manifest = {"format": "crucible-local-note-bindings-v1", "releases": [{"scheme_id": "rsa-kem",
        "version": registry.CURRENT["rsa-kem"], "note": {key: note[key] for key in ("id", "revision", "content_sha256")},
        "arguments": {"security": "correctness-proof", "correctness": "correctness-proof"}, "graph_models": {}}]}
    path.write_text(json.dumps(manifest))
    targets = reader.client.get(ROOT + "/targets").json()["targets"]
    selected = next(target for target in targets if target["target"]["kind"] == "correctness")
    return note, value, manifest, selected


def change(owner, reviewer, target, note, *, operation="select_argument", identifier="correctness-proof"):
    record = propose(owner, {"operation": operation, "target": target["target"],
        "argument": {**pin(note), "argument_id": identifier}, "evidence": [pin(note)],
        "expected_adoption_id": target["current"]["id"], "reason": "Review this exact argument for the stated proposition.",
        "idempotency_key": str(uuid4())})
    body = review_body(record)
    response = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", body)
    assert response.status_code == 200, response.text
    return record, body, response.json()


def test_canonical_conclusion_is_one_authority_and_access_checked(trial):
    _, owner, reviewer, reader = trial
    premise = create(owner, content(statements=[claim()]))
    note = create(owner, content(arguments=[canonical_argument(source=premise)]))
    publish(owner, reviewer, note)
    assert reader.client.get(context(note, "argument", "correctness-proof")).status_code == 404
    publish(owner, reviewer, premise)
    packet = reader.client.get(context(note, "argument", "correctness-proof")).json()["argument"]
    assert packet["conclusion"] == claim()["definition"]
    assert packet["resolved_conclusion_statement"]["source"] == pin(premise)
    assert reader.client.get(context(premise, "statement", "correctness-claim")).json()["related_arguments"][0]["argument_id"] == "correctness-proof"
    for argument in ({**canonical_argument(), "conclusion": "Another authority"},
                     {**canonical_argument(), "conclusion_statement": {"statement_id": "missing"}},
                     {key: value for key, value in canonical_argument().items() if key != "conclusion_statement"}):
        response = owner.post("/api/notebook/notes", {"content": content(statements=[claim()], arguments=[argument]), "idempotency_key": str(uuid4())})
        assert response.status_code == 422


def test_baseline_admission_has_no_fabricated_review_or_database_event(local_system):
    app, _, _, reader, _ = local_system
    note, _, _, target = prepare_baseline(local_system)
    assert target["current"]["origin"] == "initial_admission"
    assert target["current"]["independent_review"] is False
    assert "reviewer" not in target["current"]
    assert target["scheme_context"] == {"scheme_id": "rsa-kem", "version": registry.CURRENT["rsa-kem"]}
    assert "RSA" in target["target"]["title"] and target["target"]["title"].endswith("Correctness claim")
    assert len(target["roster"]) == 1 and target["roster"][0]["is_current"] is True
    assert target["target"]["conclusion_statement"] == {**pin(note), "statement_id": "correctness-claim"}
    packet = reader.client.get(binding_url()).json()
    assert packet["scheme_arguments"]["correctness"]["presentation"]["target_id"] == target["target"]["id"]
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 0
        assert session.scalar(select(func.count()).select_from(workflow.StatementUpdate)) == 0


def test_admit_alternative_changes_roster_head_but_not_selection_and_withdraws_alternative(local_system):
    _, owner, reviewer, reader, _ = local_system
    note, _, _, baseline = prepare_baseline(local_system)
    alternative = create(owner, content(arguments=[canonical_argument(source=note, identifier="alternative-proof")]))
    publish(owner, reviewer, alternative)
    stale = propose(owner, {"target": baseline["target"], "argument": {**pin(note), "argument_id": "correctness-proof"},
        "evidence": [pin(note)], "expected_adoption_id": baseline["current"]["id"], "reason": "An older concurrent selection.", "idempotency_key": str(uuid4())})
    record, body, response = change(owner, reviewer, baseline, alternative, operation="admit_argument", identifier="alternative-proof")
    assert reviewer.post(ROOT + "/updates/" + record["id"] + "/review", body).json() == response
    target = reader.client.get(ROOT + "/targets/" + baseline["target"]["id"]).json()
    assert target["current"]["id"] != baseline["current"]["id"]
    assert target["current"]["selection_id"] == baseline["current"]["selection_id"]
    assert target["current"]["argument"] == baseline["current"]["argument"]
    assert len(target["roster"]) == 2
    assert reviewer.post(ROOT + "/updates/" + stale["id"] + "/review", review_body(stale)).status_code == 409
    change(owner, reviewer, target, alternative, operation="withdraw_argument", identifier="alternative-proof")
    target = reader.client.get(ROOT + "/targets/" + baseline["target"]["id"]).json()
    assert target["current"]["status"] == "selected"
    assert target["current"]["argument"] == baseline["current"]["argument"]
    assert next(row for row in target["roster"] if row["argument"]["note_id"] == alternative["id"])["status"] == "withdrawn"


def test_selected_withdrawal_has_no_fallback_or_readmission_selection(local_system):
    _, owner, reviewer, reader, _ = local_system
    note, _, _, baseline = prepare_baseline(local_system)
    change(owner, reviewer, baseline, note, operation="withdraw_argument")
    target_url = ROOT + "/targets/" + baseline["target"]["id"]
    withdrawn = reader.client.get(target_url).json()
    assert withdrawn["current"]["status"] == "withdrawn"
    packet = reader.client.get(binding_url()).json()["scheme_arguments"]["correctness"]
    assert packet["source"] is None and packet["presentation"]["current_status"] == "withdrawn"
    assert packet["conclusion"] == claim()["definition"]  # no global mathematical refutation
    change(owner, reviewer, withdrawn, note, operation="admit_argument")
    readmitted = reader.client.get(target_url).json()
    assert readmitted["roster"][0]["status"] == "admitted"
    assert readmitted["current"]["status"] == "withdrawn"
    change(owner, reviewer, readmitted, note)
    restored = reader.client.get(target_url).json()
    assert restored["current"]["status"] == "selected"
    assert len(restored["history"]) == 4


def test_same_proposition_copyedit_retains_roster_but_changed_proposition_has_new_target(local_system):
    _, owner, reviewer, reader, path = local_system
    note, value, manifest, baseline = prepare_baseline(local_system)
    change(owner, reviewer, baseline, note, operation="admit_argument")
    edited = deepcopy(value); edited["body"] += " Cosmetic explanatory text."
    newer = revise(owner, note, edited); publish(owner, reviewer, newer)
    manifest["releases"][0]["note"] = {key: newer[key] for key in ("id", "revision", "content_sha256")}
    path.write_text(json.dumps(manifest))
    packet = reader.client.get(binding_url()).json()["scheme_arguments"]["correctness"]
    assert packet["presentation"]["target_id"] == baseline["target"]["id"]
    original = reader.client.get(ROOT + "/targets/" + baseline["target"]["id"]).json()
    assert original["target"]["conclusion_statement"] == baseline["target"]["conclusion_statement"]
    assert original["current"]["argument"]["revision"] == 1
    linked = reader.client.get(context(newer, "statement", "correctness-claim")).json()["targets"]
    same_result = next(row for row in linked if row["target"]["id"] == baseline["target"]["id"])
    assert same_result["conclusion_match"] == "exact_definition"
    assert same_result["target"]["conclusion_statement"] == baseline["target"]["conclusion_statement"]
    change(owner, reviewer, original, newer)  # byte-identical proposition is allowed, exact new source retained
    changed = deepcopy(edited); changed["statements"][0]["definition"] = "A different mathematical proposition."
    third = revise(owner, newer, changed); publish(owner, reviewer, third)
    manifest["releases"][0]["note"] = {key: third[key] for key in ("id", "revision", "content_sha256")}
    path.write_text(json.dumps(manifest))
    next_packet = reader.client.get(binding_url()).json()["scheme_arguments"]["correctness"]
    assert next_packet["presentation"]["target_id"] != baseline["target"]["id"]
    assert reader.client.get(ROOT + "/targets/" + baseline["target"]["id"]).status_code == 200


def test_different_conclusion_or_out_of_scope_premise_cannot_enter_roster(local_system):
    _, owner, reviewer, _, _ = local_system
    note, _, _, baseline = prepare_baseline(local_system)
    other = create(owner, content(statements=[{**claim(), "definition": "An unrelated assertion."}], arguments=[canonical_argument()]))
    publish(owner, reviewer, other)
    body = {"operation": "admit_argument", "target": baseline["target"], "argument": {**pin(other), "argument_id": "correctness-proof"},
        "evidence": [pin(other)], "expected_adoption_id": baseline["current"]["id"], "reason": "Not the same proposition.", "idempotency_key": str(uuid4())}
    assert owner.post(ROOT + "/updates", body).status_code == 409
    new_argument = canonical_argument(source=note, status="conditional")
    new_argument["dependencies"] = [{"statement": {"statement_id": "extra-premise"}, "configuration_ids": ["foreign-profile"]}]
    candidate = create(owner, content(statements=[{"id": "extra-premise", "label": "A", "title": "Premise", "definition": "An unproved condition."}], arguments=[new_argument]))
    publish(owner, reviewer, candidate)
    body.update(argument={**pin(candidate), "argument_id": "correctness-proof"}, evidence=[pin(candidate)], idempotency_key=str(uuid4()))
    assert owner.post(ROOT + "/updates", body).status_code == 409


def test_conclusion_authors_and_normative_definition_authors_are_not_independent(trial, monkeypatch):
    from crucible import protocol_proposals
    _, owner, reviewer, _ = trial
    monkeypatch.setattr(protocol_proposals, "is_reviewer", lambda settings, user: user.id in {owner.identifier, reviewer.identifier})
    assertion = create(reviewer, content(statements=[claim()]))
    publish(reviewer, owner, assertion)
    proof = create(owner, content(arguments=[canonical_argument(source=assertion)]))
    publish(owner, reviewer, proof)
    target = {"id": "canonical-conclusion", "title": "Exact assertion", "kind": "correctness", "scope": "Research only.",
              "configuration_ids": [], "conclusion_statement": {**pin(assertion), "statement_id": "correctness-claim"}}
    record = propose(owner, {"target": target, "argument": {**pin(proof), "argument_id": "correctness-proof"},
        "evidence": [pin(proof)], "expected_adoption_id": None, "reason": "Review another author's argument.", "idempotency_key": str(uuid4())})
    preview = reviewer.client.get(ROOT + "/updates/" + record["id"]).json()["update"]
    assert preview["can_review"] is False
    assert reviewer.post(ROOT + "/updates/" + record["id"] + "/review", review_body(record)).status_code == 403


def test_baseline_foreign_notes_do_not_expand_agent_scopes(local_system):
    from test_notebook_agents import agent
    app, owner, reviewer, reader, _ = local_system
    _, _, _, baseline = prepare_baseline(local_system)
    narrow = agent(app, reader, ["notebook:read"])
    assert narrow.get(ROOT + "/targets").json()["targets"] == []
    assert narrow.get(ROOT + "/targets/" + baseline["target"]["id"]).status_code == 404
    broader = agent(app, reader, ["notebook:read", "notes:read"])
    assert len(broader.get(ROOT + "/targets").json()["targets"]) == 2
    # An identical proposition does not grant access to someone else's roster.
    own_copy = create(reader, content(statements=[claim()]))
    own_context = context(own_copy, "statement", "correctness-claim")
    assert narrow.get(own_context).json()["targets"] == []
    matched = broader.get(own_context).json()["targets"]
    assert len(matched) == 2 and all(row["conclusion_match"] == "exact_definition" for row in matched)


def test_withdrawn_conclusion_blocks_display_without_reviving_another_argument(local_system):
    from test_notebook import publication_body
    app, owner, reviewer, reader, _ = local_system
    note, _, _, baseline = prepare_baseline(local_system)
    candidate = create(owner, content(arguments=[canonical_argument(source=note, identifier="separate-proof")]))
    publish(owner, reviewer, candidate)
    record, decision, _ = change(owner, reviewer, baseline, candidate, identifier="separate-proof")
    reviewer.post(f"/api/notebook/notes/{note['id']}/publication-withdrawals", {
        **publication_body(note), "reason": "The source needs correction."}).raise_for_status()
    assert reader.client.get(ROOT + "/targets/" + baseline["target"]["id"]).status_code == 404
    assert reader.client.get(binding_url()).status_code == 404
    retry = reviewer.post(ROOT + "/updates/" + record["id"] + "/review", decision)
    assert retry.status_code == 200 and retry.json()["update"]["source_unavailable"] is True
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(workflow.StatementAdoption)) == 1
