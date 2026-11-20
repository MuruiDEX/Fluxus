"""Provider abstraction for external integrations.

A provider owns exactly one vendor's OAuth policy and wire protocol. The
registry and the capability engine never import vendor modules directly:
providers are composed at startup through `app.integrations.bootstrap`.
"""

from typing import Any, Protocol

from app.integrations.types import ConnectedAccount, ProviderResult, ProviderSpec, TokenBundle


class IntegrationProvider(Protocol):
    spec: ProviderSpec

    def configured(self, settings: object) -> bool: ...

    def missing_configuration(self, settings: object) -> list[str]: ...

    def requested_scopes(self, settings: object, *, include_writes: bool) -> list[str]: ...

    def authorization_url(
        self, settings: object, *, state: str, code_challenge: str, include_writes: bool
    ) -> str: ...

    async def exchange_code(
        self,
        settings: object,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> tuple[TokenBundle, ConnectedAccount]: ...

    async def refresh(self, settings: object, refresh_token: str) -> TokenBundle: ...

    async def revoke(self, settings: object, token: str) -> None: ...

    async def account_for_token(self, settings: object, access_token: str) -> ConnectedAccount: ...

    async def execute(
        self,
        settings: object,
        *,
        capability: str,
        access_token: str,
        arguments: dict[str, Any],
        side_effect: bool = False,
    ) -> ProviderResult: ...


__all__ = ["IntegrationProvider"]
