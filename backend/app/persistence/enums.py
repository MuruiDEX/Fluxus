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


class BriefStatus(StrEnum):
    DRAFT = "draft"
    QUESTIONS_READY = "questions_ready"
    BRIEF_READY = "brief_ready"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


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


class PackItemKind(StrEnum):
    AGENT = "agent"
    WORKFLOW = "workflow"


class UsageStatus(StrEnum):
    """Confidence in a ledger row's cost (Stage 16).

    Only `confirmed` rows are aggregated into confirmed totals; `estimated`
    and `unknown` are tracked separately and never presented as costed fact.
    """

    CONFIRMED = "confirmed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class UsageCostSource(StrEnum):
    """Where a ledger row's cost came from. `unknown` means no cost at all."""

    PROVIDER = "provider"  # provider reported it (authoritative)
    TARIFF = "tariff"  # computed from our versioned tariff table
    UNKNOWN = "unknown"  # not priceable — cost_amount stays NULL


class UsageCallKind(StrEnum):
    """Which product operation produced a ledger row (Stage 16.1).

    Kept separate from provider/model/task identity and cost source so usage
    rows stay queryable by call kind for summaries and analytics. `task_execution`
    is the orchestrator (Stage 16); the rest are Team Lead brief operations.
    """

    TASK_EXECUTION = "task_execution"
    BRIEF_QUESTIONS = "brief_questions"
    BRIEF_SUMMARY = "brief_summary"
    PLAN_GENERATION = "plan_generation"
    BRIEF_FINALIZE = "brief_finalize"


class ReservationStatus(StrEnum):
    """Budget reservation lifecycle (Stage 16).

    `active` and `ambiguous` reservations count against the budget ceiling;
    `consumed` becomes the linked ledger row (counted once as confirmed);
    `released`/`written_off` no longer count. Ambiguous reservations are
    NEVER auto-released — only an explicit write-off or proven non-execution.
    """

    ACTIVE = "active"
    CONSUMED = "consumed"
    AMBIGUOUS = "ambiguous"
    RELEASED = "released"
    WRITTEN_OFF = "written_off"


def check_values(enum_cls: type[StrEnum]) -> list[str]:
    return [m.value for m in enum_cls]


def literal_check(column: str, enum_cls: type[StrEnum], name: str) -> CheckConstraint:
    """VARCHAR + CHECK over frozen contract literals (see module docstring)."""
    allowed = ", ".join(repr(v) for v in check_values(enum_cls))
    return CheckConstraint(f"{column} IN ({allowed})", name=name)
