"""Pack installation records (Stage 15).

An installation is the immutable receipt of one pack version per business.
Items record exactly which agents and workflow projects the installation
materialized, so reads and idempotent replays never have to guess. Rows are
created in the same transaction as the entities they reference; deleting an
installation therefore never deletes the agent/project rows (the receipt is
history, not ownership).
"""

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import PackItemKind, literal_check


class PackInstallation(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "pack_installations"
    __table_args__ = (
        UniqueConstraint("business_id", "pack_slug", name="uq_pack_installations_business_pack"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    pack_slug: Mapped[str] = mapped_column(String(ID_LEN))
    version: Mapped[str] = mapped_column(String(32))


class PackInstallationItem(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "pack_installation_items"
    __table_args__ = (
        literal_check("kind", PackItemKind, "ck_pack_installation_items_kind"),
        UniqueConstraint(
            "installation_id", "kind", "ref", name="uq_pack_items_installation_kind_ref"
        ),
        CheckConstraint("length(ref) > 0", name="ck_pack_installation_items_ref"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    installation_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("pack_installations.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16))
    ref: Mapped[str] = mapped_column(String(ID_LEN))
    entity_id: Mapped[str] = mapped_column(String(ID_LEN))
