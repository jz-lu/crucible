"""Draft assumptions are version-scoped reading aids, never certification."""
from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest

from crucible import assumption_sheets as sheets, registry


EXPECTED = {
    "teaching-dh-kem": "7", "rsa-kem": "4", "dhkem-x25519": "curve-compressed-v2",
    "ml-kem": "compressed-research-v4", "frodokem": "anchor-research-v3",
    "sntrup": "3", "hqc": "modeled-research-v3", "classic-mceliece": "length-research-v1",
}


def test_curated_drafts_cover_exactly_eight_explicit_current_releases():
    assert registry.CURRENT == EXPECTED
    assert set(sheets._SHEETS) == set(EXPECTED.items())
    for scheme_id, version in EXPECTED.items():
        sheet = sheets.sheet_for(scheme_id, version)
        assert sheet["format"] == "crucible-assumption-sheet-v1"
        assert sheet["schema_revision"] == 1
        assert sheet["revision"] == (3 if scheme_id in {"frodokem", "ml-kem"} else 2)
        assert (sheet["scheme_id"], sheet["scheme_version"]) == (scheme_id, version)
        assert sheet["curation_status"] == "draft" and sheet["completeness"] == "partial"
        assert sheet["scope"] == {"kind": "exact-release", "configuration_source": "registered-profile-manifests"}
        assert 3 <= len(sheet["claims"]) <= 6
        assert "not mathematical review" in sheet["limitations"][0]
        assert "security certification" in sheet["limitations"][0]
        assert {claim["role"] for claim in sheet["claims"]} >= {"hardness", "derived"}
        assert {target for claim in sheet["claims"] for target in claim["supports"]} == {"security", "correctness"}
        assert json.loads(json.dumps(sheet, allow_nan=False)) == sheet


def test_all_claims_have_scoped_questions_safe_sources_and_valid_target_ids():
    for identity in EXPECTED.items():
        sheet = sheets.sheet_for(*identity)
        targets = {row["id"] for row in sheet["targets"]}
        assert targets == {"security", "correctness"}
        identifiers = [claim["id"] for claim in sheet["claims"]]
        assert len(set(identifiers)) == len(identifiers)
        for claim in sheet["claims"]:
            assert claim["role"] in sheets.ROLES
            assert set(claim["supports"]) <= targets
            assert all(isinstance(claim[field], str) and claim[field].strip() for field in
                       ("title", "summary", "precise_statement", "scope", "challenge"))
            assert claim["limitations"]
            for source in claim["sources"]:
                parsed = urlsplit(source["url"])
                assert parsed.scheme == "https" and parsed.hostname
                assert not parsed.username and not parsed.password
                assert parsed.port in (None, 443)
                assert source["title"].strip()


@pytest.mark.parametrize("identity", [("hqc", "growth-research-v2"), ("ml-kem", "packed-research-v3"),
                                       ("dhkem-x25519", "1"), ("unknown", "new"), ("hqc", 999)])
def test_archived_or_unknown_identity_does_not_inherit_current_claims(identity):
    sheet = sheets.sheet_for(*identity)
    assert sheet["scheme_id"] == identity[0]
    assert sheet["scheme_version"] == str(identity[1])
    assert sheet["curation_status"] == "uncurated"
    assert sheet["claims"] == [] and sheet["completeness"] == "partial"
    assert "not assumption-free or secure" in sheet["limitations"][1]


def test_registry_current_pointer_cannot_relabel_curated_claims(monkeypatch):
    original = sheets.sheet_for("hqc", "modeled-research-v3")
    monkeypatch.setitem(registry.CURRENT, "hqc", "hypothetical-v4")
    assert sheets.sheet_for("hqc", "hypothetical-v4")["claims"] == []
    assert sheets.sheet_for("hqc", "modeled-research-v3") == original


def test_returned_drafts_cannot_mutate_other_readers_or_source_data():
    original = sheets.sheet_for("hqc", "modeled-research-v3")
    changed = sheets.sheet_for("hqc", "modeled-research-v3")
    changed["claims"][0]["precise_statement"] = "False replacement"
    changed["claims"][0]["sources"][0]["url"] = "https://example.org/changed"
    changed["targets"][0]["limitations"].clear()
    changed["limitations"].clear()
    assert sheets.sheet_for("hqc", "modeled-research-v3") == original


def test_reading_a_sheet_does_not_load_manifests_generate_or_touch_notes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Static curation must not load or execute protocol/notebook state")
    monkeypatch.setattr(registry, "adapter_for", forbidden)
    for identity in EXPECTED.items():
        assert sheets.sheet_for(*identity)["claims"]
    document = json.dumps([sheets.sheet_for(*identity) for identity in EXPECTED.items()])
    assert '"note_id"' not in document and '"content_sha256"' not in document
    assert '"reviewer"' not in document and '"verified"' not in document


