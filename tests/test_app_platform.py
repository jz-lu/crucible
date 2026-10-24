"""Provider boundary tests: synthetic ASGI requests, no service/data/network."""
import asyncio
import importlib.util
from pathlib import Path

import pytest
from starlette.middleware.trustedhost import TrustedHostMiddleware

source = Path(__file__).resolve().parents[1] / "crucible" / "app_platform.py"
spec = importlib.util.spec_from_file_location("candidate_app_platform", source)
hosting = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosting)


def request(headers=(), *, path="/api/session", method="GET", inner=None):
    seen, messages = [], []

    async def application(scope, receive, send):
        seen.append(scope)
        if inner:
            return await inner(scope, receive, send)
        await hosting._response(send, 200, b"{}")

    app = hosting.AppPlatformIngress(
        TrustedHostMiddleware(application, allowed_hosts=["crucible.fail"]), "https://crucible.fail")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
             "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": list(headers), "client": ("10.0.0.1", 3333),
             "server": ("127.0.0.1", 8080)}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return messages[0]["status"], seen, messages, scope


@pytest.mark.parametrize("value,expected", [
    (b"203.0.113.7", "203.0.113.7"),
    (b"2001:0DB8:0:0:0:0:0:1", "2001:db8::1"),
    (b"::ffff:203.0.113.7", "203.0.113.7"),
])
def test_provider_ip_is_canonical_and_arbitrary_forwarded_values_are_ignored(value, expected):
    headers = [(b"host", b"crucible.fail"), (b"Do-Connecting-IP", value),
               (b"forwarded", b"for=198.51.100.99;proto=http;host=evil.invalid"),
               (b"x-forwarded-for", b"198.51.100.99"), (b"X-Forwarded-Proto", b"http"),
               (b"x-forwarded-host", b"evil.invalid"), (b"x-real-ip", b"198.51.100.99")]
    status, seen, _, original = request(headers)
    assert status == 200
    assert seen[0]["client"] == (expected, 0)
    assert seen[0]["scheme"] == "https"
    assert seen[0]["headers"] == [(b"host", b"crucible.fail")]
    assert original["client"] == ("10.0.0.1", 3333)
    assert original["headers"] == headers


@pytest.mark.parametrize("values", [[], [b""], [b" 203.0.113.7"], [b"203.0.113.7 "],
    [b"203.0.113.7,198.51.100.1"], [b"203.0.113.7:123"], [b"fe80::1%eth0"],
    [b"not-an-ip"], [b"\xff"], [b"1" * 46], [b"203.0.113.7", b"203.0.113.7"]])
def test_missing_ambiguous_or_malformed_provider_header_fails_before_application(values):
    status, seen, messages, _ = request([(b"host", b"crucible.fail")]
        + [(b"do-connecting-ip", value) for value in values])
    assert status == 400 and not seen
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert messages[1]["body"] == b'{"detail":"Invalid ingress request."}'


@pytest.mark.parametrize("host", [b"unreviewed.ondigitalocean.app", b"evil.invalid"])
def test_ordinary_host_checks_remain_in_place(host):
    status, seen, _, _ = request([(b"host", host), (b"do-connecting-ip", b"203.0.113.7")])
    assert status == 400 and not seen


def test_readiness_uses_real_application_result_despite_probe_host():
    async def unavailable(scope, receive, send):
        await hosting._response(send, 503, b'{"status":"unavailable"}')
    status, seen, _, _ = request([(b"host", b"10.0.0.1:8080")], path="/health", inner=unavailable)
    assert status == 503
    assert seen[0]["headers"] == [(b"host", b"crucible.fail")]


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_liveness_never_calls_database_application(method):
    status, seen, messages, _ = request(path="/health/live", method=method)
    assert status == 200 and not seen
    assert messages[1]["body"] == (b"" if method == "HEAD" else b'{"status":"ok"}')


@pytest.mark.parametrize("path,method", [("/health/", "GET"), ("/health/private", "GET"),
    ("/health", "POST"), ("/health/live", "POST"), ("/api/notebook/notes", "GET")])
def test_probe_exception_does_not_expand_to_other_routes_or_mutations(path, method):
    status, seen, _, _ = request(path=path, method=method)
    assert status == 400 and not seen


def test_distinct_participants_have_distinct_downstream_rate_limit_keys():
    peers = []
    for address in (b"203.0.113.7", b"203.0.113.8"):
        status, seen, _, _ = request([(b"host", b"crucible.fail"), (b"do-connecting-ip", address)])
        assert status == 200
        peers.append(seen[0]["client"][0])
    assert peers == ["203.0.113.7", "203.0.113.8"]


@pytest.mark.parametrize("status_code", [200, 503])
def test_head_readiness_runs_existing_fastapi_get_check_and_preserves_its_result(status_code):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    inner = FastAPI()
    calls = []

    @inner.get("/health")
    def readiness():
        calls.append("database-check")
        return JSONResponse({"status": "ok" if status_code == 200 else "unavailable"},
                            status_code=status_code)

    status, seen, messages, original = request(path="/health", method="HEAD", inner=inner)
    assert status == status_code and calls == ["database-check"]
    assert seen[0]["method"] == "GET"
    assert original["method"] == "HEAD"
    assert all(message.get("body", b"") == b"" for message in messages)
    assert int(dict(messages[0]["headers"])[b"content-length"]) > 0


@pytest.mark.parametrize("environment,access_mode", [
    (None, None), ("development", "beta"), ("production", None),
    ("production", "open"), ("Production", "beta"),
])
def test_factory_rejects_nonexplicit_beta_before_loading_configuration_or_database(
        environment, access_mode, monkeypatch):
    import builtins

    for key, value in (("CRUCIBLE_ENV", environment), ("CRUCIBLE_ACCESS_MODE", access_mode)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    importer = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name in {"config", "app", "crucible.config", "crucible.app"}:
            pytest.fail("Hosting factory loaded configuration/application before its environment gate")
        return importer(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(RuntimeError, match="explicitly configured production beta"):
        hosting.create_app()
