"""ActivityEvent — append-only audit trail row (contract §1).

Write path is create + list ONLY: the repository exposes no update or
delete. `occurred_at` is the server truth; contract display string `at`
("2h ago") is derived client-side and never stored. `project_id` is an
internal scope column (engine scope is business+project); it does not change
the wire shape, which is defined by Pydantic schemas at Stage 5.
"""

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import ActivityKind, literal_check


class ActivityEvent(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        literal_check("kind", ActivityKind, "ck_activity_events_kind"),
        CheckConstraint("cost IS NULL OR cost >= 0", name="ck_activity_events_cost_nonneg"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(200), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
