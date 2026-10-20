"""Usage ledger + budget reservation repositories (Stage 16).

Discipline:
- `UsageRepository.append` is idempotent on `idempotency_key` and exposes NO
  update/delete — the ledger is append-only by construction.
- `BudgetReservationRepository.reserve` takes the project row lock
  (`SELECT … FOR UPDATE`) so two workers sharing a project serialize on the
  budget instead of racing a read-then-write. The caller MUST keep the
  network call OUTSIDE this transaction.
- `set_status` uses conditional UPDATEs (active → target) so settlement is a
  compare-and-set: exactly one caller settles a reservation.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.enums import ReservationStatus
from app.persistence.exceptions import new_id, validate_id
from app.persistence.models.project import Project
from app.persistence.models.usage import BudgetReservation, UsageRecord
from app.persistence.repositories.base import quantize_money

_PENDING = (ReservationStatus.ACTIVE.value, ReservationStatus.AMBIGUOUS.value)


class UsageRepository:
    model = UsageRecord

    async def get_by_key(
        self, session: AsyncSession, business_id: str, idempotency_key: str
    ) -> UsageRecord | None:
        res = await session.execute(
            select(UsageRecord).where(
                UsageRecord.business_id == business_id,
                UsageRecord.idempotency_key == idempotency_key,
            )
        )
        return res.scalar_one_or_none()

    async def append(
        self, session: AsyncSession, business_id: str, data: dict[str, Any]
    ) -> UsageRecord:
        """Insert one canonical ledger row; re-appending the same key is a no-op."""
        key = str(data["idempotency_key"])
        existing = await self.get_by_key(session, business_id, key)
        if existing is not None:
            return existing
        payload = dict(data)
        raw_id = payload.get("id")
        payload["id"] = raw_id if raw_id is not None else new_id()
        validate_id(payload["id"])
        if payload.get("cost_amount") is not None:
            payload["cost_amount"] = quantize_money(payload["cost_amount"])
        payload["business_id"] = business_id
        payload.setdefault("occurred_at", datetime.now(UTC))
        record = UsageRecord(**payload)
        try:
            # Savepoint so a concurrent writer that wins the UNIQUE
            # idempotency_key race doesn't abort the caller's transaction.
            async with session.begin_nested():
                session.add(record)
                await session.flush()
        except IntegrityError:
            existing = await self.get_by_key(session, business_id, key)
            if existing is None:
                raise
            return existing
        return record

    async def list_for_business(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[UsageRecord]:
        stmt = select(UsageRecord).where(UsageRecord.business_id == business_id)
        if project_id is not None:
            stmt = stmt.where(UsageRecord.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(UsageRecord.task_id == task_id)
        stmt = (
            stmt.order_by(UsageRecord.occurred_at.desc(), UsageRecord.id)
            .limit(limit)
            .offset(offset)
        )
        res = await session.execute(stmt)
        return list(res.scalars().all())

    async def count_for_business(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.business_id == business_id)
        )
        if project_id is not None:
            stmt = stmt.where(UsageRecord.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(UsageRecord.task_id == task_id)
        res = await session.execute(stmt)
        return int(res.scalar_one())

    async def totals_by_status(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, tuple[Decimal, int]]:
        """`status -> (summed cost, row count)` for the requested scope."""
        stmt = (
            select(
                UsageRecord.usage_status,
                func.coalesce(func.sum(UsageRecord.cost_amount), 0),
                func.count(),
            )
            .where(UsageRecord.business_id == business_id)
            .group_by(UsageRecord.usage_status)
        )
        if project_id is not None:
            stmt = stmt.where(UsageRecord.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(UsageRecord.task_id == task_id)
        rows = (await session.execute(stmt)).all()
        return {
            str(status): (quantize_money(total), int(count)) for status, total, count in rows
        }

    async def totals_by_call_kind(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, dict[str, tuple[Decimal, int]]]:
        """`call_kind -> status -> (summed cost, row count)` for the scope.

        Rows with a NULL `call_kind` (legacy Stage 16 rows written before the
        column) are grouped under the empty-string key so they are never lost
        from the summary.
        """
        stmt = (
            select(
                func.coalesce(UsageRecord.call_kind, ""),
                UsageRecord.usage_status,
                func.coalesce(func.sum(UsageRecord.cost_amount), 0),
                func.count(),
            )
            .where(UsageRecord.business_id == business_id)
            .group_by(UsageRecord.call_kind, UsageRecord.usage_status)
        )
        if project_id is not None:
            stmt = stmt.where(UsageRecord.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(UsageRecord.task_id == task_id)
        rows = (await session.execute(stmt)).all()
        out: dict[str, dict[str, tuple[Decimal, int]]] = {}
        for kind, status, total, count in rows:
            out.setdefault(str(kind), {})[str(status)] = (quantize_money(total), int(count))
        return out

    async def taskless_confirmed_total(
        self, session: AsyncSession, business_id: str, project_id: str
    ) -> Decimal:
        """Confirmed project-scoped usage with NO task (Stage 16.1).

        Disjoint from `Task.cost_accrued` (which is confirmed task-linked only),
        so adding this to project spend never double-counts.
        """
        stmt = select(func.coalesce(func.sum(UsageRecord.cost_amount), 0)).where(
            UsageRecord.business_id == business_id,
            UsageRecord.project_id == project_id,
            UsageRecord.task_id.is_(None),
            UsageRecord.usage_status == "confirmed",
        )
        total = (await session.execute(stmt)).scalar_one()
        return quantize_money(total)

    async def taskless_estimated_total(
        self, session: AsyncSession, business_id: str, project_id: str
    ) -> Decimal:
        """Estimated project-scoped usage with NO task — fail-closed headroom.

        Counted against the budget ceiling (not confirmed spend): an estimated
        task-less call is real exposure whose exact cost is uncertain.
        """
        stmt = select(func.coalesce(func.sum(UsageRecord.cost_amount), 0)).where(
            UsageRecord.business_id == business_id,
            UsageRecord.project_id == project_id,
            UsageRecord.task_id.is_(None),
            UsageRecord.usage_status == "estimated",
        )
        total = (await session.execute(stmt)).scalar_one()
        return quantize_money(total)

    async def confirmed_cost_for_task(
        self, session: AsyncSession, business_id: str, task_id: str
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(UsageRecord.cost_amount), 0)).where(
            UsageRecord.business_id == business_id,
            UsageRecord.task_id == task_id,
            UsageRecord.usage_status == "confirmed",
        )
        total = (await session.execute(stmt)).scalar_one()
        return quantize_money(total)


class BudgetReservationRepository:
    model = BudgetReservation

    async def get_by_key(
        self, session: AsyncSession, business_id: str, idempotency_key: str
    ) -> BudgetReservation | None:
        res = await session.execute(
            select(BudgetReservation).where(
                BudgetReservation.business_id == business_id,
                BudgetReservation.idempotency_key == idempotency_key,
            )
        )
        return res.scalar_one_or_none()

    async def pending_total(
        self, session: AsyncSession, business_id: str, project_id: str
    ) -> Decimal:
        """Sum of active + ambiguous reservations — the reserved-but-unsettled live exposure."""
        stmt = select(func.coalesce(func.sum(BudgetReservation.amount), 0)).where(
            BudgetReservation.business_id == business_id,
            BudgetReservation.project_id == project_id,
            BudgetReservation.status.in_(_PENDING),
        )
        total = (await session.execute(stmt)).scalar_one()
        return Decimal(str(total))

    async def reserve(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str,
        task_id: str | None = None,
        attempt: int = 0,
        attempt_key: str,
        amount: Decimal,
        currency: str = "USD",
        mode: str = "guaranteed",
    ) -> BudgetReservation | None:
        """Reserve against the project's `ai_budget`, atomically.

        Locks the project row, sums confirmed spend + task-less estimated
        exposure + pending reservations, and inserts an `active` reservation
        iff the budget ceiling still holds. Returns None when the reservation
        would breach the ceiling (caller aborts the whole surrounding
        transaction, including any task claim). A `budget`/`ai_budget` of 0 or
        NULL means "no limit": still reserve, so exposure stays visible, but
        never block.

        `task_id` is optional (Stage 16.1): Team Lead calls may be project-scoped
        without a task. Still requires a `project_id` (there is no budget to
        reserve against without a project).
        """
        amount = quantize_money(amount)
        lock = (
            select(Project)
            .where(Project.business_id == business_id, Project.id == project_id)
            .with_for_update()
        )
        project = (await session.execute(lock)).scalar_one_or_none()
        if project is None:
            return None
        # A prior run of the SAME attempt that never settled (e.g. the process
        # crashed mid-call) may have reached the provider. Converting its
        # `active` reservation to `ambiguous` is fail-closed: the exposure
        # keeps counting, and it is never auto-released. (Different attempt
        # keys — i.e. genuine retries — keep their own reservations.)
        await session.execute(
            update(BudgetReservation)
            .where(
                BudgetReservation.business_id == business_id,
                BudgetReservation.attempt_key == attempt_key,
                BudgetReservation.status == ReservationStatus.ACTIVE.value,
            )
            .values(
                status=ReservationStatus.AMBIGUOUS.value,
                reason="superseded_attempt",
            )
        )
        budget = project.ai_budget if project.ai_budget is not None else Decimal("0")
        if budget > 0:
            pending = await self.pending_total(session, business_id, project_id)
            # Task-less estimated usage is real exposure not captured by
            # ai_spent (which counts confirmed only): count it fail-closed.
            taskless_estimated = await UsageRepository().taskless_estimated_total(
                session, business_id, project_id
            )
            committed = (
                Decimal(str(project.ai_spent or 0)) + taskless_estimated + pending
            )
            if committed + amount > budget:
                return None
        last_seq = (
            await session.execute(
                select(func.coalesce(func.max(BudgetReservation.run_seq), 0)).where(
                    BudgetReservation.business_id == business_id,
                    BudgetReservation.attempt_key == attempt_key,
                )
            )
        ).scalar_one()
        run_seq = int(last_seq) + 1
        reservation = BudgetReservation(
            id=new_id(),
            business_id=business_id,
            project_id=project_id,
            task_id=task_id,
            attempt=attempt,
            attempt_key=attempt_key,
            run_seq=run_seq,
            amount=amount,
            currency=currency,
            status=ReservationStatus.ACTIVE.value,
            mode=mode,
            idempotency_key=f"reserve:{attempt_key}:{run_seq}",
        )
        session.add(reservation)
        await session.flush()
        return reservation

    async def set_status(
        self,
        session: AsyncSession,
        business_id: str,
        reservation_id: str,
        *,
        from_statuses: tuple[str, ...],
        to_status: str,
        reason: str = "",
        usage_record_id: str | None = None,
    ) -> bool:
        """Atomic compare-and-set on the reservation. Returns True iff applied."""
        values: dict[str, Any] = {"status": to_status, "reason": reason}
        if usage_record_id is not None:
            values["usage_record_id"] = usage_record_id
        stmt = (
            update(BudgetReservation)
            .where(
                BudgetReservation.business_id == business_id,
                BudgetReservation.id == reservation_id,
                BudgetReservation.status.in_(from_statuses),
            )
            .values(**values)
        )
        result = await session.execute(stmt)
        await session.flush()
        return (result.rowcount or 0) == 1

    async def mark_active_ambiguous(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        reason: str = "recovery_stale",
        task_id: str | None = None,
        project_id: str | None = None,
        attempt_key: str | None = None,
    ) -> int:
        """Convert crashed `active` holds to `ambiguous` (fail-closed).

        A run that never settled may have reached the provider, so its hold is
        NEVER released — it keeps counting against the budget until an operator
        reconciles it. Scope by any combination of task/project/attempt; with no
        scope this would match every active hold, so callers must pass one
        (enforced by the tests, not the DB).
        """
        stmt = (
            update(BudgetReservation)
            .where(
                BudgetReservation.business_id == business_id,
                BudgetReservation.status == ReservationStatus.ACTIVE.value,
            )
            .values(status=ReservationStatus.AMBIGUOUS.value, reason=reason)
        )
        if task_id is not None:
            stmt = stmt.where(BudgetReservation.task_id == task_id)
        if project_id is not None:
            stmt = stmt.where(BudgetReservation.project_id == project_id)
        if attempt_key is not None:
            stmt = stmt.where(BudgetReservation.attempt_key == attempt_key)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount or 0)

    async def mark_task_active_ambiguous(
        self,
        session: AsyncSession,
        business_id: str,
        task_id: str,
        reason: str = "recovery_stale",
    ) -> int:
        """Task-scoped convenience wrapper (Stage 16 recovery)."""
        return await self.mark_active_ambiguous(
            session, business_id, reason=reason, task_id=task_id
        )

    async def release_aged_ambiguous(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        older_than_minutes: int,
        task_id: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Release `ambiguous` holds older than the configured age.

        Disabled by default (0 == never): releasing a possibly-billed hold is
        an explicit operator decision, never an automatic assumption.
        """
        if older_than_minutes <= 0:
            return 0
        stmt = (
            update(BudgetReservation)
            .where(
                BudgetReservation.business_id == business_id,
                BudgetReservation.status == ReservationStatus.AMBIGUOUS.value,
                BudgetReservation.created_at
                < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, older_than_minutes * 60),
            )
            .values(
                status=ReservationStatus.RELEASED.value,
                reason="aged_out",
            )
        )
        if task_id is not None:
            stmt = stmt.where(BudgetReservation.task_id == task_id)
        if project_id is not None:
            stmt = stmt.where(BudgetReservation.project_id == project_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount or 0)

    async def list_pending(
        self, session: AsyncSession, business_id: str, *, project_id: str | None = None
    ) -> list[BudgetReservation]:
        stmt = select(BudgetReservation).where(
            BudgetReservation.business_id == business_id,
            BudgetReservation.status.in_(_PENDING),
        )
        if project_id is not None:
            stmt = stmt.where(BudgetReservation.project_id == project_id)
        res = await session.execute(stmt.order_by(BudgetReservation.id))
        return list(res.scalars().all())

    async def status_totals(
        self,
        session: AsyncSession,
        business_id: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Decimal]:
        stmt = select(
            BudgetReservation.status, func.coalesce(func.sum(BudgetReservation.amount), 0)
        ).where(BudgetReservation.business_id == business_id)
        if project_id is not None:
            stmt = stmt.where(BudgetReservation.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(BudgetReservation.task_id == task_id)
        stmt = stmt.group_by(BudgetReservation.status)
        rows = (await session.execute(stmt)).all()
        return {str(status): Decimal(str(total)) for status, total in rows}
