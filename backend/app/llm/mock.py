"""Mock provider — deterministic, fully offline.

Scripted queues let tests dictate exact provider behavior (responses,
chunks, errors, latencies) with zero network and zero keys. Unscripted
calls return a deterministic echo so manual runs work without configuration.
`asyncio.sleep` yields naturally, so cancellation propagates like the real
adapter (verified by tests).
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from app.llm.types import LlmChunk, LlmRequest, LlmResponse, LlmUsage


class MockProvider:
    name = "mock"

    def __init__(
        self,
        *,
        texts: list[str] | None = None,
        chunks: list[list[str]] | None = None,
        errors: list[Exception] | None = None,
        latency_s: float = 0.0,
        usage: LlmUsage | None = None,
    ) -> None:
        self._texts: deque[str] = deque(texts or [])
        self._chunks: deque[list[str]] = deque(chunks or [])
        self._errors: deque[Exception] = deque(errors or [])
        self._latency_s = latency_s
        # A test double that always reports usage; `reported=True` lets a
        # registered tariff price it as `confirmed` (default catalog is empty,
        # so an un-configured mock is recorded as `unknown`, never as free).
        self._usage = usage or LlmUsage(
            prompt_tokens=10, completion_tokens=5, total_tokens=15, reported=True
        )
        self.calls: list[LlmRequest] = []

    def _next_error(self) -> Exception | None:
        return self._errors.popleft() if self._errors else None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        if (error := self._next_error()) is not None:
            raise error
        text = self._texts.popleft() if self._texts else f"echo: {request.messages[-1].content}"
        data: Any = None
        if request.structured is not None:
            from app.llm.adapters.openai_compat import OpenAICompatProvider

            # Reuse the real parse+validate path so mocks exercise it.
            data = OpenAICompatProvider._prepare_structured(text, request.structured)
        return LlmResponse(
            text=text,
            data=data,
            usage=self._usage,
            model=request.model or "mock",
            attempts=1,
            metadata={"provider": "mock"},
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        self.calls.append(request)
        if self._latency_s:
            await asyncio.sleep(self._latency_s)
        if (error := self._next_error()) is not None:
            raise error
        parts = (
            self._chunks.popleft() if self._chunks else [f"echo: {request.messages[-1].content}"]
        )
        for part in parts:
            yield LlmChunk(delta=part)
        yield LlmChunk(done=True)
