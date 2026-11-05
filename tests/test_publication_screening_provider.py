import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from crucible.publication_screening_policy import canonical_json, prepare_input
from crucible.publication_screening_provider import (
    ENDPOINT, MAX_ERROR_BYTES, MAX_RESPONSE_BYTES, OpenAIResponsesProvider, ScreeningProviderError,
    parse_response, response_request,
)

PASS = {'outcome': 'no_concern_found', 'concerns': [], 'limitations': []}


def completed():
    return {'id': 'resp_synthetic', 'model': 'synthetic-model-snapshot', 'status': 'completed',
        'error': None, 'incomplete_details': None, 'output': [
            {'type': 'reasoning', 'summary': []},
            {'type': 'message', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': json.dumps(PASS)}]}],
        'usage': {'input_tokens': 120, 'output_tokens': 50, 'total_tokens': 170,
                  'input_tokens_details': {'cached_tokens': 20},
                  'output_tokens_details': {'reasoning_tokens': 10}}}


def provider(handler, **kwargs):
    return OpenAIResponsesProvider(model='synthetic-model', api_key='synthetic-no-real-key',
        transport=httpx.MockTransport(handler), **kwargs)


def test_mocked_request_is_stateless_tool_free_full_input_strict_schema():
    prepared = prepare_input({'body': 'synthetic note', 'extension': {'x': 'complete content'}})
    calls = []
    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert str(request.url) == ENDPOINT and request.method == 'POST'
        assert payload['store'] is False and payload['stream'] is False
        assert payload['tools'] == [] and payload['tool_choice'] == 'none'
        assert payload['input'][0]['content'][0]['text'] == prepared.input_json
        assert payload['text']['format']['strict'] is True
        assert payload['text']['format']['schema']['additionalProperties'] is False
        assert payload['max_output_tokens'] == 1500
        assert set(payload) == {'model', 'store', 'stream', 'instructions', 'input', 'tools',
                                'tool_choice', 'max_output_tokens', 'text'}
        return httpx.Response(200, json=completed())
    result = provider(handler).screen(prepared)
    assert len(calls) == 1 and result.verdict == PASS
    assert result.usage == {'input_tokens': 120, 'output_tokens': 50,
                            'cached_input_tokens': 20, 'reasoning_tokens': 10}
    assert 'reasoning' not in result.__dict__


@pytest.mark.parametrize('status,retryable,code', [
    (301, False, 'provider_http_error'), (400, False, 'provider_configuration_error'),
    (401, False, 'provider_authentication_failed'), (403, False, 'provider_access_denied'),
    (408, True, 'provider_http_error'), (429, True, 'provider_rate_limited'),
    (500, True, 'provider_http_error')])
def test_no_retry_redirect_or_provider_error_body_leak(status, retryable, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='secret echoed provider input',
                              headers={'location': 'https://attacker.invalid/'})
    with pytest.raises(ScreeningProviderError) as error:
        provider(handler).screen(prepare_input({'body': 'x'}))
    assert len(calls) == 1 and error.value.retryable == retryable
    assert str(error.value) == code


@pytest.mark.parametrize('code', ['credit_balance_exhausted', 'organization_spend_limit_exceeded',
    'project_spend_limit_exceeded', 'organization_usage_limit_exceeded',
    'insufficient_quota', 'billing_hard_limit_reached'])
def test_billing_429_is_permanent_without_retaining_provider_message(code):
    body = {'error': {'code': code, 'type': 'insufficient_quota',
                      'message': 'SYNTHETIC_SECRET_MUST_NOT_BE_RETAINED'}}
    with pytest.raises(ScreeningProviderError) as error:
        provider(lambda _: httpx.Response(429, json=body)).screen(prepare_input({'body': 'x'}))
    assert error.value.code == 'provider_quota_exhausted'
    assert error.value.retryable is False
    assert 'SYNTHETIC_SECRET' not in repr(error.value.__dict__)


@pytest.mark.parametrize('body', [
    {'error': {'code': 'slow_down', 'type': 'rate_limit_error'}},
    {'error': {'code': 'rate_limit_exceeded'}}, {'error': {'code': ['not-a-code']}},
    {'error': {'message': 'insufficient_quota is only untrusted free text'}},
])
def test_only_recognized_error_code_changes_throttling_to_permanent_failure(body):
    with pytest.raises(ScreeningProviderError) as error:
        provider(lambda _: httpx.Response(429, json=body)).screen(prepare_input({'body': 'x'}))
    assert error.value.code == 'provider_rate_limited' and error.value.retryable


def test_error_body_is_bounded_and_closed_without_retaining_raw_values():
    events = []
    class OversizedError(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'X' * (MAX_ERROR_BYTES + 1)
            pytest.fail('The provider error body was read beyond its bound.')
        async def aclose(self):
            events.append('closed')
    async def handler(request):
        return httpx.Response(429, headers={'content-type': 'application/json'}, stream=OversizedError())
    with pytest.raises(ScreeningProviderError) as error:
        provider(handler).screen(prepare_input({'body': 'x'}))
    assert error.value.code == 'provider_rate_limited' and error.value.retryable
    assert events == ['closed']


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(status='incomplete'),
    lambda v: v.update(incomplete_details={'reason': 'max_output_tokens'}),
    lambda v: v.update(error={'message': 'sensitive'}),
    lambda v: v.update(output=[]),
    lambda v: v['output'].append(v['output'][1]),
    lambda v: v['output'].append({'type': 'function_call'}),
    lambda v: v['output'][1].update(content=[{'type': 'refusal', 'refusal': 'No'}]),
    lambda v: v['output'][1].update(content=[{'type': 'output_text', 'text': '{"outcome":"reject"}'}]),
    lambda v: v.update(usage=None),
    lambda v: v['usage'].update(input_tokens=True),
    lambda v: v['usage'].update(total_tokens=0),
    lambda v: v['usage'].update(output_tokens_details={'reasoning_tokens': 999}),
    lambda v: v['usage'].update(input_tokens_details={'cached_tokens': 999}),
    lambda v: v.update(model=''),
])
def test_incomplete_refusal_tool_output_ambiguous_or_invalid_usage_never_pass(mutation):
    raw = completed(); mutation(raw)
    with pytest.raises(ScreeningProviderError):
        parse_response(json.dumps(raw), prepare_input({'body': 'x'}))


def test_limits_timeout_and_content_type_are_safe_failures():
    prepared = prepare_input({'body': 'x'})
    def timeout(request):
        raise httpx.ReadTimeout('do not expose this text', request=request)
    with pytest.raises(ScreeningProviderError, match='provider_timeout') as error:
        provider(timeout).screen(prepared)
    assert error.value.retryable
    for response, code in [
        (httpx.Response(200, text='server html'), 'unexpected_response_type'),
        (httpx.Response(200, content=b'X' * (MAX_RESPONSE_BYTES + 1),
                        headers={'content-type': 'application/json'}), 'response_size_limit')]:
        with pytest.raises(ScreeningProviderError, match=code):
            provider(lambda _: response).screen(prepared)


def test_missing_key_is_explicit_and_no_env_access_in_request_builder(monkeypatch):
    monkeypatch.delenv('SYNTHETIC_SCREENING_KEY', raising=False)
    with pytest.raises(ScreeningProviderError, match='missing_or_invalid_api_key'):
        OpenAIResponsesProvider.from_env(model='synthetic', key_env='SYNTHETIC_SCREENING_KEY')
    prepared = prepare_input({'body': 'x'})
    assert response_request(prepared, model='synthetic')['store'] is False
    with pytest.raises(ScreeningProviderError, match='input_policy_mismatch'):
        response_request(replace(prepared, input_json='changed'), model='synthetic')


@pytest.mark.parametrize('effort', ['none', 'low'])
def test_reasoning_configuration_is_explicit_and_full_request_fits_input_bound(effort):
    prepared = prepare_input({'body': ('"\\\n世界' * 5000)})
    request = response_request(prepared, model='synthetic', reasoning_effort=effort)
    assert request['reasoning'] == {'effort': effort}
    assert len(canonical_json(request).encode()) < prepared.input_budget_units
    assert provider(lambda _: httpx.Response(200, json=completed()), reasoning_effort=effort).reasoning_effort == effort


def test_absolute_deadline_cancels_prebody_wait_without_parsing_late_response(monkeypatch):
    from crucible import publication_screening_provider as module
    events = []
    async def delayed_headers(request):
        events.append('started')
        try:
            await asyncio.Event().wait()
        finally:
            events.append('cancelled')
        return httpx.Response(200, json=completed())  # Unreachable after cancellation.
    def forbidden_parse(*args):
        pytest.fail('A timed-out provider response must never be parsed.')
    monkeypatch.setattr(module, 'parse_response', forbidden_parse)
    with pytest.raises(ScreeningProviderError, match='provider_timeout') as error:
        provider(delayed_headers, timeout_seconds=1).screen(prepare_input({'body': 'x'}))
    assert error.value.retryable
    assert events == ['started', 'cancelled']


@pytest.mark.parametrize('status', [200, 429])
def test_absolute_deadline_cancels_stalled_body_and_closes_stream(monkeypatch, status):
    from crucible import publication_screening_provider as module
    events = []
    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{'
            try:
                await asyncio.Event().wait()
            finally:
                events.append('cancelled')
        async def aclose(self):
            events.append('closed')
    async def handler(request):
        return httpx.Response(status, headers={'content-type': 'application/json'}, stream=StalledBody())
    monkeypatch.setattr(module, 'parse_response', lambda *args: pytest.fail('Incomplete body was parsed.'))
    with pytest.raises(ScreeningProviderError, match='provider_timeout'):
        provider(handler, timeout_seconds=1).screen(prepare_input({'body': 'x'}))
    assert events == ['cancelled', 'closed']


def test_sync_worker_boundary_rejects_running_event_loop_before_any_transport():
    calls = []
    candidate = provider(lambda request: calls.append(request))
    async def invoked_from_event_loop():
        with pytest.raises(ScreeningProviderError, match='provider_context_error'):
            candidate.screen(prepare_input({'body': 'x'}))
    asyncio.run(invoked_from_event_loop())
    assert calls == []
