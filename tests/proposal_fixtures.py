"""Explicit review-plan fixture; never a scientific admission assertion."""
from copy import deepcopy


def correctness_review_spec(specification):
    """Populate review metadata for contract tests, not actual scheme endorsement.

    Preserve the fixture's existing mathematical claim verbatim; production
    builtin exports deliberately do not acquire this new review evidence.
    """
    spec = deepcopy(specification)
    correctness = spec["correctness"]
    correctness["justification"] = {
        "kind": "mathematical-proof" if correctness["kind"] == "perfect" else "analytical-bound",
        "statement": f"Fixture claim: honest agreement failure is bounded by {correctness['failure_bound']}.",
        "conditions": correctness["scope"],
        "configuration_coverage": "Contract-test fixture covering the exact configurations in parameters.supported_tiers; no independent scientific review is asserted.",
        "derivation": correctness["argument"],
    }
    spec["encoding"]["private_key"] = "Contract-test fixture: use the private state defined by the pinned keygen/decapsulate source. This fixture does not assert implementation completeness."
    return spec


def tier_design():
    return {
        "parameter_progression": "The fixture uses the exact registered profiles and their declared mathematical parameters; no interpolation or duplicate tiers.",
        "cost_tradeoff": "Review the registered canonical byte lengths and actual arithmetic dimensions; no timing measurement is claimed in this fixture.",
        "fixed_correctness": "Every profile must satisfy the specification's common correctness envelope under its stated scope; generation aborts remain separate.",
        "endpoint_status": "provisional",
        "endpoint_target": "The final registered research profile is provisional; this fixture does not claim a numerical security target.",
        "endpoint_evidence": "Fixture source and parameter declarations are identified by the reviewed artifact; independent attack-cost evaluation remains pending.",
        "parameter_selection": {
            "objective": "Minimize communication subject to this fixture's fixed construction; actual optimality is not asserted.",
            "constraints": "The construction and common correctness bound remain fixed; no artificial padding or entropy reduction.",
            "security_model": "No security optimum is claimed. Threat models and calibrated attack estimates require independent review.",
            "search_domain": "Only the exact registered fixture profiles were inspected; no global parameter search is claimed.",
            "comparison_evidence": "Fixture evidence only: compare recorded byte sizes and parameters; no claimed numerical security target.",
            "tradeoffs_and_uncertainty": "Other cost objectives and unsearched alternatives remain unresolved, with no global optimality assertion.",
        },
        "intermediate_steps": "Registered profiles remain distinct. This fixture makes no measured solve-time claim; practical gaps require review.",
    }


def conjectural_correctness_spec(specification, exponent=128):
    """A precise conditional claim for intake/admission contract tests."""
    spec = correctness_review_spec(specification)
    spec["correctness"].update(
        kind="statistical", failure_bound=f"2^-{exponent}",
        argument=f"Under conjecture C, the honest decoding-failure event is contained in a tail of probability at most 2^-{exponent}. No unconditional bound is claimed.",
        scope="Average over the declared honest key generation and encapsulation for every admitted configuration, conditional on conjecture C about the joint noise distribution.",
    )
    spec["correctness"].pop("policy_id", None)
    spec["correctness"]["failure_bound_exponent"] = exponent
    spec["correctness"]["justification"] = {
        "kind": "conjectural-claim",
        "statement": f"Conditional claim: average honest shared-secret disagreement probability is at most 2^-{exponent}.",
        "conditions": "Conjecture C states that the joint noise tail is bounded at every admitted configuration. This dependency is unproved and is not an independence assumption about individual coefficients.",
        "configuration_coverage": "Conjecture C is explicitly claimed for each exact registered configuration; transfer between configurations remains conjectural.",
        "derivation": "If C holds, the failure event is a subset of the bounded tail. Symmetry motivates C in this fixture but does not prove it. A distribution with a larger joint tail would refute C; actual scientific review is not represented by this contract fixture.",
    }
    spec["security"].update(
        notion="Passive recovery of the entire shared secret from an honest public transcript.",
        model="Probabilistic classical adversaries, no decapsulation oracle, and explicitly charged precomputation.",
        claim="Conjectural claim: shared-secret recovery requires solving the specified underlying problem, under the following reduction hypothesis; no numerical bit estimate or unconditional security certification is asserted.",
        assumptions="The reduction hypothesis connects transcript recovery to the underlying problem. Its applicability remains unproved and is a target for review and counterexamples.",
    )
    return spec
