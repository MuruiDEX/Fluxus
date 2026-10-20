"""MarketplaceListing — global catalog row (contract §1).

No business_id: the catalog is global, hires are per-business (Stage 5+).
`slug` is the immutable primary key — renaming it breaks hires, so the
repository exposes no slug mutation (ImmutableFieldError on attempt).
"""

from sqlalchemy import JSON, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, IdMixin, TimestampMixin
from app.persistence.enums import MarketplaceCategory, literal_check


class MarketplaceListing(IdMixin, TimestampMixin, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        literal_check("category", MarketplaceCategory, "ck_marketplace_listings_category"),
    )

    id: Mapped[str] = mapped_column(String(ID_LEN), primary_key=True)  # == slug
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(200), default="")
    blurb: Mapped[str] = mapped_column(Text, default="")
    price: Mapped[str] = mapped_column(String(64), default="")
    price_note: Mapped[str] = mapped_column(String(200), default="")
    rating: Mapped[float] = mapped_column(Float, default=0.0)
    installs: Mapped[str] = mapped_column(String(64), default="")
    badge: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    category: Mapped[str] = mapped_column(String(32))
