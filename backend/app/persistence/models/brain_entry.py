"""BrainEntry — persistent company memory row (contract §1).

Category is VARCHAR + CHECK over frozen BrainCategory literals.
`updated_label` is a contract display string ("2h ago"), stored verbatim;
server-side recency always comes from `updated_at`.
"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import BrainCategory, literal_check


class BrainEntry(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "brain_entries"
    __table_args__ = (literal_check("category", BrainCategory, "ck_brain_entries_category"),)

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    updated_label: Mapped[str] = mapped_column(String(64), default="")
