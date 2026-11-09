"""Usage ledger + budget reservations (Stage 16, contract amendment v1.17).

Two cooperating tables:

- `usage_records` is the APPEND-ONLY source of truth about accounted AI
  calls. One canonical row per actual provider attempt, enforced by
  `idempotency_key`. There is deliberately no update/delete path in the
  repository: later corrections arrive as new linked rows, never as edits.
  `cost_amount` is NULL when the cost is unknown — an unknown price is never
  represented as zero.

- `budget_reservations` is MUTABLE state (active/consumed/ambiguous/released/
  written_off/reconciled) kept OUT of the append-only ledger. A reservation is
  created before the network call and settled afterwards; `active`+`ambiguous`
  count against the budget ceiling, `consumed`/`reconciled` link to one ledger
  row (counted once, as confirmed).

- `usage_reconciliations` is the append-only audit trail for manual operator
  confirmation of an ambiguous task-execution hold. It links the reservation,
  the exact execution scope (task/attempt/generation), the confirmed ledger
  row and the acting owner. It never stores inferred/fabricated usage: token
  counts default to zero when the operator does not know them.

Money is NUMERIC(12,2). Contract literals are VARCHAR + CHECK, matching the
rest of the schema (no native ENUM).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,  # noqa: F401  (re-exported for callers building project patches)
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import (
    ReservationStatus,
    UsageCallKind,
    UsageCostSource,
    UsageReconciliationOutcome,
    UsageStatus,
    literal_check,
)

MONEY = Numeric(12, 2)


class UsageRecord(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        literal_check("usage_status", UsageStatus, "ck_usage_records_status"),
        literal_check("cost_source", UsageCostSource, "ck_usage_records_cost_source"),
        literal_check("call_kind", UsageCallKind, "ck_usage_records_call_kind"),
        CheckConstraint(
            "cost_amount IS NULL OR cost_amount >= 0",
            name="ck_usage_records_cost_nonneg",
        ),
        CheckConstraint(
            "usage_status <> 'confirmed' OR cost_amount IS NOT NULL",
            name="ck_usage_records_confirmed_has_cost",
        ),
        UniqueConstraint("idempotency_key", name="uq_usage_records_idempotency"),
        Index("ix_usage_records_business_project", "business_id", "project_id"),
        Index("ix_usage_records_business_task", "business_id", "task_id"),
        Index("ix_usage_records_call_kind", "business_id", "call_kind"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    # Logical attempt of the owning task (Task.attempts at claim time) plus a
    # stable attempt identity derived before the network call.
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    attempt_key: Mapped[str] = mapped_column(String(200), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    # Which operation produced the call (Stage 16.1). Nullable: legacy rows from
    # Stage 16 predate the column and are backfilled to `task_execution`.
    call_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    cost_source: Mapped[str] = mapped_column(String(16), default=UsageCostSource.UNKNOWN.value)
    usage_status: Mapped[str] = mapped_column(String(16), default=UsageStatus.UNKNOWN.value)
    tariff_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BudgetReservation(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        literal_check("status", ReservationStatus, "ck_budget_reservations_status"),
        CheckConstraint("amount >= 0", name="ck_budget_reservations_amount_nonneg"),
        UniqueConstraint("idempotency_key", name="uq_budget_reservations_idempotency"),
        Index("ix_budget_reservations_business_project", "business_id", "project_id"),
        Index("ix_budget_reservations_attempt", "business_id", "attempt_key"),
        Index("ix_budget_reservations_business_task", "business_id", "task_id"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    attempt_key: Mapped[str] = mapped_column(String(200), index=True)
    run_seq: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(16), default=ReservationStatus.ACTIVE.value)
    reason: Mapped[str] = mapped_column(String(200), default="")
    # `guaranteed` means a tariff+cap conservative bound existed; it is not a
    # tokenizer-independent proof. `best_effort` covers allow_unpriced_ai and
    # zero-budget/unbounded calls.
    mode: Mapped[str] = mapped_column(String(16), default="guaranteed")
    usage_record_id: Mapped[str | None] = mapped_column(String(ID_LEN), nullable=True)
    # Generation that created this hold. NULL remains valid for legacy rows
    # and Team Lead/business-scoped calls (no task generation to record).
    execution_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))


class UsageReconciliation(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    """Append-only audit of one manual ambiguous-reservation reconciliation.

    Exactly one row per reservation and, for a charge, one row per confirmed
    ledger entry. The reservation snapshot is copied here so the audit remains
    readable even if mutable reservation fields are later touched by unrelated
    flows. `charged` requires a confirmed ledger link and positive provider
    amount; `no_charge` requires no ledger link, zero amount and cost_source
    "none". Zero token counts mean "unknown/not supplied", never fabricated
    usage.
    """

    __tablename__ = "usage_reconciliations"
    __table_args__ = (
        literal_check(
            "outcome",
            UsageReconciliationOutcome,
            "ck_usage_reconciliations_outcome",
        ),
        CheckConstraint(
            "(outcome = 'charged' AND usage_record_id IS NOT NULL) "
            "OR (outcome = 'no_charge' AND usage_record_id IS NULL)",
            name="ck_usage_reconciliations_usage_record",
        ),
        CheckConstraint(
            "(outcome = 'charged' AND cost_source = 'provider') "
            "OR (outcome = 'no_charge' AND cost_source = 'none')",
            name="ck_usage_reconciliations_cost_source",
        ),
        CheckConstraint(
            "(outcome = 'charged' AND amount > 0) OR (outcome = 'no_charge' AND amount = 0)",
            name="ck_usage_reconciliations_amount",
        ),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0 "
            "AND (cached_tokens IS NULL OR cached_tokens >= 0) "
            "AND (reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name="ck_usage_reconciliations_tokens_nonneg",
        ),
        CheckConstraint("attested", name="ck_usage_reconciliations_attested"),
        UniqueConstraint("reservation_id", name="uq_usage_reconciliations_reservation"),
        UniqueConstraint("usage_record_id", name="uq_usage_reconciliations_usage_record"),
        Index(
            "ix_usage_reconciliations_business_project",
            "business_id",
            "project_id",
        ),
        Index(
            "ix_usage_reconciliations_business_task",
            "business_id",
            "task_id",
        ),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE")
    )
    task_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"))
    reservation_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("budget_reservations.id", ondelete="CASCADE")
    )
    usage_record_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("usage_records.id", ondelete="CASCADE"), nullable=True
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    attempt_key: Mapped[str] = mapped_column(String(200))
    execution_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(16), default=UsageReconciliationOutcome.CHARGED.value
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    cost_source: Mapped[str] = mapped_column(String(16), default=UsageCostSource.PROVIDER.value)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    attested: Mapped[bool] = mapped_column(Boolean, default=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str] = mapped_column(String(320), default="")
