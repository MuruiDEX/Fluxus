"""Integration repositories — business-scoped, no business logic.

Every read/write carries `business_id`; cross-tenant rows are indistinguishable
from missing rows. Token columns are only ever written with ciphertext
produced by `app.integrations.crypto`.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.types import ConnectedAccount, TokenBundle
from app.persistence.enums import IntegrationActionStatus, IntegrationConnectionStatus
from app.persistence.exceptions import new_id
from app.persistence.models.integration import (
    IntegrationAction,
    IntegrationConnection,
    IntegrationOAuthState,
)
from app.persistence.repositories.base import BaseRepository


class _GeneratedIdRepository[M](BaseRepository[M]):
    """Integration rows use server-generated ids from the contract charset."""

    async def create(  # type: ignore[override]
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> M:
        payload = dict(data)
        if not payload.get("id"):
            payload["id"] = new_id()
        return await super().create(session, business_id, payload)


class IntegrationConnectionRepository(_GeneratedIdRepository[IntegrationConnection]):
    model = IntegrationConnection

    async def lock_connection(
        self, session: AsyncSession, business_id: str, connection_id: str
    ) -> IntegrationConnection | None:
        return await session.scalar(
            select(IntegrationConnection)
            .where(
                IntegrationConnection.business_id == business_id,
                IntegrationConnection.id == connection_id,
            )
            .with_for_update()
        )

    async def list_for_business(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        provider: str | None = None,
    ) -> list[IntegrationConnection]:
        stmt = (
            select(IntegrationConnection)
            .where(IntegrationConnection.business_id == business_id)
            .order_by(IntegrationConnection.provider, IntegrationConnection.created_at)
        )
        if provider is not None:
            stmt = stmt.where(IntegrationConnection.provider == provider)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def get_by_account(
        self,
        session: AsyncSession,
        business_id: str,
        provider: str,
        external_account_id: str,
    ) -> IntegrationConnection | None:
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.external_account_id == external_account_id,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def usable_for_capability(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        provider: str,
        connection_id: str | None = None,
    ) -> IntegrationConnection | None:
        """Resolve the account a capability may use.

        An explicit id must belong to this business AND be connected. Without
        an id, exactly one connected account is required; with several the
        caller must choose (never guess across accounts).
        """
        if connection_id is not None:
            connection = await self.get(session, business_id, connection_id)
            if (
                connection is None
                or connection.provider != provider
                or connection.status != IntegrationConnectionStatus.CONNECTED.value
            ):
                return None
            return connection
        stmt = (
            select(IntegrationConnection)
            .where(
                IntegrationConnection.business_id == business_id,
                IntegrationConnection.provider == provider,
                IntegrationConnection.status == IntegrationConnectionStatus.CONNECTED.value,
            )
            .order_by(IntegrationConnection.created_at.desc())
        )
        res = await session.execute(stmt)
        rows = list(res.scalars().all())
        return rows[0] if len(rows) == 1 else None

    async def count_connected(
        self, session: AsyncSession, business_id: str, *, provider: str
    ) -> int:
        rows = await self.list_for_business(session, business_id, provider=provider)
        return sum(1 for row in rows if row.status == IntegrationConnectionStatus.CONNECTED.value)

    async def upsert_connected(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        provider: str,
        account: ConnectedAccount,
        bundle: TokenBundle,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
        connected_by_user_id: str,
    ) -> IntegrationConnection:
        existing = await self.get_by_account(
            session, business_id, provider, account.external_account_id
        )
        now = datetime.now(UTC)
        if existing is None:
            connection = await self.create(
                session,
                business_id,
                {
                    "provider": provider,
                    "external_account_id": account.external_account_id,
                    "display_name": account.display_name,
                    "status": IntegrationConnectionStatus.CONNECTED.value,
                    "scopes": list(bundle.scopes),
                    "encrypted_access_token": encrypted_access_token,
                    "encrypted_refresh_token": encrypted_refresh_token,
                    "token_expires_at": bundle.expires_at,
                    "last_refreshed_at": now,
                    "last_error": "",
                    "connected_by_user_id": connected_by_user_id,
                },
            )
            return connection
        existing.display_name = account.display_name
        existing.status = IntegrationConnectionStatus.CONNECTED.value
        existing.scopes = list(bundle.scopes)
        existing.encrypted_access_token = encrypted_access_token
        if encrypted_refresh_token:
            existing.encrypted_refresh_token = encrypted_refresh_token
        existing.token_expires_at = bundle.expires_at
        existing.last_refreshed_at = now
        existing.last_error = ""
        existing.connected_by_user_id = connected_by_user_id
        await session.flush()
        return existing

    async def mark_connected(
        self,
        session: AsyncSession,
        business_id: str,
        connection_id: str,
        *,
        bundle: TokenBundle,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None,
    ) -> IntegrationConnection | None:
        connection = await self.get(session, business_id, connection_id)
        if connection is None:
            return None
        connection.status = IntegrationConnectionStatus.CONNECTED.value
        connection.encrypted_access_token = encrypted_access_token
        if encrypted_refresh_token:
            connection.encrypted_refresh_token = encrypted_refresh_token
        connection.token_expires_at = bundle.expires_at
        connection.last_refreshed_at = datetime.now(UTC)
        connection.last_error = ""
        if bundle.scopes:
            connection.scopes = list(bundle.scopes)
        await session.flush()
        return connection

    async def mark_error(
        self, session: AsyncSession, business_id: str, connection_id: str, message: str
    ) -> None:
        connection = await self.get(session, business_id, connection_id)
        if connection is None:
            return
        connection.status = IntegrationConnectionStatus.ERROR.value
        connection.last_error = message[:500]
        await session.flush()

    async def note_error(
        self, session: AsyncSession, business_id: str, connection_id: str, message: str
    ) -> None:
        """Record a transient error without making a healthy link unusable."""
        connection = await self.get(session, business_id, connection_id)
        if connection is None:
            return
        connection.last_error = message[:500]
        await session.flush()

    async def revoke(self, session: AsyncSession, business_id: str, connection_id: str) -> bool:
        """Wipe credentials and make the connection permanently unusable."""
        connection = await self.get(session, business_id, connection_id)
        if connection is None:
            return False
        connection.status = IntegrationConnectionStatus.REVOKED.value
        connection.encrypted_access_token = ""
        connection.encrypted_refresh_token = None
        connection.token_expires_at = None
        connection.last_error = ""
        await session.flush()
        return True


class IntegrationOAuthStateRepository(_GeneratedIdRepository[IntegrationOAuthState]):
    model = IntegrationOAuthState

    async def consume(
        self,
        session: AsyncSession,
        business_id: str,
        user_id: str,
        provider: str,
        state_hash: str,
        *,
        now: datetime,
    ) -> IntegrationOAuthState | None:
        """Single-use claim: lock the row, check expiry/owner, mark consumed."""
        stmt = (
            select(IntegrationOAuthState)
            .where(
                IntegrationOAuthState.business_id == business_id,
                IntegrationOAuthState.user_id == user_id,
                IntegrationOAuthState.provider == provider,
                IntegrationOAuthState.state_hash == state_hash,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None or row.consumed_at is not None or row.expires_at <= now:
            return None
        row.consumed_at = now
        await session.flush()
        return row

    async def consume_by_hash(
        self,
        session: AsyncSession,
        user_id: str,
        state_hash: str,
        *,
        now: datetime,
    ) -> IntegrationOAuthState | None:
        """Claim a state without knowing its business up front.

        The OAuth callback only receives the opaque state; the row itself is
        the authority for business/provider. The caller must still be the same
        authenticated user that started the flow.
        """
        stmt = (
            select(IntegrationOAuthState)
            .where(
                IntegrationOAuthState.user_id == user_id,
                IntegrationOAuthState.state_hash == state_hash,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        row = res.scalar_one_or_none()
        if row is None or row.consumed_at is not None or row.expires_at <= now:
            return None
        row.consumed_at = now
        await session.flush()
        return row


class IntegrationActionRepository(_GeneratedIdRepository[IntegrationAction]):
    model = IntegrationAction

    async def get_for_approval(
        self, session: AsyncSession, business_id: str, approval_id: str, *, lock: bool = False
    ) -> IntegrationAction | None:
        stmt = select(IntegrationAction).where(
            IntegrationAction.business_id == business_id,
            IntegrationAction.approval_id == approval_id,
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def report_facts(
        self, session: AsyncSession, business_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        rows = await session.execute(
            select(
                IntegrationAction.id,
                IntegrationAction.approval_id,
                IntegrationAction.task_id,
                IntegrationAction.capability,
                IntegrationAction.status,
            )
            .where(
                IntegrationAction.business_id == business_id,
                IntegrationAction.project_id == project_id,
            )
            .order_by(IntegrationAction.id)
        )
        return [dict(row) for row in rows.mappings()]

    async def claim(
        self, session: AsyncSession, business_id: str, approval_id: str
    ) -> IntegrationAction | None:
        """Atomically move pending → executing. Only one caller can win."""
        result = await session.execute(
            update(IntegrationAction)
            .where(
                IntegrationAction.business_id == business_id,
                IntegrationAction.approval_id == approval_id,
                IntegrationAction.status == IntegrationActionStatus.PENDING.value,
            )
            .values(status=IntegrationActionStatus.EXECUTING.value)
            .execution_options(synchronize_session=False)
        )
        if (result.rowcount or 0) != 1:
            return None
        await session.flush()
        return await self.get_for_approval(session, business_id, approval_id)

    async def mark_rejected(
        self, session: AsyncSession, business_id: str, approval_id: str
    ) -> bool:
        result = await session.execute(
            update(IntegrationAction)
            .where(
                IntegrationAction.business_id == business_id,
                IntegrationAction.approval_id == approval_id,
                IntegrationAction.status == IntegrationActionStatus.PENDING.value,
            )
            .values(
                status=IntegrationActionStatus.REJECTED.value,
                error_code="approval_rejected",
                error_message="The owner rejected this external action.",
            )
            .execution_options(synchronize_session=False)
        )
        return (result.rowcount or 0) == 1

    async def finish(
        self,
        session: AsyncSession,
        business_id: str,
        action_id: str,
        *,
        status: IntegrationActionStatus,
        result: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> IntegrationAction | None:
        action = await self.get(session, business_id, action_id)
        if action is None:
            return None
        action.status = status.value
        action.result = result or {}
        action.error_code = error_code[:64]
        action.error_message = error_message[:500]
        action.executed_at = datetime.now(UTC)
        await session.flush()
        return action


__all__ = [
    "IntegrationActionRepository",
    "IntegrationConnectionRepository",
    "IntegrationOAuthStateRepository",
]
