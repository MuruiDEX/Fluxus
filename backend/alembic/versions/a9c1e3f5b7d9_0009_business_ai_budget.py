"""0009 business-scoped AI budget policy.

Revision ID: a9c1e3f5b7d9
Revises: b4e8f1a2c3d4
Create Date: 2026-10-23

Additive only: two defaulted columns on `businesses`. Existing rows get
`ai_budget = 0` (no business limit) and `allow_unpriced_ai = false`
(fail-closed when a business budget is configured later). Downgrade drops only
what this revision added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c1e3f5b7d9"
down_revision: str | None = "b4e8f1a2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("ai_budget", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "businesses",
        sa.Column(
            "allow_unpriced_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_businesses_ai_budget_nonneg",
        "businesses",
        "ai_budget >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_businesses_ai_budget_nonneg", "businesses", type_="check")
    op.drop_column("businesses", "allow_unpriced_ai")
    op.drop_column("businesses", "ai_budget")
