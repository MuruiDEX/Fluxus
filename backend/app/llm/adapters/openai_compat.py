"""OpenAI-compatible chat provider over raw httpx (no vendor SDK).

Works against api.openai.com and OpenAI-compatible `/chat/completions`
endpoints that accept Bearer auth (e.g. OpenRouter, Ollama, compatible
gateways). Azure OpenAI's native `api-key`/deployment/`api-version` shape is
not handled here; point this adapter at an OpenAI-compatible gateway instead.

Hard rules enforced here:
- `asyncio.CancelledError` is re-raised unwrapped, never retried; HTTP/SSE
  resources are released in `finally` blocks.
- The API key appears in exactly one place: the Authorization header value.
  It is never logged, never interpolated into messages, and never echoed
  in error details.
- Retries: at most `max_retries` (default 2 → 3 attempts total), exponential
  backoff with jitter, only for 429 and pre-send network failures. A 5xx or
  post-send transport failure is ambiguous/possibly billed and is NOT retried.
  Never for 401/403/other 4xx. Each attempt carries the full timeout.
- Structured output: `json_schema` mode first (when a schema is provided),
  `json_object` fallback + mandatory local parse-and-validate in both cases.
"""

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx

from app.llm.types import (
    BadResponseError,
    LlmChunk,
    LlmConfigurationError,
    LlmError,
    LlmRequest,
    LlmResponse,
    LlmUsage,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitedError,
    StructuredOutput,
    TransientProviderError,
)
from app.llm.validate import validate_against_schema

_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 8.0


