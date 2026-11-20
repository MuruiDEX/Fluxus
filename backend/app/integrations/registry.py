"""Provider registry — composition boundary for external integrations.

Core execution only ever asks this registry for metadata or a provider by id;
vendor modules are imported once by `app.integrations.bootstrap`.
"""

from app.integrations.providers.base import IntegrationProvider

_PROVIDERS: dict[str, IntegrationProvider] = {}


def register_provider(provider: IntegrationProvider) -> None:
    """Register one provider. Dup registration is a programming error."""
    existing = _PROVIDERS.get(provider.spec.id)
    if existing is not None:
        if existing is provider:
            return
        raise ValueError(f"integration provider already registered: {provider.spec.id}")
    _PROVIDERS[provider.spec.id] = provider


def get_provider(provider_id: str) -> IntegrationProvider | None:
    return _PROVIDERS.get(provider_id)


def list_providers() -> list[IntegrationProvider]:
    return sorted(_PROVIDERS.values(), key=lambda provider: provider.spec.id)


__all__ = ["get_provider", "list_providers", "register_provider"]
