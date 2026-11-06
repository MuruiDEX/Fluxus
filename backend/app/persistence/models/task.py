"""Task + task_dependencies (contract §1).

`depends_on` is a real edge table (not JSON) so the database enforces
referential integrity with CASCADE. Gate fields are nullable FKs.
Money: NUMERIC(12,2); progress is clamped repository-side per contract.

Execution ownership (Stage 18 S2.2.4.1):

- `execution_generation` is a per-task counter. Each generation-fenced claim
  increments it; the executor remembers the value it was granted and later
  uses it to prove it still owns the task.
- `lease_*` is the ownership lease. The three fields are either all NULL
  (idle/legacy/completed) or all non-NULL (an active, possibly expired lease).
  A `working` task without a lease stays legal so pre-lease claims remain
  recoverable through the legacy `updated_at` gate.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import TaskStatus, literal_check


class Task(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        literal_check("status", TaskStatus, "ck_tasks_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_tasks_progress"),
        CheckConstraint("cost_accrued >= 0", name="ck_tasks_cost_nonneg"),
        CheckConstraint("attempts >= 0", name="ck_tasks_attempts_nonneg"),
        CheckConstraint(
            "execution_generation >= 0",
            name="ck_tasks_execution_generation_nonneg",
        ),
        # All-or-nothing lease presence. This deliberately does NOT require a
        # lease for `working` tasks and does NOT reject an expired lease: a
        # crashed owner must leave its expired lease behind for recovery.
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND lease_deadline_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND lease_deadline_at IS NOT NULL)",
            name="ck_tasks_lease_consistency",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_deadline_at IS NULL "
            "OR lease_expires_at <= lease_deadline_at",
            name="ck_tasks_lease_within_deadline",
        ),
        # Recovery scans only working tasks whose lease may have lapsed.
        Index(
            "ix_tasks_working_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'working'"),
        ),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    owner_agent_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.NOT_STARTED.value)
    progress: Mapped[int] = mapped_column(default=0)
    cost_accrued: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    eta_minutes: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    approval_gate_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    gate_at: Mapped[int] = mapped_column(Integer, default=100)
    cost_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    execution_generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TaskDependency(Base):
    """Directed edge task_id -> depends_on_id. Same-business enforced repository-side."""

    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
