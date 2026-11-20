"""External integration persistence (Stage 19).

Three additive tables back the reusable integration layer:

- `integration_connections` — one authorized external account per business +
  provider. OAuth tokens are stored ONLY encrypted (see
  `app.integrations.crypto`); no plaintext token is ever persisted or logged.
- `integration_oauth_states` — short-lived, single-use authorization attempts.
  The raw state and PKCE verifier never leave the server after the redirect:
  the state is stored as a SHA-256 hash and the verifier encrypted.
- `integration_actions` — human-gated external side effects (send/draft).
  A capability only creates a `pending` row plus a real approval; the
  approval decision executes it later in its own transaction.

Content-bearing columns (message payloads) are encrypted too: an external
message is untrusted data and may contain sensitive business information.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.base import ID_LEN, Base, BusinessScopedMixin, IdMixin, TimestampMixin
from app.persistence.enums import (
    IntegrationActionStatus,
    IntegrationConnectionStatus,
    literal_check,
)


class IntegrationConnection(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "business_id",
            "provider",
            "external_account_id",
            name="uq_integration_connections_account",
        ),
        literal_check("status", IntegrationConnectionStatus, "ck_integration_connections_status"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    external_account_id: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(320), default="")
    status: Mapped[str] = mapped_column(
        String(32), default=IntegrationConnectionStatus.CONNECTED.value
    )
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    encrypted_access_token: Mapped[str] = mapped_column(Text, default="")
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(String(500), default="")
    connected_by_user_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )


class IntegrationOAuthState(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "integration_oauth_states"
    __table_args__ = (UniqueConstraint("state_hash", name="uq_integration_oauth_states_hash"),)

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    state_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_verifier_encrypted: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(String(500))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IntegrationAction(BusinessScopedMixin, IdMixin, TimestampMixin, Base):
    __tablename__ = "integration_actions"
    __table_args__ = (
        UniqueConstraint("approval_id", name="uq_integration_actions_approval"),
        literal_check("status", IntegrationActionStatus, "ck_integration_actions_status"),
    )

    business_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[str] = mapped_column(
        String(ID_LEN),
        ForeignKey("integration_connections.id", ondelete="CASCADE"),
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(
        String(ID_LEN), ForeignKey("approvals.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(ID_LEN), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    capability: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), default=IntegrationActionStatus.PENDING.value)
    payload_encrypted: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(String(500), default="")
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
