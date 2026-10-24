"""OpenAI-compat adapter: fully offline via httpx MockTransport.

Covers: success + usage, json_schema request shape + validation, fallback,
status classification (401/429/400/500), bounded retries with attempts count,
timeouts, malformed bodies, SSE fragmentation/multi-event/[DONE]/disconnect,
and cancellation mid-flight. No real network, no keys.
"""

import asyncio
import json

import httpx
import pytest

from app.core.config import Settings
from app.llm.adapters.openai_compat import OpenAICompatProvider, SseParser
from app.llm.base import create_provider
from app.llm.types import (
    BadResponseError,
    LlmConfigurationError,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    StructuredOutput,
)

SECRET = "sk-TEST-KEY-NEVER-REAL"  # also asserted absent everywhere (see test_llm_secrets)


def _req(**over) -> LlmRequest:
    base: dict = {"messages": [LlmMessage(role="user", content="hi")], "model": "m"}
    base.update(over)
    return LlmRequest(**base)


def _ok_payload(text: str = "hello", **over) -> dict:
    body: dict = {
        "id": "chatcmpl-1",
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    body.update(over)
    return body


def _provider(handler, **over) -> OpenAICompatProvider:
    return OpenAICompatProvider(api_key=SECRET, transport=httpx.MockTransport(handler), **over)


# ---------------------------------------------------------------- success ---


async def test_complete_success_and_usage() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler)
    res: LlmResponse = await provider.complete(_req())
    assert res.text == "hello"
    assert (res.usage.prompt_tokens, res.usage.total_tokens) == (3, 5)
    assert res.attempts == 1 and res.data is None
    assert seen[0]["stream"] is False


async def test_json_schema_mode_and_validation() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_ok_payload('{"name": "a", "age": 3}'))

    schema = {
        "type": "object",
        "required": ["name", "age"],
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    }
    provider = _provider(handler)
    res = await provider.complete(
        _req(structured=StructuredOutput(mode="json_schema", schema=schema, strict=True))
    )
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["response_format"]["json_schema"]["schema"] == schema
    assert res.data == {"name": "a", "age": 3}


# ------------------------------------------------- output cap (Stage 18 S1) ---


@pytest.mark.parametrize("param", ["max_tokens", "max_completion_tokens"])
async def test_output_cap_uses_the_configured_wire_field(param: str) -> None:
    """Classic and reasoning-model parameter names are both supported.

    Only the configured spelling is sent: strict servers can reject the
    unknown alternative, and two limits would make the wire cap ambiguous.
    """
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler, max_tokens_param=param)
    await provider.complete(_req(max_tokens=2048))

    assert seen[0][param] == 2048
    other = "max_completion_tokens" if param == "max_tokens" else "max_tokens"
    assert other not in seen[0]


