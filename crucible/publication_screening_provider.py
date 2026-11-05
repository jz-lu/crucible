"""Opt-in, tool-free Responses adapter. Importing this module makes no request.

No retries: the durable queue owns attempts/budget. Never log exception bodies,
request text, credentials or provider reasoning. Tests use a mocked transport.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import os

import httpx

from .publication_screening_policy import (
    INPUT_FORMAT_VERSION, OUTPUT_SCHEMA, POLICY_PROMPT, POLICY_VERSION,
    PROMPT_SHA256, ScreeningInput, ScreeningValidationError, strict_json,
    validate_verdict,
)

ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ERROR_BYTES = 16 * 1024


class ScreeningProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


@dataclass(frozen=True)
class ProviderResult:
    verdict: dict = field(repr=False)
    model: str
    response_id: str
    usage: dict


async def _http_failure(response):
    """Fixed operational codes only; never retain a provider's error message.

    A quota rejection can also be HTTP 429. Only a bounded, recognized error
    code distinguishes it from transient throttling. Malformed/unknown 429
    bodies retain bounded retry behavior; no provider instruction is executed.
    """
    status = response.status_code
    if status == 401:
        return ScreeningProviderError('provider_authentication_failed')
    if status == 403:
        return ScreeningProviderError('provider_access_denied')
    if status in (400, 404, 422):
        return ScreeningProviderError('provider_configuration_error')
    if status == 429:
        if (response.headers.get('content-encoding', 'identity') == 'identity'
                and response.headers.get('content-type', '').split(';', 1)[0].strip() == 'application/json'):
            parts, size = [], 0
            async for part in response.aiter_bytes():
                size += len(part)
                if size > MAX_ERROR_BYTES:
                    break
                parts.append(part)
            else:
                try:
                    payload = strict_json(b''.join(parts), max_bytes=MAX_ERROR_BYTES)
                    error = payload.get('error') if type(payload) is dict else None
                    if type(error) is dict and error.get('code') in (
                            'credit_balance_exhausted', 'organization_spend_limit_exceeded',
                            'project_spend_limit_exceeded', 'organization_usage_limit_exceeded',
                            'insufficient_quota', 'billing_hard_limit_reached'):
                        return ScreeningProviderError('provider_quota_exhausted')
                except ScreeningValidationError:
                    pass
        return ScreeningProviderError('provider_rate_limited', retryable=True)
    return ScreeningProviderError('provider_http_error', retryable=status == 408 or status >= 500)


def response_request(prepared: ScreeningInput, *, model: str,
                     max_output_tokens: int = 1500, reasoning_effort: str | None = None) -> dict:
    """Also used by the offline blind-request exporter; never reads a key."""
    if (type(model) is not str or not model or len(model) > 120
            or any(ord(c) < 33 or ord(c) > 126 for c in model)):
        raise ScreeningProviderError("invalid_model")
    if type(max_output_tokens) is not int or not 256 <= max_output_tokens <= 8192:
        raise ScreeningProviderError("invalid_output_budget")
    if reasoning_effort not in (None, "none", "low"):
        raise ScreeningProviderError("invalid_reasoning_effort")
    if (prepared.policy_version != POLICY_VERSION
            or prepared.prompt_sha256 != PROMPT_SHA256
            or prepared.input_format_version != INPUT_FORMAT_VERSION
            or hashlib.sha256(prepared.input_json.encode()).hexdigest() != prepared.input_sha256):
        raise ScreeningProviderError("input_policy_mismatch")
    request = {
        "model": model, "store": False, "stream": False,
        "instructions": POLICY_PROMPT,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": prepared.input_json}]}],
        "tools": [], "tool_choice": "none", "max_output_tokens": max_output_tokens,
        "text": {"format": {"type": "json_schema", "name": "crucible_publication_screen",
                              "strict": True, "schema": OUTPUT_SCHEMA}},
    }
    if reasoning_effort is not None:
        request["reasoning"] = {"effort": reasoning_effort}
    return request


def parse_response(raw: str | bytes, prepared: ScreeningInput) -> ProviderResult:
    """Accept a completed text verdict only; refusal/incomplete is human fallback."""
    try:
        result = strict_json(raw, max_bytes=MAX_RESPONSE_BYTES)
        if type(result) is not dict or result.get("status") != "completed":
            raise ScreeningProviderError("response_not_completed")
        if result.get("error") is not None or result.get("incomplete_details") is not None:
            raise ScreeningProviderError("response_not_completed")
        output = result.get("output")
        if type(output) is not list:
            raise ScreeningProviderError("invalid_provider_output")
        messages = []
        for item in output:
            if type(item) is not dict:
                raise ScreeningProviderError("invalid_provider_output")
            if item.get("type") == "reasoning":
                continue  # No reasoning body is returned, persisted or inspected.
            if (item.get("type") != "message" or item.get("role") != "assistant"
                    or item.get("status") != "completed"):
                raise ScreeningProviderError("unexpected_provider_output")
            content = item.get("content")
            if type(content) is not list or len(content) != 1 or type(content[0]) is not dict:
                raise ScreeningProviderError("ambiguous_provider_output")
            part = content[0]
            if part.get("type") == "refusal":
                raise ScreeningProviderError("provider_refusal")
            if part.get("type") != "output_text" or type(part.get("text")) is not str:
                raise ScreeningProviderError("unexpected_provider_output")
            messages.append(part["text"])
        if len(messages) != 1:
            raise ScreeningProviderError("ambiguous_provider_output")
        verdict = validate_verdict(messages[0], prepared)
        model, response_id = result.get("model"), result.get("id")
        if any(type(value) is not str or not value or len(value) > 200
               or any(ord(c) < 33 or ord(c) > 126 for c in value)
               for value in (model, response_id)):
            raise ScreeningProviderError("invalid_provider_identity")
        usage = result.get("usage")
        if type(usage) is not dict:
            raise ScreeningProviderError("missing_provider_usage")

        def count(container, key, *, default=None):
            value = container.get(key, default)
            if type(value) is not int or not 0 <= value <= 10_000_000:
                raise ScreeningProviderError("invalid_provider_usage")
            return value

        inputs, outputs = count(usage, "input_tokens"), count(usage, "output_tokens")
        input_details = usage.get("input_tokens_details", {})
        output_details = usage.get("output_tokens_details", {})
        if type(input_details) is not dict or type(output_details) is not dict:
            raise ScreeningProviderError("invalid_provider_usage")
        cached = count(input_details, "cached_tokens", default=0)
        reasoning = count(output_details, "reasoning_tokens", default=0)
        if (cached > inputs or reasoning > outputs
                or ("total_tokens" in usage and count(usage, "total_tokens") != inputs + outputs)):
            raise ScreeningProviderError("invalid_provider_usage")
        return ProviderResult(verdict, model, response_id, {
            "input_tokens": inputs, "output_tokens": outputs,
            "cached_input_tokens": cached, "reasoning_tokens": reasoning})
    except ScreeningValidationError:
        raise ScreeningProviderError("invalid_provider_verdict") from None


class OpenAIResponsesProvider:
    def __init__(self, *, model: str, api_key: str, timeout_seconds: float = 45,
                 max_output_tokens: int = 1500, reasoning_effort: str | None = None, transport=None):
        if type(api_key) is not str or not api_key.strip() or any(c.isspace() for c in api_key):
            raise ScreeningProviderError("missing_or_invalid_api_key")
        if type(timeout_seconds) not in (float, int) or not 1 <= timeout_seconds <= 120:
            raise ScreeningProviderError("invalid_timeout")
        self.model, self.max_output_tokens = model, max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self._api_key, self._transport = api_key, transport

    @classmethod
    def from_env(cls, *, model: str, key_env: str = "OPENAI_API_KEY", **kwargs):
        """Explicit invocation only. Never search files or borrow another key."""
        return cls(model=model, api_key=os.environ.get(key_env, ""), **kwargs)

    def screen(self, prepared: ScreeningInput) -> ProviderResult:
        payload = response_request(prepared, model=self.model,
                                   max_output_tokens=self.max_output_tokens,
                                   reasoning_effort=self.reasoning_effort)
        # This is the synchronous CLI-worker boundary, never an ASGI task. Do
        # not spawn a detached timeout thread that could keep sending afterward.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._screen_async(prepared, payload))
        raise ScreeningProviderError("provider_context_error")

    async def _screen_async(self, prepared, payload):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        try:
            # httpx's timeout is per network phase. This cancellation deadline
            # covers connection, upload, headers AND body, including pre-body
            # waits. Process suspension/uncancellable OS work cannot be given a
            # hard wall-clock guarantee; the separate DB lease still rejects
            # stale results and conservatively accounts for ambiguous usage.
            async with asyncio.timeout_at(deadline):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False,
                        follow_redirects=False, timeout=self.timeout_seconds) as client:
                    async with client.stream("POST", ENDPOINT, json=payload, headers={
                        "Authorization": "Bearer " + self._api_key,
                        "Accept": "application/json", "Accept-Encoding": "identity",
                    }) as response:
                        if loop.time() >= deadline:
                            raise TimeoutError
                        if response.status_code != 200:
                            raise await _http_failure(response)
                        if response.headers.get("content-encoding", "identity") != "identity":
                            raise ScreeningProviderError("unexpected_response_encoding")
                        if response.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                            raise ScreeningProviderError("unexpected_response_type")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ScreeningProviderError("response_size_limit")
                            if loop.time() >= deadline:
                                raise TimeoutError
                            chunks.append(chunk)
                        if loop.time() >= deadline:
                            raise TimeoutError
                        result = parse_response(b"".join(chunks), prepared)
                        if loop.time() >= deadline:
                            raise TimeoutError
                        return result
        except (TimeoutError, httpx.TimeoutException):
            raise ScreeningProviderError("provider_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ScreeningProviderError("provider_transport_error", retryable=True) from None
