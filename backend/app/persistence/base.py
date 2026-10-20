"""Declarative base + shared columns.

Portable subset only: String/JSON/Numeric/DateTime/CHECK — no engine-specific
types, so the same models run on PostgreSQL (primary) and SQLite
(portability check). Native ENUM is deliberately NOT used: contract literals
are enforced with VARCHAR + CHECK, which keeps additive enum evolution cheap.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID_LEN = 128


class Base(DeclarativeBase):
    pass


class IdMixin:
    """String primary key following the contract ID charset."""

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BusinessScopedMixin:
    """Every tenant-owned row carries business_id; repositories always filter it."""

    business_id: Mapped[str] = mapped_column(String(ID_LEN), index=True)
