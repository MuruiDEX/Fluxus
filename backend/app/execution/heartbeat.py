"""Lease heartbeat for one active execution attempt (Stage 18 S2.2.4.2).

The heartbeat runs beside the provider call in its own short-lived database
sessions. Every tick is a single conditional UPDATE that re-proves ownership
(business, task, `working`, generation, owner, live lease and absolute
deadline). It never changes status or generation, never extends the absolute
deadline, and deliberately leaves `updated_at` untouched so the recovery
staleness clock keeps its existing meaning.

Ownership loss is a terminal state for the heartbeat, not a retryable error:
the loop stops and signals the execution layer through `ownership_lost`.
"""

import asyncio
import math
from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.persistence.repositories.concrete import (
    LeaseRenewal,
    TaskOwnership,
    TaskRepository,
)

log = get_logger(__name__)

_tasks = TaskRepository()

# Reasons surfaced to the execution layer. The first one means the database
# proved the caller no longer owns the row; the second means the heartbeat
# could not prove otherwise (fail closed, never a successful renewal).
LOST_OWNERSHIP = "ownership_lost"
LOST_HEARTBEAT_ERROR = "heartbeat_error"


class LeaseHeartbeat:
    """Renew one task lease until stopped or ownership is provably lost."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        ownership: TaskOwnership,
        ttl_seconds: float,
        interval_seconds: float,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("heartbeat ttl must be a finite positive number of seconds")
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("heartbeat interval must be a finite positive number of seconds")
        if interval_seconds >= ttl_seconds:
            raise ValueError("heartbeat interval must be less than the lease ttl")
        self._factory = session_factory
        self._ownership = ownership
        self._ttl = ttl_seconds
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._lost = asyncio.Event()
        self._lost_reason: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._renewals = 0
        self._last_renewal: LeaseRenewal | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def ownership_lost(self) -> bool:
        return self._lost.is_set()

    @property
    def lost_reason(self) -> str | None:
        return self._lost_reason

    @property
    def renewal_count(self) -> int:
        return self._renewals

    @property
    def last_renewal(self) -> LeaseRenewal | None:
        return self._last_renewal

    def start(self) -> None:
        """Start the heartbeat task. Call only after a successful claim commit."""
        if self._task is not None:
            raise RuntimeError("lease heartbeat already started")
        self._task = asyncio.create_task(
            self._run(), name=f"lease-heartbeat:{self._ownership.task_id}"
        )

    async def wait_lost(self) -> None:
        """Resolve as soon as ownership is lost (zero rows or database error)."""
        await self._lost.wait()

    async def stop(self) -> None:
        """Stop gracefully and await the loop; never leave a detached task.

        The loop is signalled rather than cancelled so a renewal already in
        flight can finish its short transaction. If the caller is cancelled
        while waiting, the heartbeat is still awaited to completion (bounded
        by `statement_timeout`) and the cancellation is re-raised afterwards.
        """
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        cancelled: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if task.cancelled() or task.done():
                    break
                cancelled = exc
            except Exception:
                break
        with suppress(asyncio.CancelledError, Exception):
            task.result()
        if cancelled is not None:
            raise cancelled

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                except TimeoutError:
                    pass
                if self._stop.is_set():
                    return
                if not await self._renew_once():
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning(
                "lease heartbeat failed",
                exc_info=True,
                extra={"context": {"task_id": self._ownership.task_id}},
            )
            self._mark_lost(LOST_HEARTBEAT_ERROR)

    async def _renew_once(self) -> bool:
        async with self._factory() as session:
            try:
                renewal = await _tasks.renew_lease(
                    session,
                    self._ownership.business_id,
                    self._ownership.task_id,
                    execution_generation=self._ownership.execution_generation,
                    lease_owner=self._ownership.lease_owner,
                    ttl_seconds=self._ttl,
                )
                await session.commit()
            except BaseException:
                with suppress(Exception):
                    await session.rollback()
                raise
        if not renewal.renewed:
            self._mark_lost(LOST_OWNERSHIP)
            return False
        self._renewals += 1
        self._last_renewal = renewal
        return True

    def _mark_lost(self, reason: str) -> None:
        if self._lost_reason is None:
            self._lost_reason = reason
        self._lost.set()
