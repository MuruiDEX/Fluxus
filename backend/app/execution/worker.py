"""Dedicated project execution process. Start with python -m app.execution.worker."""

import asyncio
import signal
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings, validate_recovery_bound
from app.core.logging import configure_logging, get_logger
from app.execution.scheduler import run_once
from app.llm.base import LlmProvider, create_provider
from app.persistence.engine import create_engine_from_url, session_factory
from app.usage.pricing import configure_tariffs

log = get_logger(__name__)


@dataclass
class LaneStatus:
    completed_polls: int = 0
    failures: int = 0
    checking: bool = False


async def _sleep_until_stopped(stopping: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stopping.wait(), seconds)
    except TimeoutError:
        pass


async def _lane(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    provider_factory: Callable[[Settings], LlmProvider],
    stopping: asyncio.Event,
    status: LaneStatus,
) -> None:
    while not stopping.is_set():
        status.checking = True
        try:
            found = await run_once(factory, settings, provider_factory, stopping)
        except Exception as exc:
            status.failures += 1
            log.warning(
                "worker poll failed",
                extra={
                    "context": {
                        "error_type": type(exc).__name__,
                        "consecutive_failures": status.failures,
                    }
                },
            )
            delay = min(30.0, 2 ** min(status.failures - 1, 5))
        else:
            status.failures = 0
            status.completed_polls += 1
            delay = 0 if found else settings.execution_worker_poll_s
        finally:
            status.checking = False
        await _sleep_until_stopped(stopping, delay)


async def _heartbeat(lanes: list[LaneStatus], stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        log.info(
            "worker heartbeat",
            extra={
                "context": {
                    "concurrency": len(lanes),
                    "busy_lanes": sum(lane.checking for lane in lanes),
                    "completed_polls": sum(lane.completed_polls for lane in lanes),
                    "failing_lanes": sum(lane.failures > 0 for lane in lanes),
                }
            },
        )
        await _sleep_until_stopped(stopping, 30)


async def serve(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    stopping: asyncio.Event,
    *,
    provider_factory: Callable[[Settings], LlmProvider] = create_provider,
) -> None:
    statuses = [LaneStatus() for _ in range(settings.execution_worker_concurrency)]
    lanes = [
        asyncio.create_task(_lane(factory, settings, provider_factory, stopping, status))
        for status in statuses
    ]
    heartbeat = asyncio.create_task(_heartbeat(statuses, stopping))
    try:
        await stopping.wait()
    finally:
        stopping.set()
        _, pending = await asyncio.wait(lanes, timeout=settings.execution_worker_drain_s)
        if pending:
            log.warning(
                "worker drain deadline reached",
                extra={
                    "context": {
                        "interrupted_lanes": len(pending),
                    }
                },
            )
        for lane in pending:
            lane.cancel()
        # The executor's cancellation path fences ownership and preserves
        # ambiguous exposure. A process killed during cleanup is handled by
        # persisted active slots on the next startup.
        await asyncio.gather(*lanes, heartbeat, return_exceptions=True)
        log.info("worker stopped")


async def _main(settings: Settings) -> None:
    if not settings.database_url or not settings.database_url.startswith(
        ("postgresql://", "postgresql+asyncpg://")
    ):
        raise ValueError("A PostgreSQL database is required for the project worker.")
    validate_recovery_bound(settings)
    configure_tariffs(settings.usage_tariffs_json)
    create_provider(settings)  # Validate provider configuration before discovering work.
    database_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_engine_from_url(
        database_url, statement_timeout_ms=settings.db_statement_timeout_ms
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop(_signum, _frame) -> None:
        loop.call_soon_threadsafe(stopping.set)

    signals = (signal.SIGINT, signal.SIGTERM)
    previous = {sig: signal.signal(sig, request_stop) for sig in signals}
    try:
        log.info("worker starting", extra={"context": {"version": settings.version}})
        await serve(session_factory(engine), settings, stopping)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        await engine.dispose()


def main() -> None:
    configure_logging()
    try:
        settings = get_settings()
        configure_logging(settings.log_level)
        asyncio.run(_main(settings))
    except Exception as exc:
        log.error("worker could not start", extra={"context": {"error_type": type(exc).__name__}})
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
