"""Provider Protocol + factory. The only construction point for LLM access.

Orchestration (Stage 4) depends on `LlmProvider`, never on an adapter.
Default provider is "mock" so tests and key-less runs never touch network.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from app.core.config import Settings
from app.llm.types import LlmChunk, LlmConfigurationError, LlmRequest, LlmResponse


class LlmProvider(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Single non-streaming generation."""
        ...

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """Streaming generation. Cancellation propagates; stream is closed."""
        ...


def create_provider(settings: Settings) -> LlmProvider:
    name = settings.llm_provider.strip().lower()
    if name == "mock":
        from app.llm.mock import MockProvider

        return MockProvider()
    if name == "openai_compat":
        from app.llm.adapters.openai_compat import OpenAICompatProvider

        if not settings.llm_api_key:
            raise LlmConfigurationError(
                "llm_api_key is not configured (APP_LLM_API_KEY). "
                "Set a key or switch llm_provider to 'mock'."
            )
        return OpenAICompatProvider(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            default_model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
        )
    raise LlmConfigurationError(f"Unknown llm_provider: {settings.llm_provider!r}.")
