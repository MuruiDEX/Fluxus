"""0010 execution ownership foundation.

Revision ID: 0010_execution_ownership
Revises: a9c1e3f5b7d9
Create Date: 2026-10-31

Additive only (Stage 18 S2.2.4.1):

- `tasks.execution_generation` (NOT NULL, default 0) plus a non-negative CHECK.
- `tasks.lease_owner` / `lease_expires_at` / `lease_deadline_at` (all nullable)
  with an all-or-nothing consistency CHECK and an expiry<=deadline CHECK.
- A partial index for recovery scans of live `working` leases.
- `budget_reservations.execution_generation` (nullable: legacy and
  business-scoped holds legitimately have no task generation) plus a
  business/task lookup index.

No backfill is needed: existing task rows get `execution_generation = 0` and
all lease columns NULL; existing reservation rows get NULL generation. Both
satisfy the new constraints. Downgrade drops only what this revision added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_execution_ownership"
down_revision: str | None = "a9c1e3f5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "execution_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column("tasks", sa.Column("lease_owner", sa.String(64), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("lease_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tasks_execution_generation_nonneg",
        "tasks",
        "execution_generation >= 0",
    )
    op.create_check_constraint(
        "ck_tasks_lease_consistency",
        "tasks",
        "(lease_owner IS NULL AND lease_expires_at IS NULL "
        "AND lease_deadline_at IS NULL) OR "
        "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND lease_deadline_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_tasks_lease_within_deadline",
        "tasks",
        "lease_expires_at IS NULL OR lease_deadline_at IS NULL "
        "OR lease_expires_at <= lease_deadline_at",
    )
    op.create_index(
        "ix_tasks_working_lease",
        "tasks",
        ["lease_expires_at"],
        postgresql_where=sa.text("status = 'working'"),
    )

    op.add_column(
        "budget_reservations",
        sa.Column("execution_generation", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_budget_reservations_business_task",
        "budget_reservations",
        ["business_id", "task_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_budget_reservations_business_task",
        table_name="budget_reservations",
    )
    op.drop_column("budget_reservations", "execution_generation")

    op.drop_index("ix_tasks_working_lease", table_name="tasks")
    op.drop_constraint("ck_tasks_lease_within_deadline", "tasks", type_="check")
    op.drop_constraint("ck_tasks_lease_consistency", "tasks", type_="check")
    op.drop_constraint("ck_tasks_execution_generation_nonneg", "tasks", type_="check")
    op.drop_column("tasks", "lease_deadline_at")
    op.drop_column("tasks", "lease_expires_at")
    op.drop_column("tasks", "lease_owner")
    op.drop_column("tasks", "execution_generation")
