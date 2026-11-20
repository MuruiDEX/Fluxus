"""0013 external integrations (connections, OAuth state, gated actions).

Revision ID: 0013_integrations
Revises: 0012_no_charge_reconciliation
Create Date: 2026-11-18

Stage 19 adds the reusable external-integration persistence layer:

- `integration_connections`: one authorized external account per business and
  provider, with encrypted access/refresh tokens, granted scopes, health status
  and the user who connected it. A unique key prevents duplicate accounts.
- `integration_oauth_states`: single-use OAuth attempts. Only the SHA-256 hash
  of the state is stored; the PKCE verifier is encrypted and expires quickly.
- `integration_actions`: human-gated external side effects. The payload is
  encrypted and the row starts `pending`; only an approved approval can claim
  it. `unknown` records an ambiguous provider outcome that is never retried
  automatically.

Additive only: no existing table or constraint is modified.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_integrations"
down_revision: str | None = "0012_no_charge_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_connections",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(128),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_account_id", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(320), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="connected"),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
        sa.Column(
            "connected_by_user_id",
            sa.String(128),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "business_id",
            "provider",
            "external_account_id",
            name="uq_integration_connections_account",
        ),
        sa.CheckConstraint(
            "status IN ('connected', 'revoked', 'error')",
            name="ck_integration_connections_status",
        ),
    )
    op.create_index(
        "ix_integration_connections_business_id",
        "integration_connections",
        ["business_id"],
    )
    op.create_index(
        "ix_integration_connections_provider",
        "integration_connections",
        ["provider"],
    )
    op.create_index(
        "ix_integration_connections_connected_by_user_id",
        "integration_connections",
        ["connected_by_user_id"],
    )

    op.create_table(
        "integration_oauth_states",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "business_id",
            sa.String(128),
            sa.ForeignKey("businesses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(128),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("code_verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("state_hash", name="uq_integration_oauth_states_hash"),
    )
    op.create_index(
        "ix_integration_oauth_states_business_id",
        "integration_oauth_states",
        ["business_id"],
    )
    op.create_index(
        "ix_integration_oauth_states_user_id",
        "integration_oauth_states",
        ["user_id"],
    )
    op.create_index(
        "ix_integration_oauth_states_state_hash",
        "integration_oauth_states",
        ["state_hash"],
    )

    op.create_table(
        "integration_actions",
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
            "connection_id",
            sa.String(128),
            sa.ForeignKey("integration_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            sa.String(128),
            sa.ForeignKey("approvals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.String(128),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("capability", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("payload_encrypted", sa.Text(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error_message", sa.String(500), nullable=False, server_default=""),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("approval_id", name="uq_integration_actions_approval"),
        sa.CheckConstraint(
            "status IN ('pending', 'executing', 'succeeded', 'failed', 'rejected', 'unknown')",
            name="ck_integration_actions_status",
        ),
    )
    op.create_index("ix_integration_actions_business_id", "integration_actions", ["business_id"])
    op.create_index("ix_integration_actions_project_id", "integration_actions", ["project_id"])
    op.create_index(
        "ix_integration_actions_connection_id", "integration_actions", ["connection_id"]
    )
    op.create_index("ix_integration_actions_approval_id", "integration_actions", ["approval_id"])


def downgrade() -> None:
    op.drop_table("integration_actions")
    op.drop_table("integration_oauth_states")
    op.drop_table("integration_connections")
