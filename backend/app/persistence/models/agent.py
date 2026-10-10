"""Agent — AI employee row (contract §1).

Status is VARCHAR + CHECK over the frozen AgentStatus literals (enums.py),
never a native ENUM: additive literal evolution stays a data-only change.
"""

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import AgentStatus, literal_check


class Agent(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (literal_check("status", AgentStatus, "ck_agents_status"),)

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(200))
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=AgentStatus.AVAILABLE.value)
    current_task: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cost_per_month: Mapped[float] = mapped_column(Float, default=0.0)
    performance: Mapped[dict] = mapped_column(JSON, default=dict)
    hired: Mapped[bool] = mapped_column(default=True)
    avatar_hue: Mapped[int] = mapped_column(Integer, default=200)
