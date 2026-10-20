"""0004 brief final report: nullable JSON column on briefs.

Revision ID: b7e2d4f6a8c1
Revises: a3f5c7e9b1d2
Create Date: 2026-10-11

Additive only: one nullable column, no existing column altered, no data
touched. Existing briefs (any status) read back with final_report NULL, so
Stages 8.1–8.4 behavior and contracts are preserved. Downgrade drops only
the column; briefs and all business data are preserved.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2d4f6a8c1"
down_revision: str | None = "a3f5c7e9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("briefs", sa.Column("final_report", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("briefs", "final_report")
