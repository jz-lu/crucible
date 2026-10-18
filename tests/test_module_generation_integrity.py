"""A worker protocol fault must not become a retryable generation abort."""
from sqlalchemy import select

import pytest

from crucible import generation, lattice_module_v4 as module, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge, ProfileIncident
from test_api import Actor


@pytest.fixture
def beta(tmp_path, monkeypatch):
    monkeypatch.setitem(registry.CURRENT, module.SCHEME_ID, module.SCHEME_VERSION)
    app = create_app(Settings(f"sqlite:///{tmp_path / 'integrity.db'}", b"i" * 32,
        origins=("http://testserver",), rate_limits=False), clock=lambda: 3600600.0, initialize=True)
    actor = Actor(app)
    actor.register("integrity_tester")
    yield app, actor
    actor.client.close()
    app.state.db.engine.dispose()


def issue(actor):
    return actor.post("/api/challenges", {"scheme_id": module.SCHEME_ID,
        "scheme_version": module.SCHEME_VERSION, "profile_id": "module-lwe-v4-tier-1"})


def synthetic_worker_payload(p, *, consistent_flag):
    # No cryptographic computation: the private fixture tests outcome accounting.
    return (b"MLC4" + b"\x01" + bytes.fromhex(module.IMPLEMENTATION_SHA256)
            + bytes.fromhex(module.artifact_manifest()["artifact_sha256"])
            + b"p" * p.public_key_bytes + b"c" * p.ciphertext_bytes
            + b"a" * 32 + (b"a" if consistent_flag else b"b") * 32)


@pytest.mark.parametrize("failure", ["protocol", "provenance", "agreement-flag", "legacy-subclass"])
def test_integrity_fault_is_recorded_once_and_quarantined(beta, monkeypatch, failure):
    app, actor = beta
    calls = []
    private_detail = "private-worker-diagnostic-must-not-escape"
    if failure == "legacy-subclass":
        # An adapter may inherit an availability type for compatibility. The
        # explicit integrity contract must take precedence over that base class.
        class IntegritySubclass(module.GenerationLimit):
            pass
        monkeypatch.setattr(module, "GenerationIntegrityError", IntegritySubclass)

    def failed_worker(p):
        calls.append(p)
        if failure == "agreement-flag":
            return synthetic_worker_payload(p, consistent_flag=False)
        raise module.GenerationIntegrityError(private_detail + " " + failure)

    monkeypatch.setattr(module, "_worker", failed_worker)
    first, repeated = issue(actor), issue(actor)
    assert first.status_code == repeated.status_code == 503
    assert "paused for review" in first.text
    assert private_detail not in first.text + repeated.text
    assert len(calls) == 1
    with app.state.db.transaction() as session:
        assert session.scalars(select(Challenge)).all() == []
        attempts = session.scalars(select(generation.GenerationAttempt)).all()
        incidents = session.scalars(select(ProfileIncident)).all()
        assert len(attempts) == len(incidents) == 1
        assert attempts[0].outcome == "implementation_fault"
        assert attempts[0].disposition == "unissued"
        assert incidents[0].diagnostics["reason"] == "adapter_execution_failed"
        assert private_detail not in str(incidents[0].diagnostics)
    assert app.state.generation_limiter.snapshot()["inflight"] == 0


def test_genuine_resource_abort_remains_retryable_without_an_incident(beta, monkeypatch):
    app, actor = beta
    calls = []

    def worker(p):
        calls.append(p)
        if len(calls) == 1:
            raise module.GenerationLimit("Synthetic resource exhaustion")
        return synthetic_worker_payload(p, consistent_flag=True)

    monkeypatch.setattr(module, "_worker", worker)
    assert issue(actor).status_code == 503
    assert issue(actor).status_code == 200
    assert len(calls) == 2
    with app.state.db.transaction() as session:
        assert session.scalars(select(ProfileIncident)).all() == []
        assert len(session.scalars(select(Challenge)).all()) == 1
        attempts = session.scalars(select(generation.GenerationAttempt)).all()
        assert {(a.outcome, a.disposition) for a in attempts} == {
            ("generation_abort", "unissued"), ("agreement", "issued")}
