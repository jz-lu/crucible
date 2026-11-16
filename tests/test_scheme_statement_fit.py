"""Real-family coverage of the optional statement/argument contract, without writes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from crucible.statement_workflow import Statement, Argument

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scheme_statement_fit", ROOT / "scripts/check_scheme_statement_fit.py")
fit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fit)


@pytest.fixture(scope="module")
def report():
    return fit.build_report()


def argument(row, identifier):
    return next(item for item in row["arguments"] if item["candidate"]["id"] == identifier)


def test_all_eight_releases_fit_without_publishing_or_inventing_approval(report):
    assert report["read_only"] is True
    assert report["ready_for_adoption"] is False and report["scientific_approval"] is False
    assert report["summary"] == {"releases": 8, "configurations": 379, "statement_blocks": 34,
        "arguments": {"security": 8, "correctness": 8, "ram-attack": 10}, "authoring_gaps": 105}
    for row in report["releases"].values():
        assert row["ready_for_adoption"] is False
        for value in row["statements"]:
            Statement.model_validate(value)
        for value in row["arguments"]:
            Argument.model_validate(value["candidate"])
            assert value["scientific_approval"] is False


def test_exact_source_definitions_and_scope_survive(report):
    corpus = json.loads((ROOT / "crucible/data/scheme_note_presentations.json").read_text())
    for release, original in corpus["releases"].items():
        row = report["releases"][release]
        statements = {value["id"]: value for value in row["statements"]}
        sections = {value["id"]: value for value in original["sections"]}
        for premise in original["assumptions"]:
            expected = premise.get("statement") or sections[premise["section_id"]]["body"]
            if premise.get("scope"):
                expected += "\n\nDeclared scope: " + premise["scope"]
            assert statements[premise["id"]]["definition"] == expected
            assert row["statement_coverage"][premise["id"]]["configuration_ids"] == premise["profile_ids"]


def test_number_theory_correctness_and_security_are_separate(report):
    for release in ("teaching-dh-kem@7", "rsa-kem@4", "dhkem-x25519@curve-compressed-v2"):
        row = report["releases"][release]
        correctness = argument(row, "scheme-correctness")["candidate"]
        security = argument(row, "scheme-security")["candidate"]
        assert correctness["status"] == "unconditional" and correctness["dependencies"] == []
        assert security["status"] == "conditional" and len(security["dependencies"]) == 2
        assert "symbolic" in security["scope"]
        assert "exact" in correctness["scope"]


def test_module_correctness_bound_and_attack_message_failure_are_not_conflated(report):
    row = report["releases"]["ml-kem@compressed-research-v4"]
    correctness = argument(row, "scheme-correctness")["candidate"]
    uses = {use["statement"]["statement_id"]: use for use in correctness["dependencies"]}
    assert uses["module-independent-expansion"]["configuration_ids"] == [f"module-lwe-v4-tier-{n}" for n in (3,4,5,6)]
    assert uses["module-independent-compression"]["configuration_ids"] == [f"module-lwe-v4-tier-{n}" for n in (5,6)]
    attack = argument(row, "ram-module-native-message-enumeration-v1")
    assert attack["candidate"]["dependencies"][0]["configuration_ids"] == [f"module-lwe-v4-tier-{n}" for n in (3,4,5,6)]
    premise_id = attack["candidate"]["dependencies"][0]["statement"]["statement_id"]
    definition = next(item["definition"] for item in row["statements"] if item["id"] == premise_id)
    assert "1/8" in definition
    assert "message-decoding" in definition
    assert "complete referenced proposition" in attack["candidate"]["dependencies"][0]["use"]
    assert "2^-128" in correctness["scope"]
    assert attack["points"][0]["assumptions"] == []
    assert attack["points"][-1]["assumptions"]


def test_frodo_keeps_two_attack_methods_and_exact_native_parameters(report):
    row = report["releases"]["frodokem@anchor-research-v3"]
    attacks = [value for value in row["arguments"] if value["kind"] == "ram-attack"]
    assert sorted(len(value["points"]) for value in attacks) == [3, 77]
    assert len({point["profile_id"] for value in attacks for point in value["points"]}) == 80
    for config in row["configurations"].values():
        params = config["manifest"]["parameters"]
        assert params["q"] == 32768 and params["nbar"] == 8
        assert len(params["CDF"]) == 13


def test_code_rank_density_uses_do_not_merge_goppa_and_quasi_cyclic_distributions(report):
    hqc = report["releases"]["hqc@modeled-research-v3"]
    mce = report["releases"]["classic-mceliece@length-research-v1"]
    hqc_attack = argument(hqc, "ram-prange-recovery-of-the-quasi-cyclic-private-key")["candidate"]
    mce_attack = argument(mce, "ram-prange-syndrome-decoding-with-bounded-sampling")["candidate"]
    key = "ram-prange-structured-rank-density-v1"
    use_hqc = next(use for use in hqc_attack["dependencies"] if use["statement"]["statement_id"] == key)
    use_mce = next(use for use in mce_attack["dependencies"] if use["statement"]["statement_id"] == key)
    assert "hqc@modeled-research-v3" in use_hqc["use"]
    assert "classic-mceliece@length-research-v1" in use_mce["use"]
    assert not set(use_hqc["configuration_ids"]) & set(use_mce["configuration_ids"])
    assert len(hqc_attack["dependencies"]) == 3 and len(mce_attack["dependencies"]) == 1


def test_off_progression_ntru_configurations_are_not_lost(report):
    row = report["releases"]["sntrup@3"]
    assert row["configuration_count"] == 27
    extra = [key for key, config in row["configurations"].items() if config["stage"] is None]
    assert len(extra) == 2
    coverage = row["statement_coverage"]["ntru-hybrid-attack-model"]
    assert set(extra) <= set(coverage["configuration_ids"])
    assert len(coverage["configuration_ids"]) == 14 and len(coverage["stages"]) == 12


def test_ram_accounts_remain_exact_and_non_ram_references_remain_distinct(report):
    original = json.loads((ROOT / "crucible/data/ram_attack_curves.json").read_text())
    for release, row in report["releases"].items():
        source_points = {point["profile_id"]: point for point in original["releases"][release]["estimates"]}
        for value in row["arguments"]:
            if value["kind"] != "ram-attack":
                continue
            assert "not a lower bound on security" in value["candidate"]["conclusion"]
            for point in value["points"]:
                assert point == source_points[point["profile_id"]]
                assert point["manifest_sha256"] == row["configurations"][point["profile_id"]]["manifest_sha256"]
        assert all(value["metric"] != "ram" for value in row["historical_models"])
        assert {"definitions", "mapping", "required", "reviewed_adoption", "historical_non_ram_models"} <= {gap["field"] for gap in row["authoring_gaps"]}
