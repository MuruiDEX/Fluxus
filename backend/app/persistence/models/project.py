"""Project — initiative aggregate (contract §1).

`ai_spent` is a DERIVED cache, never written directly: the only legal
mutation is `recalculate_project_spend()` (repositories/project.py), which
recomputes SUM over scoped tasks with the project row locked, in the same
transaction as the task-cost writes. See contract §5.1.
`team_ids` is a JSON id-list; membership is validated repository-side
(DB cannot FK into array elements portably).
"""

from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import ProjectStatus, literal_check

MONEY = Numeric(12, 2)


class Project(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        literal_check("status", ProjectStatus, "ck_projects_status"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_projects_progress"),
        CheckConstraint("ai_spent >= 0", name="ck_projects_ai_spent_nonneg"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.PLANNING.value)
    progress: Mapped[int] = mapped_column(default=0)
    budget: Mapped[Decimal] = mapped_column(MONEY, default=0)
    ai_budget: Mapped[Decimal] = mapped_column(MONEY, default=0)
    ai_spent: Mapped[Decimal] = mapped_column(MONEY, default=0)
    # Stage 16: explicit opt-in to run AI with no priceable tariff. Off by
    # default (fail-closed: `budget_unpriced`); when on, the budget becomes
    # best-effort — the real provider bill may exceed `ai_budget`.
    allow_unpriced_ai: Mapped[bool] = mapped_column(Boolean, default=False)
    # Contract display string ("30 May 2026"): stored verbatim, never parsed.
    deadline: Mapped[str] = mapped_column(String(64), default="")
    team_ids: Mapped[list] = mapped_column(JSON, default=list)
    milestones: Mapped[list] = mapped_column(JSON, default=list)