class SseParser:
    """Incremental Server-Sent Events parser for OpenAI-style streams.

    Handles: lines split across network chunks, multiple events per chunk,
    `data: [DONE]` termination, and truncated streams (flush at EOF raises
    unless a clean terminator was seen).
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._finished = False

    @property
    def finished(self) -> bool:
        return self._finished

    def feed(self, chunk: bytes | str) -> list[str]:
        """Return complete `data:` payloads extracted from this chunk."""
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        self._buffer += chunk
        payloads: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                continue  # event separator / heartbeat
            if line.startswith(":"):
                continue  # SSE comment / keep-alive
            if line.startswith("data:"):
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    self._finished = True
                    break
                payloads.append(payload)
            # Other SSE fields (event:, id:, retry:) are ignored by design.
        return payloads

    def finish(self) -> None:
        """Call at clean stream end; raises if the stream broke mid-event."""
        if self._buffer.strip() and not self._finished:
            raise ProviderError("Stream ended with an incomplete SSE event.")
        self._buffer = ""


def _extract_delta(payload: dict[str, Any]) -> str:
    try:
        return str(payload["choices"][0]["delta"].get("content") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _extract_text(payload: Any) -> str:
    if not isinstance(payload, dict) or "choices" not in payload:
        raise BadResponseError("Provider response has no 'choices'.")
    try:
        content = payload["choices"][0]["message"].get("content") or ""
        return content if isinstance(content, str) else json.dumps(content)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise BadResponseError(f"Response has no usable message content: {exc}") from exc


def _nonnegative_count(value: Any) -> int | None:
    """Parse a provider token count, or None when it is not usable evidence."""
    if value is None or isinstance(value, bool):
        return None
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    # A JSON float such as 3.5 is not a token count; integer-valued floats are
    # accepted because some compatible gateways serialise counts that way.
    if isinstance(value, float) and not value.is_integer():
        return None
    if count < 0:
        return None
    return count


def _details_count(node: Any, key: str) -> int | None:
    if not isinstance(node, dict):
        return None
    return _nonnegative_count(node.get(key))


def _extract_usage(payload: dict[str, Any]) -> LlmUsage:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        # No usage object at all: reported stays False so the cost path can
        # classify the call as `estimated`/`unknown` rather than pretend 0.
        return LlmUsage()

    # An OpenAI-compatible usage object must carry both token counts. Missing
    # keys are not zero evidence: an empty or total-only object must fall back
    # to the conservative reservation path instead of becoming a confirmed $0.
    if "prompt_tokens" not in usage or "completion_tokens" not in usage:
        return LlmUsage()
    prompt = _nonnegative_count(usage.get("prompt_tokens"))
    completion = _nonnegative_count(usage.get("completion_tokens"))
    if prompt is None or completion is None:
        return LlmUsage()

    expected_total = prompt + completion
    if "total_tokens" in usage:
        total = _nonnegative_count(usage.get("total_tokens"))
        # The OpenAI-compatible contract makes total_tokens the sum of the
        # two counts. A contradictory total is not trustworthy usage evidence;
        # an omitted total remains supported via the computed fallback.
        if total is None or total != expected_total:
            return LlmUsage()
    else:
        total = expected_total

    return LlmUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=_details_count(usage.get("prompt_tokens_details"), "cached_tokens"),
        reasoning_tokens=_details_count(usage.get("completion_tokens_details"), "reasoning_tokens"),
        reported=True,
    )


def _error_for_status(status: int) -> LlmError:
    if status in (401, 403):
        return ProviderAuthError(f"Provider rejected server credentials (HTTP {status}).")
    if status == 429:
        return RateLimitedError(f"Provider rate limit (HTTP {status}).")
    if 500 <= status <= 599:
        # A server error can be returned after the provider has accepted or
        # billed the request. Treat the outcome as ambiguous and do not resend.
        error = TransientProviderError(f"Provider server error (HTTP {status}).")
        error.ambiguous = True
        error.retryable = False
        return error
    return ProviderError(f"Provider returned HTTP {status}.")


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o-mini",
        timeout_s: float = 30.0,
        max_retries: int = 2,
        max_tokens_param: Literal["max_tokens", "max_completion_tokens"] = "max_tokens",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise LlmConfigurationError("OpenAICompatProvider requires a non-empty api_key.")
        if max_tokens_param not in ("max_tokens", "max_completion_tokens"):
            raise LlmConfigurationError(f"Unsupported max_tokens_param: {max_tokens_param!r}.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_s = timeout_s
        self._max_retries = max(0, max_retries)
        self._max_tokens_param = max_tokens_param
        self._transport = transport
        # Idempotency-Key is only documented for the official OpenAI endpoint.
        # Sending it to arbitrary compatible gateways is avoided unless opted in.
        self._supports_idempotency = self._base_url.startswith("https://api.openai.com")

    def _headers(self, request: LlmRequest | None = None) -> dict[str, str]:
        # The single place the key is ever used. Never log this dict.
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        if request is not None and request.idempotency_key and self._supports_idempotency:
            headers["Idempotency-Key"] = request.idempotency_key
        return headers

    def _body(self, request: LlmRequest, *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": stream,
        }
        if request.max_tokens is not None:
            # Exactly one spelling: sending both can be rejected by strict
            # servers and would make the wire cap ambiguous.
            body[self._max_tokens_param] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.structured is not None:
            body["response_format"] = self._response_format(request.structured)
        return body

    @staticmethod
    def _response_format(structured: StructuredOutput) -> dict[str, Any]:
        if structured.mode == "json_schema" and structured.schema:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "strict": structured.strict,
                    "schema": structured.schema,
                },
            }
        return {"type": "json_object"}

    @staticmethod
    def _prepare_structured(text: str, structured: StructuredOutput | None) -> Any:
        if structured is None:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BadResponseError(f"Structured output is not valid JSON: {exc}") from exc
        if structured.schema:
            violations = validate_against_schema(data, structured.schema)
            if violations:
                raise BadResponseError(f"Structured output violates schema: {violations}")
        return data

    @staticmethod
    def _backoff_delay(retry_index: int) -> float:
        # 0.5, 1.0, 2.0 … seconds, capped, plus jitter.
        return min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2**retry_index)) + random.uniform(0, 0.25)

    def _classify_transport_error(self, exc: Exception) -> LlmError:
        """Map a transport failure, distinguishing pre-send from post-send.

        A pre-send failure (connect refused, connect timeout, pool timeout)
        proves the request never reached the provider, so it is safe to retry
        and its reservation can be released. A post-send failure (read/write
        error, read timeout) leaves the outcome unknown — possibly billed — so
        it is marked `ambiguous` and is NOT retried (an unsafe retry could
        double-bill), and its reservation is held, never auto-released.
        """
        if isinstance(exc, httpx.TimeoutException):
            error: LlmError = ProviderTimeoutError(
                f"Provider request timed out: {type(exc).__name__}."
            )
            error.ambiguous = not isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout))
        elif isinstance(exc, (httpx.ConnectError, httpx.PoolTimeout)):
            error = TransientProviderError(
                f"Provider unreachable before send: {type(exc).__name__}."
            )
            error.ambiguous = False
        else:
            error = TransientProviderError(f"Provider network failure: {type(exc).__name__}.")
            error.ambiguous = True
        # Never retry an ambiguous (possibly-billed) failure.
        error.retryable = error.retryable and not error.ambiguous
        return error

    async def _single_attempt(
        self, client: httpx.AsyncClient, request: LlmRequest, *, stream: bool
    ) -> httpx.Response:
        url = f"{self._base_url}/chat/completions"
        try:
            response = await client.post(
                url, headers=self._headers(request), json=self._body(request, stream=stream)
            )
        except asyncio.CancelledError:
            raise
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.PoolTimeout,
            httpx.HTTPError,
        ) as exc:
            raise self._classify_transport_error(exc) from exc
        if 200 <= response.status_code < 300:
            return response
        # Auth + other 4xx are final. 429 is retryable; 5xx is ambiguous and
        # explicitly marked non-retryable by `_error_for_status`.
        raise _error_for_status(response.status_code)

    async def _with_retries(
        self, client: httpx.AsyncClient, request: LlmRequest, *, stream: bool
    ) -> tuple[httpx.Response, int]:
        """Send with bounded retries. Returns (response, attempts_used)."""
        attempts = self._max_retries + 1
        last_error: LlmError | None = None
        for attempt in range(attempts):
            try:
                return await self._single_attempt(client, request, stream=stream), attempt + 1
            except asyncio.CancelledError:
                raise
            except LlmError as exc:
                last_error = exc
                if not exc.retryable or attempt == attempts - 1:
                    raise
                await asyncio.sleep(self._backoff_delay(attempt))
        assert last_error is not None  # attempts >= 1 always
        raise last_error

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=timeout_s)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        try:
            async with self._client(request.timeout_s or self._timeout_s) as client:
                response, attempts = await self._with_retries(client, request, stream=False)
        except asyncio.CancelledError:
            raise
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise BadResponseError(f"Provider returned non-JSON body: {exc}") from exc
        text = _extract_text(payload)
        data = self._prepare_structured(text, request.structured)
        usage = _extract_usage(payload) if isinstance(payload, dict) else LlmUsage()
        request_id = payload.get("id") if isinstance(payload, dict) else None
        return LlmResponse(
            text=text,
            data=data,
            usage=usage,
            model=str(payload.get("model", "")) if isinstance(payload, dict) else "",
            provider_request_id=str(request_id) if request_id else None,
            attempts=attempts,
            metadata={"provider": "openai_compat"},
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        # The client MUST stay open for the whole stream: it owns the socket.
        client = self._client(request.timeout_s or self._timeout_s)
        response: httpx.Response | None = None
        try:
            try:
                response, _ = await self._with_retries(client, request, stream=True)
            except asyncio.CancelledError:
                raise
            parser = SseParser()
            try:
                async for chunk in response.aiter_bytes():
                    for payload_text in parser.feed(chunk):
                        try:
                            payload = json.loads(payload_text)
                        except json.JSONDecodeError as exc:
                            raise BadResponseError(f"Malformed SSE payload: {exc}") from exc
                        delta = _extract_delta(payload)
                        if delta:
                            yield LlmChunk(delta=delta)
                    if parser.finished:
                        break
                parser.finish()
            finally:
                await response.aclose()
            yield LlmChunk(done=True)
        except asyncio.CancelledError:
            raise
        finally:
            await client.aclose()