@pytest.mark.parametrize("param", ["max_tokens", "max_completion_tokens"])
async def test_output_cap_is_omitted_when_absent(param: str) -> None:
    """An explicitly uncapped request must NOT invent a limit."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler, max_tokens_param=param)
    await provider.complete(_req(max_tokens=None))

    assert "max_tokens" not in seen[0]
    assert "max_completion_tokens" not in seen[0]


def test_unknown_output_cap_field_is_rejected() -> None:
    with pytest.raises(LlmConfigurationError, match="Unsupported max_tokens_param"):
        OpenAICompatProvider(api_key=SECRET, max_tokens_param="max_output_tokens")  # type: ignore[arg-type]


def test_factory_forwards_the_configured_output_cap_field() -> None:
    provider = create_provider(
        Settings(
            environment="test",
            llm_provider="openai_compat",
            llm_api_key=SECRET,
            llm_max_tokens_param="max_completion_tokens",
        )
    )
    assert isinstance(provider, OpenAICompatProvider)
    body = provider._body(_req(max_tokens=77), stream=False)
    assert body["max_completion_tokens"] == 77
    assert "max_tokens" not in body


async def test_usage_details_and_provider_request_id_extracted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ok_payload(
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_tokens_details": {"cached_tokens": 64},
                    "completion_tokens_details": {"reasoning_tokens": 8},
                }
            ),
        )

    res = await _provider(handler).complete(_req())
    assert res.provider_request_id == "chatcmpl-1"
    assert res.usage.reported is True
    assert res.usage.cached_tokens == 64
    assert res.usage.reasoning_tokens == 8


async def test_missing_usage_is_reported_false() -> None:
    body = _ok_payload()
    body.pop("usage")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    res = await _provider(handler).complete(_req())
    assert res.usage.reported is False
    assert res.usage.total_tokens == 0


async def test_transport_error_ambiguity_classification() -> None:
    provider = _provider(lambda request: httpx.Response(200, json=_ok_payload()))
    # Pre-send failures prove no paid operation: retryable, not ambiguous.
    pre = provider._classify_transport_error(httpx.ConnectError("refused"))
    assert pre.ambiguous is False and pre.retryable is True
    pre_to = provider._classify_transport_error(httpx.ConnectTimeout("x"))
    assert pre_to.ambiguous is False and pre_to.retryable is True
    # Post-send failures are possibly billed: ambiguous, never retried.
    post = provider._classify_transport_error(httpx.ReadTimeout("stall"))
    assert post.ambiguous is True and post.retryable is False
    post_read = provider._classify_transport_error(httpx.ReadError("dropped"))
    assert post_read.ambiguous is True and post_read.retryable is False


async def test_idempotency_header_only_for_official_openai() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, json=_ok_payload())

    official = _provider(handler)
    await official.complete(_req(idempotency_key="abc"))
    assert seen[-1].get("idempotency-key") == "abc"

    gateway = OpenAICompatProvider(
        api_key=SECRET,
        base_url="https://gateway.example/v1",
        transport=httpx.MockTransport(handler),
    )
    await gateway.complete(_req(idempotency_key="abc"))
    assert seen[-1].get("idempotency-key") is None


async def test_schema_violation_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload('{"name": "a"}'))

    schema = {"type": "object", "required": ["name", "age"]}
    provider = _provider(handler)
    with pytest.raises(BadResponseError, match="violates schema"):
        await provider.complete(
            _req(structured=StructuredOutput(mode="json_schema", schema=schema))
        )


async def test_json_object_fallback_when_no_schema() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        return httpx.Response(200, json=_ok_payload('{"k": 1}'))

    provider = _provider(handler)
    res = await provider.complete(_req(structured=StructuredOutput(mode="json_object")))
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert res.data == {"k": 1}


async def test_invalid_json_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_payload("not-json{{"))

    provider = _provider(handler)
    with pytest.raises(BadResponseError, match="not valid JSON"):
        await provider.complete(_req(structured=StructuredOutput(mode="json_object")))


async def test_missing_choices_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "m"})

    provider = _provider(handler)
    with pytest.raises(BadResponseError, match="choices"):
        await provider.complete(_req())


async def test_non_json_body_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>nope</html>")

    provider = _provider(handler)
    with pytest.raises(BadResponseError, match="non-JSON"):
        await provider.complete(_req())


# ---------------------------------------------------------------- statuses ---


async def test_auth_error_not_retried_and_distinct_code() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    provider = _provider(handler)
    with pytest.raises(ProviderAuthError) as exc_info:
        await provider.complete(_req())
    assert calls == 1  # never retried
    assert exc_info.value.code == "provider_auth_error"
    assert exc_info.value.code != "unauthorized"


async def test_plain_4xx_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    provider = _provider(handler)
    with pytest.raises(ProviderError):
        await provider.complete(_req())
    assert calls == 1


async def test_429_retries_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler, max_retries=2)
    res = await provider.complete(_req())
    assert res.text == "hello" and res.attempts == 2 and calls == 2


async def test_500_response_is_ambiguous_and_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": {"message": "boom"}})

    provider = _provider(handler, max_retries=2)
    with pytest.raises(ProviderError) as caught:
        await provider.complete(_req())
    assert calls == 1
    assert caught.value.ambiguous is True
    assert caught.value.retryable is False


async def test_pre_send_connect_failure_is_safely_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler, max_retries=1)
    response = await provider.complete(_req())

    assert calls == 2
    assert response.attempts == 2


async def test_timeout_classified() -> None:
    # MockTransport performs no real I/O, so client timeouts never fire
    # there. What is ours to verify: a transport-level timeout maps to
    # ProviderTimeoutError (the timer itself belongs to httpx).
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("simulated stall")

    provider = _provider(handler)
    with pytest.raises(ProviderTimeoutError):
        await provider.complete(_req(timeout_s=30))

    provider2 = _provider(handler, max_retries=1)
    with pytest.raises(ProviderTimeoutError):
        await provider2.complete(_req())
    assert calls == 2  # one submission per provider instance; no post-send retry
    # Transport mapping unit checks (all timeout flavors, no network).
    assert isinstance(
        provider2._classify_transport_error(httpx.ConnectTimeout("x")), ProviderTimeoutError
    )


async def test_backoff_is_bounded_and_jittered() -> None:
    d0 = OpenAICompatProvider._backoff_delay(0)
    d5 = OpenAICompatProvider._backoff_delay(5)
    assert 0.5 <= d0 <= 0.75
    assert d5 <= 8.25
    assert len({OpenAICompatProvider._backoff_delay(0) for _ in range(20)}) > 1


# --------------------------------------------------------------------- SSE ---


class ScriptStream(httpx.AsyncByteStream):
    """Yield scripted raw byte chunks to split SSE lines across packets."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _sse_response(chunks: list[bytes], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, headers={"content-type": "text/event-stream"}, stream=ScriptStream(chunks)
    )


