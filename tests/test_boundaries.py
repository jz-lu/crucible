"""API boundaries: concurrency, secret retention, failure holds, and receipt time.

Concurrency tests use independent requests against one file-backed SQLite
database. They do not replace integration tests against production PostgreSQL.
"""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from crucible import app as app_module
from crucible import kem
from crucible.app import RequestBoundary, create_app
from crucible.config import Settings, load_settings
from crucible.db import Challenge, LoginSession, ProfileIncident, Submission, User
from crucible.security import target_mac, token_hash
from scripts.solve_teaching import recover
from test_api import pin_legacy_releases


ORIGIN = "http://testserver"
PASSWORD = "correct-horse-with-private-tail"
COMPUTE = {
    "hardware": "Test fixture CPU",
    "workers": 1,
    "wall_seconds": 0.1,
    "precomputation": "None",
    "peak_memory": "1 MiB",
    "notes": "Synthetic test report, not a measured benchmark.",
}


def headers(csrf):
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf}


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    pin_legacy_releases(monkeypatch)
    now = [3_600_000.0 + 600]
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'boundary.db'}",
        secret=b"B" * 32,
        origins=(ORIGIN,),
        rate_limits=False,
    )
    app = create_app(settings, clock=lambda: now[0], initialize=True)
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = client.get("/api/session").json()["csrf_token"]
        response = client.post("/api/auth/register", headers=headers(csrf),
                               json={"handle": "boundary_user", "password": PASSWORD})
        assert response.status_code == 200, response.text
        csrf = response.json()["csrf_token"]
        yield SimpleNamespace(app=app, client=client, now=now, settings=settings, csrf=csrf, directory=tmp_path)
    app.state.db.engine.dispose()


def start(boundary, profile="dh-20"):
    response = boundary.client.post("/api/challenges", headers=headers(boundary.csrf), json={"profile_id": profile})
    assert response.status_code == 200, response.text
    return response.json()["challenge"]


def method(boundary):
    response = boundary.client.post("/api/methods", headers=headers(boundary.csrf), json={
        "name": "Boundary BSGS fixture",
        "pseudocode": "Solve discrete log x; hash the specified transcript with c^x mod p.",
    })
    assert response.status_code == 200, response.text
    return response.json()["method"]["id"]


def answer(boundary, challenge, method_id=None):
    return {"candidate": recover(challenge["public"]), "method_id": method_id or method(boundary),
            "compute": COMPUTE.copy(), "idempotency_key": str(uuid4())}


def simultaneous(callables):
    barrier = Barrier(len(callables))

    def run(call):
        barrier.wait(timeout=10)
        return call()

    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        futures = [executor.submit(run, call) for call in callables]
        return [future.result(timeout=25) for future in futures]


def test_concurrent_issue_returns_one_durable_instance(boundary):
    def issue():
        return boundary.client.post("/api/challenges", headers=headers(boundary.csrf), json={"profile_id": "dh-20"})

    responses = simultaneous([issue] * 6)
    assert all(r.status_code in {200, 503} for r in responses)
    assert all(r.headers["retry-after"] == "3" for r in responses if r.status_code == 503)
    responses = [issue() if response.status_code == 503 else response for response in responses]
    assert [r.status_code for r in responses] == [200] * 6
    instances = [r.json()["challenge"] for r in responses]
    assert len({c["id"] for c in instances}) == 1
    assert len({json.dumps(c["public"], sort_keys=True) for c in instances}) == 1
    assert len({c["issued_at"] for c in instances}) == 1
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Challenge)) == 1


def test_concurrent_identical_answer_retries_have_one_result(boundary):
    challenge = start(boundary)
    payload = answer(boundary, challenge)
    path = f"/api/challenges/{challenge['id']}/submit"

    def submit():
        return boundary.client.post(path, headers=headers(boundary.csrf), json=payload)

    responses = simultaneous([submit] * 6)
    assert [r.status_code for r in responses] == [200] * 6
    results = [r.json()["submission"] for r in responses]
    assert {r["status"] for r in results} == {"correct"}
    assert len({r["id"] for r in results}) == 1
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1
    # A lost success response remains retrievable after the challenge deadline.
    boundary.now[0] = challenge["ends_at"] + 1
    retry = submit()
    assert retry.status_code == 200
    assert retry.json()["submission"]["id"] == results[0]["id"]


def test_concurrent_distinct_final_answers_cannot_both_be_recorded(boundary):
    challenge = start(boundary)
    original = answer(boundary, challenge)
    payloads = [{**original, "idempotency_key": str(uuid4())} for _ in range(4)]
    path = f"/api/challenges/{challenge['id']}/submit"
    responses = simultaneous([
        lambda payload=payload: boundary.client.post(path, headers=headers(boundary.csrf), json=payload)
        for payload in payloads
    ])
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1


