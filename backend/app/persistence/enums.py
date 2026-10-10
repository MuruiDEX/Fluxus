"""Contract enumerations as Python str-Enums (docs/contracts/domain-api.md В§2).

Wire literals live here exactly once. Stored as VARCHAR + CHECK (see models),
never as native DB ENUM вЂ” additive evolution stays a data-only change.
"""

from enum import StrEnum

from sqlalchemy import CheckConstraint


class AgentStatus(StrEnum):
    AVAILABLE = "available"
    THINKING = "thinking"
    WORKING = "working"
    WAITING_DEPENDENCY = "waiting_dependency"
    WAITING_APPROVAL = "waiting_approval"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"


class TaskStatus(StrEnum):
    NOT_STARTED = "not_started"
    WORKING = "working"
    WAITING_DEPENDENCY = "waiting_dependency"
    WAITING_ASSETS = "waiting_assets"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class ProjectStatus(StrEnum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BrainCategory(StrEnum):
    COMPANY = "company"
    AUDIENCE = "audience"
    MODEL = "model"
    BRAND = "brand"
    GOALS = "goals"
    DECISIONS = "decisions"
    KNOWLEDGE = "knowledge"
    RESULTS = "results"


class MarketplaceCategory(StrEnum):
    EMPLOYEE = "employee"
    PACK = "pack"
    AUTOMATION = "automation"
    TEMPLATE = "template"
    INTEGRATION = "integration"


class ActivityKind(StrEnum):
    WORK = "work"
    APPROVAL = "approval"
    SYSTEM = "system"
    RESULT = "result"


def check_values(enum_cls: type[StrEnum]) -> list[str]:
    return [m.value for m in enum_cls]


def literal_check(column: str, enum_cls: type[StrEnum], name: str) -> CheckConstraint:
    """VARCHAR + CHECK over frozen contract literals (see module docstring)."""
    allowed = ", ".join(repr(v) for v in check_values(enum_cls))
    return CheckConstraint(f"{column} IN ({allowed})", name=name)
