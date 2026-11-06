"""The illustrative lifecycle cases must be arithmetically and structurally honest."""
from fractions import Fraction
import importlib.util
from pathlib import Path

import pytest

from crucible import notebook
from crucible.statement_workflow import definition_hash

SPEC = importlib.util.spec_from_file_location("statement_trial_fixture",
    Path(__file__).resolve().parents[1] / "scripts/prepare_statement_trial.py")
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)
PIN = {"id": "00000000-0000-0000-0000-000000000001", "revision": 1, "content_sha256": "a" * 64}


def test_finite_counts_separate_two_claims():
    result = trial.audit()
    assert result["counts"] == {"alpha": 1, "beta": 8}
    assert Fraction(result["probabilities"]["alpha"]) <= Fraction(1, 128)
    assert Fraction(result["probabilities"]["beta"]) > Fraction(1, 128)
    assert Fraction(result["beta_success"]) >= Fraction(7, 8)


def test_loose_upper_bound_is_not_a_refutation():
    # Both a value satisfying the strong claim and a value violating it satisfy
    # the weaker upper bound. Knowing only that bound cannot decide the claim.
    assert Fraction(1, 256) <= Fraction(1, 128) < Fraction(1, 32) <= Fraction(1, 16)
    body = trial.rough_document(PIN)["body"]
    assert "does not refute" in body and "does not establish" in body
    assert "sections" not in trial.rough_document(PIN)
    assert "statements" not in trial.rough_document(PIN)
    assert "arguments" not in trial.rough_document(PIN)


def test_restricting_domain_changes_definition_identity():
    first = trial.premise_document()["statements"][0]
    later = trial.premise_document(restricted=True)["statements"][0]
    assert first["id"] == later["id"] and first["label"] == later["label"]
    assert definition_hash(first) != definition_hash(later)
    assert r"c\in\{\alpha,\beta\}" in first["definition"]
    assert r"\forall c=\alpha" in later["definition"]


def test_two_uses_of_one_pinned_statement_have_different_requirements():
    values = trial.argument_document(PIN, PIN, PIN)["arguments"]
    attack, alternative, correctness, unavailable = values
    attack_use, correctness_use = attack["dependencies"][0], correctness["dependencies"][0]
    assert attack_use["statement"] == correctness_use["statement"]
    assert attack_use["required"] != correctness_use["required"]
    assert attack["conclusion"] == alternative["conclusion"]
    assert alternative["dependencies"] == []
    assert unavailable["status"] == "incomplete"


def test_conditional_proof_retains_its_premise():
    content = trial.conditional_document()
    argument = content["arguments"][0]
    assert Fraction(1, 256) + Fraction(7, 256) == Fraction(1, 32)
    assert argument["status"] == "conditional"
    assert argument["dependencies"][0]["statement"] == {"statement_id": "sampler-distance"}
    assert "does not prove either" in content["body"]


@pytest.mark.parametrize("content", [
    trial.premise_document(), trial.premise_document(restricted=True),
    trial.rough_document(PIN), trial.exact_document(PIN),
    trial.conditional_document(), trial.argument_document(PIN, PIN, PIN),
])
def test_fixture_notes_are_valid_optional_structured_content(content):
    normalized = notebook._content(content)
    assert normalized == content
    assert content["extensions"]["illustrative"] is True
    assert "not a KEM" in content["conditions"]
