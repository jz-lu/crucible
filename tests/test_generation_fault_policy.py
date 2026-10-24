"""Service policy for frozen workers: faults hold; resource aborts can retry.

All accounts/attempts live in temporary SQLite. The malformed-output tests run
tiny synthetic child processes through the real frozen process/parser paths;
they never rebuild artifacts or run cryptographic native generation.
"""
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from crucible import generation, generation_policy, pq_native, registry
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import Challenge, ProfileIncident
from test_api import Actor


@pytest.fixture
def system(tmp_path):
    now = [3600600.0]
    app = create_app(Settings(f"sqlite:///{tmp_path / 'policy.db'}", b"f" * 32,
        origins=("http://testserver",), rate_limits=False), clock=lambda: now[0], initialize=True)
    actor = Actor(app)
    actor.register("fault_policy_tester")
    yield SimpleNamespace(app=app, actor=actor, now=now)
    actor.client.close()
    app.state.db.engine.dispose()


def issue(system, adapter):
    return system.actor.post("/api/challenges", {"scheme_id": adapter.SCHEME_ID,
        "scheme_version": adapter.SCHEME_VERSION, "profile_id": adapter.manifests()[0]["id"]})


def synthetic_result(adapter, profile_id, *, agrees=True):
    return SimpleNamespace(public={"synthetic_test_only": True, "profile_id": profile_id},
        target=b"s" * adapter.get_manifest(profile_id)["shared_secret_bytes"],
        diagnostics={"shared_secret_matches": agrees})


def rows(system, model):
    with system.app.state.db.transaction() as session:
        return session.scalars(select(model)).all()


@pytest.mark.parametrize("scheme", ["sntrup", "frodokem", "hqc", "classic-mceliece"])
@pytest.mark.parametrize("failure", ["malformed-output", "unexpected-exit"])
def test_actual_worker_protocol_fault_is_redacted_and_quarantined(system, monkeypatch, scheme, failure):
    adapter = registry.adapter_for(scheme)
    calls = []
    actual_popen = subprocess.Popen
    script = ("import os; os.write(1, b'private-invalid-worker-output')" if failure == "malformed-output"
              else "import sys; sys.exit(7)")

    def synthetic_child(arguments, **kwargs):
        calls.append(arguments)
        return actual_popen([sys.executable, "-I", "-c", script], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", synthetic_child)
    first, repeated = issue(system, adapter), issue(system, adapter)
    assert first.status_code == repeated.status_code == 503
    assert "paused for review" in first.text
    assert "private-invalid-worker-output" not in first.text + repeated.text
    assert len(calls) == 1  # The second request never enters a paused worker.
    assert rows(system, Challenge) == []
    attempts, incidents = rows(system, generation.GenerationAttempt), rows(system, ProfileIncident)
    assert len(attempts) == len(incidents) == 1
    assert (attempts[0].outcome, attempts[0].disposition) == ("implementation_fault", "unissued")
    assert incidents[0].diagnostics == {"reason": "adapter_execution_failed",
        "implementation_sha256": adapter.get_manifest(adapter.manifests()[0]["id"])["implementation"]["source_sha256"]}
    assert system.app.state.generation_limiter.snapshot()["inflight"] == 0


@pytest.mark.parametrize("scheme", ["sntrup", "frodokem", "hqc", "classic-mceliece"])
def test_declared_resource_abort_remains_retryable(system, monkeypatch, scheme):
    adapter = registry.adapter_for(scheme)
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        if len(calls) == 1:
            raise adapter.GenerationLimit("private-resource-details")
        return synthetic_result(adapter, profile_id)

    monkeypatch.setattr(adapter, "generate", generate)
    first, retry = issue(system, adapter), issue(system, adapter)
    assert first.status_code == 503 and "private-resource-details" not in first.text
    assert retry.status_code == 200 and len(calls) == 2
    assert rows(system, ProfileIncident) == []
    assert {(row.outcome, row.disposition) for row in rows(system, generation.GenerationAttempt)} == {
        ("generation_abort", "unissued"), ("agreement", "issued")}


def test_raw_native_deadline_and_abnormal_exit_are_distinct(monkeypatch):
    actual_popen = subprocess.Popen
    code = ["import time; time.sleep(2)"]
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw:
        actual_popen([sys.executable, "-I", "-c", code[0]], **kw))
    adapter = registry.adapter_for("hqc")
    with pytest.raises(pq_native.GenerationLimit) as limit:
        pq_native._execute("synthetic-worker", "generate", timeout=0.02)
    assert generation_policy.failure_outcome(adapter, limit.value) == "generation_abort"
    code[0] = "import sys; sys.exit(7)"
    with pytest.raises(pq_native.Unavailable) as fault:
        pq_native._execute("synthetic-worker", "generate", timeout=2)
    assert generation_policy.failure_outcome(adapter, fault.value) == "implementation_fault"


