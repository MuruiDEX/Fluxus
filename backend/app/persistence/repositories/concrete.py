"""Concrete repositories вЂ” scope enforcement + reference validation.

Every cross-entity reference is resolved inside the same business_id before
write; violations raise DanglingReferenceError (в†’ 422 dangling_reference at
the API boundary). No business logic beyond integrity lives here.
"""

from datetime import UTC
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.exceptions import (
    AgentDisabledError,
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
from app.persistence.models.brief import Brief
from app.persistence.models.business import Business
from app.persistence.models.marketplace_listing import MarketplaceListing
from app.persistence.models.membership import Membership
from app.persistence.models.pack import PackInstallation, PackInstallationItem
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

    async def hired_slugs(self, session: AsyncSession, business_id: str) -> set[str]:
        """Slugs this business already hired (one query for the whole set).

        Used by the marketplace read API to mark listings — list endpoints
        must not fan out per row.
        """
        from sqlalchemy import select

        stmt = select(Agent.source_slug).where(
            Agent.business_id == business_id, Agent.source_slug.isnot(None)
        )
        res = await session.execute(stmt)
        return {row for row in res.scalars().all() if row}

    async def set_hired(
        self, session: AsyncSession, business_id: str, agent_id: str, hired: bool
    ) -> Agent | None:
        """Activate/deactivate an agent in scope (idempotent, no-op if same).

        Returns the row, or None when missing/foreign (callers map to
        uniform 404). Disabled agents keep every row, task link, and audit
        event — only future staffing is refused elsewhere.
        """
        agent = await self.get(session, business_id, agent_id)
        if agent is None:
            return None
        agent.hired = bool(hired)
        await session.flush()
        return agent

    @staticmethod
    def require_active(agent: Agent) -> None:
        """Staffing gate: disabled rows cannot take new work (any caller)."""
        if not agent.hired:
            raise AgentDisabledError(f"agent {agent.id!r} is disabled")


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
            agent = await repo.get(session, business_id, agent_id)
            if agent is None:
                raise DanglingReferenceError(f"team_ids references missing agent: {agent_id}")
            AgentRepository.require_active(agent)


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
        owner = await AgentRepository().get(session, business_id, owner_id)
        if owner is None:
            raise DanglingReferenceError(f"task references missing agent: {owner_id}")
        AgentRepository.require_active(owner)
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

    async def list_for_project(
        self,
        session: AsyncSession,
        business_id: str,
        project_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Task]:
        """Tasks of one project, stable id order (read API paging)."""
        from sqlalchemy import select

        stmt = (
            select(Task)
            .where(Task.business_id == business_id, Task.project_id == project_id)
            .order_by(Task.id)
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def count_for_project(
        self, session: AsyncSession, business_id: str, project_id: str
    ) -> int:
        from sqlalchemy import select

        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.business_id == business_id, Task.project_id == project_id)
        )
        res = await session.execute(stmt)
        return int(res.scalar_one())

    async def dependency_map(
        self, session: AsyncSession, task_ids: list[str]
    ) -> dict[str, list[str]]:
        """Outgoing edges for the given tasks, in stable order.

        One query for the whole set (list endpoints must not fan out per
        row). Callers pass ids of already scope-checked tasks, so no
        business filter is applied here — edges only ever reference tasks
        created in the same project scope.
        """
        from sqlalchemy import select

        if not task_ids:
            return {}
        stmt = (
            select(TaskDependency.task_id, TaskDependency.depends_on_id)
            .where(TaskDependency.task_id.in_(task_ids))
            .order_by(TaskDependency.task_id, TaskDependency.depends_on_id)
        )
        res = await session.execute(stmt)
        edges: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
        for task_id, depends_on_id in res.all():
            edges.setdefault(task_id, []).append(depends_on_id)
        return edges


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

    async def list_for_project(
        self,
        session: AsyncSession,
        business_id: str,
        project_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Approval]:
        """Approvals of one project, stable id order (read API paging)."""
        from sqlalchemy import select

        stmt = (
            select(Approval)
            .where(Approval.business_id == business_id, Approval.project_id == project_id)
            .order_by(Approval.id)
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def decide(
        self,
        session: AsyncSession,
        business_id: str,
        approval_id: str,
        *,
        to_status: str,
    ) -> bool:
        """Atomic decision: single conditional UPDATE from pending only.

        Returns True iff this caller transitioned the row (rowcount==1).
        Concurrent deciders serialize on the row: exactly one wins, the rest
        see rowcount 0 and must re-read instead of assuming ownership — the
        same pattern as TaskRepository.claim_task. The caller MUST write the
        audit event in the SAME transaction and commit promptly.
        """
        from app.persistence.enums import ApprovalStatus

        allowed = (ApprovalStatus.APPROVED.value, ApprovalStatus.REJECTED.value)
        if to_status not in allowed:
            raise ValueError(f"invalid decision: {to_status!r}")
        stmt = (
            update(Approval)
            .where(
                Approval.business_id == business_id,
                Approval.id == approval_id,
                Approval.status == ApprovalStatus.PENDING.value,
            )
            # Explicit updated_at: bulk UPDATE does not fire column onupdate.
            .values(status=to_status, updated_at=func.now())
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1


class BrainRepository(BaseRepository[BrainEntry]):
    model = BrainEntry

    async def list(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        category: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BrainEntry]:
        """Entries of one business, stable id order; optional category filter."""
        from sqlalchemy import select

        stmt = select(BrainEntry).where(BrainEntry.business_id == business_id)
        if category is not None:
            stmt = stmt.where(BrainEntry.category == category)
        stmt = stmt.order_by(BrainEntry.id).limit(limit).offset(offset)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def count(
        self, session: AsyncSession, business_id: str, *, category: str | None = None
    ) -> int:
        from sqlalchemy import select

        stmt = (
            select(func.count())
            .select_from(BrainEntry)
            .where(BrainEntry.business_id == business_id)
        )
        if category is not None:
            stmt = stmt.where(BrainEntry.category == category)
        res = await session.execute(stmt)
        return int(res.scalar_one())


class BriefRepository(BaseRepository[Brief]):
    model = Brief

    async def create(self, session: AsyncSession, business_id: str, data: dict[str, Any]) -> Brief:
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        goal = payload.get("goal", "")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        payload["goal"] = goal.strip()
        return await super().create(session, business_id, payload)

    async def save_answers(
        self,
        session: AsyncSession,
        business_id: str,
        brief_id: str,
        *,
        answers: list[dict],
        summary: dict,
    ) -> bool:
        """Atomic finalize: single conditional UPDATE from questions_ready only.

        Returns True iff this caller transitioned the row (rowcount==1).
        Concurrent finalizers serialize on the row: exactly one wins, the
        rest see rowcount 0 and must re-read instead of assuming ownership —
        the same pattern as TaskRepository.claim_task. The caller MUST commit
        promptly; answers and summary are already validated.
        """
        from app.persistence.enums import BriefStatus

        stmt = (
            update(Brief)
            .where(
                Brief.business_id == business_id,
                Brief.id == brief_id,
                Brief.status == BriefStatus.QUESTIONS_READY.value,
            )
            # Explicit updated_at: bulk UPDATE does not fire column onupdate.
            .values(
                answers=answers,
                summary=summary,
                status=BriefStatus.BRIEF_READY.value,
                updated_at=func.now(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1

    async def mark_planned(
        self,
        session: AsyncSession,
        business_id: str,
        brief_id: str,
        *,
        project_id: str,
    ) -> bool:
        """Atomic plan commit: single conditional UPDATE from brief_ready only.

        Returns True iff this caller transitioned the row (rowcount==1).
        Concurrent planners serialize on the row: exactly one wins, the rest
        see rowcount 0 and must re-read instead of assuming ownership — the
        same pattern as save_answers / TaskRepository.claim_task. The caller
        creates the project graph in the SAME transaction before this call,
        so a win means graph + brief transition commit together, and a loss
        rolls the whole graph back with it.
        """
        from app.persistence.enums import BriefStatus

        stmt = (
            update(Brief)
            .where(
                Brief.business_id == business_id,
                Brief.id == brief_id,
                Brief.status == BriefStatus.BRIEF_READY.value,
            )
            # Explicit updated_at: bulk UPDATE does not fire column onupdate.
            .values(
                project_id=project_id,
                status=BriefStatus.PLANNED.value,
                updated_at=func.now(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1

    async def mark_completed(
        self,
        session: AsyncSession,
        business_id: str,
        brief_id: str,
        *,
        final_report: dict,
    ) -> bool:
        """Atomic finalization claim: single conditional UPDATE from planned only.

        Returns True iff this caller transitioned the row (rowcount==1).
        Concurrent finalizers serialize on the row: exactly one wins, the rest
        see rowcount 0 and must re-read instead of assuming ownership — the
        same pattern as mark_planned / save_answers. The caller persists the
        report envelope, Brain entries, and audit event in the SAME
        transaction before this call, so a win commits everything together
        and a loss rolls everything back.
        """
        from app.persistence.enums import BriefStatus

        stmt = (
            update(Brief)
            .where(
                Brief.business_id == business_id,
                Brief.id == brief_id,
                Brief.status == BriefStatus.PLANNED.value,
            )
            # Explicit updated_at: bulk UPDATE does not fire column onupdate.
            .values(
                final_report=final_report,
                status=BriefStatus.COMPLETED.value,
                updated_at=func.now(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1


class PlanningRepository:
    """Atomic work-plan materialization (Stage 8.3).

    `create_plan_graph` turns a validated + normalized plan into a project,
    its tasks, dependency edges, and gate approvals. The caller MUST hold one
    database transaction around this call plus `BriefRepository.mark_planned`:
    any error rolls the whole graph back, and the brief transition commits
    if and only if the graph does.

    Insert ordering is explicit, never assumed from a single flush: every
    referenced row is flushed by its repository create before anything points
    at it (project → gateless/edgeless tasks → approvals → gate links +
    edges). Tasks and approvals reference each other through two nullable FKs
    (`tasks.approval_gate_id`, `approvals.gates_task_id`), so the links land
    last, by direct attribute assignment on same-session ORM objects.
    """

    async def create_plan_graph(
        self, session: AsyncSession, business_id: str, plan: dict[str, Any]
    ) -> tuple[Project, list[Task], list[Approval]]:
        raw_project = plan["project"]
        plan_tasks = plan["tasks"]
        owners = sorted({t["owner_agent_id"] for t in plan_tasks})
        requesters = sorted({t["gate"]["requested_by_agent_id"] for t in plan_tasks if "gate" in t})
        agents = AgentRepository()
        for agent_id in (*owners, *requesters):
            if await agents.get(session, business_id, agent_id) is None:
                raise DanglingReferenceError(f"plan references missing agent: {agent_id}")
        projects = ProjectRepository()
        project_payload: dict[str, Any] = {
            "title": raw_project["title"],
            "goal": raw_project.get("goal", ""),
            "deadline": raw_project.get("deadline", ""),
            "team_ids": owners,
        }
        for money_key in ("budget", "ai_budget"):
            if money_key in raw_project:
                project_payload[money_key] = raw_project[money_key]
        project = await projects.create(session, business_id, project_payload)

        tasks_repo = TaskRepository()
        made: list[Task] = []
        by_key: dict[str, Task] = {}
        for item in plan_tasks:
            task_payload: dict[str, Any] = {
                "project_id": project.id,
                "title": item["title"],
                "summary": item.get("summary", ""),
                "eta_minutes": item.get("eta_minutes", 0),
                "gate_at": item.get("gate_at", 100),
                "owner_agent_id": item["owner_agent_id"],
            }
            task = await tasks_repo.create(session, business_id, task_payload)
            made.append(task)
            by_key[item["key"]] = task

        approvals_repo = ApprovalRepository()
        made_approvals: list[Approval] = []
        for item, task in zip(plan_tasks, made, strict=True):
            gate = item.get("gate")
            if gate is None:
                continue
            approval_payload: dict[str, Any] = {
                "project_id": project.id,
                "title": gate["title"],
                "requested_by_agent_id": gate["requested_by_agent_id"],
                "requested_by": gate.get("requested_by", ""),
                "what": gate.get("what", ""),
                "expected_result": gate.get("expected_result", ""),
                "cost": gate.get("cost", ""),
                "affects": gate.get("affects", ""),
                "rationale": gate.get("rationale", ""),
                "gates_task_id": task.id,
            }
            approval = await approvals_repo.create(session, business_id, approval_payload)
            task.approval_gate_id = approval.id
            made_approvals.append(approval)

        for item in plan_tasks:
            task = by_key[item["key"]]
            for dep_key in item.get("depends_on", []):
                session.add(TaskDependency(task_id=task.id, depends_on_id=by_key[dep_key].id))
        await session.flush()
        return project, made, made_approvals


class FinalizeNotEligible(Exception):
    """Persisted state does not permit finalization (maps to 409 conflict)."""


class FinalizationRepository:
    """Atomic brief finalization (Stage 8.5).

    `finalize` revalidates the authoritative state inside the caller's
    transaction, persists the validated report envelope + Brain entries +
    audit event, and claims planned → completed via one conditional UPDATE.
    Returns the finalization result, or None when the CAS lost (caller
    rolled back, must re-read). Raises FinalizeNotEligible when the
    persisted graph is not terminally complete — nothing is written then
    either. Any error rolls the whole batch back: never a completed brief
    without its report, never a report with partial Brain entries.
    """

    _TERMINAL_TASKS = ("completed", "failed")
    _TERMINAL_APPROVALS = ("approved", "rejected")

    async def finalize(
        self, session: AsyncSession, business_id: str, brief_id: str, *, result: dict
    ) -> tuple[dict, list[BrainEntry], ActivityEvent] | None:
        briefs = BriefRepository()
        brief = await briefs.get(session, business_id, brief_id)
        if brief is None:
            return None
        if brief.status == "completed":
            # A concurrent finalizer won — serve the stored result instead.
            return None
        if brief.status != "planned":
            raise FinalizeNotEligible(f"Brief is {brief.status}, not finalizable.")
        if not brief.project_id:
            raise FinalizeNotEligible("Brief has no project to finalize.")
        project = await ProjectRepository().get(session, business_id, brief.project_id)
        if project is None:
            raise FinalizeNotEligible("Brief project is missing in scope.")
        tasks = await TaskRepository().list_for_project(session, business_id, project.id)
        for task in tasks:
            if task.status not in self._TERMINAL_TASKS:
                raise FinalizeNotEligible(f"Task {task.id} is {task.status}, not terminal.")
        approvals = await ApprovalRepository().list_for_project(session, business_id, project.id)
        for approval in approvals:
            if approval.status not in self._TERMINAL_APPROVALS:
                raise FinalizeNotEligible(
                    f"Approval {approval.id} is {approval.status}, not terminal."
                )
        brains: list[BrainEntry] = []
        for learning in result["learnings"]:
            brains.append(
                await BrainRepository().create(
                    session,
                    business_id,
                    {
                        "id": new_id(),
                        "category": learning["category"],
                        "title": learning["title"],
                        "body": learning["body"],
                    },
                )
            )
        completed = sum(1 for t in tasks if t.status == "completed")
        # Snapshot semantics (Stage 16.1): the metering layer settles the
        # finalize call BEFORE this transaction, so `ai_spent` (confirmed only,
        # task-linked + task-less project-scoped) already includes it. Estimated
        # cost is reported separately and never folded into ai_spent.
        from app.persistence.repositories.usage import UsageRepository

        status_totals = await UsageRepository().totals_by_status(
            session, business_id, project_id=project.id
        )
        ai_estimated = status_totals.get("estimated", (Decimal("0"), 0))[0]
        costs = {
            "budget": str(project.budget),
            "ai_budget": str(project.ai_budget),
            "ai_spent": str(project.ai_spent),
            "ai_estimated": str(ai_estimated),
            "note": (
                "Confirmed AI spend from the usage ledger (task-linked plus "
                "project-scoped Team Lead calls, including this finalization). "
                "Estimated cost is tracked separately and never presented as "
                "confirmed spend."
            ),
        }
        envelope = {
            "project_id": project.id,
            "report": result["report"],
            "learnings": [
                {"id": b.id, "category": b.category, "title": b.title, "body": b.body}
                for b in brains
            ],
            "costs": costs,
        }
        won = await briefs.mark_completed(session, business_id, brief_id, final_report=envelope)
        if not won:
            return None
        event = await ActivityRepository().append(
            session,
            business_id,
            {
                "project_id": project.id,
                "agent_name": "Team Lead",
                "kind": "result",
                "text": (
                    f"Brief {brief_id} finalized: {completed} of {len(tasks)} "
                    f"tasks completed, {len(brains)} learnings saved."
                ),
            },
        )
        return envelope, brains, event


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


class PackInstallationRepository(BaseRepository[PackInstallation]):
    """Installation receipts are immutable: create, add items, and read."""

    model = PackInstallation

    async def get_by_slug(
        self, session: AsyncSession, business_id: str, pack_slug: str
    ) -> PackInstallation | None:
        from sqlalchemy import select

        res = await session.execute(
            select(PackInstallation).where(
                PackInstallation.business_id == business_id,
                PackInstallation.pack_slug == pack_slug,
            )
        )
        return res.scalar_one_or_none()

    async def list_for_business(
        self, session: AsyncSession, business_id: str
    ) -> list[PackInstallation]:
        from sqlalchemy import select

        res = await session.execute(
            select(PackInstallation)
            .where(PackInstallation.business_id == business_id)
            .order_by(PackInstallation.pack_slug)
        )
        return list(res.scalars().all())

    async def add_item(
        self,
        session: AsyncSession,
        business_id: str,
        installation_id: str,
        *,
        kind: str,
        ref: str,
        entity_id: str,
    ) -> PackInstallationItem:
        item = PackInstallationItem(
            id=new_id(),
            business_id=business_id,
            installation_id=installation_id,
            kind=kind,
            ref=ref,
            entity_id=entity_id,
        )
        session.add(item)
        await session.flush()
        return item


class ActivityRepository:
    """Append-only: create + scoped list. No update, no delete - by design."""

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
        offset: int = 0,
    ) -> list[ActivityEvent]:
        from sqlalchemy import select

        stmt = (
            select(ActivityEvent)
            .where(ActivityEvent.business_id == business_id)
            .order_by(ActivityEvent.created_at.desc(), ActivityEvent.id)
            .limit(limit)
            .offset(offset)
        )
        if project_id is not None:
            stmt = stmt.where(ActivityEvent.project_id == project_id)
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def count(
        self, session: AsyncSession, business_id: str, *, project_id: str | None = None
    ) -> int:
        from sqlalchemy import select

        stmt = (
            select(func.count())
            .select_from(ActivityEvent)
            .where(ActivityEvent.business_id == business_id)
        )
        if project_id is not None:
            stmt = stmt.where(ActivityEvent.project_id == project_id)
        res = await session.execute(stmt)
        return int(res.scalar_one())
