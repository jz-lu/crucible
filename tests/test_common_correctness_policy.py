"""The common claimed goal is required; unproved dependencies remain reviewable."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from crucible import correctness_policy
from crucible.protocol_specs import ProtocolSpec, builtin_spec, canonical_spec, correctness_review_gaps
from proposal_fixtures import correctness_review_spec


def prepared():
    return correctness_review_spec(builtin_spec("teaching-dh-kem", "4"))


def test_legacy_spec_and_explicit_nulls_keep_the_same_canonical_document():
    original = builtin_spec("teaching-dh-kem", "4")
    with_nulls = deepcopy(original)
    with_nulls["correctness"].update(policy_id=None, failure_bound_exponent=None)
    assert canonical_spec(ProtocolSpec.model_validate(with_nulls)) == original
    assert "policy_id" not in original["correctness"]
    assert correctness_review_gaps(original)  # Historical missing review metadata stays missing.
    assert not any("policy_id" in gap for gap in correctness_review_gaps(original))


def test_perfect_claim_needs_no_optional_target_or_invented_failure():
    spec = prepared()
    assert canonical_spec(ProtocolSpec.model_validate(spec)) == spec
    assert spec["correctness"]["failure_bound"] == "0"
    assert "policy_id" not in spec["correctness"]
    assert "failure_bound_exponent" not in spec["correctness"]
    assert correctness_review_gaps(spec) == []


@pytest.mark.parametrize("exponent", [0, 64, 127, 128, 133, 138])
@pytest.mark.parametrize("include_metadata", [False, True])
def test_drafts_accept_weaker_claims_but_review_requires_the_common_declared_goal(exponent, include_metadata):
    spec = prepared()
    spec["correctness"].update(kind="statistical", failure_bound=f"2^-{exponent}")
    if include_metadata:
        spec["correctness"]["failure_bound_exponent"] = exponent
    spec["correctness"]["justification"].update(
        kind="analytical-bound", statement=f"Fixture claim: honest agreement failure <=2^-{exponent}.")
    value = canonical_spec(ProtocolSpec.model_validate(spec))
    assert (correctness_review_gaps(value) == []) is (include_metadata and exponent >= 128)
    # Schema accepts every draft above; completeness checks declarations, not truth.


@pytest.mark.parametrize("exponent", [True, "128", 128.0, -1, 1_000_001])
def test_bound_exponent_is_bounded_integer_data_not_an_expression(exponent):
    spec = prepared()
    spec["correctness"].update(kind="statistical", failure_bound="2^-128", failure_bound_exponent=exponent)
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(spec)


def test_optional_reference_declaration_never_replaces_justification():
    spec = prepared()
    spec["correctness"].update(kind="statistical", failure_bound="2^-128", failure_bound_exponent=128,
                               policy_id=correctness_policy.POLICY_ID)
    spec["correctness"].pop("justification")
    assert any("justification" in gap for gap in correctness_review_gaps(spec))
    value = canonical_spec(ProtocolSpec.model_validate(spec))
    assert value["correctness"]["policy_id"] == correctness_policy.POLICY_ID


@pytest.mark.parametrize("kind", ["model-based-analysis", "conjectural-claim"])
def test_unproved_dependencies_are_preserved_and_require_nonblank_reasoning(kind):
    spec = prepared()
    spec["correctness"].update(kind="statistical", failure_bound="2^-128", failure_bound_exponent=128)
    spec["correctness"]["justification"].update(
        kind=kind,
        statement="Conditional fixture claim: average honest disagreement probability <=2^-128.",
        conditions="Conjecture C bounds the joint noise tail for every admitted configuration; applicability to the concrete sampler is unproved.",
        derivation="Under C, the failure event is contained in that tail. Fixture rationale: symmetry motivates C; the dependency-preserving tail estimate and its parameter transfer remain unproved and can be falsified by a violating distribution.",
    )
    value = canonical_spec(ProtocolSpec.model_validate(spec))
    assert value["correctness"]["justification"] == spec["correctness"]["justification"]
    assert correctness_review_gaps(value) == []
    assert "verified" not in value["correctness"]
    for field in ("statement", "conditions", "configuration_coverage", "derivation"):
        incomplete = deepcopy(spec)
        incomplete["correctness"]["justification"][field] = ""
        with pytest.raises(ValidationError):
            ProtocolSpec.model_validate(incomplete)
