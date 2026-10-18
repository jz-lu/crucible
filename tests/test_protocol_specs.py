"""Specification intake is bounded inert text, distinct from trusted adapters."""
import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from crucible import calibration, registry
from crucible.protocol_specs import (
    SPEC_FORMAT, SPEC_MAX_BYTES, ProtocolSpec, builtin_spec, canonical_spec, template_spec,
)
from proposal_fixtures import correctness_review_spec, tier_design


@pytest.fixture
def example():
    return builtin_spec("rsa-kem", "1")


def test_legacy_specification_canonical_bytes_and_hash_remain_identical(example):
    original = json.dumps(example, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    optional_nulls = deepcopy(example)
    optional_nulls["encoding"]["private_key"] = None
    optional_nulls["correctness"]["justification"] = None
    for candidate in (example, {**example, "tier_design": None}, optional_nulls):
        canonical = canonical_spec(ProtocolSpec.model_validate(candidate))
        current = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        assert current == original
        assert hashlib.sha256(current).digest() == hashlib.sha256(original).digest()
        assert "tier_design" not in canonical
        assert "private_key" not in canonical["encoding"]
        assert "justification" not in canonical["correctness"]


def test_mathematical_justification_is_versioned_inert_evidence_not_execution(example):
    prepared = correctness_review_spec(example)
    prepared["correctness"]["justification"]["conditions"] = "Conditional on the named setup, exact integer arithmetic, and the stated sampling distribution."
    prepared["correctness"]["justification"]["derivation"] = "An author-supplied expression such as __import__('os').system('false') is inert text, not evidence of truth."
    prepared["implementation"] = None  # Specification-only intake remains valid.
    canonical = canonical_spec(ProtocolSpec.model_validate(prepared))
    assert canonical == prepared
    assert canonical["correctness"]["justification"]["derivation"].startswith("An author-supplied")
    changed = deepcopy(prepared)
    changed["correctness"]["justification"]["conditions"] += " Additional hypothesis."
    assert canonical_spec(ProtocolSpec.model_validate(changed)) != canonical
    assert template_spec()["correctness"]["justification"] is None


@pytest.mark.parametrize("field,value", [
    ("kind", "empirical-tests"), ("kind", "verified"), ("statement", " "),
    ("statement", "x" * 3001), ("conditions", None), ("conditions", "x" * 4001),
    ("configuration_coverage", True), ("configuration_coverage", "x" * 4001),
    ("derivation", []), ("derivation", "x" * 6001), ("execute", "never"),
])
def test_mathematical_justification_requires_bounded_explicit_fields(example, field, value):
    prepared = correctness_review_spec(example)
    prepared["correctness"]["justification"][field] = value
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(prepared)


@pytest.mark.parametrize("field", ["kind", "statement", "conditions", "configuration_coverage", "derivation"])
def test_partial_mathematical_justification_is_not_silently_completed(example, field):
    prepared = correctness_review_spec(example)
    prepared["correctness"]["justification"].pop(field)
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(prepared)


@pytest.mark.parametrize("status", ["supported-estimate", "provisional", "unestimated"])
def test_tier_design_is_reviewable_inert_bounded_metadata(example, status):
    plan = tier_design()
    plan["endpoint_status"] = status
    plan["endpoint_evidence"] = "An unverified expression or https://example.invalid/reference stays inert text."
    canonical = canonical_spec(ProtocolSpec.model_validate({**example, "tier_design": plan}))
    assert canonical["tier_design"] == plan
    assert template_spec()["tier_design"]["endpoint_status"] == "unestimated"
    assert "tier_design" in ProtocolSpec.model_json_schema()["properties"]


@pytest.mark.parametrize("field,value", [
    ("parameter_progression", " "), ("cost_tradeoff", "x" * 4001),
    ("fixed_correctness", True), ("endpoint_status", "verified-secure"),
    ("endpoint_target", "x" * 2001), ("endpoint_evidence", None),
    ("intermediate_steps", []), ("execute", "arbitrary code"),
])
def test_incomplete_or_unbounded_tier_design_is_rejected(example, field, value):
    plan = tier_design()
    plan[field] = value
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate({**example, "tier_design": plan})


@pytest.mark.parametrize("identity", list(registry.ADAPTERS))
def test_every_builtin_is_valid_uniform_and_preserves_exact_source_provenance(identity):
    adapter = registry.ADAPTERS[identity]
    before = deepcopy(adapter.manifests())
    source_before = Path(inspect.getfile(adapter)).read_bytes()
    with patch.object(adapter, "generate", side_effect=AssertionError("Do not generate an instance")):
        with patch.object(adapter, "keygen", side_effect=AssertionError("Do not generate keys"), create=True):
            spec = builtin_spec(*identity)
    assert spec == canonical_spec(ProtocolSpec.model_validate(spec))
    assert spec["format"] == SPEC_FORMAT
    assert spec["name"] == registry.research_schemes.details_for(adapter, registry.DETAILS[identity[0]])["name"]
    assert spec["version"] == identity[1]
    assert set(spec["algorithms"]) == {"setup", "keygen", "encapsulate", "decapsulate"}
    assert "justification" not in spec["correctness"]  # Never manufacture new review evidence.
    assert "private_key" not in spec["encoding"]
    if before[0].get("profile_kind") == "research-tier":
        assert spec["parameters"]["distribution"].startswith(before[0]["distribution"])
        assert "conditioned on agreement" in spec["parameters"]["distribution"]
        correctness = getattr(adapter, "CORRECTNESS_SPEC", before[0]["correctness"])
        assert spec["correctness"]["argument"] == correctness["argument"]
        assert spec["correctness"]["failure_bound"] == correctness["failure_bound"]
        assert spec["parameters"]["supported_tiers"]["values"] == [p["instance_size"] for p in before]
        assert spec["implementation"]["artifact_sha256"] == before[0]["implementation"]["artifact_sha256"]
        assert spec["implementation"]["sha256"] == hashlib.sha256(source_before).hexdigest()
        assert before[0]["id"] in spec["parameters"]["mapping"] and before[-1]["id"] in spec["parameters"]["mapping"]
        assert adapter.manifests() == before
        assert Path(inspect.getfile(adapter)).read_bytes() == source_before
        return
    if before[0].get("profile_kind") == "standard-parameter-set":
        assert spec["parameters"]["distribution"].startswith(before[0]["distribution"])
        assert "conditioned on honest agreement" in spec["parameters"]["distribution"]
        assert spec["correctness"]["argument"] == before[0]["correctness"]["argument"]
        assert spec["implementation"]["artifact_sha256"] == before[0]["implementation"]["artifact_sha256"]
        assert spec["implementation"]["sha256"] == hashlib.sha256(source_before).hexdigest()
        assert all(profile["id"] in spec["parameters"]["mapping"] for profile in before)
        assert adapter.manifests() == before
        assert Path(inspect.getfile(adapter)).read_bytes() == source_before
        return
    assert spec["parameters"]["distribution"] == before[0]["distribution"]
    assert spec["correctness"]["argument"] == before[0]["correctness"]["argument"]
    assert before[0]["correctness"]["scope"] in spec["correctness"]["scope"]
    assert before[0]["correctness"]["assumptions"] in spec["correctness"]["scope"]
    assert before[0]["correctness"]["evidence_status"] in spec["correctness"]["evidence"]
    if "initial_security_estimate" in before[0]:
        assert spec["security"]["claim"].startswith(before[0]["security_claim"] + " ")
        model = registry.INITIAL_SECURITY_MODELS[before[0]["initial_security_estimate"]["model_id"]]
        assert model["formula"] in spec["security"]["claim"]
        assert all(limit in spec["security"]["claim"] for limit in model["limitations"])
    else:
        # Registered archive claims remain byte-for-byte unchanged.
        assert spec["security"]["claim"] == before[0]["security_claim"]
    assert spec["security"]["assumptions"] == before[0]["assumption_context"]
    assert "No IND-CPA or IND-CCA security claim" in spec["security"]["notion"]
    assert "no decapsulation oracle" in spec["security"]["model"]
    assert "ValueError" in spec["algorithms"]["decapsulate"]
    assert spec["implementation"]["url"] == f"/api/protocol/source?scheme_id={identity[0]}&version={identity[1]}"
    assert spec["implementation"]["sha256"] == hashlib.sha256(source_before).hexdigest()
    assert spec["implementation"]["sha256"] == before[0]["implementation"]["source_sha256"]
    assert spec["encoding"]["shared_secret_bytes"] == 32
    for field in before[0]["kdf"]["fields"]:
        assert field in spec["encoding"]["shared_secret"]
    assert before[0]["kdf"]["domain_hex"] in spec["encoding"]["shared_secret"]
    assert adapter.manifests() == before
    assert Path(inspect.getfile(adapter)).read_bytes() == source_before


def test_builtin_release_ranges_and_mappings_stay_historical():
    one = builtin_spec("teaching-dh-kem", "1")
    assert one["parameters"]["supported_tiers"] == {"kind": "list", "values": [20, 24, 28, 32, 36]}
    for manifest in registry.adapter_for("teaching-dh-kem", "1").manifests():
        for value in manifest["parameters"].values():
            assert "0x" + value in one["parameters"]["mapping"]
    for version, maximum in (("2", 36), ("3", 128), ("4", 300)):
        spec = builtin_spec("teaching-dh-kem", version)
        assert spec["parameters"]["supported_tiers"] == {"kind": "range", "min": 20, "max": maximum, "step": 1}
        assert spec["parameters"]["mapping"] == registry.adapter_for("teaching-dh-kem", version).manifests()[0]["size_parameter"]["mapping"]
        assert "shared across all keys, accounts and rounds" in spec["parameters"]["reuse"]
    rsa = builtin_spec("rsa-kem", "1")
    assert rsa["parameters"]["supported_tiers"]["max"] == 128
    assert "fresh primes" in rsa["parameters"]["reuse"]
    assert "e is padded to the same width as N" in rsa["encoding"]["public_key"]
    assert "not a standardized RSA-KEM" in rsa["security"]["claim"]


def test_builtin_default_tracks_current_but_explicit_release_does_not(monkeypatch):
    assert builtin_spec("teaching-dh-kem")["version"] == registry.CURRENT["teaching-dh-kem"]
    monkeypatch.setitem(registry.CURRENT, "teaching-dh-kem", "2")
    assert builtin_spec("teaching-dh-kem")["version"] == "2"
    assert builtin_spec("teaching-dh-kem", "4")["parameters"]["supported_tiers"]["max"] == 300
    with pytest.raises(ValueError):
        builtin_spec("unregistered")
    with pytest.raises(ValueError):
        builtin_spec("rsa-kem", "999")


def test_blank_template_requires_editing_and_calls_out_security_boundaries():
    template = template_spec()
    assert template["format"] == SPEC_FORMAT
    assert len(json.dumps(template).encode()) < SPEC_MAX_BYTES
    assert "invalid" in template["_instructions"]["algorithms"] or "malformed" in template["_instructions"]["algorithms"]
    assert "before activation" in template["_instructions"]["implementation"]
    assert "document shape only" in template["_instructions"]["purpose"]
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(template)
    template["algorithms"]["setup"] = "changed"
    assert template_spec()["algorithms"]["setup"] == ""


def test_instruction_guidance_is_optional_stripped_and_not_content(example):
    expected = canonical_spec(ProtocolSpec.model_validate(example))
    example["_instructions"] = {"help": "This is inert guidance."}
    parsed = ProtocolSpec.model_validate(example)
    assert canonical_spec(parsed) == expected
    assert canonical_spec(parsed) == expected
    serialized = parsed.model_dump(mode="json", by_alias=True)
    assert "_instructions" not in serialized
    assert canonical_spec(ProtocolSpec.model_validate(serialized)) == expected
    assert "_instructions" in ProtocolSpec.model_json_schema()["properties"]
    example["_instructions"]["help"] = "Different optional guidance does not change the content hash."
    assert canonical_spec(ProtocolSpec.model_validate(example)) == expected


@pytest.mark.parametrize("instructions", [None, "instructions", ["text"], {"help": 1}, {"": "text"}, {"help": " "}, {str(i): "x" for i in range(33)}])
def test_invalid_instructions_are_not_silently_discarded(example, instructions):
    example["_instructions"] = instructions
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)