@pytest.mark.parametrize("failure", ["wrong-source", "inconsistent-agreement-flag"])
def test_frodo_valid_length_worker_integrity_fault_is_not_a_resource_abort(system, monkeypatch, failure):
    adapter = registry.adapter_for("frodokem")
    manifest = adapter.manifests()[0]
    pk, ct = (manifest["encoding"][key] for key in ("public_key_bytes", "ciphertext_bytes"))
    source = bytes.fromhex(adapter.IMPLEMENTATION_SHA256) if failure != "wrong-source" else bytes(32)
    payload = b"FLA3\x01" + source + b"p" * pk + b"c" * ct + b"a" * 16
    payload += (b"a" if failure == "wrong-source" else b"b") * 16
    actual_popen = subprocess.Popen
    calls = []

    def child(arguments, **kwargs):
        calls.append(arguments)
        return actual_popen([sys.executable, "-I", "-c", "import os; os.write(1, " + repr(payload) + ")"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", child)
    assert issue(system, adapter).status_code == issue(system, adapter).status_code == 503
    assert len(calls) == 1
    attempt, = rows(system, generation.GenerationAttempt)
    assert attempt.outcome == "implementation_fault"
    assert len(rows(system, ProfileIncident)) == 1


@pytest.mark.parametrize("unexpected", [HTTPException(503, "private-adapter-message"),
    generation.GenerationBusy("private-adapter-message")])
def test_adapters_cannot_bypass_failure_accounting_with_web_or_limiter_exceptions(system, monkeypatch, unexpected):
    adapter = registry.adapter_for("teaching-dh-kem")
    def generate(_profile_id):
        raise unexpected
    monkeypatch.setattr(adapter, "generate", generate)
    response = issue(system, adapter)
    assert response.status_code == 503 and "private-adapter-message" not in response.text
    attempt, = rows(system, generation.GenerationAttempt)
    assert attempt.outcome == "implementation_fault"
    assert len(rows(system, ProfileIncident)) == 1


def test_only_reviewed_resource_types_are_retryable():
    class GenerationLimit(RuntimeError):
        pass
    fake_adapter = SimpleNamespace(GenerationLimit=GenerationLimit)
    for error in (GenerationLimit("timeout"), RuntimeError("resource exhaustion"), TimeoutError()):
        assert generation_policy.failure_outcome(fake_adapter, error) == "implementation_fault"
    for resource_type in generation_policy.RESOURCE_LIMIT_TYPES:
        assert generation_policy.failure_outcome(SimpleNamespace(), resource_type()) == "generation_abort"


@pytest.mark.parametrize("scheme", ["teaching-dh-kem", "rsa-kem"])
def test_dh_and_rsa_new_draws_are_counted_but_reuse_is_not(system, monkeypatch, scheme):
    adapter = registry.adapter_for(scheme)
    monkeypatch.setattr(adapter, "generate", lambda profile_id: synthetic_result(adapter, profile_id))
    first, repeated = issue(system, adapter), issue(system, adapter)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["challenge"] == repeated.json()["challenge"]
    attempts = rows(system, generation.GenerationAttempt)
    assert len(attempts) == 1
    assert (attempts[0].outcome, attempts[0].disposition) == ("agreement", "issued")
    response = system.actor.client.get("/api/protocol/correctness", params={"scheme_id": scheme})
    assert response.status_code == 200
    assert response.json()["outcomes"] == [{"profile_id": attempts[0].profile_id,
        "outcome": "agreement", "disposition": "issued", "count": 1}]
    assert "Historical unrecorded draws are not reconstructed" in response.json()["interpretation"]
    assert "user_id" not in response.text and '"target"' not in response.text


def test_rsa_search_exhaustion_is_recorded_without_a_hold(system, monkeypatch):
    adapter = registry.adapter_for("rsa-kem")
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        if len(calls) == 1:
            raise adapter.PrimeGenerationLimit("private-prime-search-details")
        return synthetic_result(adapter, profile_id)

    monkeypatch.setattr(adapter, "generate", generate)
    assert issue(system, adapter).status_code == 503
    assert issue(system, adapter).status_code == 200
    assert rows(system, ProfileIncident) == []
    assert {(row.outcome, row.disposition) for row in rows(system, generation.GenerationAttempt)} == {
        ("generation_abort", "unissued"), ("agreement", "issued")}


def test_honest_disagreement_is_held_without_being_called_an_implementation_fault(system, monkeypatch):
    adapter = registry.adapter_for("teaching-dh-kem")
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        return synthetic_result(adapter, profile_id, agrees=False)

    monkeypatch.setattr(adapter, "generate", generate)
    assert issue(system, adapter).status_code == issue(system, adapter).status_code == 503
    assert len(calls) == 1
    attempt, = rows(system, generation.GenerationAttempt)
    incident, = rows(system, ProfileIncident)
    assert (attempt.outcome, attempt.disposition) == ("agreement_failure_under_review", "quarantined")
    assert incident.diagnostics["reason"] == "agreement_failure_under_review"
    assert rows(system, Challenge) == []


@pytest.mark.parametrize("malformation", ["missing-public", "public-list", "missing-target",
    "target-string", "target-bytearray", "target-length", "missing-diagnostics",
    "diagnostics-list", "missing-match", "match-string", "match-integer", "match-null"])
def test_malformed_adapter_result_is_accounted_and_held(system, monkeypatch, malformation):
    adapter = registry.adapter_for("teaching-dh-kem")
    calls = []

    def generate(profile_id):
        calls.append(profile_id)
        result = synthetic_result(adapter, profile_id)
        if malformation.startswith("missing-") and malformation != "missing-match":
            delattr(result, malformation.removeprefix("missing-"))
        elif malformation == "public-list":
            result.public = []
        elif malformation == "target-string":
            result.target = "private-invalid-target"
        elif malformation == "target-bytearray":
            result.target = bytearray(result.target)
        elif malformation == "target-length":
            result.target = result.target[:-1]
        elif malformation == "diagnostics-list":
            result.diagnostics = []
        elif malformation == "missing-match":
            result.diagnostics = {}
        else:
            result.diagnostics["shared_secret_matches"] = {
                "match-string": "true", "match-integer": 1, "match-null": None}[malformation]
        return result

    monkeypatch.setattr(adapter, "generate", generate)
    first, repeated = issue(system, adapter), issue(system, adapter)
    assert first.status_code == repeated.status_code == 503
    assert "private-invalid-target" not in first.text + repeated.text
    assert len(calls) == 1 and rows(system, Challenge) == []
    attempt, = rows(system, generation.GenerationAttempt)
    incident, = rows(system, ProfileIncident)
    assert (attempt.outcome, attempt.disposition) == ("implementation_fault", "unissued")
    assert incident.diagnostics["reason"] == "adapter_execution_failed"
