"""Agent-sized evidence batches retain independent account and network bounds."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from crucible.app import create_app
from crucible.db import Method, RateBucket
from crucible.method_sources import MethodSource
from crucible.security import mac
from test_api import Actor
from test_calibrated_api import current_system
from test_machine_auth import connected
from test_method_sources import bundle


@pytest.fixture
def limited(current_system):
    _, now, settings = current_system
    app = create_app(replace(settings, rate_limits=True), clock=lambda: now[0])
    yield app, now, settings
    app.state.db.engine.dispose()


def evidence_request(actor, kind):
    if kind == "methods":
        return "/api/methods", {"name": "Recovery method", "pseudocode": "Synthetic rate-limit fixture."}
    return f'/api/methods/{actor.method()["id"]}/source', bundle()


def set_bucket(app, settings, now, subject, count):
    key = mac(settings.secret, "rate-v1", subject.encode())
    bucket = int(now) // 3600
    with app.state.db.transaction(write=True) as session:
        session.merge(RateBucket(key=key, bucket=bucket, count=count, expires_at=(bucket + 2) * 3600))


def test_fifty_distinct_methods_and_sources_fit_one_account_hour(limited):
    app, _, _ = limited
    actor = Actor(app)
    actor.register("fifty_tier_solver")
    for tier in range(1, 51):
        method = actor.method(f"Tier {tier} evidence")
        response = actor.post(f'/api/methods/{method["id"]}/source', bundle(f"# Exact tier {tier} fixture"))
        assert response.status_code == 200, response.text
    with app.state.db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(Method)) == 50
        assert session.scalar(select(func.count()).select_from(MethodSource)) == 50
    actor.client.close()


@pytest.mark.parametrize("kind", ["methods", "method-source"])
def test_account_budget_survives_ip_change_without_blocking_other_accounts(limited, kind):
    app, now, _ = limited
    actor, other = Actor(app), Actor(app)
    actor.register("limited_solver")
    other.register("independent_solver")
    path, body = evidence_request(actor, kind)
    for _ in range(100):
        response = actor.post(path, body)
        assert response.status_code == 200, response.text
    blocked = actor.post(path, body)
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    with TestClient(app, client=("203.0.113.42", 12345)) as moved:
        moved.cookies.update(actor.client.cookies)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": actor.csrf}
        assert moved.post(path, json=body, headers=headers).status_code == 429
    other_path, other_body = evidence_request(other, kind)
    assert other.post(other_path, other_body).status_code == 200
    now[0] += 3600
    assert actor.post(path, body).status_code == 200
    actor.client.close()
    other.client.close()


@pytest.mark.parametrize("kind", ["methods", "method-source"])
def test_coarse_ip_budget_still_bounds_evidence_requests(limited, kind):
    app, now, settings = limited
    actor = Actor(app)
    actor.register("network_limited")
    path, body = evidence_request(actor, kind)
    set_bucket(app, settings, now[0], f"{kind}-ip:testclient:", 999)
    assert actor.post(path, body).status_code == 200
    assert actor.post(path, body).status_code == 429
    actor.client.close()


@pytest.mark.parametrize("kind", ["methods", "method-source"])
def test_browser_and_agent_share_one_account_budget(limited, kind):
    app, now, settings = limited
    browser, agent, _, credential = connected(app)
    path, body = evidence_request(browser, kind)
    account_id = browser.client.get("/api/session").json()["user"]["id"]
    set_bucket(app, settings, now[0], f"{kind}-account:account:{account_id}", 99)
    assert agent.post(path, json=body).status_code == 200
    assert browser.post(path, body).status_code == 429
    agent.close()
    browser.client.close()
