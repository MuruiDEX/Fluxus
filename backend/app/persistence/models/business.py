"""Business — the tenant scope root (contract §1).

Business has NO business_id column: it IS the scope everything else hangs
under. Money/series are JSON (contract shapes); ledger money lives on tasks.
"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, IdMixin, TimestampMixin


class Business(IdMixin, TimestampMixin, Base):
    __tablename__ = "businesses"

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
