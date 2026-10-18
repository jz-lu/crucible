"""Changed geometries keep original evidence and expose honest cost contracts."""
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from crucible import ladder_designs, registry, scoring
from crucible.db import Challenge, Submission
from crucible.protocol_specs import template_spec
from test_calibrated_api import current_system
from test_people import add_user, person, registered_receipt


def test_every_current_family_explains_a_fixed_correctness_requirement():
    assert len(registry.CURRENT) == 8
    for scheme_id, version in registry.CURRENT.items():
        adapter = registry.adapter_for(scheme_id)
        description = registry.descriptor(adapter)
        design = description["ladder_design"]
        original_design = ladder_designs.for_release(scheme_id, version)
        assert {k: v for k, v in design.items() if k not in {"progression", "endpoint"}} == {
            k: v for k, v in original_design.items() if k not in {"progression", "endpoint"}}
        assert design["progression"] == description["default_progression"]["rule"]
        assert design["endpoint"] == description["default_progression"]["endpoint"]
        assert all(design[name].strip() for name in ("progression", "cost", "qualification"))
        bounds = {p["correctness"]["failure_bound"] for p in adapter.manifests()}
        if scheme_id == "frodokem":
            assert design["correctness_bound"] == "2^-133"
            assert bounds == {"0", "At most 2^-133 for every profile (conservative common bound; per-profile calculation in evidence)."}
        elif scheme_id == "ml-kem":
            assert design["correctness_bound"].startswith("2^-128 under")
            assert "model" in design["correctness_bound"]
            assert bounds == {"0", "2^-128"}
        elif scheme_id == "hqc":
            assert "conservative-transfer conjecture" in design["correctness_bound"]
            assert bounds == {"<=2^-128 under the declared conditional model"}
            assert all(p["correctness"]["failure_bound_exponent"] == 128 for p in adapter.manifests())
        else:
            assert bounds == {design["correctness_bound"]}
        assert "not optimized minima" in description["profile_metrics_basis"]["encoding"]
        design["cost"] = "mutated caller data"
        assert registry.descriptor(adapter)["ladder_design"]["cost"] != design["cost"]


@pytest.mark.parametrize("old,new,count", [
    (registry.lattice_module_v2, registry.lattice_module_v4, 6),
    (registry.lattice_module_v3, registry.lattice_module_v4, 6),
    (registry.lattice_frodo, registry.lattice_frodo_v3, 80),
    (registry.code_hqc, registry.code_hqc_v3, 66),
    (registry.code_hqc_v2, registry.code_hqc_v3, 66),
])
def test_new_ladders_do_not_alias_old_profiles_or_inherit_recoveries(old, new, count):
    assert registry.adapter_for(old.SCHEME_ID) is new
    assert registry.adapter_for(old.SCHEME_ID, old.SCHEME_VERSION) is old
    assert len(new.manifests()) == count
    assert set(p["id"] for p in old.manifests()).isdisjoint(p["id"] for p in new.manifests())
    for profile in new.manifests():
        assert profile["record_inheritance"] is False
        assert scoring.manifest_eligible(profile)
        assert registry.comparison_versions(new.SCHEME_ID, new.SCHEME_VERSION, profile["id"]) == [new.SCHEME_VERSION]
    assert not any(scoring.manifest_eligible(p) for p in old.manifests())
    assert registry.CURRENT[old.SCHEME_ID] != old.SCHEME_VERSION
    old_description = registry.descriptor(old)
    old_design = ladder_designs.for_release(old.SCHEME_ID, old.SCHEME_VERSION)
    assert {k:v for k,v in old_description["ladder_design"].items() if k not in {"progression","endpoint"}} == {
        k:v for k,v in old_design.items() if k not in {"progression","endpoint"}}
    assert registry.descriptor(old)["protocol"]["construction"] != registry.descriptor(new)["protocol"]["construction"]


@pytest.mark.parametrize("old,new", [
    (registry.lattice_module_v2, registry.lattice_module_v4),
    (registry.lattice_module_v3, registry.lattice_module_v4),
    (registry.lattice_frodo, registry.lattice_frodo_v3),
    (registry.lattice_frodo_v2, registry.lattice_frodo_v3),
    (registry.code_hqc, registry.code_hqc_v3),
    (registry.code_hqc_v2, registry.code_hqc_v3),
])
def test_old_receipts_stay_exact_and_do_not_reserve_new_first_places(current_system, old, new):
    app, _, _ = current_system
    alice, bob = (add_user(app, name) for name in ("alice", "bob"))
    archived = registered_receipt(app, alice, old, 1, 0)
    with app.state.db.transaction() as session:
        receipt = session.get(Submission, archived["submission_id"])
        challenge = session.get(Challenge, receipt.challenge_id)
        before = (receipt.payload_mac, receipt.received_at, deepcopy(challenge.manifest),
                  deepcopy(challenge.public_data), challenge.scheme_version, challenge.profile_id)
    current = registered_receipt(app, bob, new, 1, 1)
    assert person(app, "alice")["points"] == 0
    assert person(app, "bob")["points"] == {"ml-kem": 415, "frodokem": 30, "hqc": 35}[new.SCHEME_ID]
    assert person(app, "bob")["first_records"][0]["submission_id"] == current["submission_id"]
    assert archived["submission_id"] in {r["submission_id"] for r in person(app, "alice")["recent_recoveries"]}
    client = TestClient(app)
    scheme = next(s for s in client.get("/api/schemes").json()["schemes"] if s["id"] == new.SCHEME_ID)
    old_description = next(s for s in scheme["previous_releases"] if s["version"] == old.SCHEME_VERSION)
    assert old_description["recovered_profile_ids"] == [old.manifests()[0]["id"]]
    assert scheme["recovered_profile_ids"] == [new.manifests()[0]["id"]]
    with app.state.db.transaction() as session:
        receipt = session.get(Submission, archived["submission_id"])
        challenge = session.get(Challenge, receipt.challenge_id)
        after = (receipt.payload_mac, receipt.received_at, challenge.manifest,
                 challenge.public_data, challenge.scheme_version, challenge.profile_id)
        assert before == after


def test_proposal_template_teaches_ladder_requirements_and_approval_gate():
    instructions = template_spec()["_instructions"]["tier_design"]
    assert "one explicit common correctness claim with its dependencies" in instructions
    assert "message/seed enumeration" in instructions
    assert "new approval/admission requires it" in instructions
