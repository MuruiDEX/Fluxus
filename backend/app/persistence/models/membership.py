"""Membership — which user may access which business, in which role.

Many-to-many from day one so SaaS never reshapes scoping. Composite PK
(user_id, business_id); role is VARCHAR + CHECK ('owner' | 'member').
Single-user desktop = exactly one row.
"""

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),)

    user_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), default="member")
