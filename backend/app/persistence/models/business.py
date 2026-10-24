"""Business — the tenant scope root (contract §1).

Business has NO business_id column: it IS the scope everything else hangs
under. Money/series are JSON (contract shapes); ledger money lives on tasks.
"""

from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, IdMixin, TimestampMixin

MONEY = Numeric(12, 2)


class Business(IdMixin, TimestampMixin, Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint("ai_budget >= 0", name="ck_businesses_ai_budget_nonneg"),
    )

    name: Mapped[str] = mapped_column(String(200))
    tagline: Mapped[str] = mapped_column(String(300), default="")
    industry: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    owner_name: Mapped[str] = mapped_column(String(200), default="")
    health_score: Mapped[int] = mapped_column(Integer, default=0)
    health_breakdown: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    revenue_series: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Stage 18 S2: business-scoped Team Lead calls (no project yet) reserve
    # against this ceiling. Zero means no business limit; project budgets are
    # independent and remain the only cap for project-scoped calls.
    ai_budget: Mapped[Decimal] = mapped_column(MONEY, default=0)
    allow_unpriced_ai: Mapped[bool] = mapped_column(Boolean, default=False)
