"""0003 briefs: Team-Lead intake records.

Revision ID: a3f5c7e9b1d2
Revises: 7f3a9c1e4b52
Create Date: 2026-10-11

Additive only: one new table, no existing table altered, no data touched.
Status CHECK covers the full brief lifecycle up front (draft through
failed) so later Stage 8 phases never need a constraint migration.
Downgrade drops only the briefs table; all business data is preserved.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3f5c7e9b1d2"
down_revision: str | None = "7f3a9c1e4b52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BRIEF_STATUSES = (
    "draft",
    "questions_ready",
    "brief_ready",
    "planned",
    "running",
    "completed",
    "failed",
)


def upgrade() -> None:
    op.create_table(
        "briefs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(repr(s) for s in _BRIEF_STATUSES)),
            name="ck_briefs_status",
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_briefs_business_id"), "briefs", ["business_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_briefs_business_id"), table_name="briefs")
    op.drop_table("briefs")