@pytest.mark.parametrize("path,value", [
    (("other",), "field"), (("instructions",), {"help": "wrong alias"}),
    (("algorithms", "run"), "never execute"), (("encoding", "extra"), "field"),
    (("name",), " \t\n"), (("summary",), ""), (("algorithms", "decapsulate"), " "),
    (("parameters", "distribution"), ""), (("correctness", "scope"), ""),
    (("security", "model"), ""), (("encoding", "shared_secret"), ""),
    (("version",), 1), (("name",), True), (("summary",), ["text"]),
    (("format",), "crucible-kem-spec-v2"), (("correctness", "kind"), "verified"),
    (("encoding", "shared_secret_bytes"), True), (("encoding", "shared_secret_bytes"), 32.0),
    (("encoding", "shared_secret_bytes"), "32"), (("encoding", "shared_secret_bytes"), 0),
    (("encoding", "shared_secret_bytes"), 4097),
    (("implementation", "sha256"), "a" * 63), (("implementation", "sha256"), "A" * 64),
])
def test_unknown_blank_and_coerced_fields_are_rejected(example, path, value):
    target = example
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)


@pytest.mark.parametrize("tiers", [
    {"kind": "range", "min": 20, "max": 19, "step": 1},
    {"kind": "range", "min": 20, "max": 25, "step": 2},
    {"kind": "range", "min": 20, "max": 24, "step": 0},
    {"kind": "range", "min": True, "max": 24, "step": 1},
    {"kind": "range", "min": 20, "max": 24.0, "step": 1},
    {"kind": "range", "min": 1, "max": 1_000_000_001, "step": 1},
    {"kind": "list", "values": []}, {"kind": "list", "values": [2, 1]},
    {"kind": "list", "values": [1, 1]}, {"kind": "list", "values": [1, "2"]},
    {"kind": "list", "values": [True]}, {"kind": "list", "values": list(range(1, 1026))},
    {"kind": "expression", "values": "range(100)"},
])
def test_supported_tiers_are_explicit_bounded_and_strict(example, tiers):
    example["parameters"]["supported_tiers"] = tiers
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)


