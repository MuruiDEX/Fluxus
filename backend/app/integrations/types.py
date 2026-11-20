"""Integration vocabulary: providers, tokens, connections and safe errors.

Nothing here performs I/O. Provider implementations translate their wire
errors into these types so the API layer never has to understand a vendor's
error envelope (and never leaks one to the frontend or to an AI model).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class IntegrationError(Exception):
    """A safe integration failure. `message` may be shown to an owner."""

    code = "integration_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class IntegrationNotConfigured(IntegrationError):
    """Server-side OAuth/encryption configuration is missing."""

    code = "integration_not_configured"


class IntegrationCredentialsUnavailable(IntegrationError):
    """Encrypted storage cannot be used because no valid key is configured."""

    code = "credentials_unavailable"


class IntegrationAuthError(IntegrationError):
    """The external account must be reconnected (revoked/expired grant)."""

    code = "connection_reconnect_required"


class IntegrationScopeMissing(IntegrationError):
    """The connection was authorized without the scope a capability needs."""

    code = "scope_missing"


class IntegrationRateLimited(IntegrationError):
    """The provider asked us to slow down beyond our bounded retry budget."""

    code = "provider_rate_limited"


class IntegrationProviderError(IntegrationError):
    """A provider rejected the request; `message` is already sanitized."""

    code = "provider_error"


class IntegrationAmbiguous(IntegrationError):
    """The provider may have performed the action (e.g. 5xx after send).

    Callers must never retry an ambiguous external side effect automatically.
    """

    code = "provider_result_unknown"


@dataclass(frozen=True)
class ProviderCapability:
    """Capability metadata a provider contributes to the capability registry."""

    id: str
    title: str
    description: str
    risk: str  # "read" | "write"
    aliases: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSpec:
    """Static, frontend-safe provider metadata."""

    id: str
    name: str
    description: str
    docs_url: str
    read_scopes: tuple[str, ...]
    write_scopes: tuple[str, ...]
    capabilities: tuple[ProviderCapability, ...]


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]
    token_type: str = "Bearer"


@dataclass(frozen=True)
class ConnectedAccount:
    external_account_id: str
    display_name: str


@dataclass(frozen=True)
class ProviderResult:
    """Bounded, non-sensitive data returned by a provider operation."""

    data: dict[str, Any]
    external_id: str = ""
    thread_id: str = ""


__all__ = [
    "ConnectedAccount",
    "IntegrationAmbiguous",
    "IntegrationAuthError",
    "IntegrationCredentialsUnavailable",
    "IntegrationError",
    "IntegrationNotConfigured",
    "IntegrationProviderError",
    "IntegrationRateLimited",
    "IntegrationScopeMissing",
    "ProviderCapability",
    "ProviderResult",
    "ProviderSpec",
    "TokenBundle",
]
