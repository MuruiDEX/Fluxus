"""User — identity root for Stage 6 authentication.

No business_id: a user exists independently and reaches businesses only
through memberships. Email is unique (CITEXT would be ideal; portable
UNIQUE + application-level normalization is used instead — see
AuthRepository.normalize_email).
"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import Base, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(200), default="")
