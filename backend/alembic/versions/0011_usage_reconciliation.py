"""0011 manual usage reconciliation audit.

Revision ID: 0011_usage_reconciliation
Revises: 0010_execution_ownership
Create Date: 2026-11-06

Stage 18 S2.2.4.6 slice 4:

- Add `reconciled` to the budget-reservation lifecycle. The CHECK is replaced
  (drop + create) because the literal set is stored as VARCHAR + CHECK; no row
  is rewritten and no column/type changes.
- Add `usage_reconciliations`, the append-only audit trail for an operator-
  confirmed provider charge against an ambiguous task-execution hold. It links
  the exact reservation/task/attempt/generation scope to the single confirmed
  ledger row, the acting owner and the operator-supplied evidence.

Downgrade drops only what this revision added. It refuses to run while any
reservation is still `reconciled`: reverting the status CHECK would either fail
on existing rows or force silently discarding a confirmed charge/audit. Resolve
or export those rows first.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_usage_reconciliation"
down_revision: str | None = "0010_execution_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = "'active', 'consumed', 'ambiguous', 'released', 'written_off'"
_NEW_STATUSES = f"{_OLD_STATUSES}, 'reconciled'"


def upgrade() -> None:
    op.drop_constraint(
        "ck_budget_reservations_status",
        "budget_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_budget_reservations_status",
        "budget_reservations",
        f"status IN ({_NEW_STATUSES})",
    )

    op.create_table(
        "usage_reconciliations",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(128),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.String(128),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reservation_id",
            sa.String(128),
            sa.ForeignKey("budget_reservations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "usage_record_id",
            sa.String(128),
            sa.ForeignKey("usage_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_key", sa.String(200), nullable=False),
        sa.Column("execution_generation", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="charged"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("cost_source", sa.String(16), nullable=False, server_default="provider"),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("attested", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "actor_user_id",
            sa.String(128),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_email", sa.String(320), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('charged')",
            name="ck_usage_reconciliations_outcome",
        ),
        sa.CheckConstraint(
            "cost_source = 'provider'",
            name="ck_usage_reconciliations_cost_source",
        ),
        sa.CheckConstraint("amount > 0", name="ck_usage_reconciliations_amount_pos"),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0 "
            "AND (cached_tokens IS NULL OR cached_tokens >= 0) "
            "AND (reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name="ck_usage_reconciliations_tokens_nonneg",
        ),
        sa.CheckConstraint("attested", name="ck_usage_reconciliations_attested"),
        sa.UniqueConstraint("reservation_id", name="uq_usage_reconciliations_reservation"),
        sa.UniqueConstraint("usage_record_id", name="uq_usage_reconciliations_usage_record"),
    )
    op.create_index(
        "ix_usage_reconciliations_business_id",
        "usage_reconciliations",
        ["business_id"],
    )
    op.create_index(
        "ix_usage_reconciliations_business_project",
        "usage_reconciliations",
        ["business_id", "project_id"],
    )
    op.create_index(
        "ix_usage_reconciliations_business_task",
        "usage_reconciliations",
        ["business_id", "task_id"],
    )


def downgrade() -> None:
    reconciled = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM budget_reservations WHERE status = 'reconciled'")
    )
    if int(reconciled.scalar_one()) > 0:
        raise RuntimeError(
            "Cannot downgrade 0011: reconciled budget reservations exist. "
            "Export or explicitly resolve them first; a silent downgrade would "
            "discard confirmed-charge audit state."
        )

    op.drop_index(
        "ix_usage_reconciliations_business_task",
        table_name="usage_reconciliations",
    )
    op.drop_index(
        "ix_usage_reconciliations_business_project",
        table_name="usage_reconciliations",
    )
    op.drop_index(
        "ix_usage_reconciliations_business_id",
        table_name="usage_reconciliations",
    )
    op.drop_table("usage_reconciliations")

    op.drop_constraint(
        "ck_budget_reservations_status",
        "budget_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_budget_reservations_status",
        "budget_reservations",
        f"status IN ({_OLD_STATUSES})",
    )