def test_large_declared_range_remains_a_small_description_without_expansion(example):
    example["parameters"]["supported_tiers"] = {"kind": "range", "min": 1, "max": 1_000_000_000, "step": 1}
    parsed = canonical_spec(ProtocolSpec.model_validate(example))
    assert parsed["parameters"]["supported_tiers"]["max"] == 1_000_000_000
    assert len(json.dumps(parsed)) < 10000


@pytest.mark.parametrize("kind,bound", [("perfect", None), ("perfect", "2^-128"), ("statistical", None), ("unknown", "0")])
def test_correctness_labels_are_consistent_but_not_verified(example, kind, bound):
    example["correctness"].update(kind=kind, failure_bound=bound)
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)


def test_unknown_claims_variable_outputs_and_optional_implementation_are_honest_intake(example):
    example["correctness"].update(kind="unknown", failure_bound=None, argument="No argument has been supplied; review is needed.", evidence=None)
    example["encoding"].update(shared_secret_bytes=None, shared_secret="At tier n, output n bytes using the stated derivation.")
    example["implementation"] = None
    example["references"] = []
    example["security"]["claim"] = "<script>alert('unverified author text')</script>"
    parsed = canonical_spec(ProtocolSpec.model_validate(example))
    assert parsed["encoding"]["shared_secret_bytes"] is None
    assert parsed["implementation"] is None
    assert parsed["correctness"]["evidence"] is None
    assert parsed["security"]["claim"] == example["security"]["claim"]
    # Parsing a claimed probability never evaluates or endorses its expression.
    example["correctness"].update(kind="statistical", failure_bound="unproved expression")
    assert ProtocolSpec.model_validate(example).correctness.failure_bound == "unproved expression"


