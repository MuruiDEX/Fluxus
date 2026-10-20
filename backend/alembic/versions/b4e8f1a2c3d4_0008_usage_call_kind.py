"""0008 usage_records.call_kind.

Revision ID: b4e8f1a2c3d4
Revises: f1a2b3c4d5e6
Create Date: 2026-10-19

Additive only: one nullable VARCHAR + CHECK column on `usage_records` plus an
index for call-kind grouping. All pre-existing rows are Stage 16 executor
calls, so they are backfilled to `task_execution`; the column stays nullable to
tolerate rows written before the column existed (NULL is not a `confirmed`
cost and never enters confirmed aggregates). Downgrade drops only what this
revision added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4e8f1a2c3d4"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = (
    "task_execution",
    "brief_questions",
    "brief_summary",
    "plan_generation",
    "brief_finalize",
)


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column("call_kind", sa.String(32), nullable=True),
    )
    op.execute(
        sa.text("UPDATE usage_records SET call_kind = 'task_execution' WHERE call_kind IS NULL")
    )
    op.create_check_constraint(
        "ck_usage_records_call_kind",
        "usage_records",
        "call_kind IS NULL OR call_kind IN (" + ", ".join(repr(k) for k in _KINDS) + ")",
    )
    op.create_index(
        "ix_usage_records_call_kind", "usage_records", ["business_id", "call_kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_call_kind", table_name="usage_records")
    op.drop_constraint("ck_usage_records_call_kind", "usage_records", type_="check")
    op.drop_column("usage_records", "call_kind")
