"""Reusable external integration layer (Stage 19).

Provider-specific code lives under `app.integrations.providers`; lifecycle,
encryption, OAuth state and approval-gated actions live here. The core
execution engine only sees generic capability specs and handlers.
"""

from app.integrations.types import (
    IntegrationAmbiguous,
    IntegrationAuthError,
    IntegrationError,
    IntegrationNotConfigured,
    IntegrationProviderError,
    IntegrationRateLimited,
    IntegrationScopeMissing,
)

__all__ = [
    "IntegrationAmbiguous",
    "IntegrationAuthError",
    "IntegrationError",
    "IntegrationNotConfigured",
    "IntegrationProviderError",
    "IntegrationRateLimited",
    "IntegrationScopeMissing",
]
