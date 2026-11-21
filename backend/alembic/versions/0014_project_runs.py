"""Add durable project run intent. Existing projects remain unmanaged."""

import sqlalchemy as sa

from alembic import op

revision = "0014_project_runs"
down_revision = "0013_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_projects_business_id", "projects", ["business_id", "id"])
    op.create_unique_constraint("uq_tasks_project_id", "tasks", ["business_id", "project_id", "id"])
    op.create_table(
        "project_runs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("authorizing_user_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("blocker_id", sa.String(128)),
        sa.Column("active_task_id", sa.String(128)),
        sa.Column("active_task_generation", sa.Integer()),
        sa.Column("next_check_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "project_id"],
            ["projects.business_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_project_runs_project",
        ),
        sa.ForeignKeyConstraint(
            ["business_id", "project_id", "active_task_id"],
            ["tasks.business_id", "tasks.project_id", "tasks.id"],
            name="fk_project_runs_active_task",
        ),
        sa.CheckConstraint(
            "state IN ('running', 'waiting_approval', 'paused', 'needs_attention', "
            "'completed', 'cancelled')",
            name="ck_project_runs_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_project_runs_version"),
        sa.CheckConstraint(
            "(active_task_id IS NULL) = (active_task_generation IS NULL) "
            "AND (active_task_generation IS NULL OR active_task_generation > 0)",
            name="ck_project_runs_active_task",
        ),
    )
    op.create_index(
        "uq_project_runs_project", "project_runs", ["business_id", "project_id"], unique=True
    )
    op.create_index(
        "ix_project_runs_due",
        "project_runs",
        ["next_check_at"],
        postgresql_where=sa.text("next_check_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("project_runs")
    op.drop_constraint("uq_tasks_project_id", "tasks", type_="unique")
    op.drop_constraint("uq_projects_business_id", "projects", type_="unique")
