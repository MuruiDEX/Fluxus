"""Approval — human gate row (contract §1).

Both directions of the task gate are nullable FKs: a task may wait on an
approval (`tasks.approval_gate_id`) and an approval may target a task
(`gates_task_id`). Cost/expected result are contract display strings,
stored verbatim and never parsed.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import ApprovalStatus, literal_check


class Approval(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (literal_check("status", ApprovalStatus, "ck_approvals_status"),)

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    requested_by: Mapped[str] = mapped_column(String(200), default="")
    requested_by_agent_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("agents.id", ondelete="RESTRICT"), index=True
    )
    age_label: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default=ApprovalStatus.PENDING.value)
    what: Mapped[str] = mapped_column(Text, default="")
    expected_result: Mapped[str] = mapped_column(Text, default="")
    cost: Mapped[str] = mapped_column(String(200), default="")
    affects: Mapped[str] = mapped_column(Text, default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    gates_task_id: Mapped[str | None] = mapped_column(
        # use_alter breaks the tasks<->approvals FK cycle: the constraint is
        # added via ALTER after both tables exist (see migration 0001).
        # The explicit name is required so DROP CONSTRAINT can target it.
        String(ID_LEN),
        ForeignKey(
            "tasks.id", ondelete="SET NULL", use_alter=True, name="fk_approvals_gates_task_id_tasks"
        ),
        nullable=True,
    )
