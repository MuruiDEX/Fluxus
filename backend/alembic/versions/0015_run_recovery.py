"""Persist the recovered fence and one-shot retry authorization."""

import sqlalchemy as sa

from alembic import op

revision = "0015_run_recovery"
down_revision = "0014_project_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_runs", sa.Column("recovered_task_generation", sa.Integer()))
    op.add_column("project_runs", sa.Column("retry_task_id", sa.String(128)))
    op.add_column("project_runs", sa.Column("retry_task_generation", sa.Integer()))
    op.create_foreign_key(
        "fk_project_runs_retry_task",
        "project_runs",
        "tasks",
        ["business_id", "project_id", "retry_task_id"],
        ["business_id", "project_id", "id"],
    )
    op.create_check_constraint(
        "ck_project_runs_retry_task",
        "project_runs",
        "(retry_task_id IS NULL) = (retry_task_generation IS NULL) "
        "AND (retry_task_generation IS NULL OR retry_task_generation >= 0) "
        "AND (retry_task_id IS NULL OR active_task_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_project_runs_recovered_generation",
        "project_runs",
        "recovered_task_generation IS NULL OR (active_task_generation IS NOT NULL "
        "AND recovered_task_generation = active_task_generation + 1)",
    )
    op.execute(
        "UPDATE project_runs SET next_check_at = clock_timestamp() "
        "WHERE active_task_id IS NOT NULL AND reason_code = 'lease_recovery_required'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_project_runs_recovered_generation", "project_runs", type_="check")
    op.drop_constraint("ck_project_runs_retry_task", "project_runs", type_="check")
    op.drop_constraint("fk_project_runs_retry_task", "project_runs", type_="foreignkey")
    op.drop_column("project_runs", "retry_task_generation")
    op.drop_column("project_runs", "retry_task_id")
    op.drop_column("project_runs", "recovered_task_generation")