@pytest.mark.parametrize("bad_url", ["javascript:alert(1)", "http://example.org/paper", "https://user:secret@example.org/paper",
                                    "https://example.org:8080/paper", "https://example.org/\npaper"])
def test_invalid_authored_source_urls_fail_closed(bad_url):
    claim = deepcopy(sheets.sheet_for("hqc", "modeled-research-v3")["claims"][0])
    claim["sources"][0]["url"] = bad_url
    with pytest.raises(ValueError):
        sheets._validate_claims([claim])


@pytest.mark.parametrize("field,value", [("supports", ["unproved-global-security"]), ("role", "verified"), ("scope", "")])
def test_missing_scope_or_fabricated_trust_labels_fail_closed(field, value):
    claim = deepcopy(sheets.sheet_for("hqc", "modeled-research-v3")["claims"][0])
    claim[field] = value
    with pytest.raises(ValueError):
        sheets._validate_claims([claim])


def test_model_specific_obligations_and_local_certificate_provenance_remain_visible():
    module = {row["id"]: row for row in sheets.sheet_for("ml-kem", "compressed-research-v4")["claims"]}
    assert "independent draws" in module["module-rounding"]["precise_statement"]
    assert "jointly independent" in module["module-rounding"]["precise_statement"]
    assert "not assumed independent" in module["module-rounding"]["precise_statement"]
    for identity, claim_id, path in [
        (("ml-kem", "compressed-research-v4"), "module-agreement", "docs/mlkem-correctness-candidates.json"),
        (("frodokem", "anchor-research-v3"), "frodo-agreement", "docs/lattice-frodo-correctness.json"),
        (("hqc", "modeled-research-v3"), "hqc-agreement", "crucible/data/hqc_model_profiles.json"),
    ]:
        claim = next(row for row in sheets.sheet_for(*identity)["claims"] if row["id"] == claim_id)
        assert path in claim["scope"] and "Crucible" in claim["scope"]


def test_module_noise_reading_items_define_the_distribution_where_it_is_used():
    sheet = sheets.sheet_for("ml-kem", "compressed-research-v4")
    claims = {claim["id"]: claim for claim in sheet["claims"]}
    # Either item can be opened on its own in the focused reading panel.
    for identifier in ("module-lwe", "module-expansion"):
        statement = claims[identifier]["precise_statement"]
        assert "CBD3 means the centered binomial distribution" in statement
        assert "three independent fair 0/1 bits" in statement
        assert "three other independent fair bits" in statement
        assert "not uniform" in statement
        assert "CBD2 uses two bits in each sum" in statement
        assert "from -2 to 2" in statement
    rounding = claims["module-rounding"]["precise_statement"]
    assert "vector component" in rounding and "polynomial component" in rounding
    assert "not assumed independent" in rounding
    assert "number-theoretic transform (NTT" in claims["module-implementation"]["precise_statement"]


def test_frodo_separates_security_premises_correctness_model_result_and_open_obligations():
    sheet = sheets.sheet_for("frodokem", "anchor-research-v3")
    claims = {claim["id"]: claim for claim in sheet["claims"]}
    assert set(claims) == {"frodo-lwe", "frodo-expansion", "frodo-noise-model",
                           "frodo-agreement", "frodo-security-argument", "frodo-implementation"}
    assert all(claim["purpose"].strip() for claim in claims.values())
    assert claims["frodo-lwe"]["supports"] == ["security"]
    assert claims["frodo-expansion"]["supports"] == ["security"]
    assert claims["frodo-noise-model"]["supports"] == ["correctness"]
    assert claims["frodo-agreement"]["role"] == "derived"
    assert claims["frodo-agreement"]["supports"] == ["correctness"]
    assert claims["frodo-agreement"]["depends_on"] == ["frodo-noise-model", "frodo-implementation"]
    # Correctness's reading references must not acquire a hardness dependency.
    referenced = set(claims["frodo-agreement"]["depends_on"])
    for identifier in tuple(referenced):
        referenced.update(claims[identifier]["depends_on"])
    assert not referenced & {"frodo-lwe", "frodo-expansion", "frodo-security-argument"}
    noise = claims["frodo-noise-model"]
    assert "not distributionally identical" in noise["precise_statement"]
    assert "not sample independence" in noise["scope"]
    assert "Neither result assumes LWE hardness" in claims["frodo-agreement"]["scope"]
    proof = claims["frodo-security-argument"]
    assert proof["role"] == "reduction" and proof["status"] == "open-obligation"
    assert proof["supports"] == ["security"]
    assert set(proof["depends_on"]) == {"frodo-lwe", "frodo-expansion", "frodo-agreement", "frodo-implementation"}
    assert "does not provide that application" in proof["precise_statement"]
    assert "average disagreement bound alone does not discharge" in proof["precise_statement"]
    assert "not a verified implication" in proof["scope"]


