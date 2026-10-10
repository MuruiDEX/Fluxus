"""Secret hygiene + provider factory.

The fake key below is distinctive on purpose: every failure path must be
provably free of it — exceptions, details, and captured logs. Factory rules:
unknown provider and missing key fail fast, before any network exists.
"""

import logging

import httpx
import pytest

from app.core.config import Settings
from app.llm.adapters.openai_compat import OpenAICompatProvider
from app.llm.base import create_provider
from app.llm.mock import MockProvider
from app.llm.types import (
    BadResponseError,
    LlmConfigurationError,
    LlmError,
    LlmMessage,
    LlmRequest,
)

FAKE_KEY = "sk-SECRET-XYZ-12345"


def _req(**over) -> LlmRequest:
    base: dict = {"messages": [LlmMessage(role="user", content="hi")], "model": "m"}
    base.update(over)
    return LlmRequest(**base)


def _provider(handler, **over) -> OpenAICompatProvider:
    return OpenAICompatProvider(api_key=FAKE_KEY, transport=httpx.MockTransport(handler), **over)


async def _failing_paths() -> list[Exception]:
    async def boom_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def boom_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down"}})

    async def boom_hang(request: httpx.Request) -> httpx.Response:
        # Transport-level timeout (MockTransport never fires real timers;
        # raising here exercises the same mapping the timer would hit).
        raise httpx.ReadTimeout("simulated stall")

    async def boom_html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    caught: list[Exception] = []
    for handler, kwargs in [
        (boom_401, {}),
        (boom_500, {"max_retries": 0}),
        (boom_hang, {}),
        (boom_html, {}),
    ]:
        provider = _provider(handler, **kwargs)
        try:
            await provider.complete(_req())
        except LlmError as exc:
            caught.append(exc)
    return caught


async def test_key_absent_from_exceptions(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        caught = await _failing_paths()
    assert len(caught) == 4
    for exc in caught:
        assert FAKE_KEY not in str(exc)
        assert FAKE_KEY not in repr(exc)
        details = getattr(exc, "details", {})
        assert FAKE_KEY not in str(details)
    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()


async def test_key_absent_from_stream_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: {broken\n\n")

    provider = _provider(handler)
    try:
        async for _ in provider.stream(_req()):
            pass
        raise AssertionError("should have raised")
    except BadResponseError as exc:
        assert FAKE_KEY not in str(exc)


def test_factory_defaults_to_mock() -> None:
    provider = create_provider(Settings(llm_provider="mock"))
    assert isinstance(provider, MockProvider)


def test_factory_openai_needs_key() -> None:
    with pytest.raises(LlmConfigurationError, match="APP_LLM_API_KEY"):
        create_provider(Settings(llm_provider="openai_compat", llm_api_key=None))


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(LlmConfigurationError, match="Unknown llm_provider"):
        create_provider(Settings(llm_provider="nope"))


def test_factory_builds_openai_compat() -> None:
    provider = create_provider(
        Settings(llm_provider="openai_compat", llm_api_key=FAKE_KEY, llm_model="x")
    )
    assert isinstance(provider, OpenAICompatProvider)
    assert provider._default_model == "x"


def test_empty_key_rejected_at_construction() -> None:
    with pytest.raises(LlmConfigurationError):
        OpenAICompatProvider(api_key="")
