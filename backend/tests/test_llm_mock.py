"""Mock provider: scripted determinism, offline defaults, cancellation."""

import asyncio

import pytest

from app.llm.mock import MockProvider
from app.llm.types import BadResponseError, LlmMessage, LlmRequest, StructuredOutput


def _req(**over) -> LlmRequest:
    base: dict = {"messages": [LlmMessage(role="user", content="hello")], "model": "m"}
    base.update(over)
    return LlmRequest(**base)


async def test_default_echo_without_config() -> None:
    provider = MockProvider()
    res = await provider.complete(_req())
    assert res.text == "echo: hello"
    assert res.metadata["provider"] == "mock"

    parts = [c async for c in provider.stream(_req())]
    assert "".join(c.delta for c in parts if not c.done) == "echo: hello"
    assert parts[-1].done is True


async def test_scripted_texts_errors_and_calls() -> None:
    err = BadResponseError("scripted")
    provider = MockProvider(texts=["first", "second"], errors=[err])
    with pytest.raises(BadResponseError):
        await provider.complete(_req())
    assert (await provider.complete(_req())).text == "first"
    assert len(provider.calls) == 2


async def test_scripted_chunks() -> None:
    provider = MockProvider(chunks=[["a", "b"], ["c"]])
    first = "".join(c.delta for c in [c async for c in provider.stream(_req())])
    second = "".join(c.delta for c in [c async for c in provider.stream(_req())])
    assert (first, second) == ("ab", "c")


async def test_structured_goes_through_real_validation() -> None:
    schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
    provider = MockProvider(texts=['{"ok": true}', '{"ok": "yes"}'])
    good = await provider.complete(
        _req(structured=StructuredOutput(mode="json_object", schema=schema))
    )
    assert good.data == {"ok": True}
    with pytest.raises(BadResponseError):
        await provider.complete(
            _req(structured=StructuredOutput(mode="json_object", schema=schema))
        )


async def test_mock_cancellation_propagates() -> None:
    provider = MockProvider(latency_s=5.0)
    task = asyncio.create_task(provider.complete(_req()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