def _event(text: str | None) -> bytes:
    if text is None:
        return b"data: [DONE]\n\n"
    payload = json.dumps({"choices": [{"delta": {"content": text}}]})
    return f"data: {payload}\n\n".encode()


async def _collect(provider: OpenAICompatProvider, request: LlmRequest) -> tuple[str, bool]:
    text = ""
    done = False
    async for chunk in provider.stream(request):
        text += chunk.delta
        done = done or chunk.done
    return text, done


async def test_sse_split_lines_and_multi_event_chunks() -> None:
    raw = _event("Hel") + _event("lo")
    # Split mid-line and pack two events in one packet.
    chunks = [raw[:17], raw[17:40], raw[40:], b": keep-alive\n\n", _event(None)]

    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(chunks)

    text, done = await _collect(_provider(handler), _req())
    assert text == "Hello" and done is True


async def test_sse_done_terminates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([_event("a"), _event(None), _event("b")])

    text, done = await _collect(_provider(handler), _req())
    assert text == "a" and done is True


async def test_sse_truncated_stream_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([b'data: {"choices": [{"delta": {"cont'])

    with pytest.raises(ProviderError, match="incomplete SSE"):
        await _collect(_provider(handler), _req())


async def test_sse_malformed_payload_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response([b"data: {oops\n\n"])

    with pytest.raises(BadResponseError, match="Malformed SSE"):
        await _collect(_provider(handler), _req())


async def test_sse_parser_unit() -> None:

    parser = SseParser()
    assert parser.feed(b'data: {"a":') == []
    assert parser.feed(b"1}\n\ndata: [DO") == ['{"a":1}']
    assert parser.feed(b"NE]\n\n") == []
    assert parser.finished is True
    parser.finish()  # no raise on clean end

    broken = SseParser()
    broken.feed(b"data: partial")
    with pytest.raises(ProviderError, match="incomplete SSE"):
        broken.finish()


# ------------------------------------------------------------ cancellation ---


async def test_complete_cancellation_propagates_unwrapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=_ok_payload())

    provider = _provider(handler)
    task = asyncio.create_task(provider.complete(_req(timeout_s=30)))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_stream_cancellation_closes_and_propagates() -> None:
    served_chunks = 0

    async def gen():
        nonlocal served_chunks
        served_chunks += 1
        yield _event("a")
        await asyncio.sleep(5)
        served_chunks += 1
        yield _event("b")  # never reached

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            async for part in gen():
                yield part

        async def aclose(self) -> None:
            return None

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=SlowStream()
        )

    provider = _provider(handler)

    async def consume() -> list:
        return [chunk async for chunk in provider.stream(_req())]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let the first chunk arrive, then cancel mid-stream
    task.cancel()
    # CancelledError derives from BaseException (not Exception/LlmError):
    # matching it here already proves it was never wrapped as a provider error.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == 1  # cancelled streams are never retried
    assert served_chunks == 1
