"""0005 agent hire provenance: nullable source_slug + partial unique index.

Revision ID: c9d4e2f7a5b3
Revises: b7e2d4f6a8c1
Create Date: 2026-10-12

Additive only: one nullable column on agents, no existing column altered, no
data touched. The partial unique index (business_id, source_slug) WHERE
source_slug IS NOT NULL forbids hiring the same listing twice into one
business while pre-hire agents (source_slug NULL) are unaffected — NULLs
never collide. Different businesses hire the same slug independently.
Downgrade drops the index and the column; agents and all business data are
preserved (provenance only).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d4e2f7a5b3"
down_revision: str | None = "b7e2d4f6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("source_slug", sa.String(128), nullable=True))
    op.create_index(
        "uq_agents_business_source",
        "agents",
        ["business_id", "source_slug"],
        unique=True,
        postgresql_where=sa.text("source_slug IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agents_business_source", table_name="agents")
    op.drop_column("agents", "source_slug")