def test_concurrent_reuse_of_retry_id_across_challenges_is_rejected(boundary):
    first, second = start(boundary), start(boundary, "dh-24")
    method_id = method(boundary)
    one, two = answer(boundary, first, method_id), answer(boundary, second, method_id)
    two["idempotency_key"] = one["idempotency_key"]
    responses = simultaneous([
        lambda: boundary.client.post(f"/api/challenges/{first['id']}/submit", headers=headers(boundary.csrf), json=one),
        lambda: boundary.client.post(f"/api/challenges/{second['id']}/submit", headers=headers(boundary.csrf), json=two),
    ])
    assert sorted(r.status_code for r in responses) == [200, 409]
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 1


def test_raw_credentials_keys_and_answers_are_not_persisted_or_returned(boundary):
    challenge = start(boundary)
    payload = answer(boundary, challenge)
    candidate = payload["candidate"]
    token = boundary.client.cookies.get(boundary.settings.cookie_name)
    response = boundary.client.post(f"/api/challenges/{challenge['id']}/submit", headers=headers(boundary.csrf), json=payload)
    assert response.status_code == 200
    responses = [response]
    responses += [boundary.client.get(url) for url in (
        "/api/challenges?profile_id=dh-20",
        f"/api/challenges/{challenge['id']}/download",
        "/api/leaderboard?profile_id=dh-20",
        "/api/me/submissions",
        "/api/session",
    )]
    for response in responses:
        assert response.status_code == 200
        for private in (candidate, PASSWORD, token):
            assert private not in response.text
        assert response.headers.get("cache-control") == "no-store"
        assert "target_mac" not in response.text
        assert "password_hash" not in response.text
    with boundary.app.state.db.transaction() as db:
        row = db.get(Challenge, challenge["id"])
        assert row.target_mac == target_mac(boundary.settings.secret, row.id, bytes.fromhex(candidate))
        assert row.target_mac != candidate
        account = db.scalar(select(User))
        assert account.password_hash.startswith("$argon2id$")
        login = db.scalar(select(LoginSession))
        assert login.token_hash == token_hash(token)
        assert "candidate" not in Submission.__table__.columns
        assert "target" not in Challenge.__table__.columns
    # Includes SQLite files/journals if present, rather than merely ORM columns.
    stored = b"".join(path.read_bytes() for path in boundary.directory.iterdir() if path.is_file())
    for private in (candidate.encode(), bytes.fromhex(candidate), PASSWORD.encode(), token.encode()):
        assert private not in stored


def test_validation_errors_do_not_echo_candidate_or_password(boundary):
    challenge = start(boundary)
    payload = answer(boundary, challenge)
    marker = "PRIVATE-INVALID-CANDIDATE-DO-NOT-ECHO"
    payload["candidate"] = marker
    response = boundary.client.post(f"/api/challenges/{challenge['id']}/submit", headers=headers(boundary.csrf), json=payload)
    assert response.status_code == 422
    assert marker not in response.text
    invalid_password = "SHORTSECRET"
    response = boundary.client.post("/api/auth/login", headers=headers(boundary.csrf),
                                    json={"handle": "boundary_user", "password": invalid_password})
    assert response.status_code == 422
    assert invalid_password not in response.text


def test_correctness_fault_pauses_profile_across_requests_rounds_and_restart(boundary, monkeypatch):
    real_generate = kem.generate
    generated = real_generate("dh-20")
    failed = replace(generated, diagnostics={**generated.diagnostics, "shared_secret_matches": False})
    calls = []

    def bad_generate(profile_id):
        calls.append(profile_id)
        return failed

    monkeypatch.setattr(app_module.registry.adapter_for("teaching-dh-kem"), "generate", bad_generate)
    response = boundary.client.post("/api/challenges", headers=headers(boundary.csrf), json={"profile_id": "dh-20"})
    assert response.status_code == 503
    assert generated.target.hex() not in response.text
    assert calls == ["dh-20"]
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Challenge)) == 0
        incident = db.scalar(select(ProfileIncident))
        assert incident is not None and not incident.resolved
        assert generated.target.hex() not in json.dumps(incident.diagnostics)
    monkeypatch.setattr(app_module.registry.adapter_for("teaching-dh-kem"), "generate", real_generate)
    boundary.now[0] += 3600
    restarted = create_app(boundary.settings, clock=lambda: boundary.now[0], initialize=False)
    with TestClient(restarted, base_url=ORIGIN) as client:
        client.cookies.update(boundary.client.cookies)
        csrf = client.get("/api/session").json()["csrf_token"]
        response = client.post("/api/challenges", headers=headers(csrf), json={"profile_id": "dh-20"})
        assert response.status_code == 503
        # Pausing a faulty profile does not silently hold another profile.
        response = client.post("/api/challenges", headers=headers(csrf), json={"profile_id": "dh-24"})
        assert response.status_code == 200
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(ProfileIncident)) == 1
        assert db.scalar(select(func.count()).select_from(Challenge)) == 1
    restarted.state.db.engine.dispose()


