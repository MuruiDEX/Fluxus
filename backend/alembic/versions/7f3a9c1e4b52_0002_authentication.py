"""0002 authentication: users, memberships, auth_sessions.

Revision ID: 7f3a9c1e4b52
Revises: 54c46892a254
Create Date: 2026-10-10

Additive only: no existing table is altered, no data is touched.
Downgrade drops the three tables in dependency order; business data
is never deleted (no CASCADE into it from these tables' removal —
dropping memberships only removes access links, and downgrade is
intended for empty/staging schemas; production rollback = restore
from pg_dump backup taken before upgrade).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7f3a9c1e4b52"
down_revision: str | None = "54c46892a254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "memberships",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "business_id"),
    )
    op.create_index(
        op.f("ix_memberships_business_id"), "memberships", ["business_id"], unique=False
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_sessions_user_id"), "auth_sessions", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_auth_sessions_expires_at"), "auth_sessions", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_sessions_expires_at"), table_name="auth_sessions")
    op.drop_index(op.f("ix_auth_sessions_user_id"), table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index(op.f("ix_memberships_business_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
