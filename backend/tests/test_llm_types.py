"""Types, error taxonomy, cancellation rule, and schema-subset validation."""

import asyncio

import pytest

from app.llm.types import (
    BadResponseError,
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


def _req(**over) -> LlmRequest:
    from app.llm.types import LlmMessage

    base: dict = {"messages": [LlmMessage(role="user", content="hi")], "model": "m"}
    base.update(over)
    return LlmRequest(**base)


def test_error_codes_are_contract_safe() -> None:
    assert ProviderAuthError("x").code == "provider_auth_error"
    assert ProviderAuthError("x").code != "unauthorized"
    assert RateLimitedError("x").code == "rate_limited"
    assert ProviderTimeoutError("x").code == "provider_timeout"
    assert BadResponseError("x").code == "bad_response"
    assert TransientProviderError("x").retryable is True
    assert ProviderError("x").retryable is False
    assert ProviderAuthError("x").retryable is False


def test_cancelled_error_is_not_an_llm_error() -> None:
    # The hard rule: cancellation must never be caught as a provider error.
    assert not issubclass(asyncio.CancelledError, LlmError)

    async def run() -> str:
        try:
            raise asyncio.CancelledError()
        except LlmError:
            return "wrapped"  # must NOT happen
        except asyncio.CancelledError:
            return "propagated"

    assert asyncio.run(run()) == "propagated"


def test_retryable_flags() -> None:
    assert RateLimitedError("x").retryable is True
    assert ProviderTimeoutError("x").retryable is True
    assert TransientProviderError("x").retryable is True
    assert BadResponseError("x").retryable is False
    assert LlmConfigurationError("x").retryable is False


def test_usage_defaults_and_response_shape() -> None:
    r = LlmResponse(text="hi")
    assert r.usage == LlmUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    assert r.data is None and r.attempts == 1


def test_request_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _req(messages=[])
    assert _req().timeout_s == 30.0


def test_validate_object_subset() -> None:
    schema = {
        "type": "object",
        "required": ["name", "age"],
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    }
    assert validate_against_schema({"name": "a", "age": 3}, schema) == []
    violations = validate_against_schema({"name": "a"}, schema)
    assert any("age" in v for v in violations)
    violations = validate_against_schema({"name": "a", "age": "old"}, schema)
    assert any("integer" in v for v in violations)
    assert validate_against_schema(["x"], schema) != []


def test_validate_array_enum_and_bool_safety() -> None:
    assert validate_against_schema([1, 2], {"type": "array", "items": {"type": "integer"}}) == []
    assert validate_against_schema([1, "x"], {"type": "array", "items": {"type": "integer"}}) != []
    assert validate_against_schema("a", {"enum": ["a", "b"]}) == []
    assert validate_against_schema("z", {"enum": ["a", "b"]}) != []
    # bool is not integer/number — strict, no silent coercion.
    assert validate_against_schema(True, {"type": "integer"}) != []
    assert validate_against_schema(True, {"type": "boolean"}) == []
    # Unknown keywords never reject (outside the covered subset).
    assert validate_against_schema(object(), {"type": "mystery"}) == []


def test_structured_output_model() -> None:
    s = StructuredOutput(mode="json_schema", schema={"type": "object"}, strict=True)
    assert s.mode == "json_schema" and s.strict is True
    assert StructuredOutput().mode == "json_object"