@pytest.mark.parametrize("url", [
    "http://example.org/source", "javascript:alert(1)", "data:text/plain,source", "file:///tmp/source",
    "//example.org/source", "https://user:password@example.org/source", "https://example.org:99999/source",
    "https://example.org/\nsource", "https://example.org\\@other.org/source", "https:///source",
    "/api/protocol/source", "/api/protocol/source?scheme_id=rsa-kem&version=1&extra=1",
    "/api/protocol/source?scheme_id=rsa-kem&version=999", "/api/protocol/source?scheme_id=rsa-kem&version=1&version=1",
    "/api/protocol/../protocol/source?scheme_id=rsa-kem&version=1", "/some-other-local-resource",
])
def test_unsafe_or_unregistered_links_are_rejected_in_artifacts_and_references(example, url):
    for section in ("implementation", "reference"):
        candidate = deepcopy(example)
        if section == "implementation":
            candidate["implementation"]["url"] = url
        else:
            candidate["references"] = [{"title": "Reference", "url": url}]
        with pytest.raises(ValidationError):
            ProtocolSpec.model_validate(candidate)


def test_https_artifacts_are_inert_and_do_not_need_to_exist_to_describe(example):
    example["implementation"] = {"url": "https://example.invalid/source.py"}
    example["references"] = [{"title": "Future reference", "url": "https://example.invalid/paper#section"}]
    parsed = canonical_spec(ProtocolSpec.model_validate(example))
    assert parsed["implementation"] == {"url": "https://example.invalid/source.py", "sha256": None, "runtime": None, "entrypoint": None}


