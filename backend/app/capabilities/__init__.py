"""Agent Capability Engine.

One typed registry of the tools an agent may use during task execution.
Capabilities are resolved from the agent's existing `tools` / `permissions`
profile, executed by the host (never by the model), and every call is
authorized, audited and size-limited.
"""

from app.capabilities.engine import CapabilityExecutor, aggregate_usage
from app.capabilities.registry import CAPABILITIES, get_spec, resolve_grants
from app.capabilities.types import (
    ApprovalPolicy,
    CapabilityCall,
    CapabilityContext,
    CapabilityError,
    CapabilityOwnershipLost,
    CapabilityResult,
    CapabilityRisk,
    CapabilitySpec,
)

__all__ = [
    "ApprovalPolicy",
    "CAPABILITIES",
    "CapabilityCall",
    "CapabilityContext",
    "CapabilityError",
    "CapabilityExecutor",
    "CapabilityOwnershipLost",
    "CapabilityResult",
    "CapabilityRisk",
    "CapabilitySpec",
    "aggregate_usage",
    "get_spec",
    "resolve_grants",
]
