"""Concrete repositories вЂ” scope enforcement + reference validation.

Every cross-entity reference is resolved inside the same business_id before
write; violations raise DanglingReferenceError (в†’ 422 dangling_reference at
the API boundary). No business logic beyond integrity lives here.
"""

from datetime import UTC
from typing import Any

from sqlalchemy import case, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import (
    DanglingReferenceError,
    ImmutableFieldError,
    new_id,
    validate_id,
)
from app.persistence.models.activity_event import ActivityEvent
from app.persistence.models.agent import Agent
from app.persistence.models.approval import Approval
from app.persistence.models.auth_session import AuthSession
from app.persistence.models.brain_entry import BrainEntry
from app.persistence.models.business import Business
from app.persistence.models.marketplace_listing import MarketplaceListing
from app.persistence.models.membership import Membership
from app.persistence.models.project import Project
from app.persistence.models.task import Task, TaskDependency
from app.persistence.models.user import User
from app.persistence.repositories.base import BaseRepository, clamp_progress, quantize_money


class BusinessRepository(BaseRepository[Business]):
    model = Business
    scoped = False  # scope root: addressed by id directly

    async def get(self, session: AsyncSession, business_id: str, id: str) -> Business | None:
        if business_id != id:
            return None
        return await super().get(session, business_id, id)

    async def create(
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> Business:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else business_id or new_id()
        validate_id(payload["id"])
        payload.pop("business_id", None)
        obj = self.model(**payload)
        session.add(obj)
        await session.flush()
        return obj


class AgentRepository(BaseRepository[Agent]):
    model = Agent

    async def create(self, session: AsyncSession, business_id: str, data: dict[str, Any]) -> Agent:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        return await super().create(session, business_id, payload)


class ProjectRepository(BaseRepository[Project]):
    model = Project

    async def create(
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> Project:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        payload["progress"] = clamp_progress(payload.get("progress", 0))
        for money_key in ("budget", "ai_budget"):
            if money_key in payload and payload[money_key] is not None:
                payload[money_key] = quantize_money(payload[money_key])
        payload.pop("ai_spent", None)  # derived only вЂ” never accepted on write
        team_ids: list[str] = payload.get("team_ids", [])
        await self._require_agents(session, business_id, team_ids)
        return await super().create(session, business_id, payload)

    async def _require_agents(
        self, session: AsyncSession, business_id: str, team_ids: list[str]
    ) -> None:
        if not team_ids:
            return
        repo = AgentRepository()
        for agent_id in team_ids:
            if await repo.get(session, business_id, agent_id) is None:
                raise DanglingReferenceError(f"team_ids references missing agent: {agent_id}")


class TaskRepository(BaseRepository[Task]):
    model = Task

    async def create(self, session: AsyncSession, business_id: str, data: dict[str, Any]) -> Task:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        payload["progress"] = clamp_progress(payload.get("progress", 0))
        if "cost_accrued" in payload and payload["cost_accrued"] is not None:
            payload["cost_accrued"] = quantize_money(payload["cost_accrued"])
        if payload.get("cost_rate") is not None:
            payload["cost_rate"] = quantize_money(payload["cost_rate"])
        depends_on: list[str] = payload.pop("depends_on", [])
        await self._require_refs(session, business_id, payload)
        task = await super().create(session, business_id, payload)
        await self._link_dependencies(session, business_id, task.id, depends_on)
        return task

    async def _require_refs(
        self, session: AsyncSession, business_id: str, payload: dict[str, Any]
    ) -> None:
        project_id = payload.get("project_id")
        if await ProjectRepository().get(session, business_id, project_id) is None:
            raise DanglingReferenceError(f"task references missing project: {project_id}")
        owner_id = payload.get("owner_agent_id")
        if await AgentRepository().get(session, business_id, owner_id) is None:
            raise DanglingReferenceError(f"task references missing agent: {owner_id}")
        gate_id = payload.get("approval_gate_id")
        if (
            gate_id is not None
            and await ApprovalRepository().get(session, business_id, gate_id) is None
        ):
            raise DanglingReferenceError(f"task references missing approval gate: {gate_id}")

    async def _link_dependencies(
        self, session: AsyncSession, business_id: str, task_id: str, depends_on: list[str]
    ) -> None:
        for dep_id in depends_on:
            target = await self.get(session, business_id, dep_id)
            if target is None:
                raise DanglingReferenceError(f"task references missing dependency: {dep_id}")
            if dep_id == task_id:
                raise DanglingReferenceError("task cannot depend on itself")
            session.add(TaskDependency(task_id=task_id, depends_on_id=dep_id))
        await session.flush()

    async def claim_task(
        self,
        session: AsyncSession,
        business_id: str,
        task_id: str,
        *,
        from_statuses: tuple[str, ...],
        to_status: str,
    ) -> bool:
        """Atomic claim: single conditional UPDATE, no preceding read.

        Returns True iff this caller transitioned the row (rowcount==1).
        Concurrent claimants serialize on the row: exactly one wins, the rest
        see rowcount 0 and must re-read instead of assuming ownership.
        The caller MUST commit promptly and MUST NOT hold the transaction
        open across network calls.
        """
        for status in (*from_statuses, to_status):
            if not isinstance(status, str) or not status:
                raise ValueError(f"invalid status literal: {status!r}")
        stmt = (
            update(Task)
            .where(
                Task.business_id == business_id,
                Task.id == task_id,
                Task.status.in_(from_statuses),
            )
            # Explicit updated_at: bulk UPDATE does not fire column onupdate,
            # and the recovery staleness clock starts at claim time.
            .values(status=to_status, updated_at=func.now())
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1

    async def recover_stale(
        self,
        session: AsyncSession,
        business_id: str,
        task_id: str,
        *,
        stale_seconds: float,
    ) -> str | None:
        """Reset a provably-stale `working` row, atomically with audit intent.

        Safety (all in ONE conditional UPDATE, evaluated by the database):
        - row must be `working` (never touch a live run or a finished run);
        - `updated_at` must be older than `now() - stale_seconds`, compared in
          SQL so no application clock skew can shrink the window;
        - a concurrent holder's row lock makes this statement wait (bounded by
          statement_timeout) instead of proceeding — fail-closed, no reset.

        Reset target preserves budget semantics: attempts==0 (the stuck run
        never recorded a failure) → `not_started`; otherwise the `failed`
        history is kept and the attempts budget keeps applying.
        Returns the status the row was reset to, or None when the row did not
        qualify — in which case NOTHING was written, including no audit event.
        Caller appends the audit event in the same transaction on success.
        """
        stmt = (
            update(Task)
            .where(
                Task.business_id == business_id,
                Task.id == task_id,
                Task.status == "working",
                Task.updated_at < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, stale_seconds),
            )
            .values(
                status=case(
                    (Task.attempts == 0, "not_started"),
                    else_="failed",
                ),
                updated_at=func.now(),
            )
            .returning(Task.status)
        )
        result = await session.execute(stmt)
        await session.flush()
        row = result.first()
        return str(row[0]) if row else None


class ApprovalRepository(BaseRepository[Approval]):
    model = Approval

    async def create(
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> Approval:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        project_id = payload.get("project_id")
        if await ProjectRepository().get(session, business_id, project_id) is None:
            raise DanglingReferenceError(f"approval references missing project: {project_id}")
        requester = payload.get("requested_by_agent_id")
        if await AgentRepository().get(session, business_id, requester) is None:
            raise DanglingReferenceError(f"approval references missing agent: {requester}")
        gated = payload.get("gates_task_id")
        if gated is not None and await TaskRepository().get(session, business_id, gated) is None:
            raise DanglingReferenceError(f"approval references missing gated task: {gated}")
        return await super().create(session, business_id, payload)


class BrainRepository(BaseRepository[BrainEntry]):
    model = BrainEntry


class UserRepository:
    """Identity root — deliberately NOT business-scoped (users exist first)."""

    async def get(self, session: AsyncSession, user_id: str) -> User | None:
        return await session.get(User, user_id)

    async def get_by_email(self, session: AsyncSession, email: str) -> User | None:
        from sqlalchemy import select

        stmt = select(User).where(User.email == email)
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(
        self,
        session: AsyncSession,
        *,
        id: str,
        email: str,
        password_hash: str,
        display_name: str = "",
    ) -> User:
        validate_id(id)
        obj = User(id=id, email=email, password_hash=password_hash, display_name=display_name)
        session.add(obj)
        await session.flush()
        return obj

    async def set_password_hash(
        self, session: AsyncSession, user_id: str, password_hash: str
    ) -> bool:
        obj = await self.get(session, user_id)
        if obj is None:
            return False
        obj.password_hash = password_hash
        await session.flush()
        return True


class MembershipRepository:
    """Access links user ↔ business. All reads filter by caller side."""

    async def businesses_of(self, session: AsyncSession, user_id: str) -> list[Membership]:
        from sqlalchemy import select

        res = await session.execute(select(Membership).where(Membership.user_id == user_id))
        return list(res.scalars().all())

    async def has_access(self, session: AsyncSession, user_id: str, business_id: str) -> bool:
        from sqlalchemy import select

        res = await session.execute(
            select(Membership.role).where(
                Membership.user_id == user_id, Membership.business_id == business_id
            )
        )
        return res.scalar_one_or_none() is not None

    async def grant(
        self, session: AsyncSession, user_id: str, business_id: str, role: str = "member"
    ) -> Membership:
        if role not in ("owner", "member"):
            raise ValueError(f"invalid role: {role!r}")
        obj = Membership(user_id=user_id, business_id=business_id, role=role)
        session.add(obj)
        await session.flush()
        return obj

    async def revoke(self, session: AsyncSession, user_id: str, business_id: str) -> bool:
        from sqlalchemy import delete

        res = await session.execute(
            delete(Membership).where(
                Membership.user_id == user_id, Membership.business_id == business_id
            )
        )
        await session.flush()
        return (res.rowcount or 0) > 0


class AuthSessionRepository:
    """Server-side sessions. Lookup is ALWAYS by hash, never by raw id."""

    async def create(
        self,
        session: AsyncSession,
        *,
        id_hash: str,
        user_id: str,
        csrf_token: str,
        created_at,
        expires_at,
    ) -> AuthSession:
        obj = AuthSession(
            id=id_hash,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=created_at,
            expires_at=expires_at,
        )
        session.add(obj)
        await session.flush()
        return obj

    async def get_valid(self, session: AsyncSession, id_hash: str, now):
        """Row iff usable: exists, not revoked, not expired. Otherwise None."""
        from sqlalchemy import select

        res = await session.execute(select(AuthSession).where(AuthSession.id == id_hash))
        obj = res.scalar_one_or_none()
        if obj is None or obj.revoked_at is not None or obj.expires_at <= now:
            return None
        return obj

    async def revoke(self, session: AsyncSession, id_hash: str) -> bool:
        from datetime import datetime

        from sqlalchemy import update

        res = await session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == id_hash,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await session.flush()
        return (res.rowcount or 0) > 0


class MarketplaceRepository(BaseRepository[MarketplaceListing]):
    model = MarketplaceListing
    scoped = False  # global catalog

    async def get(
        self, session: AsyncSession, business_id: str, id: str
    ) -> MarketplaceListing | None:
        return await super().get(session, business_id, id)

    async def update(
        self, session: AsyncSession, business_id: str, id: str, patch: dict[str, Any]
    ) -> MarketplaceListing | None:
        if "id" in patch and patch["id"] != id:
            raise ImmutableFieldError("marketplace slug is immutable")
        clean = {k: v for k, v in patch.items() if k != "id"}
        return await super().update(session, business_id, id, clean)


class ActivityRepository:
    """Append-only: create + scoped list. No update, no delete вЂ” by design."""

    async def append(
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> ActivityEvent:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        if payload.get("cost") is not None:
            payload["cost"] = quantize_money(payload["cost"])
        payload["business_id"] = business_id
        event = ActivityEvent(**payload)
        session.add(event)
        await session.flush()
        return event

    async def list(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        limit: int = 200,
    ) -> list[ActivityEvent]:
        from sqlalchemy import select

        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.business_id == business_id)
            .order_by(ActivityEvent.created_at.desc())
            .limit(limit)
        )
        if project_id is not None:
            stmt = stmt.where(ActivityEvent.project_id == project_id)
        res = await session.execute(stmt)
        return list(res.scalars().all())