@pytest.mark.parametrize("value", ["\ud800", "\udfff", float("nan"), float("inf")])
def test_nonunicode_and_nonfinite_input_is_rejected_even_in_ignored_guidance(example, value):
    example["_instructions"] = {"bad": value}
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)


def test_document_and_character_budgets_are_enforced_before_normalization(example):
    example["name"] = "a" * 101
    with pytest.raises(ValidationError):
        ProtocolSpec.model_validate(example)
    example["name"] = "Valid name"
    # Each entry fits its own length, but the complete document does not.
    example["_instructions"] = {str(i): "x" * 4000 for i in range(13)}
    with pytest.raises(ValidationError, match="48 KiB"):
        ProtocolSpec.model_validate(example)
    example["_instructions"] = {str(i): "\u2603" * 1300 for i in range(13)}
    with pytest.raises(ValidationError, match="48 KiB"):
        ProtocolSpec.model_validate(example)


def test_canonical_normalization_is_repeatable_independent_and_strips_whitespace(example):
    example["name"] = "  Example  "
    original = deepcopy(example)
    first = canonical_spec(ProtocolSpec.model_validate(example))
    assert first["name"] == "Example"
    assert example == original
    assert first == canonical_spec(ProtocolSpec.model_validate(first))
    first["algorithms"]["setup"] = "Changed copy"
    assert builtin_spec("rsa-kem", "1")["algorithms"]["setup"] != "Changed copy"
    with pytest.raises(ValueError):
        canonical_spec(example)


def test_parameter_selection_preserves_earlier_plan_canonical_bytes(example):
    plan = tier_design()
    plan.pop('parameter_selection')
    legacy = {**example, 'tier_design': plan}
    assert canonical_spec(ProtocolSpec.model_validate(legacy)) == legacy
    assert canonical_spec(ProtocolSpec.model_validate({**example, 'tier_design': {
        **plan, 'parameter_selection': None}})) == legacy


@pytest.mark.parametrize('field,value', [('objective', ''), ('constraints', 'x' * 3001),
    ('security_model', 'x' * 4001), ('search_domain', 7), ('comparison_evidence', None),
    ('tradeoffs_and_uncertainty', ' '), ('unrecognized', 'must reject')])
def test_parameter_selection_is_bounded_inert_and_complete_when_provided(example, field, value):
    plan = tier_design()
    plan['parameter_selection'][field] = value
    with pytest.raises(ValueError):
        ProtocolSpec.model_validate({**example, 'tier_design': plan})
