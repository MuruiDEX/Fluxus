"""Persisted authorization and control state for a single project run."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, IdMixin, TimestampMixin
from app.persistence.enums import literal_check


class RunState(StrEnum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    NEEDS_ATTENTION = "needs_attention"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ProjectRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "project_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "project_id"],
            ["projects.business_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_project_runs_project",
        ),
        ForeignKeyConstraint(
            ["business_id", "project_id", "active_task_id"],
            ["tasks.business_id", "tasks.project_id", "tasks.id"],
            name="fk_project_runs_active_task",
        ),
        Index("uq_project_runs_project", "business_id", "project_id", unique=True),
        Index(
            "ix_project_runs_due",
            "next_check_at",
            postgresql_where=text("next_check_at IS NOT NULL"),
        ),
        literal_check("state", RunState, "ck_project_runs_state"),
        CheckConstraint("version >= 1", name="ck_project_runs_version"),
        CheckConstraint(
            "(active_task_id IS NULL) = (active_task_generation IS NULL) "
            "AND (active_task_generation IS NULL OR active_task_generation > 0)",
            name="ck_project_runs_active_task",
        ),
        ForeignKeyConstraint(
            ["business_id", "project_id", "retry_task_id"],
            ["tasks.business_id", "tasks.project_id", "tasks.id"],
            name="fk_project_runs_retry_task",
        ),
        CheckConstraint(
            "(retry_task_id IS NULL) = (retry_task_generation IS NULL) "
            "AND (retry_task_generation IS NULL OR retry_task_generation >= 0) "
            "AND (retry_task_id IS NULL OR active_task_id IS NULL)",
            name="ck_project_runs_retry_task",
        ),
        CheckConstraint(
            "recovered_task_generation IS NULL OR "
            "(active_task_generation IS NOT NULL "
            "AND recovered_task_generation = active_task_generation + 1)",
            name="ck_project_runs_recovered_generation",
        ),
    )

    business_id: Mapped[str] = mapped_column(String(ID_LEN))
    project_id: Mapped[str] = mapped_column(String(ID_LEN))
    # Keep the historical identity even after membership or account deletion.
    authorizing_user_id: Mapped[str] = mapped_column(String(ID_LEN))
    state: Mapped[str] = mapped_column(String(32))
    version: Mapped[int]
    reason_code: Mapped[str | None] = mapped_column(String(64))
    blocker_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    active_task_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    active_task_generation: Mapped[int | None]
    recovered_task_generation: Mapped[int | None]
    retry_task_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    retry_task_generation: Mapped[int | None]
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
