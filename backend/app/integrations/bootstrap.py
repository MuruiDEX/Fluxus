"""Composition of external providers into the core registries.

Provider modules are imported here only. Core code never imports Gmail (or any
future vendor); it asks the capability and provider registries for metadata
and execution. Importing this module is idempotent.
"""

from app.capabilities.builtin import HANDLERS
from app.integrations.gmail_capabilities import register as register_gmail_capabilities
from app.integrations.providers.gmail import GMAIL
from app.integrations.registry import register_provider

_registered = False


def bootstrap_integrations() -> None:
    global _registered
    if _registered:
        return
    register_provider(GMAIL)
    _specs, handlers = register_gmail_capabilities()
    for capability_id, handler in handlers.items():
        HANDLERS.setdefault(capability_id, handler)
    _registered = True


bootstrap_integrations()

__all__ = ["bootstrap_integrations"]
