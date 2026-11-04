"""Rollout observations are bounded, private, retryable and never mutate hosting."""
from copy import deepcopy
import json

import pytest

from deploy import verify_deployment as rollout

SHA = 'a' * 40


def healthy(url, timeout):
    assert 0 < timeout <= 5
    if '/health?' in url:
        return 200, {'status': 'ok', 'source_revision': SHA}
    assert url.endswith('/api/notebook/notes?summary=true')
    return 401, {'code': 'authentication_required'}


class Clock:
    def __init__(self): self.now = 0
    def __call__(self): return self.now
    def sleep(self, duration):
        assert 0 < duration <= 10
        self.now += duration


def test_requires_three_consecutive_passes_on_both_domains():
    clock, records, requests = Clock(), [], []
    def request(url, timeout):
        requests.append(url)
        if clock.now == 10 and url.startswith(rollout.TARGETS[1]) and '/health?' in url:
            return 200, {'status': 'ok', 'source_revision': 'b' * 40}
        return healthy(url, timeout)
    result = rollout.verify(SHA, request=request, clock=clock, sleep=clock.sleep,
                            save=lambda value: records.append(deepcopy(value)))
    assert result['status'] == 'passed' and result['rounds'] == 5 and clock.now == 40
    assert [row['consecutive_passes'] for row in records] == [0, 1, 0, 1, 2, 3]
    assert all(url.startswith(rollout.TARGETS) for url in requests)
    assert not any(url.startswith(rollout.TARGETS[1]) and '/api/' in url for url in requests)
    assert result['remote_writes_performed'] is False
    assert result['image_digest_verified'] is False


def test_transient_outage_recovers_without_repromotion():
    clock = Clock()
    def request(url, timeout):
        if clock.now < 20: raise OSError('synthetic private error must not be retained')
        return healthy(url, timeout)
    result = rollout.verify(SHA, request=request, clock=clock, sleep=clock.sleep)
    assert result['status'] == 'passed' and clock.now == 40
    assert 'synthetic' not in json.dumps(result)


def test_wrong_revision_or_domain_failure_times_out_and_retains_evidence():
    clock = Clock()
    def request(url, timeout):
        if url.startswith(rollout.TARGETS[0]): raise OSError('no network')
        return 200, {'status': 'ok', 'source_revision': 'b' * 40}
    records = []
    result = rollout.verify(SHA, request=request, wait_seconds=25, clock=clock, sleep=clock.sleep,
                            save=lambda value: records.append(deepcopy(value)))
    assert result['status'] == 'needs_review' and clock.now == 25
    assert result['observations'] == {rollout.TARGETS[0]: 'request_failed', rollout.TARGETS[1]: 'revision_pending'}
    assert result['consecutive_passes'] == 0


def test_unexpected_private_success_stops_immediately_without_recording_content():
    clock = Clock()
    def request(url, timeout):
        if '/health?' in url: return healthy(url, timeout)
        return 200, {'notes': ['sensitive-fixture']}
    result = rollout.verify(SHA, request=request, clock=clock, sleep=clock.sleep)
    assert result['status'] == 'needs_review' and result['rounds'] == 1 and clock.now == 0
    assert 'sensitive-fixture' not in json.dumps(result)
    assert result['observations'] == {rollout.TARGETS[0]: 'private_access_failed', rollout.TARGETS[1]: 'passed'}


def test_provider_hostname_requires_readiness_only_under_trusted_host_policy():
    def request(url, timeout):
        assert url.startswith(rollout.TARGETS[1] + '/health?')
        return healthy(url, timeout)
    assert rollout.probe(rollout.TARGETS[1], SHA, request=request) == 'passed'


@pytest.mark.parametrize('status,body,expected', [
    (503, {}, 'health_unavailable'), (200, [], 'health_invalid'),
    (200, {'status': 'ok'}, 'revision_pending'),
])
def test_unhealthy_missing_or_invalid_health_cannot_pass(status, body, expected):
    assert rollout.probe(rollout.TARGETS[0], SHA, request=lambda *args: (status, body)) == expected


@pytest.mark.parametrize('status,body', [(302, {}), (403, {}), (401, {}), (401, {'code': 'wrong'})])
def test_denial_requires_application_authentication_response(status, body):
    def request(url, timeout):
        return healthy(url, timeout) if '/health?' in url else (status, body)
    assert rollout.probe(rollout.TARGETS[0], SHA, request=request) == 'private_denial_unverified'


def test_request_is_anonymous_get_bounded_and_refuses_redirects(monkeypatch):
    observed = {}
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            observed['limit'] = count
            return b'{"status":"ok"}'
    class Opener:
        def open(self, request, timeout):
            observed.update(method=request.get_method(), headers=request.header_items(), timeout=timeout)
            return Response()
    def opener(*handlers):
        assert any(isinstance(handler, rollout.NoRedirect) for handler in handlers)
        assert any(isinstance(handler, rollout.urllib.request.ProxyHandler) and not handler.proxies for handler in handlers)
        return Opener()
    monkeypatch.setattr(rollout.urllib.request, 'build_opener', opener)
    assert rollout.fetch(rollout.TARGETS[0] + '/health', 5) == (200, {'status': 'ok'})
    assert observed['method'] == 'GET' and observed['limit'] == 65537 and observed['timeout'] == 5
    assert all(name.lower() not in ('authorization', 'cookie') for name, value in observed['headers'])
    assert rollout.NoRedirect().redirect_request(None, None, None, None, None, None) is None
    with pytest.raises(ValueError): rollout.fetch('https://crucible.fail.evil.invalid/health', 5)


@pytest.mark.parametrize('revision,wait', [('main', 10), (SHA, -1), (SHA, 901), (SHA, True)])
def test_invalid_config_fails_without_network(revision, wait):
    with pytest.raises(ValueError):
        rollout.verify(revision, wait_seconds=wait, request=lambda *args: pytest.fail('No network'))
