"""Overview summaries describe exact releases without changing their contracts."""
import hashlib
import json
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible import dh_v3, dh_v4, kem, registry, rsa, scoring
from crucible.db import Challenge, Submission
from crucible.legacy import teaching_dh_v1
from test_api import system


@pytest.mark.parametrize("adapter,scope,count", [
    (teaching_dh_v1, "the five legacy configurations 20, 24, 28, 32, and 36 in release 1", 5),
    (kem, "every registered configuration from 20 through 36 in release 2", 17),
    (dh_v3, "every registered configuration from 20 through 128 in release 3", 109),
    (dh_v4, "every registered configuration from 20 through 300 in release 4", 281),
    (rsa, "every registered configuration from 20 through 128 in release 1", 109),
])
def test_overview_is_bounded_plain_text_with_exact_honest_correctness_scope(adapter, scope, count):
    descriptor = registry.descriptor(adapter)
    overview = descriptor["overview"]
    assert set(overview) == {"strengths", "limitations", "correctness", "security"}
    assert len(descriptor["profiles"]) == count
    for field in ("strengths", "limitations"):
        assert type(overview[field]) is list and 1 <= len(overview[field]) <= 4
        assert all(type(item) is str and 1 <= len(item) <= 240 for item in overview[field])
    for field in ("correctness", "security"):
        assert type(overview[field]) is str and 1 <= len(overview[field]) <= 400
    text = " ".join([*overview["strengths"], *overview["limitations"], overview["correctness"], overview["security"]])
    assert "<" not in text and ">" not in text
    assert scope in overview["correctness"]
    assert "failure probability 0" in overview["correctness"]
    assert "Independent implementation review is pending." in overview["correctness"]
    assert all(profile["correctness"]["kind"] == "perfect" for profile in descriptor["profiles"])
    assert all(profile["correctness"]["failure_bound"] == "0" for profile in descriptor["profiles"])
    assert "no security reduction or security level" in overview["security"]
    assert "no IND-CPA or IND-CCA guarantee is claimed" in text
    assert "not estimated security bits" in text
    assert "does not establish 256-bit security" in text
    assert all(profile["shared_secret_bytes"] == 32 for profile in descriptor["profiles"])


def test_construction_properties_do_not_turn_into_cross_scheme_security_comparisons():
    dh = registry.descriptor(dh_v4)["overview"]
    rsa_overview = registry.descriptor(rsa)["overview"]
    assert dh["strengths"] == [
        "The public key and ciphertext each encode one group element; the group parameters are shared separately.",
        "Every new challenge independently samples the private exponent and encapsulation exponent in the registered prime-order group.",
    ]
    assert dh["limitations"][0] == (
        "Each configuration reuses its registered group. Group-specific preparation may be reusable across challenges and should be reported separately from online work."
    )
    assert rsa_overview["strengths"] == [
        "Key generation independently samples primes for each key; the modulus is not a shared scheme-wide parameter.",
        "The public key encodes the modulus and exponent; the ciphertext encodes one modulus-width integer.",
    ]
    assert "not a standardized RSA-KEM implementation" in rsa_overview["limitations"][0]
    assert "not minimal RSA encodings" in rsa_overview["limitations"][2]
    assert "factoring the modulus enables inversion, but equivalence is not claimed" in rsa_overview["security"]
    assert "even for nonunits" in rsa_overview["correctness"]
    for overview in (dh, rsa_overview):
        assert "variable-time Python arithmetic" in " ".join(overview["limitations"])
        assert not any(term in " ".join(overview["strengths"]).lower() for term in (
            "faster", "more secure", "post-quantum", "256-bit security", "constant-time",
        ))


def test_unknown_releases_do_not_inherit_reviewed_facts_and_results_are_independent():
    unknown = registry.overview_for(kem.SCHEME_ID, "99")
    assert unknown == {
        "strengths": [], "limitations": [],
        "correctness": "No correctness overview has been reviewed for this release.",
        "security": "No security overview has been reviewed for this release.",
    }
    assert registry.overview_for("future-kem", "1") == unknown
    current = registry.overview_for(kem.SCHEME_ID, "4")
    current["strengths"].clear()
    current["limitations"].append("Injected display value")
    current["correctness"] = "Wrong scope"
    assert len(registry.overview_for(kem.SCHEME_ID, "4")["strengths"]) == 2
    assert "Injected display value" not in registry.overview_for(kem.SCHEME_ID, "4")["limitations"]
    assert "release 4" in registry.overview_for(kem.SCHEME_ID, "4")["correctness"]
    assert "release 1" in registry.overview_for(kem.SCHEME_ID, "1")["correctness"]


def test_api_overview_preserves_archived_manifests_source_pins_and_byte_metrics_without_issuing(system):
    app, _, _ = system
    client = TestClient(app)
    originals = {identity: adapter.manifests() for identity, adapter in registry.ADAPTERS.items()}
    with ExitStack() as stack:
        for adapter in registry.ADAPTERS.values():
            stack.enter_context(patch.object(adapter, "generate", side_effect=AssertionError("Metadata cannot issue an instance")))
            if hasattr(adapter, "keygen"):
                stack.enter_context(patch.object(adapter, "keygen", side_effect=AssertionError("Metadata cannot generate a key")))
        response = client.get("/api/schemes")
        assert response.status_code == 200
        schemes = response.json()["schemes"]
        releases = [release for scheme in schemes for release in [scheme, *scheme["previous_releases"]]]
        assert {(item["id"], item["version"]) for item in releases} == set(registry.ADAPTERS)
        for release in releases:
            identity = release["id"], release["version"]
            adapter = registry.ADAPTERS[identity]
            assert release["overview"] == registry.overview_for(*identity)
            # The frozen contract remains exact, including an older release's
            # historical scoring flag. Only the catalogue overlays live policy.
            assert adapter.manifests() == originals[identity]
            display_fields = {"stage_number", "native_parameter_label", "progression_id", "stage_label", "stage_position", "stage_context", "exact_parameter_json"}
            for profile in registry.descriptor(adapter)["profiles"]:
                if "exact_parameter_json" in profile:
                    assert json.loads(profile["exact_parameter_json"]) == {
                        key: profile[key] for key in ("algorithms", "parameters", "encoding", "kdf") if key in profile}
            assert [{k: v for k, v in p.items() if k not in display_fields}
                    for p in registry.descriptor(adapter)["profiles"]] == originals[identity]
            presented = deepcopy(originals[identity])
            for profile in presented:
                if "scoring_eligible" in profile:
                    profile["scoring_eligible"] = scoring.manifest_eligible(profile)
                    profile["scoring_policy_id"] = scoring.POLICY_ID
            assert [{k: v for k, v in p.items() if k not in display_fields}
                    for p in release["profiles"]] == presented
            assert release["profile_metrics"] == {
                profile["id"]: registry.profile_metrics(profile) for profile in originals[identity]
            }
            source = client.get(release["protocol"]["source_url"])
            assert source.status_code == 200
            assert hashlib.sha256(source.content).hexdigest() == adapter.IMPLEMENTATION_SHA256
            assert release["participant_count"] == 0
            assert release["recovered_profile_ids"] == []
            assert release["largest_recovered_profile_id"] is None
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Challenge)) == 0
        assert session.scalar(select(func.count()).select_from(Submission)) == 0
