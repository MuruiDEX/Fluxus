"""Task + task_dependencies (contract §1).

`depends_on` is a real edge table (not JSON) so the database enforces
referential integrity with CASCADE. Gate fields are nullable FKs.
Money: NUMERIC(12,2); progress is clamped repository-side per contract.
"""

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text
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


class TaskDependency(Base):
    """Directed edge task_id -> depends_on_id. Same-business enforced repository-side."""

    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
