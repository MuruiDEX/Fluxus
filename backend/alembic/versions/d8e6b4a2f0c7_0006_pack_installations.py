"""0006 pack installations: installation receipts + materialized item links.

Revision ID: d8e6b4a2f0c7
Revises: c9d4e2f7a5b3
Create Date: 2026-10-19

Additive only: two new tenant-scoped tables, no existing table altered and no
data touched. `pack_installations` is unique per (business_id, pack_slug) so a
business can hold exactly one installed version of a pack. Items are scoped by
the same business and reference the installation; entity ids are receipts for
agents/projects and intentionally carry no FK so uninstalling history never
cascades into real domain rows. Downgrade drops only these two tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8e6b4a2f0c7"
down_revision: str | None = "c9d4e2f7a5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pack_installations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(128),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pack_slug", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("business_id", "pack_slug", name="uq_pack_installations_business_pack"),
    )
    op.create_index("ix_pack_installations_business_id", "pack_installations", ["business_id"])
    op.create_table(
        "pack_installation_items",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(128),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "installation_id",
            sa.String(128),
            sa.ForeignKey("pack_installations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("ref", sa.String(128), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('agent', 'workflow')", name="ck_pack_installation_items_kind"),
        sa.CheckConstraint("length(ref) > 0", name="ck_pack_installation_items_ref"),
        sa.UniqueConstraint(
            "installation_id", "kind", "ref", name="uq_pack_items_installation_kind_ref"
        ),
    )
    op.create_index(
        "ix_pack_installation_items_business_id", "pack_installation_items", ["business_id"]
    )
    op.create_index(
        "ix_pack_installation_items_installation_id",
        "pack_installation_items",
        ["installation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pack_installation_items_installation_id", table_name="pack_installation_items"
    )
    op.drop_index("ix_pack_installation_items_business_id", table_name="pack_installation_items")
    op.drop_table("pack_installation_items")
    op.drop_index("ix_pack_installations_business_id", table_name="pack_installations")
    op.drop_table("pack_installations")