def test_frodo_overview_separates_existing_proof_assumptions_and_local_gaps():
    sheet = sheets.sheet_for("frodokem", "anchor-research-v3")
    presentation = sheet["presentation"]
    assert "published conditional security argument" in presentation["lead"]
    sections = {section["id"]: section for section in presentation["sections"]}
    assert sections["details"]["claim_ids"] == [claim["id"] for claim in sheet["claims"]]
    overview = presentation["overview"]
    assert "IND-CCA" in overview["goal"] and "other, attacker-chosen ciphertexts" in overview["goal"]
    assert "smaller research configurations" in overview["scope"]
    groups = {group["id"]: group for group in overview["groups"]}
    evidence = " ".join(item["text"] for item in groups["evidence"]["items"])
    assert "conditional IND-CCA proofs" in evidence and "noise model" in evidence
    assert "2^{-133}" in evidence and "640-dimensional endpoint" in evidence
    assumptions = " ".join(item["text"] for item in groups["assumptions"]["items"])
    assert "does not prove this hardness" in assumptions and "not literally" in assumptions
    assert "does not establish IND-CCA" in overview["boundary"]
    assert "still leave the stated assumptions" in overview["boundary"]
    assert all(item["claim_id"] in sections["details"]["claim_ids"]
               for group in groups.values() for item in group["items"])
    assert "proof" not in sheet["curation_status"]
    archived = sheets.sheet_for("frodokem", "unreviewed-future-release")
    assert "presentation" not in archived and archived["revision"] == 1


@pytest.mark.parametrize("identity", list(EXPECTED.items()))
def test_every_current_overview_keeps_scope_and_all_formal_details_reachable(identity):
    sheet = sheets.sheet_for(*identity)
    presentation = sheet["presentation"]
    sheets._validate_presentation(presentation, sheet["claims"])
    overview = presentation["overview"]
    assert [group["id"] for group in overview["groups"]] == ["evidence", "assumptions", "gaps"]
    assert "IND-CCA" in overview["goal"] and "IND-CCA" in overview["boundary"]
    assert overview["scope"]
    reachable = {item["claim_id"] for group in overview["groups"] for item in group["items"]}
    claims = {claim["id"]: claim for claim in sheet["claims"]}
    pending = list(reachable)
    while pending:
        for linked in claims[pending.pop()].get("depends_on", []):
            if linked not in reachable:
                reachable.add(linked)
                pending.append(linked)
    assert reachable == set(claims), "The reader must not orphan formal statements or sources."
    archived = sheets.sheet_for(identity[0], "unreviewed-future-release")
    assert "presentation" not in archived and archived["claims"] == []


def test_frodo_reading_metadata_is_copied_for_each_reader():
    original = sheets.sheet_for("frodokem", "anchor-research-v3")
    changed = sheets.sheet_for("frodokem", "anchor-research-v3")
    changed["presentation"]["sections"][0]["claim_ids"].clear()
    changed["presentation"]["overview"]["groups"][0]["items"][0]["text"] = "changed"
    changed["claims"][0]["depends_on"].append("fabricated")
    changed["claims"][0]["purpose"] = "changed"
    assert sheets.sheet_for("frodokem", "anchor-research-v3") == original


@pytest.mark.parametrize("field,value", [
    ("purpose", ""), ("depends_on", ["module-lwe"]), ("depends_on", ["frodo-lwe"]),
    ("depends_on", ["frodo-expansion", "frodo-expansion"]), ("depends_on", "frodo-expansion"),
    ("status", "verified"),
])
def test_invalid_reading_metadata_cannot_assert_status_or_cross_sheet_dependencies(field, value):
    claims = sheets.sheet_for("frodokem", "anchor-research-v3")["claims"]
    claims[0][field] = value
    with pytest.raises(ValueError):
        sheets._validate_claims(claims)


@pytest.mark.parametrize("bad_ids", [["frodo-lwe"], ["module-lwe"], ["frodo-lwe", "frodo-lwe"]])
def test_presentation_cannot_hide_duplicate_or_import_other_sheet_items(bad_ids):
    sheet = sheets.sheet_for("frodokem", "anchor-research-v3")
    sheet["presentation"]["sections"][0]["claim_ids"] = bad_ids
    with pytest.raises(ValueError):
        sheets._validate_presentation(sheet["presentation"], sheet["claims"])


@pytest.mark.parametrize("mutation", [
    lambda overview: overview["groups"].pop(),
    lambda overview: overview["groups"][0]["items"][0].update(claim_id="other-release"),
    lambda overview: overview.update(scope=""),
])
def test_overviews_require_scope_and_same_sheet_references(mutation):
    sheet = sheets.sheet_for("frodokem", "anchor-research-v3")
    mutation(sheet["presentation"]["overview"])
    with pytest.raises(ValueError):
        sheets._validate_presentation(sheet["presentation"], sheet["claims"])
