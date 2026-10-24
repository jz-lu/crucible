"""Packaged issuance policy, without native workers, provider settings or secrets."""
import base64
from dataclasses import replace
import json
import os

import pytest

from crucible import config
from scripts import beta_preflight


APPROVED = (
    "teaching-dh-kem@7", "rsa-kem@4", "dhkem-x25519@curve-compressed-v2",
    "ml-kem@compressed-research-v4", "frodokem@anchor-research-v3", "sntrup@3",
    "hqc@modeled-research-v3", "classic-mceliece@length-research-v1",
)


@pytest.fixture
def production(monkeypatch, tmp_path):
    for key in tuple(os.environ):
        if key.startswith("CRUCIBLE_") or key == "DATABASE_URL":
            monkeypatch.delenv(key)
    for key, value in {
        "CRUCIBLE_ENV": "production", "CRUCIBLE_ACCESS_MODE": "beta",
        "CRUCIBLE_SECRET": base64.b64encode(b"synthetic-policy-test-secret-0000").decode(),
        "CRUCIBLE_ORIGIN": "https://policy.example.invalid",
        "DATABASE_URL": "postgresql+psycopg://fixture@db.example.invalid/unused?sslmode=verify-full",
        "CRUCIBLE_DATA_DIR": str(tmp_path / "must-not-be-created"),
    }.items():
        monkeypatch.setenv(key, value)
    yield
    assert not (tmp_path / "must-not-be-created").exists()


def test_packaged_selection_is_explicit_reviewed_eight():
    assert config.approved_beta_releases() == APPROVED


@pytest.mark.parametrize("override", [None, "", "   "])
def test_unset_or_blank_production_uses_packaged_selection(production, monkeypatch, override):
    if override is not None:
        monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", override)
    settings = config.load_settings()
    assert settings.beta_releases == APPROVED
    assert settings.beta_issuance_disabled is False


def test_override_only_restricts_and_can_be_removed(production, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", "teaching-dh-kem@7, rsa-kem@4")
    assert config.load_settings().beta_releases == APPROVED[:2]
    monkeypatch.delenv("CRUCIBLE_BETA_RELEASES")
    assert config.load_settings().beta_releases == APPROVED


@pytest.mark.parametrize("override", ["new-scheme@1", "dhkem-x25519@wrong", "*", "NONE",
    "teaching-dh-kem@7,", ",rsa-kem@4", "rsa-kem@4,rsa-kem@4", "none,rsa-kem@4"])
def test_invalid_override_never_falls_back(production, monkeypatch, override):
    monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", override)
    with pytest.raises(RuntimeError, match="subset"):
        config.load_settings()


def test_none_is_explicit_browse_only_not_accidental_empty_selection(production, monkeypatch):
    monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", "none")
    settings = config.load_settings()
    assert settings.beta_releases == () and settings.beta_issuance_disabled
    checks = beta_preflight.configuration_checks(settings)
    release_check = next(row for row in checks if row["check"] == "release_allowlist")
    assert release_check["status"] == "pass" and "intentionally disabled" in release_check["message"]
    unexplained = replace(settings, beta_issuance_disabled=False)
    assert next(row for row in beta_preflight.configuration_checks(unexplained)
                if row["check"] == "release_allowlist")["status"] == "fail"
    with pytest.raises(RuntimeError, match="Disabled beta issuance"):
        replace(settings, beta_releases=APPROVED)


@pytest.mark.parametrize("override", [None, "rsa-kem@4", "none"])
@pytest.mark.parametrize("damage", ["missing", "json", "duplicate-key", "duplicates", "empty",
    "type", "identity", "format", "review", "extra", "oversize"])
def test_invalid_catalogue_fails_closed_even_with_override(production, monkeypatch, tmp_path, override, damage):
    data = json.loads(config.APPROVED_BETA_RELEASES.read_bytes())
    path = tmp_path / "policy.json"
    if damage == "duplicates": data["releases"].append(data["releases"][0])
    if damage == "empty": data["releases"] = []
    if damage == "type": data["releases"] = "rsa-kem@4"
    if damage == "identity": data["releases"][0] = "any@*"
    if damage == "format": data["format"] = "future-format"
    if damage == "review": data["review_record"] = "unknown-review"
    if damage == "extra": data["allow_all"] = True
    raw = json.dumps(data)
    if damage == "json": raw = "not json"
    if damage == "duplicate-key": raw = raw[:-1] + ',"releases":["rsa-kem@4"]}'
    if damage == "oversize": raw = " " * 65537 + raw
    if damage != "missing": path.write_text(raw)
    monkeypatch.setattr(config, "APPROVED_BETA_RELEASES", path)
    if override is not None: monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", override)
    with pytest.raises(RuntimeError, match="catalogue is missing or invalid"):
        config.load_settings()


def test_development_policy_is_unchanged_and_does_not_read_deployment_catalogue(monkeypatch):
    monkeypatch.setattr(config, "approved_beta_releases", lambda: pytest.fail("Development must not depend on hosted policy"))
    monkeypatch.delenv("CRUCIBLE_BETA_RELEASES", raising=False)
    assert config.configured_beta_releases(production=False) == ()
    monkeypatch.setenv("CRUCIBLE_BETA_RELEASES", "local-example@1")
    assert config.configured_beta_releases(production=False) == ("local-example@1",)
