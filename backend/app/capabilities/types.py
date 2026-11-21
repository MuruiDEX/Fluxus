"""Typed vocabulary for the Agent Capability Engine.

A capability is a named, host-executed action an agent may use while working
on a task. The model only ever *requests* a capability in structured output:
authorization, argument validation, side effects, auditing and size limits all
happen on the server. Nothing in this module performs I/O.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

# Hard wire limits. These bound what a model can ask for and what can be fed
# back, independently of provider token accounting.
MAX_CAPABILITY_NAME_CHARS = 80
MAX_CAPABILITY_ARGUMENT_CHARS = 2000
MAX_CAPABILITY_REQUEST_ID_CHARS = 80
MAX_CAPABILITY_REASON_CHARS = 300


class CapabilityRisk(StrEnum):
    """How dangerous a capability is.

    READ actions only read the caller's business scope. WRITE actions create
    persistent rows (pending approvals, gated child tasks) and are subject to
    first-attempt-only and per-run rate limits.
    """

    READ = "read"
    WRITE = "write"


class ApprovalPolicy(StrEnum):
    """Whether the capability itself needs a human gate before execution."""

    NONE = "none"
    REQUIRED = "required"


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    title: str
    description: str
    risk: CapabilityRisk
    approval: ApprovalPolicy = ApprovalPolicy.NONE
    # Human-readable names from the existing agent profile (tools/permissions
    # strings) that grant this capability. Matching is deterministic: the
    # normalized string must equal an alias exactly.
    aliases: tuple[str, ...] = ()
    # Minimal JSON-Schema subset for arguments (validated host-side).
    parameters: dict[str, Any] = field(default_factory=dict)

    def wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "risk": self.risk.value,
            "approvalRequired": self.approval is ApprovalPolicy.REQUIRED,
        }


@dataclass(frozen=True)
class CapabilityCall:
    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    reason: str = ""


@dataclass
class CapabilityResult:
    capability: str
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    approval_id: str | None = None
    task_id: str | None = None
    request_id: str = ""

    def wire(self) -> dict[str, Any]:
        """Bounded, model-facing shape. Sensitive/internal keys never appear."""
        payload: dict[str, Any] = {
            "capability": self.capability,
            "ok": self.ok,
        }
        if self.request_id:
            payload["requestId"] = self.request_id
        if self.ok:
            payload["data"] = self.data or {}
        else:
            payload["error"] = {
                "code": self.error_code or "capability_error",
                "message": self.error_message or "Capability failed.",
            }
        if self.approval_id is not None:
            payload["approvalId"] = self.approval_id
        if self.task_id is not None:
            payload["taskId"] = self.task_id
        return payload


class CapabilityError(Exception):
    """A safe, model-facing capability failure (never leaks internals)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CapabilityOwnershipLost(Exception):
    """The execution lease was lost while capability side effects were due.

    The executor maps this to its own ownership-lost path; no capability side
    effect may be written after it is raised.
    """


@dataclass
class CapabilityContext:
    session: "AsyncSession"
    business_id: str
    project_id: str
    task_id: str
    task_title: str
    agent_id: str
    agent_name: str
    attempt: int
    execution_generation: int
    lease_owner: str
    settings: "Settings"
    # Per-run write budget (shared by every write capability call).
    writes_used: int = 0
    authorizer_id: str | None = None

    def consume_write(self) -> None:
        self.writes_used += 1


def text_limit(value: str, limit: int) -> str:
    """Truncate a string to a hard character cap (never raises)."""
    if not isinstance(value, str):
        return ""
    return value[: max(0, int(limit))]