@pytest.mark.parametrize("offset,expected", [(-0.001, 200), (0, 409), (0.001, 409)])
def test_deadline_is_exclusive_at_exact_hour(boundary, offset, expected):
    challenge = start(boundary)
    payload = answer(boundary, challenge)
    boundary.now[0] = challenge["ends_at"] + offset
    response = boundary.client.post(f"/api/challenges/{challenge['id']}/submit", headers=headers(boundary.csrf), json=payload)
    assert response.status_code == expected
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == (1 if expected == 200 else 0)


async def asgi_request(app, *, path, chunks, request_headers, before_receive=None):
    messages = []
    received = 0
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": request_headers,
        "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
    }

    async def receive():
        nonlocal received
        if received >= len(chunks):
            return {"type": "http.disconnect"}
        if before_receive:
            before_receive(received)
        index = received
        received += 1
        return {"type": "http.request", "body": chunks[index], "more_body": received < len(chunks)}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages, received


def test_last_body_byte_must_arrive_before_deadline(boundary):
    challenge = start(boundary)
    payload = json.dumps(answer(boundary, challenge)).encode()
    boundary.now[0] = challenge["ends_at"] - 0.1
    token = boundary.client.cookies.get(boundary.settings.cookie_name)
    request_headers = [(b"host", b"testserver"), (b"origin", ORIGIN.encode()), (b"content-type", b"application/json"),
                       (b"x-csrf-token", boundary.csrf.encode()),
                       (b"cookie", f"{boundary.settings.cookie_name}={token}".encode())]

    def final_byte_arrives(index):
        if index == 1:
            boundary.now[0] = challenge["ends_at"]

    messages, _ = asyncio.run(asgi_request(boundary.app,
        path=f"/api/challenges/{challenge['id']}/submit", chunks=[payload[:-1], payload[-1:]],
        request_headers=request_headers, before_receive=final_byte_arrives))
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 409
    with boundary.app.state.db.transaction() as db:
        assert db.scalar(select(func.count()).select_from(Submission)) == 0


@pytest.mark.parametrize("advertised", [True, False])
def test_oversized_body_is_rejected_before_application_parses(advertised, tmp_path):
    called = []

    async def should_never_parse(scope, receive, send):
        called.append(True)
        raise AssertionError("Oversized body reached application parser")

    settings = Settings(database_url="sqlite://", secret=b"x" * 32, origins=(ORIGIN,))
    # This anonymous, oversized request must not reach authentication or a DB.
    boundary = RequestBoundary(should_never_parse, settings, lambda: 100, db=None)
    request_headers = [(b"origin", ORIGIN.encode()), (b"content-type", b"application/json")]
    if advertised:
        request_headers.append((b"content-length", b"65537"))
    # Omitted Content-Length still must be enforced against actual bytes.
    messages, reads = asyncio.run(asgi_request(boundary, path="/api/auth/register",
        chunks=[b"{" * 32768, b"x" * 32769], request_headers=request_headers))
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 413
    assert not called
    assert reads == (0 if advertised else 2)


