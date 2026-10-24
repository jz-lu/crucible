"""Reference evidence is read-only, release-specific and behind beta admission."""
from unittest.mock import patch

from fastapi.testclient import TestClient

from crucible import benchmark_reports, machine_auth, registry
from crucible.reference_policy import METHOD
from test_api import system
from test_beta_boundary import private_app, connect


def test_method_and_missing_report_do_not_execute_crypto(system):
    app, _, _ = system
    adapter = registry.adapter_for("teaching-dh-kem", "7")
    with patch.object(adapter, "generate", side_effect=AssertionError("No generation")), \
            patch.object(adapter, "keygen", side_effect=AssertionError("No timing")), \
            patch.object(benchmark_reports, "report_for", return_value=None) as read:
        client = TestClient(app)
        assert client.get("/api/protocol/reference-method").json()["method"] == METHOD
        response = client.get("/api/protocol/benchmarks", params={"scheme_id": "teaching-dh-kem", "version": "7"})
        assert response.status_code == 200
        assert response.json()["report"] is None
        assert response.json()["method"] == METHOD
        read.assert_called_once_with(adapter)
        assert client.get("/api/protocol/benchmarks", params={"scheme_id": "../../anything"}).status_code == 404
        assert client.get("/api/protocol/benchmarks", params={"scheme_id": "teaching-dh-kem", "version": "missing"}).status_code == 404
        assert read.call_count == 1


def test_reference_evidence_requires_active_beta_membership_for_browser_and_agent(private_app):
    paths = ("/api/protocol/reference-method", "/api/protocol/benchmarks?scheme_id=teaching-dh-kem&version=7")
    visitor = TestClient(private_app)
    for path in paths:
        assert visitor.get(path).status_code == 401
    member = private_app.test_login("participant")
    connection = connect(member)
    token = member.client.post("/api/agent/connect/poll", json={"device_code": connection["device_code"]}).json()["access_token"]
    for path in paths:
        assert member.client.get(path).status_code == 200
        assert visitor.get(path, headers={"Authorization": "Bearer " + token}).status_code == 200
        route = path.split("?", 1)[0]
        assert machine_auth.required_scope("GET", route) == "challenges:read"
        assert machine_auth.required_scope("POST", route) is None


def test_method_separates_security_correctness_and_latency():
    assert METHOD["baseline"]["public_key_bytes"] == 800
    assert METHOD["baseline"]["ciphertext_bytes"] == 768
    assert "not a cryptographic failure" in METHOD["limits"]
    assert "does not establish IND-CCA" in METHOD["correctness"]
    assert "no universal NIST or TLS millisecond" in METHOD["interpretation"]
