"""LLM provider types — shared vocabulary for all adapters.

Cancellation contract (hard rule): `asyncio.CancelledError` is NEVER wrapped,
converted, or retried. It propagates unchanged; streams are closed in
`finally` blocks. `CancelledError` is deliberately NOT an `LlmError`.

Secrets contract: API keys live in Settings only. They are never interpolated
into messages, logs, or error details (enforced by tests/test_llm_secrets.py).
"""

import asyncio  # noqa: F401  (re-exported so adapters import the rule from here)
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class LlmMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class StructuredOutput(BaseModel):
    """How the caller wants machine-readable output.

    mode="json_schema": adapter sends the provider's native JSON-Schema
      constrained decoding (when supported) AND validates the result
      against `schema` locally. Compliance is never claimed without this
      local validation step.
    mode="json_object": fallback — provider returns free-form JSON, which is
      parsed and validated locally the same way.
    """

    mode: Literal["json_schema", "json_object"] = "json_object"
    schema: dict[str, Any] | None = None
    strict: bool = False


class LlmRequest(BaseModel):
    messages: list[LlmMessage] = Field(min_length=1)
    model: str
    max_tokens: int | None = None
    temperature: float | None = None
    structured: StructuredOutput | None = None
    timeout_s: float = 30.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class LlmUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Set only when the provider reports pricing; no billing tables exist.
    cost_estimate: Decimal | None = None


class LlmResponse(BaseModel):
    text: str
    # Parsed + schema-validated payload (None when no structured output asked).
    data: Any = None
    usage: LlmUsage = Field(default_factory=LlmUsage)
    model: str = ""
    # Attempts actually performed (1 + retries). Retried generations may be
    # metered twice provider-side after a network failure of unknown outcome.
    attempts: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class LlmChunk(BaseModel):
    delta: str = ""
    done: bool = False


class LlmError(Exception):
    """Base for all provider errors. Carries a contract-safe code."""

    code: str = "provider_error"
    # Whether the operation may be retried (429/5xx/network only).
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class ProviderError(LlmError):
    code = "provider_error"


class TransientProviderError(ProviderError):
    """Network failure, timeout-class transport error, or 5xx.

    Safe to retry, with one caveat surfaced via response metadata: after a
    network failure of unknown outcome the generation may already have
    executed (and been metered) provider-side.
    """

    retryable: bool = True


class ProviderTimeoutError(LlmError):
    code = "provider_timeout"
    retryable: bool = True


class RateLimitedError(LlmError):
    code = "rate_limited"
    retryable: bool = True


class ProviderAuthError(LlmError):
    """Provider rejected OUR server-side credentials.

    Distinct from Business OS user auth (`unauthorized`): a provider key
    problem must never look like a user login failure. Maps to the
    `provider_auth_error` envelope code (contract amendment v1.2).
    """

    code = "provider_auth_error"


class BadResponseError(LlmError):
    code = "bad_response"


class LlmConfigurationError(LlmError):
    code = "configuration_error"


# Re-exported cancellation rule: adapters must `except asyncio.CancelledError: raise`
# BEFORE any `except LlmError` / `except Exception`, and must never include it
# in retry classification.
__all__ = [
    "LlmMessage",
    "StructuredOutput",
    "LlmRequest",
    "LlmUsage",
    "LlmResponse",
    "LlmChunk",
    "LlmError",
    "ProviderError",
    "TransientProviderError",
    "ProviderTimeoutError",
    "RateLimitedError",
    "ProviderAuthError",
    "BadResponseError",
    "LlmConfigurationError",
]