@pytest.fixture
def production_env(monkeypatch, tmp_path):
    for name in ("DATABASE_URL", "CRUCIBLE_ENV", "CRUCIBLE_SECRET", "CRUCIBLE_ORIGIN", "CRUCIBLE_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    values = {
        "CRUCIBLE_ENV": "production",
        "CRUCIBLE_SECRET": base64.b64encode(b"P" * 32).decode(),
        "CRUCIBLE_ORIGIN": "https://crucible.example",
        "DATABASE_URL": "postgresql://fixture:fixture@localhost/crucible",
        "CRUCIBLE_DATA_DIR": str(tmp_path / "production-must-not-create-local-key"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.mark.parametrize("name,value", [
    ("CRUCIBLE_SECRET", None),
    ("CRUCIBLE_SECRET", "not!base64"),
    ("CRUCIBLE_SECRET", base64.b64encode(b"too short").decode()),
    ("CRUCIBLE_ORIGIN", None),
    ("CRUCIBLE_ORIGIN", "http://crucible.example"),
    ("CRUCIBLE_ORIGIN", "https://user:password@crucible.example"),
    ("CRUCIBLE_ORIGIN", "https://crucible.example/path"),
    ("DATABASE_URL", "sqlite:///production.db"),
])
def test_production_config_fails_closed(production_env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        load_settings()
    assert not Path(production_env["CRUCIBLE_DATA_DIR"]).exists()


def test_production_config_requires_secure_cookie_and_postgres(production_env):
    settings = load_settings()
    assert settings.production
    assert settings.cookie_name == "__Host-crucible"
    assert settings.origins == ("https://crucible.example",)
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.rate_limits
    assert not Path(production_env["CRUCIBLE_DATA_DIR"]).exists()


@pytest.mark.parametrize("value", ["", "prod", "Production", "production ", "staging"])
def test_unknown_environment_is_rejected_before_local_state_creation(production_env, monkeypatch, value):
    monkeypatch.setenv("CRUCIBLE_ENV", value)
    monkeypatch.delenv("CRUCIBLE_SECRET")
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError, match="CRUCIBLE_ENV must be exactly development or production"):
        load_settings()
    assert not Path(production_env["CRUCIBLE_DATA_DIR"]).exists()


@pytest.mark.parametrize("value", [None, "development"])
def test_unset_and_explicit_development_keep_local_defaults(production_env, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CRUCIBLE_ENV")
    else:
        monkeypatch.setenv("CRUCIBLE_ENV", value)
    for name in ("DATABASE_URL", "CRUCIBLE_SECRET", "CRUCIBLE_ORIGIN"):
        monkeypatch.delenv(name)
    settings = load_settings()
    assert not settings.production
    assert settings.cookie_name == "crucible_session"
    assert settings.origins == ("http://127.0.0.1:8000", "http://localhost:8000")
    assert settings.database_url == f'sqlite:///{Path(production_env["CRUCIBLE_DATA_DIR"]) / "crucible.db"}'
    assert (Path(production_env["CRUCIBLE_DATA_DIR"]) / "server.key").read_bytes() == settings.secret


@pytest.mark.parametrize("origin", [
    "https://crucible.example:wrong", "https://crucible.example:99999",
    "https://crucible.example:", "https://@crucible.example",
    "https://:password@crucible.example", "https://crucible.example/path",
    "https://crucible.example?query", "https://crucible.example#fragment",
    " https://crucible.example", "https://crucible.\texample",
    "https://crucible.example\\other", "https://[not-ipv6]",
])
@pytest.mark.parametrize("environment", ["development", "production"])
def test_invalid_origin_is_rejected_before_local_state_creation(production_env, monkeypatch, origin, environment):
    monkeypatch.setenv("CRUCIBLE_ENV", environment)
    monkeypatch.setenv("CRUCIBLE_ORIGIN", origin)
    if environment == "development":
        monkeypatch.delenv("CRUCIBLE_SECRET")
        monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError, match="CRUCIBLE_ORIGIN must be an origin"):
        load_settings()
    assert not Path(production_env["CRUCIBLE_DATA_DIR"]).exists()


@pytest.mark.parametrize("configured,canonical,environment", [
    ("https://Crucible.EXAMPLE:443/", "https://crucible.example", "production"),
    ("https://Crucible.EXAMPLE:8443/", "https://crucible.example:8443", "production"),
    ("https://[2001:DB8::1]:443/", "https://[2001:db8::1]", "production"),
    ("https://[2001:DB8::1]:8443", "https://[2001:db8::1]:8443", "production"),
    ("http://Crucible.EXAMPLE:80/", "http://crucible.example", "development"),
    ("http://127.0.0.1:8000/", "http://127.0.0.1:8000", "development"),
])
def test_origin_normalization_preserves_valid_network_policy(production_env, monkeypatch, configured, canonical, environment):
    monkeypatch.setenv("CRUCIBLE_ENV", environment)
    monkeypatch.setenv("CRUCIBLE_ORIGIN", configured)
    assert load_settings().origins == (canonical,)
    assert not Path(production_env["CRUCIBLE_DATA_DIR"]).exists()


def test_default_https_port_accepts_browser_same_origin_mutation(production_env, monkeypatch, tmp_path):
    monkeypatch.setenv("CRUCIBLE_ORIGIN", "https://Crucible.EXAMPLE:443/")
    settings = replace(load_settings(), database_url=f"sqlite:///{tmp_path / 'origin-boundary.db'}")
    application = create_app(settings, initialize=True)
    try:
        with TestClient(application, base_url="https://crucible.example") as browser:
            session = browser.get("/api/session")
            assert session.status_code == 200
            response = browser.post("/api/auth/logout", json={}, headers={
                "Origin": "https://crucible.example", "X-CSRF-Token": session.json()["csrf_token"],
            })
            assert response.status_code == 200
    finally:
        application.state.db.engine.dispose()
