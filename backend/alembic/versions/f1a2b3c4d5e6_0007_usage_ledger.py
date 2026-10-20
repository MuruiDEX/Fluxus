"""0007 usage ledger + budget reservations + projects.allow_unpriced_ai.

Revision ID: f1a2b3c4d5e6
Revises: d8e6b4a2f0c7
Create Date: 2026-10-19

Additive only: two new tenant-scoped tables and one new NOT NULL column on
`projects` with a server default, so existing rows are backfilled to `false`
automatically. `usage_records` is append-only (guarded in the repository, not
the DB); `budget_reservations` carries mutable reservation state. Downgrade
drops only what this revision added.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d8e6b4a2f0c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "allow_unpriced_ai",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "usage_records",
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
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "agent_id",
            sa.String(128),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_key", sa.String(200), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("provider_request_id", sa.String(200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("cost_source", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("usage_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("tariff_version", sa.String(64), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "usage_status IN ('confirmed', 'estimated', 'unknown')",
            name="ck_usage_records_status",
        ),
        sa.CheckConstraint(
            "cost_source IN ('provider', 'tariff', 'unknown')",
            name="ck_usage_records_cost_source",
        ),
        sa.CheckConstraint(
            "cost_amount IS NULL OR cost_amount >= 0",
            name="ck_usage_records_cost_nonneg",
        ),
        sa.CheckConstraint(
            "usage_status <> 'confirmed' OR cost_amount IS NOT NULL",
            name="ck_usage_records_confirmed_has_cost",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_usage_records_idempotency"),
    )
    op.create_index("ix_usage_records_business_id", "usage_records", ["business_id"])
    op.create_index(
        "ix_usage_records_business_project", "usage_records", ["business_id", "project_id"]
    )
    op.create_index("ix_usage_records_business_task", "usage_records", ["business_id", "task_id"])
    op.create_index("ix_usage_records_attempt_key", "usage_records", ["attempt_key"])
    op.create_table(
        "budget_reservations",
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
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_key", sa.String(200), nullable=False),
        sa.Column("run_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("mode", sa.String(16), nullable=False, server_default="guaranteed"),
        sa.Column("usage_record_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'consumed', 'ambiguous', 'released', 'written_off')",
            name="ck_budget_reservations_status",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_budget_reservations_amount_nonneg"),
        sa.UniqueConstraint("idempotency_key", name="uq_budget_reservations_idempotency"),
    )
    op.create_index("ix_budget_reservations_business_id", "budget_reservations", ["business_id"])
    op.create_index(
        "ix_budget_reservations_business_project",
        "budget_reservations",
        ["business_id", "project_id"],
    )
    op.create_index(
        "ix_budget_reservations_attempt", "budget_reservations", ["business_id", "attempt_key"]
    )


def downgrade() -> None:
    op.drop_index("ix_budget_reservations_attempt", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_business_project", table_name="budget_reservations")
    op.drop_index("ix_budget_reservations_business_id", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_index("ix_usage_records_attempt_key", table_name="usage_records")
    op.drop_index("ix_usage_records_business_task", table_name="usage_records")
    op.drop_index("ix_usage_records_business_project", table_name="usage_records")
    op.drop_index("ix_usage_records_business_id", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_column("projects", "allow_unpriced_ai")
