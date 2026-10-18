"""Compact catalogue queries retain the full catalogue's beta/bearer boundary."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from crucible import beta_access, machine_auth
from test_beta_boundary import connect, private_app


def test_progress_query_requires_beta_access_before_parameter_validation(private_app):
    with TestClient(private_app) as client:
        for query in ("progress_only=true", "progress_only=false", "progress_only=invalid"):
            for method in ("GET", "HEAD"):
                response = client.request(method, "/api/schemes?" + query)
                assert response.status_code == 401
                assert response.headers["cache-control"] == "no-store"
                assert "catalogue_revision" not in response.text


def test_progress_query_preserves_agent_scope_and_membership_revocation(private_app):
    member = private_app.test_login("participant")
    connection = connect(member)
    token = member.client.post("/api/agent/connect/poll", json={
        "device_code": connection["device_code"]}).json()["access_token"]
    header = {"Authorization": "Bearer " + token}
    path = "/api/schemes?progress_only=true"
    try:
        for headers in ({}, header):
            response = member.client.get(path, headers=headers)
            assert response.status_code == 200 and response.json()["projection"] == "progress"
            assert response.headers["cache-control"] == "no-store"
        with private_app.state.db.transaction(write=True) as session:
            record = session.scalar(select(machine_auth.MachineToken))
            record.scopes = [scope for scope in record.scopes if scope != "challenges:read"]
        assert member.client.get(path, headers=header).status_code == 403
        assert member.client.get(path).status_code == 200
        with private_app.state.db.transaction(write=True) as session:
            beta_access.set_membership(session, private_app.test_member, "revoked", private_app.test_now[0])
        assert member.client.get(path).status_code == 401
        assert member.client.get(path, headers=header).status_code == 401
    finally:
        member.client.close()
